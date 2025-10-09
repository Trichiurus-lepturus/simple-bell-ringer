import sched
import time
import threading
from datetime import datetime, timedelta
import logging

from config import Config
from scheduler import Scheduler

logger = logging.getLogger(__name__)


class TaskRefresher:
    def __init__(self):
        self.scheduler_instance = sched.scheduler(time.time, time.sleep)
        self.task_scheduler = Scheduler()

        self._thread = None
        self._lock = threading.Lock()
        self._running = False

        self._validate_config()

    def _validate_config(self):
        try:
            datetime.strptime(Config.TASK_REFRESH_TIME, "%H:%M:%S")
        except ValueError as e:
            raise ValueError(
                f"配置的 TASK_REFRESH_TIME '{Config.TASK_REFRESH_TIME}' "
                f"格式错误，应为 HH:MM:SS 格式: {e}"
            )

    def _task_callback(self):
        logger.info("开始刷新任务列表")

        try:
            self.task_scheduler.refresh_task_list()
            logger.info("刷新任务列表完成")
        except Exception as e:
            logger.error(f"刷新任务列表失败: {e}", exc_info=True)
            # 刷新失败也保持线程运行

        with self._lock:
            if self._running:
                try:
                    self._schedule_next_task()
                except Exception as e:
                    logger.error(f"下次刷新安排失败: {e}", exc_info=True)
                    # 此处安排失败是严重问题，停止运行
                    self._running = False

    def _schedule_next_task(self):
        # 持有锁时调用
        try:
            now = datetime.now()
            due = datetime.strptime(Config.TASK_REFRESH_TIME, "%H:%M:%S")
            next_run_time = now.replace(
                hour=due.hour, minute=due.minute, second=due.second, microsecond=0
            )
            if next_run_time < now:
                next_run_time += timedelta(days=1)
            delay = (next_run_time - now).total_seconds()
            if delay < 1:
                logger.info("检测到执行时间临界点，延迟到明天执行")
                next_run_time += timedelta(days=1)
                delay = (next_run_time - now).total_seconds()
            logger.info(
                f"下次刷新任务列表时间：{next_run_time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"(延迟 {delay:.2f} 秒)"
            )
            self.scheduler_instance.enter(delay, 1, self._task_callback)
        except ValueError as e:
            logger.error(f"解析任务刷新时间失败 '{Config.TASK_REFRESH_TIME}': {e}")
            raise
        except Exception as e:
            logger.error(f"安排下次任务失败: {e}", exc_info=True)
            raise

    def _run_scheduler(self):
        logger.info("任务刷新器线程已启动")
        try:
            self.scheduler_instance.run()
        except Exception as e:
            logger.error(f"刷新器运行异常: {e}", exc_info=True)
        finally:
            with self._lock:
                self._running = False
            logger.info("任务刷新器线程已退出")

    def start(self):
        with self._lock:
            if self._running:
                logger.warning("任务刷新器已在运行中")
                return
            self._running = True
            logger.info(f"启动任务刷新器，执行时间: {Config.TASK_REFRESH_TIME}")
            try:
                self._schedule_next_task()
                self._thread = threading.Thread(
                    target=self._run_scheduler, name="TaskRefresherThread", daemon=False
                )
                self._thread.start()
                logger.info("任务刷新器启动成功")
            except Exception as e:
                logger.error(f"启动任务刷新器失败: {e}", exc_info=True)
                self._running = False
                raise

    def stop(self, timeout: int = 12):
        with self._lock:
            if not self._running:
                logger.warning("任务刷新器未在运行")
                return
            self._running = False

        logger.info("正在停止任务刷新器...")
        try:
            events_to_cancel = list(self.scheduler_instance.queue)
            cancelled_count = 0
            for event in events_to_cancel:
                try:
                    self.scheduler_instance.cancel(event)
                    cancelled_count += 1
                except ValueError:
                    pass
            if cancelled_count > 0:
                logger.info(f"已取消 {cancelled_count} 个待执行任务")
            self.scheduler_instance.enter(0, 0, lambda: None)
        except Exception as e:
            logger.error(f"清理任务队列时出错: {e}", exc_info=True)
        if self._thread and self._thread.is_alive():
            logger.info(f"等待任务刷新线程完成（超时 {timeout} 秒）...")
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("任务刷新线程未在超时时间内退出")
            else:
                logger.info("任务刷新线程已成功退出")
        logger.info("任务刷新器已停止")

    def is_running(self) -> bool:
        with self._lock:
            return self._running
