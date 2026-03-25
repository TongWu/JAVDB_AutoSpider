"""Shared ingestion data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from scripts.spider.dedup_checker import DedupRecord


@dataclass
class ParsedMovie:
    """Normalized parsed-movie payload shared across ingestion call sites."""

    href: str
    video_code: str
    page_num: int
    actor_name: str = ''
    actor_gender: str = ''
    actor_link: str = ''
    supporting_actors: str = ''
    magnet_links: Dict[str, str] = field(default_factory=dict)
    size_links: Dict[str, str] = field(default_factory=dict)
    file_count_links: Dict[str, int] = field(default_factory=dict)
    resolution_links: Dict[str, Optional[int]] = field(default_factory=dict)
    entry: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SpiderIngestionPlan:
    """Decision payload for spider ingestion/reporting/history writes."""

    should_skip: bool
    skip_reason: str = ''
    history_torrent_types: List[str] = field(default_factory=list)
    redownload_categories: List[str] = field(default_factory=list)
    dedup_records: List[DedupRecord] = field(default_factory=list)
    report_row: Optional[dict] = None
    has_any_torrents: bool = False
    has_new_torrents: bool = False
    should_include_in_report: bool = False
    new_magnet_links: Dict[str, str] = field(default_factory=dict)
    new_sizes: Dict[str, str] = field(default_factory=dict)
    new_file_counts: Dict[str, int] = field(default_factory=dict)
    new_resolutions: Dict[str, Optional[int]] = field(default_factory=dict)


@dataclass
class AlignmentUpgradePlan:
    """Upgrade-plan payload for inventory alignment."""

    chosen_upgrade_category: str = ''
    chosen_upgrade_categories: List[str] = field(default_factory=list)
    parsed_best_rank: int = 0
    inventory_best_rank: int = 0
    qb_rows: List[dict] = field(default_factory=list)
    purge_plan_rows: List[dict] = field(default_factory=list)

