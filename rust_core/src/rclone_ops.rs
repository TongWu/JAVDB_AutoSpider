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

/// Parse rclone lsjson output for a year directory and return a list of
/// dicts with keys: actor, folder_name, movie_code, sensor, subtitle, size, file_count
#[pyfunction]
pub fn parse_lsjson_for_year(py: Python<'_>, json_str: &str) -> PyResult<Vec<Py<PyDict>>> {
    let entries: Vec<LsjsonEntry> =
        serde_json::from_str(json_str).map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    // (actor, folder_name) -> (size_sum, file_count)
    let mut movie_dirs: HashMap<(String, String), (i64, i64)> = HashMap::new();

    for entry in &entries {
        let parts: Vec<&str> = entry.path.split('/').collect();
        if entry.is_dir && parts.len() == 2 {
            movie_dirs
                .entry((parts[0].to_string(), parts[1].to_string()))
                .or_insert((0, 0));
            continue;
        }
        if !entry.is_dir && parts.len() >= 3 {
            let key = (parts[0].to_string(), parts[1].to_string());
            let stats = movie_dirs.entry(key).or_insert((0, 0));
            stats.0 += entry.size;
            stats.1 += 1;
        }
    }

    let mut results = Vec::new();
    for ((actor, folder_name), (size, file_count)) in &movie_dirs {
        if let Some((movie_code, sensor, subtitle)) = parse_folder_name(folder_name) {
            let dict = PyDict::new(py);
            dict.set_item("actor", &actor)?;
            dict.set_item("folder_name", &folder_name)?;
            dict.set_item("movie_code", movie_code)?;
            dict.set_item("sensor", sensor)?;
            dict.set_item("subtitle", subtitle)?;
            dict.set_item("size", size)?;
            dict.set_item("file_count", file_count)?;
            results.push(dict.into());
        }
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
    let result = PyDict::new(py);

    for entry in &entries {
        let code: String = entry
            .get_item("movie_code")?
            .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("missing movie_code"))?
            .extract()?;

        if let Some(existing) = result.get_item(&code)? {
            let list: Bound<'py, pyo3::types::PyList> = existing.downcast_into()?;
            list.append(entry)?;
        } else {
            let list = pyo3::types::PyList::new(py, &[entry.as_any()])?;
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
        let parts: Vec<&str> = trimmed.splitn(5, ' ').collect();
        if parts.len() >= 5 {
            folders.push(parts[4..].join(" "));
        }
    }
    folders
}
