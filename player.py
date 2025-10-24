import subprocess
import logging
import os
import platform
import time
import signal
from typing import Optional

from task import Task
from config import Config

logger = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"


class AudioPlayer:
    def __init__(self):
        self._current_process: Optional[subprocess.Popen[bytes]] = None
        self._validate_config()
        logger.info("播放器初始化完成")

    def _validate_config(self):
        if Config.AUDIO_PATH_PLACEHOLDER not in Config.RING_COMMAND_LIST:
            raise ValueError("RING_COMMAND_LIST 必须包含音频路径占位符")

    def run(self, task: Task) -> bool:
        self.stop()
        if not os.path.exists(task.audio_path):
            logger.error(f"音频文件不存在：{task.audio_path}", exc_info=True)
            return False
        try:
            command_list = [
                task.audio_path if part == Config.AUDIO_PATH_PLACEHOLDER else part
                for part in Config.RING_COMMAND_LIST
            ]
            logger.info(
                f"执行播放命令：{' '.join(command_list)} （任务：{task.description}）"
            )
            if IS_WINDOWS:
                self._current_process = subprocess.Popen(
                    command_list,
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                )
            else:
                self._current_process = subprocess.Popen(
                    command_list,
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            if (exit_code := self._current_process.poll()) is not None:
                logger.error(f"播放进程创建失败，立即退出，退出码：{exit_code}")
                self._current_process = None
                return False
            time.sleep(0.5)  # 给进程0.5秒启动时间
            if (exit_code := self._current_process.poll()) is not None:
                logger.error(f"播放进程启动后不稳定，0.5秒后退出，退出码：{exit_code}")
                self._current_process = None
                return False
            logger.info(f"开始播放：{task.description}")
            return True
        except Exception as e:
            logger.error(f"播放失败：{e}", exc_info=True)
            self._current_process = None
            return False

    def stop(self) -> bool:
        if self._current_process is None:
            return True
        process = self._current_process
        self._current_process = None
        try:
            if IS_WINDOWS:
                return self._stop_windows(process)
            else:
                return self._stop_unix(process)
        except Exception as e:
            logger.error(f"停止播放时出错：{e}", exc_info=True)
            return False

    def _stop_windows(self, process: subprocess.Popen[bytes]) -> bool:
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            if result.returncode == 0:
                logger.info("已停止当前播放（终止进程树）")
                return True
            elif result.returncode == 128:
                logger.debug("播放进程已自然结束")
                return True
            else:
                logger.warning(f"taskkill 返回非零退出码：{result.returncode}")
                return False
        except subprocess.TimeoutExpired:
            logger.error("taskkill 执行超时")
            return False

    def _stop_unix(self, process: subprocess.Popen[bytes]) -> bool:
        try:
            pgid = os.getpgid(process.pid)
            os.killpg(pgid, signal.SIGTERM)
            logger.debug(f"已向进程组 {pgid} 发送 SIGTERM")
            try:
                process.wait(timeout=2)
                logger.info("已停止当前播放（终止进程组）")
                return True
            except subprocess.TimeoutExpired:
                # 超时则发送 SIGKILL
                logger.warning("进程组在2秒内未响应，发送 SIGKILL")
                os.killpg(pgid, signal.SIGKILL)
                process.wait()
                logger.info("已强制停止播放")
                return True
        except ProcessLookupError:
            logger.debug("播放进程已自然结束")
            return True
        except PermissionError:
            logger.error("没有权限终止进程组")
            return False

    def is_playing(self) -> bool:
        if self._current_process is None:
            return False
        return self._current_process.poll() is None

    def __del__(self):
        if self._current_process is not None:
            self.stop()
