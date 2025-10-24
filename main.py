import os
import sys
import signal
import threading
import logging
import platform
from datetime import datetime
from logging import handlers
from typing import Optional, Dict, Union, Callable, Any
from types import FrameType

from config import Config
from scheduler import Scheduler
from player import AudioPlayer
from poller import BellPoller
from refresher import TaskRefresher

logger = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"

SignalHandler = Union[
    Callable[[int, Optional[FrameType]], Any],
    int,
    signal.Handlers,
    None,
]


def setup_logging():
    log_dir = Config.LOG_DIRECTORY
    os.makedirs(log_dir, exist_ok=True)
    log_filename = datetime.now().strftime("bell_ringer_%Y%m%d_%H%M%S.log")
    log_filepath = os.path.join(log_dir, log_filename)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)-10s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_filepath, encoding="utf-8"),
            logging.StreamHandler(),
            handlers.RotatingFileHandler(
                os.path.join(log_dir, "bell_ringer_latest.log"),
                maxBytes=32 * 1024 * 1024,  # 32MB
                backupCount=5,
                encoding="utf-8",
            ),
        ],
    )


class Application:
    def __init__(self):
        self._scheduler = Scheduler()
        self._audio_player = AudioPlayer()
        self._refresher = TaskRefresher(self._scheduler)
        self._poller = BellPoller(self._scheduler, self._audio_player)

        self._shutdown_event = threading.Event()
        self._is_running = False
        self._lock = threading.Lock()
        self._original_handlers: Dict[signal.Signals, SignalHandler] = {}

        self._win32_handler: Optional[Callable[[int], bool]] = None

        logger.info("=" * 60)
        logger.info("打铃系统应用程序初始化完成")
        logger.info(f"运行平台：{platform.system()} {platform.release()}")
        logger.info("=" * 60)

    def start(self) -> None:
        with self._lock:
            if self._is_running:
                logger.warning("应用程序已在运行中")
                return
            self._is_running = True

        logger.info("正在启动打铃系统...")
        try:
            self._register_signal_handlers()
            logger.info("启动任务刷新器...")
            self._refresher.start()
            logger.info("启动打铃轮询器...")
            self._poller.start()

            logger.info("=" * 60)
            logger.info("打铃系统启动成功！")
            logger.info(f"任务刷新时间：{Config.TASK_REFRESH_TIME}")
            logger.info(f"轮询间隔：{Config.POLLING_INTERVAL}秒")
            logger.info(f"时间容差：±{Config.TIME_TOLERANCE.total_seconds()}秒")
            logger.info("=" * 60)

        except Exception as e:
            logger.error(f"启动失败：{e}", exc_info=True)
            with self._lock:
                self._is_running = False
            self.shutdown()
            raise

    def wait_for_shutdown(self) -> None:
        if IS_WINDOWS:
            logger.info("应用程序运行中，按 Ctrl+C 或 Ctrl+Break 退出...")
        else:
            logger.info("应用程序运行中，按 Ctrl+C 或发送 SIGTERM 信号退出...")
        try:
            self._shutdown_event.wait()
        except KeyboardInterrupt:
            logger.info("收到键盘中断信号")

    def shutdown(
        self, signum: Optional[int] = None, frame: Optional[FrameType] = None
    ) -> None:
        with self._lock:
            if not self._is_running and signum is None:
                logger.debug("应用程序未运行，无需关闭")
                return
            self._is_running = False

        if signum is not None:
            try:
                signal_name = signal.Signals(signum).name
            except (ValueError, AttributeError):
                signal_name = str(signum)
            logger.info(f"收到信号 {signal_name} ({signum})，开始关闭...")
        else:
            logger.info("开始关闭应用程序...")

        self._shutdown_event.set()
        try:
            logger.info("停止打铃轮询器...")
            try:
                self._poller.stop(timeout=5.0)
            except Exception as e:
                logger.error(f"停止轮询器时出错：{e}", exc_info=True)
            logger.info("停止任务刷新器...")
            try:
                self._refresher.stop(timeout=5.0)
            except Exception as e:
                logger.error(f"停止刷新器时出错：{e}", exc_info=True)
            logger.info("停止音频播放器...")
            try:
                self._audio_player.stop()
            except Exception as e:
                logger.error(f"停止音频播放器时出错：{e}", exc_info=True)

            logger.info("=" * 60)
            logger.info("所有组件已停止")
            logger.info("=" * 60)

        except Exception as e:
            logger.error(f"关闭过程中出错：{e}", exc_info=True)
        finally:
            self._restore_signal_handlers()

    def _signal_handler(self, signum: int, frame: Optional[FrameType]) -> None:
        self.shutdown(signum, frame)

    def _register_signal_handlers(self) -> None:
        try:
            self._original_handlers[signal.SIGINT] = signal.signal(
                signal.SIGINT, self._signal_handler
            )
            logger.info("已注册 SIGINT (Ctrl+C) 处理器")

            if IS_WINDOWS:
                try:
                    sigbreak = getattr(signal, "SIGBREAK", None)
                    if sigbreak is not None:
                        self._original_handlers[sigbreak] = signal.signal(
                            sigbreak, self._signal_handler
                        )
                        logger.info("已注册 SIGBREAK (Ctrl+Break) 处理器")
                    else:
                        logger.warning("当前 Python 环境不支持 SIGBREAK")
                except (AttributeError, OSError) as e:
                    logger.warning(f"无法注册 SIGBREAK 处理器：{e}")
                self._register_windows_console_handler()

            else:
                try:
                    self._original_handlers[signal.SIGTERM] = signal.signal(
                        signal.SIGTERM, self._signal_handler
                    )
                    logger.info("已注册 SIGTERM 处理器")
                except (AttributeError, OSError) as e:
                    logger.warning(f"无法注册 SIGTERM 处理器：{e}")

        except Exception as e:
            logger.warning(f"注册信号处理器失败：{e}")

    def _register_windows_console_handler(self) -> None:
        try:
            import win32api
            import win32con

            def windows_console_handler(event_type: int) -> bool:
                if event_type in (
                    win32con.CTRL_CLOSE_EVENT,
                    win32con.CTRL_LOGOFF_EVENT,
                    win32con.CTRL_SHUTDOWN_EVENT,
                ):
                    logger.info(f"收到 Windows 控制台事件：{event_type}")
                    self.shutdown()
                    return True
                return False

            win32api.SetConsoleCtrlHandler(windows_console_handler, True)
            self._win32_handler = windows_console_handler
            logger.info("已注册 Windows 控制台事件处理器")

        except ImportError:
            logger.debug("pywin32 未安装，跳过 Windows 控制台事件处理器")
        except Exception as e:
            logger.warning(f"注册 Windows 控制台处理器失败：{e}")

    def _restore_signal_handlers(self) -> None:
        try:
            for sig, handler in self._original_handlers.items():
                if handler is not None:
                    signal.signal(sig, handler)
            if self._original_handlers:
                logger.debug("信号处理器已恢复")
            if IS_WINDOWS and self._win32_handler is not None:
                try:
                    import win32api

                    win32api.SetConsoleCtrlHandler(self._win32_handler, False)
                except Exception as e:
                    logger.debug(f"清理 Windows 控制台处理器失败：{e}")

        except Exception as e:
            logger.warning(f"恢复信号处理器失败：{e}")

    def run(self) -> None:
        try:
            self.start()
            self.wait_for_shutdown()
        except KeyboardInterrupt:
            logger.info("收到键盘中断")
        except Exception as e:
            logger.error(f"应用程序运行出错：{e}", exc_info=True)
        finally:
            self.shutdown()

    def is_running(self) -> bool:
        with self._lock:
            return self._is_running


def main() -> int:
    setup_logging()

    logger.info("=" * 60)
    logger.info("打铃系统启动中...")
    logger.info(f"Python 版本：{sys.version}")
    logger.info(f"操作系统：{platform.system()} {platform.release()}")
    logger.info(f"配置文件：{Config.__name__}")
    logger.info(f"日志目录：{Config.LOG_DIRECTORY}")
    logger.info("=" * 60)

    app = Application()
    try:
        app.run()
    except Exception as e:
        logger.critical(f"应用程序异常退出：{e}", exc_info=True)
        return 1
    finally:
        logger.info("=" * 60)
        logger.info("打铃系统已退出")
        logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
