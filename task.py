from dataclasses import dataclass
from datetime import datetime
import os


@dataclass
class Task:
    datetime: datetime
    audio_path: str  # 绝对路径
    description: str

    def __post_init__(self):
        if (not self.audio_path) or (not self.audio_path.strip()):
            raise ValueError("音频路径为空！")
        if not os.path.isabs(self.audio_path):
            raise ValueError(f"音频路径不是绝对路径：{self.audio_path}")

    def __lt__(self, other: "Task") -> bool:
        return self.datetime < other.datetime
