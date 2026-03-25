import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from scripts.spider.services.dedup import RcloneEntry, should_skip_from_rclone, check_dedup_upgrade


def test_should_skip_from_rclone_behavior():
    inventory = {
        "ABC-123": [
            RcloneEntry(
                video_code="ABC-123",
                sensor_category="有码",
                subtitle_category="中字",
                folder_path="/x/y",
                folder_size=1,
                file_count=1,
                scan_datetime="2026-01-01 00:00:00",
            )
        ]
    }
    assert should_skip_from_rclone("ABC-123", inventory, enable_dedup=False) is True
    assert should_skip_from_rclone("ABC-123", inventory, enable_dedup=True) is False


def test_check_dedup_upgrade_generates_subtitle_upgrade():
    entries = [
        RcloneEntry(
            video_code="ABC-123",
            sensor_category="有码",
            subtitle_category="无字",
            folder_path="/x/y",
            folder_size=100,
            file_count=10,
            scan_datetime="2026-01-01 00:00:00",
        )
    ]
    records = check_dedup_upgrade(
        "ABC-123",
        {"subtitle": True, "hacked_subtitle": False, "hacked_no_subtitle": False, "no_subtitle": False},
        entries,
    )
    assert len(records) == 1
    assert "Subtitle upgrade" in records[0].deletion_reason

