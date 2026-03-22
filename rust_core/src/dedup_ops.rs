//! Dedup-related hot path operations.
//!
//! UNCENSORED_SENSOR_PRIORITY mirrors `utils.contracts.UNCENSORED_SENSOR_PRIORITY`.

use pyo3::prelude::*;
use pyo3::types::PyDict;

fn as_upper(s: &str) -> String {
    s.trim().to_uppercase()
}

/// Priority table mirroring Python's UNCENSORED_SENSOR_PRIORITY.
fn uncensored_sensor_priority(sensor: &str) -> u8 {
    match sensor {
        "无码流出" => 3,
        "无码" => 2,
        "无码破解" => 1,
        _ => 0,
    }
}

#[pyfunction]
pub fn should_skip_from_rclone<'py>(
    video_code: &str,
    entries: Vec<Bound<'py, PyDict>>,
    enable_dedup: bool,
) -> PyResult<bool> {
    if enable_dedup {
        return Ok(false);
    }
    let code = as_upper(video_code);
    if code.is_empty() {
        return Ok(false);
    }
    for entry in &entries {
        let vc: String = entry
            .get_item("video_code")?
            .and_then(|v| v.extract().ok())
            .unwrap_or_default();
        if as_upper(&vc) != code {
            continue;
        }
        let subtitle: String = entry
            .get_item("subtitle_category")?
            .and_then(|v| v.extract().ok())
            .unwrap_or_default();
        if subtitle == "中字" {
            return Ok(true);
        }
    }
    Ok(false)
}

#[pyfunction]
pub fn check_dedup_upgrade<'py>(
    py: Python<'py>,
    video_code: &str,
    new_torrent_types: Bound<'py, PyDict>,
    entries: Vec<Bound<'py, PyDict>>,
) -> PyResult<Vec<Py<PyDict>>> {
    let has_subtitle = new_torrent_types
        .get_item("subtitle")?
        .and_then(|v| v.extract::<bool>().ok())
        .unwrap_or(false)
        || new_torrent_types
            .get_item("hacked_subtitle")?
            .and_then(|v| v.extract::<bool>().ok())
            .unwrap_or(false);

    let hacked = new_torrent_types
        .get_item("hacked_subtitle")?
        .and_then(|v| v.extract::<bool>().ok())
        .unwrap_or(false)
        || new_torrent_types
            .get_item("hacked_no_subtitle")?
            .and_then(|v| v.extract::<bool>().ok())
            .unwrap_or(false);

    // Infer new sensor priority: non-hacked implies 無碼 (prio 2)
    let inferred_new_prio: u8 = if !hacked {
        uncensored_sensor_priority("无码")
    } else {
        0
    };

    let now_str = chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string();
    let mut out = Vec::new();

    for entry in &entries {
        let sensor: String = entry
            .get_item("sensor_category")?
            .and_then(|v| v.extract().ok())
            .unwrap_or_default();
        let subtitle: String = entry
            .get_item("subtitle_category")?
            .and_then(|v| v.extract().ok())
            .unwrap_or_default();
        let folder_path: String = entry
            .get_item("folder_path")?
            .and_then(|v| v.extract().ok())
            .unwrap_or_default();
        let folder_size: i64 = entry
            .get_item("folder_size")?
            .and_then(|v| v.extract().ok())
            .unwrap_or(0);

        let mut reasons: Vec<String> = Vec::new();

        if has_subtitle && subtitle == "无字" {
            reasons.push("Subtitle upgrade (中字 found, replacing 无字)".to_string());
        }

        let existing_prio = uncensored_sensor_priority(&sensor);
        if existing_prio > 0 && inferred_new_prio > existing_prio {
            reasons.push(format!("Sensor upgrade (无码 > {})", sensor));
        }

        if reasons.is_empty() {
            continue;
        }

        let category = if has_subtitle { "中字" } else { "无字" };
        let mut new_cat = category.to_string();
        if hacked {
            new_cat.push_str("-破解");
        }

        let rec = PyDict::new_bound(py);
        rec.set_item("video_code", as_upper(video_code))?;
        rec.set_item("existing_sensor", sensor)?;
        rec.set_item("existing_subtitle", subtitle)?;
        rec.set_item("existing_gdrive_path", folder_path)?;
        rec.set_item("existing_folder_size", folder_size)?;
        rec.set_item("new_torrent_category", new_cat)?;
        rec.set_item("deletion_reason", reasons.join("; "))?;
        rec.set_item("detect_datetime", now_str.clone())?;
        rec.set_item("is_deleted", "False")?;
        rec.set_item("delete_datetime", "")?;
        out.push(rec.into());
    }

    Ok(out)
}
