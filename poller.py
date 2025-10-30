import threading
import logging
import time
from typing import Optional

from scheduler import Scheduler
from player import AudioPlayer
from config import Config

logger = logging.getLogger(__name__)


class BellPoller:
    def __init__(self, scheduler: Scheduler, audio_player: AudioPlayer):
        self._scheduler = scheduler
        self._audio_player = audio_player
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._lock = threading.Lock()
        self._validate_config()
        logger.info("轮询器初始化完成")

    def _validate_config(self) -> None:
        time_window = Config.TIME_TOLERANCE * 2
        if Config.POLLING_INTERVAL >= time_window:
            raise ValueError(
                f"轮询间隔过大！\n"
                f"  打铃时间窗口：{time_window}秒 (±{Config.TIME_TOLERANCE}秒)\n"
                f"  当前轮询间隔：{Config.POLLING_INTERVAL}秒\n"
                f"  要求：POLLING_INTERVAL < {time_window}秒\n"
                f"  建议：POLLING_INTERVAL <= {time_window / 2}秒"
            )
        if Config.POLLING_INTERVAL > time_window / 2:
            logger.warning(
                f"轮询间隔 ({Config.POLLING_INTERVAL}秒) 超过时间窗口的一半 "
                f"({time_window / 2}秒)，建议降低以提高可靠性"
            )
        logger.info(
            f"轮询配置验证通过 - 时间窗口：{time_window}秒, "
            f"轮询间隔：{Config.POLLING_INTERVAL}秒, "
            f"窗口内轮询次数：~{int(time_window / Config.POLLING_INTERVAL)}次"
        )

    def start(self) -> None:
        with self._lock:
            if self._running:
                logger.warning("轮询器已在运行中，无需重复启动")
                return
            self._running = True
            self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._poll_loop, name="BellPollerThread", daemon=False
        )
        self._thread.start()
        logger.info(f"打铃轮询器已启动，轮询间隔：{Config.POLLING_INTERVAL}秒")

    def stop(self, timeout: Optional[float] = None) -> None:
        with self._lock:
            if not self._running:
                logger.info("轮询器未运行，无需停止")
                return
            self._running = False

        logger.info("正在停止打铃轮询器...")
        self._stop_event.set()
        if timeout is None:
            timeout = Config.POLLING_INTERVAL * 3
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning(f"轮询器在 {timeout} 秒内未能停止")
            else:
                logger.info("打铃轮询器已停止")
                self._thread = None

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def _poll_loop(self) -> None:
        logger.info("轮询循环开始")
        next_poll_time = time.perf_counter() + Config.POLLING_INTERVAL

        try:
            while not self._stop_event.is_set():
                try:
                    task = self._scheduler.pop_current_task()
                    if task is not None:
                        logger.info(f"检测到打铃任务：{task.description}")
                        success = self._audio_player.run(task)
                        if not success:
                            logger.error(f"打铃任务执行失败：{task.description}")
                        else:
                            logger.info(f"打铃任务执行成功：{task.description}")
                    else:
                        logger.debug("当前无待执行任务")
                except Exception as e:
                    logger.error(f"轮询过程中发生错误：{e}", exc_info=True)

                now = time.perf_counter()
                wait_time = max(0, next_poll_time - now)
                if (
                    wait_time == 0
                    and (now - next_poll_time + Config.POLLING_INTERVAL)
                    > Config.POLLING_INTERVAL * 0.1
                ):
                    overshoot = now - next_poll_time + Config.POLLING_INTERVAL
                    logger.warning(
                        f"轮询执行耗时过长，超出间隔 {overshoot:.3f}秒，立即进行下次轮询"
                    )
                if self._stop_event.wait(timeout=wait_time):
                    logger.info("收到停止信号，退出轮询循环")
                    break
                next_poll_time += Config.POLLING_INTERVAL
                if next_poll_time < now - Config.POLLING_INTERVAL:
                    logger.warning(
                        f"检测到时间漂移，重置轮询时间基准 "
                        f"(漂移：{now - next_poll_time:.1f}秒)"
                    )
                    next_poll_time = now + Config.POLLING_INTERVAL
        finally:
            with self._lock:
                self._running = False
            logger.info("轮询循环结束")
