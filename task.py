from dataclasses import dataclass, field
from datetime import datetime
import os


@dataclass(order=True)
class Task:
    ring_time: datetime = field(compare=True)
    audio_path: str = field(compare=False)
    description: str = field(compare=False)

    def __post_init__(self):
        if (not self.audio_path) or (not self.audio_path.strip()):
            raise ValueError("音频路径为空！")
        if not os.path.isabs(self.audio_path):
            raise ValueError(f"音频路径不是绝对路径：{self.audio_path}")
