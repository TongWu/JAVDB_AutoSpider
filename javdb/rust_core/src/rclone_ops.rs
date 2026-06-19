//! High-performance rclone operations exposed to Python via PyO3.
//!
//! Functions in this module accelerate hot paths that are called
//! thousands of times during inventory scans and dedup analysis.

use pyo3::prelude::*;
use pyo3::types::PyDict;
use regex::Regex;
use serde::Deserialize;
use std::collections::HashMap;

use once_cell::sync::Lazy;

// Pre-compiled regex for folder name parsing
static FOLDER_PATTERN: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^(.+?)\s*\[(.+?)-(.+?)\]$").unwrap());

static VALID_SENSORS: &[&str] = &["有码", "无码", "无码流出", "无码破解"];
static VALID_SUBTITLES: &[&str] = &["中字", "无字"];

/// Parse a movie folder name and return (movie_code, sensor, subtitle)
/// or None if parsing fails.
#[pyfunction]
pub fn parse_folder_name(folder_name: &str) -> Option<(String, String, String)> {
    let trimmed = folder_name.trim();
    let caps = FOLDER_PATTERN.captures(trimmed)?;

    let movie_code = caps.get(1)?.as_str().trim().to_string();
    let sensor = caps.get(2)?.as_str().trim().to_string();
    let subtitle = caps.get(3)?.as_str().trim().to_string();

    if !VALID_SENSORS.contains(&sensor.as_str()) {
        return None;
    }
    if !VALID_SUBTITLES.contains(&subtitle.as_str()) {
        return None;
    }

    Some((movie_code, sensor, subtitle))
}

#[derive(Deserialize)]
struct LsjsonEntry {
    #[serde(rename = "Path")]
    path: String,
    #[serde(rename = "IsDir", default)]
    is_dir: bool,
    #[serde(rename = "Size", default)]
    size: i64,
}

/// Parse a leaf directory name ``<sensor>-<subtitle>`` into ``(sensor, subtitle)``.
///
/// Mirrors the Python ``parse_leaf_name`` in ``scan.py``: strips surrounding
/// brackets/parens, requires a ``-``, splits on the LAST ``-`` (rpartition),
/// and validates both halves against the known sensor/subtitle vocabularies.
/// Returns ``None`` when the leaf is empty, dash-less, or fails validation.
fn parse_leaf_name(leaf_name: &str) -> Option<(String, String)> {
    if leaf_name.is_empty() {
        return None;
    }
    let name = leaf_name
        .trim()
        .trim_matches(|c| c == '[' || c == ']' || c == '(' || c == ')');
    let (sensor, subtitle) = name.rsplit_once('-')?;
    let sensor = sensor.trim();
    let subtitle = subtitle.trim();
    if !VALID_SENSORS.contains(&sensor) || !VALID_SUBTITLES.contains(&subtitle) {
        return None;
    }
    Some((sensor.to_string(), subtitle.to_string()))
}

/// One parsed movie-folder row produced from a year-level ``lsjson -R`` dump.
struct YearFolderRow {
    actor: String,
    movie_code: String,
    folder_name: String,
    sensor: String,
    subtitle: String,
    size: i64,
    file_count: i64,
}

/// Pure-Rust core of [`parse_lsjson_for_year`] — no Python API so it stays
/// linkable under ``cargo test`` (the ``extension-module`` feature omits the
/// host interpreter's symbols).
///
/// Layout (post ``rclone_group_jav.py``):
/// ``<actor>/<movie_code>/<sensor-subtitle>/<files...>``. Directories live at
/// ``parts.len() == 3``; files contributing size/file_count live at
/// ``parts.len() >= 4`` keyed by the same ``(actor, movie_code, leaf)`` 3-tuple.
/// Mirrors the Python ``get_all_movie_folders_for_year`` in ``scan.py``.
fn parse_lsjson_rows(json_str: &str) -> Result<Vec<YearFolderRow>, serde_json::Error> {
    let entries: Vec<LsjsonEntry> = serde_json::from_str(json_str)?;

    // (actor, movie_code, leaf) -> (size_sum, file_count)
    let mut movie_dirs: HashMap<(String, String, String), (i64, i64)> = HashMap::new();

    for entry in &entries {
        let parts: Vec<&str> = entry.path.split('/').collect();
        if entry.is_dir && parts.len() == 3 {
            movie_dirs
                .entry((
                    parts[0].to_string(),
                    parts[1].to_string(),
                    parts[2].to_string(),
                ))
                .or_insert((0, 0));
            continue;
        }
        if !entry.is_dir && parts.len() >= 4 {
            let key = (
                parts[0].to_string(),
                parts[1].to_string(),
                parts[2].to_string(),
            );
            let stats = movie_dirs.entry(key).or_insert((0, 0));
            stats.0 += entry.size;
            stats.1 += 1;
        }
    }

    let mut rows = Vec::new();
    for ((actor, movie_code, leaf), (size, file_count)) in movie_dirs {
        if let Some((sensor, subtitle)) = parse_leaf_name(&leaf) {
            rows.push(YearFolderRow {
                actor,
                movie_code,
                folder_name: leaf,
                sensor,
                subtitle,
                size,
                file_count,
            });
        }
    }
    Ok(rows)
}

