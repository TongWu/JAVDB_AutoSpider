"""Data types for skip-time dedup checks."""

from typing import NamedTuple


class RcloneEntry(NamedTuple):
    """A single record from rclone_inventory.csv."""
    video_code: str
    sensor_category: str
    subtitle_category: str
    folder_path: str
    folder_size: int
    file_count: int
    scan_datetime: str


class DedupRecord(NamedTuple):
    """A record to be written to dedup.csv."""
    video_code: str
    existing_sensor: str
    existing_subtitle: str
    existing_gdrive_path: str
    existing_folder_size: int
    new_torrent_category: str
    deletion_reason: str
    detect_datetime: str
    is_deleted: str      # "True" / "False"
    delete_datetime: str  # empty or timestamp


DEDUP_FIELDNAMES = [
    'video_code',
    'existing_sensor',
    'existing_subtitle',
    'existing_gdrive_path',
    'existing_folder_size',
    'new_torrent_category',
    'deletion_reason',
    'detect_datetime',
    'is_deleted',
    'delete_datetime',
]
