from dataclasses import dataclass
from datetime import datetime, date, timedelta, time as dt_time
from typing import Optional
import os
import csv
import heapq
import threading
import logging

from task import Task
from config import Config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduleEntry:
    ring_time_of_day: dt_time
    audio_path: str
    description: str
    row_num: int


class Scheduler:
    def __init__(self):
        self._tasks_heap: list[Task] = []
        self._lock = threading.Lock()
        logger.info("调度器初始化完成")

    def pop_current_task(self) -> Optional[Task]:
        now = datetime.now()
        expired_boundary = now - timedelta(seconds=Config.TIME_TOLERANCE)
        execute_boundary = now + timedelta(seconds=Config.TIME_TOLERANCE)

        with self._lock:
            while self._tasks_heap and self._tasks_heap[0].ring_time < expired_boundary:
                task = heapq.heappop(self._tasks_heap)
                logger.info(
                    f"清理过期任务：{task.description}（计划打铃时间：{task.ring_time}）"
                )
            if self._tasks_heap and self._tasks_heap[0].ring_time <= execute_boundary:
                task = heapq.heappop(self._tasks_heap)
                logger.info(f"获得当前任务：{task.description}")
                return task
            return None

    def refresh_task_list(self) -> None:
        """刷新任务列表。

        装载规则：
        - 今天需要打铃，装入今天所有尚未过期的任务；
        - 明天需要打铃，装入明天全部任务。
        """
        now = datetime.now()
        today = now.date()
        tomorrow = today + timedelta(days=1)

        logger.info(
            f"刷新任务列表：当前时间 {now.strftime('%Y-%m-%d %H:%M:%S')}，"
            f"今天 {today}，明天 {tomorrow}"
        )

        ring_dates = self._load_date_set(Config.RING_DATES_FILE_PATH)
        no_ring_dates = self._load_date_set(Config.NO_RING_DATES_FILE_PATH)

        ring_today = self._is_ringing_date(today, ring_dates, no_ring_dates)
        ring_tomorrow = self._is_ringing_date(tomorrow, ring_dates, no_ring_dates)
        logger.info(f"日期 {today} {'需要' if ring_today else '无需'}打铃")
        logger.info(f"日期 {tomorrow} {'需要' if ring_tomorrow else '无需'}打铃")

        entries = self._load_schedule_entries()
        tasks: list[Task] = []

        if not entries:
            logger.warning("任务列表没有可装载的有效任务，本次加载为空")
        else:
            if ring_today:
                today_tasks = self._build_tasks(
                    today,
                    entries,
                    not_before=now - timedelta(seconds=Config.TIME_TOLERANCE),
                )
                logger.info(f"今天剩余任务：{len(today_tasks)} 个")
                tasks.extend(today_tasks)
            if ring_tomorrow:
                tomorrow_tasks = self._build_tasks(tomorrow, entries)
                logger.info(f"明天任务：{len(tomorrow_tasks)} 个")
                tasks.extend(tomorrow_tasks)

        if tasks:
            heapq.heapify(tasks)

        with self._lock:
            self._tasks_heap = tasks

        if tasks:
            logger.info(f"成功加载 {len(tasks)} 个任务到调度器")
        else:
            logger.info("本次加载的任务列表为空")

    def _is_ringing_date(
        self,
        target_date: date,
        ring_dates: set[date],
        no_ring_dates: set[date],
    ) -> bool:
        scheduled = target_date.weekday() < 5  # 默认工作日打铃，周末不打铃
        if target_date in no_ring_dates:
            scheduled = False
        if target_date in ring_dates:
            scheduled = True
        return scheduled

    def _normalize_date_string(self, date_str: str) -> tuple[str, bool]:
        date_str = date_str.strip()
        if not date_str:
            return date_str, False
        parts = date_str.split("-")
        if parts and len(parts) == 3:
            try:
                year = parts[0].zfill(4)
                month = parts[1].zfill(2)
                day = parts[2].zfill(2)
                result = f"{year}-{month}-{day}"
                modified = result != date_str
                return result, modified
            except Exception:
                pass
        return date_str, False

    def _normalize_time_string(self, time_str: str) -> tuple[str, bool]:
        time_str = time_str.strip()
        if not time_str:
            return time_str, False
        parts = time_str.split(":")
        if len(parts) == 3:
            try:
                hour = parts[0].zfill(2)
                minute = parts[1].zfill(2)
                second = parts[2].zfill(2)
                result = f"{hour}:{minute}:{second}"
                modified = result != time_str
                return result, modified
            except Exception:
                pass
        elif len(parts) == 2:
            try:
                hour = parts[0].zfill(2)
                minute = parts[1].zfill(2)
                return f"{hour}:{minute}:00", True
            except Exception:
                pass
        return time_str, False

    def _load_date_set(self, file_path: str) -> set[date]:
        dates: set[date] = set()
        if not os.path.exists(file_path):
            logger.warning(f"文件未找到：{file_path}")
            return dates
        if not os.path.isabs(file_path):
            logger.warning(f"相对路径已转换为绝对路径：{file_path}")
            file_path = os.path.abspath(file_path)

        try:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                for row_num, row in enumerate(reader, 1):
                    if not row or all(not cell.strip() for cell in row):
                        continue
                    if row[0].strip().startswith("#"):
                        continue
                    date_str = row[0].strip()
                    if not date_str:
                        continue
                    if len(row) > 1:
                        non_empty_extra_cols = [
                            cell.strip() for cell in row[1:] if cell.strip()
                        ]
                        if non_empty_extra_cols:
                            ignored_content = ",".join(non_empty_extra_cols)
                            logger.warning(
                                f"文件 {file_path} 的第 {row_num} 行包含多列，"
                                f"已忽略以下内容：{ignored_content}"
                            )
                    normalized_date_str, is_normalized = self._normalize_date_string(
                        date_str
                    )
                    if is_normalized:
                        logger.warning(
                            f"文件 {file_path} 的第 {row_num} 行，"
                            f"日期格式不规范，已自动补0：'{date_str}' → '{normalized_date_str}'"
                        )
                    try:
                        file_date = datetime.strptime(
                            normalized_date_str, "%Y-%m-%d"
                        ).date()
                        dates.add(file_date)
                    except ValueError:
                        logger.warning(
                            f"文件 {file_path} 第 {row_num} 行包含无效格式的日期：{date_str}",
                            exc_info=True,
                        )
        except Exception as e:
            logger.error(f"读取 {file_path} 时出错：{e}", exc_info=True)
        return dates

    def _load_schedule_entries(self) -> list[ScheduleEntry]:
        entries: list[ScheduleEntry] = []
        try:
            with open(Config.SCHEDULE_FILE_PATH, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                for row_num, row in enumerate(reader, 1):
                    if not row or all(not cell.strip() for cell in row):
                        continue
                    if row[0].strip().startswith("#"):
                        continue
                    if len(row) < 3:
                        logger.warning(
                            f"文件 {Config.SCHEDULE_FILE_PATH} 的第 {row_num} 行格式不正确，跳过"
                        )
                        continue
                    time_str, audio_filename, description = (
                        row[0].strip(),
                        row[1].strip(),
                        row[2].strip(),
                    )
                    if not audio_filename:
                        logger.warning(
                            f"文件 {Config.SCHEDULE_FILE_PATH} 的第 {row_num} 行音频文件名为空，跳过"
                        )
                        continue
                    normalized_time_str, is_normalized = self._normalize_time_string(
                        time_str
                    )
                    if is_normalized:
                        logger.warning(
                            f"文件 {Config.SCHEDULE_FILE_PATH} 的第 {row_num} 行，"
                            f"时间格式不规范，已自动补0：'{time_str}' → '{normalized_time_str}'"
                        )
                    try:
                        time_obj = datetime.strptime(
                            normalized_time_str, "%H:%M:%S"
                        ).time()
                    except ValueError as e:
                        logger.warning(
                            f"文件 {Config.SCHEDULE_FILE_PATH} 的第 {row_num} 行时间格式错误：{time_str}, 错误：{e}"
                        )
                        continue

                    audio_path = os.path.join(
                        Config.AUDIO_FILES_DIRECTORY, audio_filename
                    )
                    if not os.path.isabs(audio_path):
                        audio_path = os.path.abspath(audio_path)
                        logger.warning(f"相对路径已转换为绝对路径：{audio_path}")
                    if not os.path.exists(audio_path):
                        logger.warning(f"音频文件不存在：{audio_path}")
                        continue

                    entries.append(
                        ScheduleEntry(
                            ring_time_of_day=time_obj,
                            audio_path=audio_path,
                            description=description,
                            row_num=row_num,
                        )
                    )
                    logger.debug(
                        f"成功解析打铃任务（第{row_num}行）：{time_str} -> {normalized_time_str} - {description}"
                    )
        except FileNotFoundError:
            logger.error(f"文件未找到：{Config.SCHEDULE_FILE_PATH}")
        except Exception as e:
            logger.error(
                f"读取 {Config.SCHEDULE_FILE_PATH} 时出错：{e}", exc_info=True
            )

        logger.info(
            f"从文件 {Config.SCHEDULE_FILE_PATH} 读取了 {len(entries)} 条有效打铃任务"
        )
        self._warn_overlapping_entries(entries)
        return entries

    def _warn_overlapping_entries(self, entries: list[ScheduleEntry]) -> None:
        if len(entries) < 2:
            return

        threshold = 2 * Config.TIME_TOLERANCE
        ordered = sorted(entries, key=lambda entry: entry.ring_time_of_day)
        for previous, current in zip(ordered, ordered[1:]):
            previous_seconds = (
                previous.ring_time_of_day.hour * 3600
                + previous.ring_time_of_day.minute * 60
                + previous.ring_time_of_day.second
            )
            current_seconds = (
                current.ring_time_of_day.hour * 3600
                + current.ring_time_of_day.minute * 60
                + current.ring_time_of_day.second
            )
            difference = current_seconds - previous_seconds
            if difference <= threshold:
                logger.warning(
                    "任务列表第 %d 行（%s）与第 %d 行（%s）的间隔为 %.3f 秒，"
                    "不超过 2×TIME_TOLERANCE（%.3f 秒）；"
                    "两条任务时间间隔过近，请检查任务列表。",
                    previous.row_num,
                    previous.ring_time_of_day.strftime("%H:%M:%S"),
                    current.row_num,
                    current.ring_time_of_day.strftime("%H:%M:%S"),
                    difference,
                    threshold,
                )

    def _build_tasks(
        self,
        target_date: date,
        entries: list[ScheduleEntry],
        not_before: Optional[datetime] = None,
    ) -> list[Task]:
        tasks: list[Task] = []
        for entry in entries:
            ring_time = datetime.combine(target_date, entry.ring_time_of_day)
            if not_before is not None and ring_time < not_before:
                continue
            try:
                tasks.append(
                    Task(
                        ring_time=ring_time,
                        audio_path=entry.audio_path,
                        description=entry.description,
                    )
                )
            except ValueError as e:
                logger.error(
                    f"创建任务时出错（任务列表第 {entry.row_num} 行）：{e}",
                    exc_info=True,
                )
        return tasks

    def print_status(self) -> None:
        with self._lock:
            now = datetime.now()
            logger.debug(f"=== 调度器状态 {now.strftime('%Y-%m-%d %H:%M:%S')} ===")
            logger.debug(f"任务堆大小：{len(self._tasks_heap)}")
            if not self._tasks_heap:
                logger.debug("任务堆为空")
                return
            logger.debug("当前任务列表：")
            for i, task in enumerate(self._tasks_heap):
                status = "已过期" if task.ring_time < now else "待执行"
                logger.debug(
                    f"  {i + 1}. {task.ring_time.strftime('%H:%M:%S')} - "
                    f"{task.description} ({task.audio_path}) [{status}]"
                )
            next_task = self._tasks_heap[0]
            logger.debug(f"\n下一个任务：{next_task.description}")
            logger.debug(f"执行时间：{next_task.ring_time.strftime('%H:%M:%S')}")
