import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "migration" / "tools" / "migrate_to_d1.sh"


def test_prepare_routes_post_data_ddl_after_data_chunks(tmp_path):
    (tmp_path / "history.sql").write_text(
        "\n".join(
            [
                "CREATE TABLE MovieHistory(Id INTEGER);",
                "INSERT INTO MovieHistory VALUES(1);",
                "CREATE INDEX idx_moviehistory_id ON MovieHistory(Id);",
                "CREATE TRIGGER trg_moviehistory_ai AFTER INSERT ON MovieHistory",
                "BEGIN",
                "  SELECT 1;",
                "END;",
                "",
            ]
        ),
        encoding="utf-8",
    )

    subprocess.run(
        ["bash", str(SCRIPT), "prepare"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    schema = (tmp_path / "d1_chunks" / "history_00_schema.sql").read_text(encoding="utf-8")
    data = (tmp_path / "d1_chunks" / "history_data_001.sql").read_text(encoding="utf-8")
    post_data = (tmp_path / "d1_chunks" / "history_99_post_data.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE MovieHistory" in schema
    assert "CREATE INDEX" not in schema
    assert "CREATE TRIGGER" not in schema
    assert "INSERT INTO MovieHistory" in data
    assert "CREATE INDEX idx_moviehistory_id" in post_data
    assert "CREATE TRIGGER trg_moviehistory_ai" in post_data
    assert "END;" in post_data
    assert post_data.index("CREATE INDEX") < post_data.index("CREATE TRIGGER")


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 is required")
def test_verify_fails_when_sqlite_table_enumeration_fails(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "history.db").write_text("not a sqlite database", encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), "verify"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert proc.returncode != 0
    assert "failed to enumerate user tables in reports/history.db" in proc.stderr
