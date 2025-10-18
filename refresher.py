import threading
from datetime import datetime, timedelta
import logging
from typing import Optional

from config import Config
from scheduler import Scheduler

logger = logging.getLogger(__name__)


class TaskRefresher:
    def __init__(self, task_scheduler: Scheduler):
        self._task_scheduler = task_scheduler
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._lock = threading.Lock()

        self._validate_config()

    def _validate_config(self):
        try:
            datetime.strptime(Config.TASK_REFRESH_TIME, "%H:%M:%S")
        except ValueError as e:
            raise ValueError(
                f"配置的 TASK_REFRESH_TIME '{Config.TASK_REFRESH_TIME}' "
                f"格式错误，应为 HH:MM:SS 格式: {e}"
            )

    def _calculate_next_run_time(
        self, reference_time: Optional[datetime] = None
    ) -> datetime:
        now = reference_time or datetime.now()
        target_time = datetime.strptime(Config.TASK_REFRESH_TIME, "%H:%M:%S")
        next_run = now.replace(
            hour=target_time.hour,
            minute=target_time.minute,
            second=target_time.second,
            microsecond=0,
        )
        if next_run <= now:
            next_run += timedelta(days=1)
        return next_run

    def _task_callback(self):
        logger.info("开始刷新任务列表")
        try:
            self._task_scheduler.refresh_task_list()
            logger.info("刷新任务列表完成")
        except Exception as e:
            logger.error(f"刷新任务列表失败: {e}", exc_info=True)

    def _run_loop(self):
        logger.info("任务刷新器线程已启动")
        try:
            self._task_callback()
        except Exception as e:
            logger.error(f"首次刷新任务失败: {e}", exc_info=True)

        while not self._stop_event.is_set():
            try:
                now = datetime.now()
                next_run = self._calculate_next_run_time(reference_time=now)
                delay = max(0, (next_run - now).total_seconds())
                logger.info(
                    f"下次刷新时间：{next_run.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"(延迟 {delay:.2f} 秒)"
                )
                if self._stop_event.wait(timeout=delay):
                    logger.info("收到停止信号，退出循环")
                    break
                if not self._stop_event.is_set():
                    self._task_callback()
            except Exception as e:
                logger.error(f"刷新循环异常: {e}", exc_info=True)
                logger.info("30秒后重试...")
                if self._stop_event.wait(timeout=30):
                    logger.info("重试等待期间收到停止信号")
                    break

        with self._lock:
            self._running = False
        logger.info("任务刷新器线程已退出")

    def start(self):
        with self._lock:
            if self._running:
                logger.warning("任务刷新器已在运行中")
                return
            self._running = True
            self._stop_event.clear()

        logger.info(f"启动任务刷新器，刷新时间: {Config.TASK_REFRESH_TIME}")
        try:
            self._thread = threading.Thread(
                target=self._run_loop, name="TaskRefresherThread", daemon=False
            )
            self._thread.start()
            logger.info("任务刷新器启动成功")
        except Exception as e:
            logger.error(f"启动任务刷新器失败: {e}", exc_info=True)
            with self._lock:
                self._running = False
            raise

    def stop(self, timeout: float = 5.0):
        with self._lock:
            if not self._running:
                logger.info("任务刷新器未在运行")
                return
            self._running = False

        logger.info("正在停止任务刷新器...")
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            logger.info(f"等待任务刷新线程完成（超时 {timeout} 秒）...")
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning(f"任务刷新线程未在 {timeout} 秒内退出，但停止信号已发送")
            else:
                logger.info("任务刷新线程已成功退出")
                self._thread = None
        logger.info("任务刷新器已停止")

    def is_running(self) -> bool:
        with self._lock:
            return self._running
