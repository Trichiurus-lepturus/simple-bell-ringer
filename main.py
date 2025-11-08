import os
import sys
import signal
import threading
import logging
import platform
from datetime import datetime
from logging import handlers
from typing import Optional, Union, Callable, Any
from types import FrameType

from config import Config
from scheduler import Scheduler
from player import AudioPlayer
from poller import BellPoller
from refresher import TaskRefresher

logger = logging.getLogger(__name__)

SignalHandler = Union[
    Callable[[int, Optional[FrameType]], Any],
    int,
    signal.Handlers,
    None,
]


class ColorFormatter(logging.Formatter):
    COLOR_MAP = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[38;5;214m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[41;30m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord):
        formatted_message = super().format(record)
        if record.levelno in self.COLOR_MAP:
            color = self.COLOR_MAP[record.levelno]
            levelname_with_color = f"{color}{record.levelname}{self.RESET}"
            formatted_message = formatted_message.replace(
                f"[{record.levelname}]", f"[{levelname_with_color}]"
            )
        return formatted_message


def enable_windows_ansi_support() -> None:
    if not sys.platform == "win32":
        return

    import ctypes
    import ctypes.wintypes as wintypes

    STD_OUTPUT_HANDLE = -11
    STD_ERROR_HANDLE = -12
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    kernel32 = ctypes.windll.kernel32
    h_out = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    h_err = kernel32.GetStdHandle(STD_ERROR_HANDLE)
    if h_out == -1:
        raise RuntimeError("无法获取标准输出句柄")
    if h_err == -1:
        raise RuntimeError("无法获取标准错误句柄")
    mode_out = wintypes.DWORD()
    mode_err = wintypes.DWORD()
    if not kernel32.GetConsoleMode(h_out, ctypes.byref(mode_out)):
        raise RuntimeError("无法获取标准输出的控制台模式")
    if not kernel32.GetConsoleMode(h_err, ctypes.byref(mode_err)):
        raise RuntimeError("无法获取标准错误的控制台模式")
    mode_out.value |= ENABLE_VIRTUAL_TERMINAL_PROCESSING
    mode_err.value |= ENABLE_VIRTUAL_TERMINAL_PROCESSING
    if not kernel32.SetConsoleMode(h_out, mode_out):
        raise RuntimeError("无法为标准输出设置虚拟终端处理模式")
    if not kernel32.SetConsoleMode(h_err, mode_err):
        raise RuntimeError("无法为标准错误设置虚拟终端处理模式")


def setup_logging():
    ansi_error: Optional[str] = None
    try:
        enable_windows_ansi_support()
    except Exception as e:
        ansi_error = str(e)
    log_dir = Config.LOG_DIRECTORY
    os.makedirs(log_dir, exist_ok=True)
    log_filename = datetime.now().strftime("bell_ringer_%Y%m%d_%H%M%S.log")
    log_filepath = os.path.join(log_dir, log_filename)
    log_format = "%(asctime)s [%(levelname)s] %(name)-10s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    console_formatter = ColorFormatter(log_format, datefmt=date_format)
    file_formatter = logging.Formatter(log_format, datefmt=date_format)
    handlers_config: list[tuple[logging.Handler, logging.Formatter]] = [
        (logging.FileHandler(log_filepath, encoding="utf-8"), file_formatter),
        (logging.StreamHandler(), console_formatter),
        (
            handlers.RotatingFileHandler(
                os.path.join(log_dir, "bell_ringer_latest.log"),
                maxBytes=32 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            ),
            file_formatter,
        ),
    ]
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers = []
    for handler, formatter in handlers_config:
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    if sys.platform == "win32":
        if ansi_error:
            logger.warning(f"启用 Windows ANSI 支持失败：{ansi_error}")
        else:
            logger.info("Windows 控制台 ANSI 颜色支持已启用")


class Application:
    def __init__(self):
        self._scheduler = Scheduler()
        self._audio_player = AudioPlayer()
        self._refresher = TaskRefresher(self._scheduler)
        self._poller = BellPoller(self._scheduler, self._audio_player)

        self._shutdown_event = threading.Event()
        self._is_running = False
        self._lock = threading.Lock()
        self._original_handlers: dict[signal.Signals, SignalHandler] = {}

        self._win32_handler: Optional[Callable[[int], bool]] = None

        logger.info("打铃系统应用程序初始化完成")
        logger.info(f"运行平台：{platform.system()} {platform.release()}")
        logger.info("=" * 48)

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

            logger.info("打铃系统启动成功！")
            logger.info(f"任务刷新时间：{Config.TASK_REFRESH_TIME}")
            logger.info(f"轮询间隔：{Config.POLLING_INTERVAL}秒")
            logger.info(f"时间容差：±{Config.TIME_TOLERANCE}秒")
            logger.info("=" * 48)

        except Exception as e:
            logger.error(f"启动失败：{e}", exc_info=True)
            with self._lock:
                self._is_running = False
            self.shutdown()
            raise

    def wait_for_shutdown(self) -> None:
        if sys.platform == "win32":
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

            logger.info("所有组件已停止")
            logger.info("=" * 48)

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

            if sys.platform == "win32":
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
            if sys.platform == "win32" and self._win32_handler is not None:
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

    logger.info("打铃系统启动中...")
    logger.info(f"Python 版本：{sys.version}")
    logger.info(f"操作系统：{platform.system()} {platform.release()}")
    logger.info(f"配置文件：{Config.__name__}")
    logger.info(f"日志目录：{Config.LOG_DIRECTORY}")
    logger.info("=" * 48)

    app = Application()
    try:
        app.run()
    except Exception as e:
        logger.critical(f"应用程序异常退出：{e}", exc_info=True)
        return 1
    finally:
        logger.info("打铃系统已退出")
        logger.info("=" * 48)

    return 0


if __name__ == "__main__":
    sys.exit(main())
