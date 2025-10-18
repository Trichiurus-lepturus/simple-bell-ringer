import subprocess
import logging
import os
import platform
import time
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
                f"执行播放命令：{' '.join(command_list)} （任务: {task.description}）"
            )
            if IS_WINDOWS:
                self._current_process = subprocess.Popen(
                    command_list,
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=(
                        subprocess.CREATE_NEW_PROCESS_GROUP  # pyright: ignore
                        | subprocess.DETACHED_PROCESS  # pyright: ignore
                    ),
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
            if exit_code := self._current_process.poll() is not None:
                logger.error(f"播放进程创建失败，立即退出，退出码: {exit_code}")
                self._current_process = None
                return False
            time.sleep(0.5)  # 给进程0.5秒启动时间
            if exit_code := self._current_process.poll() is not None:
                logger.error(f"播放进程启动后不稳定，0.5秒后退出，退出码: {exit_code}")
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
            process.terminate()
            try:
                process.wait(timeout=2)
                logger.info("已停止当前播放")
                return True
            except subprocess.TimeoutExpired:
                logger.warning(
                    "播放进程在2秒内未响应，强制终止",
                    exc_info=True,
                )
                process.kill()
                process.wait()
                logger.info("已强制停止播放")
                return True
        except ProcessLookupError:
            logger.debug("播放进程已自然结束")
            return True
        except Exception as e:
            logger.error(f"停止播放时出错：{e}", exc_info=True)
            return False

    def is_playing(self) -> bool:
        if self._current_process is None:
            return False
        return self._current_process.poll() is None

    def __del__(self):
        if self._current_process is not None:
            self.stop()
