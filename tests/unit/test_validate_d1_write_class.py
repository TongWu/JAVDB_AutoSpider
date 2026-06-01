from __future__ import annotations

from scripts.ci import validate_d1_write_class as v


def _violations(*files: tuple[str, str]) -> list[v.Violation]:
    return v.find_violations(list(files))


def test_create_table_without_write_class_is_violation():
    content = "-- 2026-06-01: Add Foo.\nCREATE TABLE Foo (id TEXT);\n"
    result = _violations(("javdb/migrations/d1/2026_06_01_add_foo.sql", content))
    assert len(result) == 1
    assert "no `-- Write-Class:`" in result[0].message


def test_create_table_with_valid_class_passes():
    content = "-- Write-Class: additive\nCREATE TABLE Foo (id TEXT);\n"
    assert _violations(("m.sql", content)) == []


def test_write_class_keyword_is_case_insensitive():
    content = "-- write-class: Authoritative\nCREATE TABLE Foo (id TEXT);\n"
    assert _violations(("m.sql", content)) == []


def test_alter_table_without_tag_is_not_a_violation():
    content = "-- 2026-06-01: add column.\nALTER TABLE Foo ADD COLUMN bar TEXT;\n"
    assert _violations(("m.sql", content)) == []


def test_version_bump_without_tag_is_not_a_violation():
    content = "UPDATE SchemaVersion SET version = 15;\n"
    assert _violations(("m.sql", content)) == []


def test_na_value_is_rejected_for_migration():
    content = "-- Write-Class: n/a\nCREATE TABLE Foo (id TEXT);\n"
    result = _violations(("m.sql", content))
    assert len(result) == 1
    assert "invalid Write-Class" in result[0].message


def test_invalid_value_is_rejected():
    content = "-- Write-Class: bogus\nCREATE TABLE Foo (id TEXT);\n"
    result = _violations(("m.sql", content))
    assert len(result) == 1
    assert "bogus" in result[0].message


def test_mixed_valid_and_invalid_tags_is_violation():
    content = (
        "-- Write-Class: additive\n"
        "-- Write-Class: bogus\n"
        "CREATE TABLE Foo (id TEXT);\n"
    )
    result = _violations(("m.sql", content))
    assert len(result) == 1
    assert "bogus" in result[0].message


def test_multiple_distinct_valid_classes_is_violation():
    content = (
        "-- Write-Class: authoritative\n"
        "-- Write-Class: diagnostic\n"
        "CREATE TABLE Foo (id TEXT);\n"
    )
    result = _violations(("m.sql", content))
    assert len(result) == 1
    assert "multiple write classes" in result[0].message


def test_duplicate_identical_class_passes():
    # Repeating the same class is harmless redundancy, not a conflict.
    content = (
        "-- Write-Class: additive\n"
        "-- Write-Class: additive\n"
        "CREATE TABLE Foo (id TEXT);\n"
    )
    assert _violations(("m.sql", content)) == []


def test_multiple_tables_single_tag_passes():
    content = (
        "-- Write-Class: additive\n"
        "CREATE TABLE Foo (id TEXT);\n"
        "CREATE TABLE IF NOT EXISTS Bar (id TEXT);\n"
    )
    assert _violations(("m.sql", content)) == []


def test_non_sql_file_is_skipped():
    assert _violations(("notes.md", "CREATE TABLE Foo (id TEXT);")) == []


def test_create_table_if_not_exists_is_detected():
    assert v.introduces_write_surface("create table if not exists foo (id);")
    content = "CREATE TABLE IF NOT EXISTS Foo (id TEXT);\n"
    assert len(_violations(("m.sql", content))) == 1  # missing tag


def test_declared_classes_lowercases():
    assert v.declared_classes("-- Write-Class: ADDITIVE\n") == ["additive"]


def test_main_paths_mode_returns_nonzero_on_violation(tmp_path):
    bad = tmp_path / "bad.sql"
    bad.write_text("CREATE TABLE Foo (id TEXT);\n", encoding="utf-8")
    assert v.main(["--paths", str(bad)]) == 1


def test_main_paths_mode_returns_zero_when_clean(tmp_path):
    good = tmp_path / "good.sql"
    good.write_text("-- Write-Class: additive\nCREATE TABLE Foo (id TEXT);\n", encoding="utf-8")
    assert v.main(["--paths", str(good)]) == 0


def test_main_no_paths_is_zero():
    assert v.main(["--paths"]) == 0
