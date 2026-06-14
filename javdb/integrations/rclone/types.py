"""Rclone helper data types split from the historical helper module."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from javdb.spider.contracts import UNCENSORED_SENSOR_PRIORITY

# ============================================================================
# Data Classes and Type Definitions
# ============================================================================

class SensorCategory:
    """Sensor category constants with priority order"""
    YOUMA = "有码"
    WUMA = "无码"
    WUMA_LIUCHU = "无码流出"
    WUMA_POJIE = "无码破解"

    WUMA_PRIORITY = UNCENSORED_SENSOR_PRIORITY

    @classmethod
    def is_wuma_category(cls, category: str) -> bool:
        return category in UNCENSORED_SENSOR_PRIORITY

    @classmethod
    def get_priority(cls, category: str) -> int:
        return UNCENSORED_SENSOR_PRIORITY.get(category, 0)


class SubtitleCategory:
    """Subtitle category constants"""
    ZHONGZI = "中字"
    WUZI = "无字"


# ============================================================================
# Constants
# ============================================================================

SIZE_THRESHOLD_RATIO = 1.30

BATCH_SIZE = 1000

DRY_RUN_MAX_YEARS = 2
DRY_RUN_MAX_ACTORS_PER_YEAR = 50
DRY_RUN_MAX_COMBINATIONS = 100

INCREMENTAL_DAYS = 30

VIDEO_EXTENSIONS = {
    '.mp4', '.mkv', '.avi', '.wmv', '.mov',
    '.flv', '.webm', '.m4v', '.ts', '.iso',
}


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class FolderInfo:
    """Information about a movie folder"""
    full_path: str
    year: str
    actor: str
    movie_code: str
    sensor_category: str
    subtitle_category: str
    folder_name: str
    size: int = 0
    file_count: int = 0
    video_mod_time: Optional[datetime] = None


@dataclass
class DeletionRecord:
    """Record of a folder to be deleted"""
    movie_code: str
    sensor_category: str
    subtitle_category: str
    deletion_reason: str
    size: int
    file_count: int
    full_path: str
    delete_command: str = ""
    delete_datetime: str = ""
    kept_folder_path: str = ""


@dataclass
class DedupResult:
    """Result of deduplication analysis for a movie code"""
    movie_code: str
    year: str
    actor: str
    folders_to_keep: List[FolderInfo] = field(default_factory=list)
    folders_to_delete: List[Tuple[FolderInfo, str]] = field(default_factory=list)

_VALID_SENSORS = (
    SensorCategory.YOUMA, SensorCategory.WUMA,
    SensorCategory.WUMA_LIUCHU, SensorCategory.WUMA_POJIE,
)
_VALID_SUBTITLES = (SubtitleCategory.ZHONGZI, SubtitleCategory.WUZI)
