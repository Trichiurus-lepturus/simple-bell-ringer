import subprocess
import logging
import os
import platform
from typing import Optional

from task import Task
from config import Config

logger = logging.getLogger(__name__)


class AudioPlayer:
    def __init__(self):
        self._current_process: Optional[subprocess.Popen[bytes]] = None

    def run(self, task: Task) -> bool:
        self.stop()
        if not os.path.exists(task.audio_path):
            logger.error(f"音频文件不存在：{task.audio_path}", exc_info=True)
            return False
        try:
            command_template = Config.RING_COMMAND
            command_str = self._prepare_command(command_template, task.audio_path)
            logger.info(f"执行播放命令：{command_str}")
            self._current_process = subprocess.Popen(
                command_str,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            if self._current_process.poll() is not None:
                logger.error("播放进程启动后立即退出")
                self._current_process = None
                return False
            logger.info(f"开始播放：{task.description}")
            return True
        except Exception as e:
            logger.error(f"播放失败：{e}", exc_info=True)
            self._current_process = None
            return False

    def _prepare_command(self, command_template: str, audio_path: str) -> str:
        safe_path = self._escape_path(audio_path)
        if Config.AUDIO_PATH_PLACEHOLDER in command_template:
            return command_template.replace(Config.AUDIO_PATH_PLACEHOLDER, safe_path)
        else:
            logger.error(f"打铃命令错误，未包含 {Config.AUDIO_PATH_PLACEHOLDER} 占位符")
            raise ValueError("打铃命令未包含音频路径占位符")

    def _escape_path(self, path: str) -> str:
        system = platform.system().lower()
        if system == "windows":
            # Windows: 使用双引号包裹路径，替换正斜杠为反斜杠
            normalized_path = path.replace("/", "\\").replace('"', '\\"')
            return f'"{normalized_path}"'
        else:
            # Unix-like: 使用单引号，并在单引号内转义单引号
            escaped_path = path.replace("'", "'\\''")
            return f"'{escaped_path}'"

    def stop(self) -> bool:
        if self._current_process is None:
            return True
        process = self._current_process
        self._current_process = None
        try:
            process.terminate()
            try:
                process.wait(timeout=Config.PROCESS_TERMINATE_TIMEOUT)
                logger.info("已停止当前播放")
                return True
            except subprocess.TimeoutExpired:
                logger.warning(
                    f"播放进程在 {Config.PROCESS_TERMINATE_TIMEOUT} 秒内未响应，强制终止",
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
