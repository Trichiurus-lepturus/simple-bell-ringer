from datetime import datetime, date, timedelta
from typing import Optional
import os
import csv
import heapq
import threading
import logging

from task import Task
from config import Config

logger = logging.getLogger(__name__)


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
        target_date = self._get_target_date()
        needs_ringing = self._is_scheduled_today()

        if needs_ringing:
            logger.info(f"日期 {target_date} 需要打铃")
            tasks = self._load_tasks()
            if tasks:
                heapq.heapify(tasks)
            else:
                tasks = []
                logger.warning("需要打铃但任务列表为空")
        else:
            logger.info(f"日期 {target_date} 无需打铃")
            tasks = []

        with self._lock:
            self._tasks_heap = tasks
            if tasks:
                logger.info(f"成功加载 {len(tasks)} 个任务到调度器")

    def _get_target_date(self) -> date:
        target_date = date.today()
        if Config.LOAD_TASKS_FOR_TOMORROW:
            target_date += timedelta(days=1)
        return target_date

    def _is_scheduled_today(self) -> bool:
        target_date = self._get_target_date()
        scheduled = target_date.weekday() < 5  # 默认工作日打铃，周末不打铃
        if self._is_date_in_file(target_date, Config.NO_RING_DATES_FILE_PATH):
            scheduled = False
        if self._is_date_in_file(target_date, Config.RING_DATES_FILE_PATH):
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

    def _is_date_in_file(self, target_date: date, file_path: str) -> bool:
        if not os.path.exists(file_path):
            logger.warning(f"文件未找到：{file_path}")
            return False
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
                    normalized_date_str, is_normalized = self._normalize_date_string(
                        date_str
                    )
                    if is_normalized:
                        logger.warning(
                            f"文件 {Config.SCHEDULE_FILE_PATH} 的第 {row_num} 行，"
                            f"日期格式不规范，已自动补0：'{date_str}' → '{normalized_date_str}'"
                        )
                    try:
                        file_date = datetime.strptime(
                            normalized_date_str, "%Y-%m-%d"
                        ).date()
                        if target_date == file_date:
                            logger.debug(
                                f"在 {file_path} 第 {row_num} 行找到匹配日期：{date_str}"
                            )
                            return True
                    except ValueError:
                        logger.warning(
                            f"文件 {file_path} 第 {row_num} 行包含无效格式的日期：{date_str}",
                            exc_info=True,
                        )
            return False
        except Exception as e:
            logger.error(f"读取 {file_path} 时出错：{e}", exc_info=True)
            return False

    def _load_tasks(self) -> list[Task]:
        target_date = self._get_target_date()
        tasks: list[Task] = []
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
                        task_datetime = datetime.combine(target_date, time_obj)
                        audio_path = os.path.join(
                            Config.AUDIO_FILES_DIRECTORY, audio_filename
                        )
                        if not os.path.isabs(audio_path):
                            audio_path = os.path.abspath(audio_path)
                            logger.warning(f"相对路径已转换为绝对路径：{audio_path}")
                        if not os.path.exists(audio_path):
                            logger.warning(f"音频文件不存在：{audio_path}")
                            continue
                        task = Task(
                            ring_time=task_datetime,
                            audio_path=audio_path,
                            description=description,
                        )
                        tasks.append(task)
                        logger.debug(
                            f"成功加载任务(第{row_num}行): {time_str} -> {normalized_time_str} - {description}"
                        )
                    except ValueError as e:
                        logger.warning(
                            f"文件 {Config.SCHEDULE_FILE_PATH} 的第 {row_num} 行时间格式错误：{time_str}, 错误：{e}"
                        )
                    except Exception as e:
                        logger.error(
                            f"创建任务时出错（第{row_num}行）：{e}", exc_info=True
                        )
        except FileNotFoundError:
            logger.error(f"文件未找到：{Config.SCHEDULE_FILE_PATH}")
        except Exception as e:
            logger.error(f"读取 {Config.SCHEDULE_FILE_PATH} 时出错：{e}", exc_info=True)
        logger.info(f"从文件 {Config.SCHEDULE_FILE_PATH} 读取了 {len(tasks)} 个任务")
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
