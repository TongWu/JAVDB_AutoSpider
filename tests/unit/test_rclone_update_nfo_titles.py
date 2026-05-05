import pytest

from scripts import rclone_update_nfo_titles as updater


def test_run_rclone_uses_default_timeout(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return updater.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(updater.subprocess, "run", fake_run)

    updater.run_rclone(["lsjson", "remote:path"])
    updater.run_rclone(["cat", "remote:path/file.nfo"], timeout=None)

    assert calls[0][1]["timeout"] == 300.0
    assert calls[1][1]["timeout"] is None


def test_select_year_dirs_excludes_temp_and_sorts_unknown_after_years():
    years, missing = updater.select_year_dirs(
        ["temp", "2026", "unknown", "2018", "2017", "未知", "Temp"]
    )

    assert years == ["2017", "2018", "2026", "未知"]
    assert missing == set()


def test_select_year_dirs_start_from_keeps_unknown_dirs_after_matching_years():
    years, missing = updater.select_year_dirs(
        ["2017", "2018", "2020", "2026", "manual", "未知", "temp"],
        start_from=2018,
    )

    assert years == ["2018", "2020", "2026", "未知"]
    assert missing == set()


def test_select_year_dirs_requested_years_intersect_start_from_and_exclude_temp():
    years, missing = updater.select_year_dirs(
        ["2017", "2018", "2026", "manual", "未知", "temp"],
        requested_years=["2017", "2018", "manual", "未知", "temp", "missing"],
        start_from=2018,
    )

    assert years == ["2018", "未知"]
    assert missing == {"manual", "temp", "missing"}


def test_validate_year_requires_exactly_four_digits():
    assert updater.validate_year("2026") == 2026
    for value in ("20", "99999", "abcd"):
        with pytest.raises(updater.argparse.ArgumentTypeError):
            updater.validate_year(value)


def test_transform_title_hides_coded_and_no_subtitle_suffix():
    title = "从放学后到第二天早晨 [MNGS-030 有码-无字]"

    assert updater.transform_title(title, "七濑爱丽丝") == (
        "[MNGS-030 七濑爱丽丝] 从放学后到第二天早晨"
    )


def test_transform_title_uses_first_actor_and_chinese_subtitle_suffix():
    title = "【特典版】在派对酒吧发生性关系 [START-498 有码-中字]"

    assert updater.transform_title(title, "青空光") == (
        "[START-498 青空光] 【特典版】在派对酒吧发生性关系 (中字)"
    )


def test_normalize_classification_maps_subtitle_aliases():
    assert updater.normalize_classification("有码-有字") == "中字"
    assert updater.normalize_classification("有码-有字幕") == "中字"
    assert updater.normalize_classification("无码流出-有字幕") == "无码流出-中字"
    assert updater.normalize_classification("无码-无字幕") == "无码"


def test_extract_first_actor_reads_first_actor_block():
    nfo = """<?xml version="1.0" encoding="UTF-8" ?>
<movie>
  <actor>
    <name>青空光</name>
    <type>Actor</type>
  </actor>
  <actor>
    <name>神木丽</name>
    <type>Actor</type>
  </actor>
</movie>"""

    assert updater.extract_first_actor(nfo) == "青空光"


def test_update_nfo_content_rewrites_only_title_cdata():
    nfo = """<?xml version="1.0" encoding="UTF-8" ?>
<movie>
  <title><![CDATA[标题 [ABC-123 有码-无字幕]]]></title>
  <originaltitle><![CDATA[ABC-123 原始标题]]></originaltitle>
  <actor>
    <name>演员A</name>
    <type>Actor</type>
  </actor>
</movie>"""

    new_content, old_title, new_title = updater.update_nfo_content(nfo)

    assert old_title == "标题 [ABC-123 有码-无字幕]"
    assert new_title == "[ABC-123 演员A] 标题"
    assert new_content is not None
    assert "<title><![CDATA[[ABC-123 演员A] 标题]]></title>" in new_content
    assert "<originaltitle><![CDATA[ABC-123 原始标题]]></originaltitle>" in new_content


def test_update_nfo_content_skips_already_updated_title():
    nfo = """<movie>
  <title><![CDATA[[ABC-123 演员A] 标题]]></title>
  <actor><name>演员A</name><type>Actor</type></actor>
</movie>"""

    assert updater.update_nfo_content(nfo) == (None, "[ABC-123 演员A] 标题", None)


def test_collect_jobs_keeps_multiple_nfos_in_one_folder(monkeypatch):
    monkeypatch.setattr(
        updater,
        "_list_code_dirs",
        lambda year_path, year, actor: (
            "remote/2026/演员A",
            "2026/演员A",
            ["ABC-123"],
        ),
    )
    monkeypatch.setattr(
        updater,
        "_list_leaf_dirs",
        lambda code_path, rel_code: (
            "remote/2026/演员A/ABC-123",
            "2026/演员A/ABC-123",
            ["有码-中字"],
        ),
    )
    monkeypatch.setattr(
        updater,
        "_list_leaf_nfos",
        lambda leaf_path, rel_leaf: (
            "remote/2026/演员A/ABC-123/有码-中字",
            "2026/演员A/ABC-123/有码-中字",
            ["ABC-123.nfo", "ABC-123-CD2.nfo"],
        ),
    )

    jobs, cleanup_jobs, code_dirs, leaf_dirs, leaf_without_nfo = updater.collect_jobs(
        "2026", "remote/2026", ["演员A"], workers=1
    )

    assert [job.nfo_name for job in jobs] == ["ABC-123.nfo", "ABC-123-CD2.nfo"]
    assert cleanup_jobs == []
    assert code_dirs == 1
    assert leaf_dirs == 1
    assert leaf_without_nfo == 0


def test_collect_jobs_creates_cleanup_job_for_folder_without_nfo(monkeypatch):
    monkeypatch.setattr(
        updater,
        "_list_code_dirs",
        lambda year_path, year, actor: (
            "remote/2026/演员A",
            "2026/演员A",
            ["ABC-123"],
        ),
    )
    monkeypatch.setattr(
        updater,
        "_list_leaf_dirs",
        lambda code_path, rel_code: (
            "remote/2026/演员A/ABC-123",
            "2026/演员A/ABC-123",
            ["有码-中字"],
        ),
    )
    monkeypatch.setattr(
        updater,
        "_list_leaf_nfos",
        lambda leaf_path, rel_leaf: (
            "remote/2026/演员A/ABC-123/有码-中字",
            "2026/演员A/ABC-123/有码-中字",
            [],
        ),
    )

    jobs, cleanup_jobs, code_dirs, leaf_dirs, leaf_without_nfo = updater.collect_jobs(
        "2026", "remote/2026", ["演员A"], workers=1
    )

    assert jobs == []
    assert cleanup_jobs == [
        updater.CleanupJob(
            leaf_path="remote/2026/演员A/ABC-123/有码-中字",
            rel_leaf="2026/演员A/ABC-123/有码-中字",
        )
    ]
    assert code_dirs == 1
    assert leaf_dirs == 1
    assert leaf_without_nfo == 1


def test_execute_cleanup_moves_large_files_then_purges(monkeypatch):
    calls = []
    job = updater.CleanupJob(
        leaf_path="remote/2026/演员A/ABC-123/有码-中字",
        rel_leaf="2026/演员A/ABC-123/有码-中字",
    )
    temp_path = updater.cleanup_temp_path("remote", job)

    monkeypatch.setattr(
        updater,
        "list_files",
        lambda path: [
            updater.RemoteFile("movie.mp4", 101 * 1024 * 1024),
            updater.RemoteFile("sample.jpg", 10 * 1024 * 1024),
        ],
    )
    monkeypatch.setattr(updater, "mkdir_remote", lambda path: calls.append(("mkdir", path)))
    monkeypatch.setattr(
        updater,
        "moveto_remote",
        lambda src, dst: calls.append(("moveto", src, dst)),
    )
    monkeypatch.setattr(updater, "purge_remote", lambda path: calls.append(("purge", path)))

    result_job, status, msg = updater.execute_cleanup(job, "remote", dry_run=False)

    assert result_job == job
    assert status == "OK"
    assert msg == "moved_large_files=1 purged=2026/演员A/ABC-123/有码-中字"
    assert calls == [
        ("mkdir", temp_path),
        (
            "moveto",
            "remote/2026/演员A/ABC-123/有码-中字/movie.mp4",
            updater.join_remote(temp_path, "movie.mp4"),
        ),
        ("purge", "remote/2026/演员A/ABC-123/有码-中字"),
    ]


def test_cleanup_temp_path_is_unique_per_leaf():
    job_a = updater.CleanupJob(
        leaf_path="remote/2026/演员A/ABC-123/有码-中字",
        rel_leaf="2026/演员A/ABC-123/有码-中字",
    )
    job_b = updater.CleanupJob(
        leaf_path="remote/2026/演员B/ABC-123/有码-中字",
        rel_leaf="2026/演员B/ABC-123/有码-中字",
    )

    assert updater.cleanup_temp_path("remote", job_a) != updater.cleanup_temp_path(
        "remote", job_b
    )