/// Parse rclone lsjson output for a year directory and return a list of
/// dicts with keys: actor, movie_code, folder_name, sensor, subtitle, size, file_count
#[pyfunction]
pub fn parse_lsjson_for_year(py: Python<'_>, json_str: &str) -> PyResult<Vec<Py<PyDict>>> {
    let rows = parse_lsjson_rows(json_str)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    let mut results = Vec::with_capacity(rows.len());
    for row in rows {
        let dict = PyDict::new_bound(py);
        dict.set_item("actor", row.actor)?;
        dict.set_item("movie_code", row.movie_code)?;
        dict.set_item("folder_name", row.folder_name)?;
        dict.set_item("sensor", row.sensor)?;
        dict.set_item("subtitle", row.subtitle)?;
        dict.set_item("size", row.size)?;
        dict.set_item("file_count", row.file_count)?;
        results.push(dict.into());
    }

    Ok(results)
}

/// Group a flat list of dicts (each having "movie_code") by that key.
/// Returns {movie_code: [dict, ...]}.
#[pyfunction]
pub fn group_by_movie_code<'py>(
    py: Python<'py>,
    entries: Vec<Bound<'py, PyDict>>,
) -> PyResult<Bound<'py, PyDict>> {
    let result = PyDict::new_bound(py);

    for entry in &entries {
        let code: String = entry
            .get_item("movie_code")?
            .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("missing movie_code"))?
            .extract()?;

        if let Some(existing) = result.get_item(&code)? {
            let list: Bound<'py, pyo3::types::PyList> = existing.downcast_into()?;
            list.append(entry)?;
        } else {
            let list = pyo3::types::PyList::new_bound(py, &[entry.as_any()]);
            result.set_item(&code, list)?;
        }
    }

    Ok(result)
}

/// Parse ``rclone lsd`` output and return a list of folder names.
/// Each line has format: ``-1 2024-01-01 00:00:00 -1 folder_name``
#[pyfunction]
pub fn parse_lsd_output(output: &str) -> Vec<String> {
    let mut folders = Vec::new();
    for line in output.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let parts: Vec<&str> = trimmed.split_whitespace().collect();
        if parts.len() >= 5 {
            folders.push(parts[4..].join(" "));
        }
    }
    folders
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_leaf_name_valid() {
        assert_eq!(
            parse_leaf_name("有码-中字"),
            Some(("有码".to_string(), "中字".to_string()))
        );
        assert_eq!(
            parse_leaf_name("无码流出-无字"),
            Some(("无码流出".to_string(), "无字".to_string()))
        );
    }

    #[test]
    fn test_parse_leaf_name_strips_brackets() {
        assert_eq!(
            parse_leaf_name("[有码-中字]"),
            Some(("有码".to_string(), "中字".to_string()))
        );
        assert_eq!(
            parse_leaf_name("(无码-无字)"),
            Some(("无码".to_string(), "无字".to_string()))
        );
    }

    #[test]
    fn test_parse_leaf_name_invalid() {
        assert_eq!(parse_leaf_name(""), None);
        assert_eq!(parse_leaf_name("有码中字"), None); // no dash
        assert_eq!(parse_leaf_name("未知-中字"), None); // bad sensor
        assert_eq!(parse_leaf_name("有码-未知"), None); // bad subtitle
    }

    #[test]
    fn test_parse_lsjson_rows_three_level_dirs_and_files() {
        let json = r#"[
            {"Path": "ActorA", "IsDir": true},
            {"Path": "ActorA/ABC-123", "IsDir": true},
            {"Path": "ActorA/ABC-123/有码-中字", "IsDir": true},
            {"Path": "ActorA/ABC-123/有码-中字/movie.mp4", "IsDir": false, "Size": 100},
            {"Path": "ActorA/ABC-123/有码-中字/cover.jpg", "IsDir": false, "Size": 20}
        ]"#;
        let rows = parse_lsjson_rows(json).unwrap();
        assert_eq!(rows.len(), 1);
        let row = &rows[0];
        assert_eq!(row.actor, "ActorA");
        assert_eq!(row.movie_code, "ABC-123");
        assert_eq!(row.folder_name, "有码-中字");
        assert_eq!(row.sensor, "有码");
        assert_eq!(row.subtitle, "中字");
        assert_eq!(row.size, 120);
        assert_eq!(row.file_count, 2);
    }

    #[test]
    fn test_parse_lsjson_rows_dir_with_no_files() {
        // A leaf dir with no file entries must still be emitted with size/count 0.
        let json = r#"[{"Path": "ActorA/ABC-123/有码-中字", "IsDir": true}]"#;
        let rows = parse_lsjson_rows(json).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].size, 0);
        assert_eq!(rows[0].file_count, 0);
    }

    #[test]
    fn test_parse_lsjson_rows_skips_invalid_leaf() {
        // An invalid leaf (bad subtitle) must be skipped even though it has files.
        let json = r#"[
            {"Path": "ActorA/ABC-123/有码-中字", "IsDir": true},
            {"Path": "ActorA/DEF-456/无码-badsub", "IsDir": true},
            {"Path": "ActorA/DEF-456/无码-badsub/movie.mp4", "IsDir": false, "Size": 999}
        ]"#;
        let rows = parse_lsjson_rows(json).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].movie_code, "ABC-123");
    }

    #[test]
    fn test_parse_lsd_output() {
        let output = "-1 2024-01-01 00:00:00 -1 2024\n-1 2024-01-01 00:00:00 -1 未知\n";
        assert_eq!(parse_lsd_output(output), vec!["2024", "未知"]);
    }
}
