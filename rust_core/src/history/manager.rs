use chrono::Local;
use log::{debug, error, info, warn};
use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyDict};
use std::collections::HashMap;
use std::fs;
use std::io::{BufReader, Write};
use std::path::Path;

const CSV_HEADER: &[&str] = &[
    "href",
    "phase",
    "video_code",
    "create_date",
    "update_date",
    "hacked_subtitle",
    "hacked_no_subtitle",
    "subtitle",
    "no_subtitle",
];

const TORRENT_CATEGORIES: &[&str] = &[
    "hacked_subtitle",
    "hacked_no_subtitle",
    "subtitle",
    "no_subtitle",
];

// ── CSV I/O helpers ─────────────────────────────────────────────────────

type Record = HashMap<String, String>;

fn read_csv_records(path: &str) -> Result<(Vec<String>, Vec<Record>), String> {
    let file = fs::File::open(path).map_err(|e| e.to_string())?;
    let mut reader = csv::ReaderBuilder::new()
        .has_headers(true)
        .from_reader(BufReader::new(file));

    let headers: Vec<String> = reader
        .headers()
        .map_err(|e| e.to_string())?
        .iter()
        .map(|h| h.trim_start_matches('\u{feff}').to_string())
        .collect();

    let mut records = Vec::new();
    for result in reader.records() {
        let row = result.map_err(|e| e.to_string())?;
        let mut map = HashMap::new();
        for (i, field) in row.iter().enumerate() {
            if let Some(key) = headers.get(i) {
                map.insert(key.clone(), field.to_string());
            }
        }
        records.push(map);
    }
    Ok((headers, records))
}

fn write_csv_records(path: &str, records: &[Record]) -> Result<(), String> {
    let bom = b"\xef\xbb\xbf";
    let mut file = fs::File::create(path).map_err(|e| e.to_string())?;
    file.write_all(bom).map_err(|e| e.to_string())?;

    let mut writer = csv::Writer::from_writer(file);
    writer
        .write_record(CSV_HEADER)
        .map_err(|e| e.to_string())?;

    for rec in records {
        let row: Vec<String> = CSV_HEADER
            .iter()
            .map(|h| rec.get(*h).cloned().unwrap_or_default())
            .collect();
        writer.write_record(&row).map_err(|e| e.to_string())?;
    }
    writer.flush().map_err(|e| e.to_string())?;
    Ok(())
}

fn get_update_date(record: &Record) -> String {
    record
        .get("update_date")
        .filter(|v| !v.is_empty())
        .or_else(|| record.get("parsed_date"))
        .cloned()
        .unwrap_or_default()
}

fn normalize_record(record: &mut Record) {
    // Backward compat: parsed_date → create_date / update_date
    if !record.contains_key("create_date") || record["create_date"].is_empty() {
        if let Some(pd) = record.get("parsed_date").cloned() {
            record.insert("create_date".into(), pd);
        }
    }
    if !record.contains_key("update_date") || record["update_date"].is_empty() {
        if let Some(pd) = record.get("parsed_date").cloned() {
            record.insert("update_date".into(), pd);
        }
    }

    // Old torrent_type column → individual columns
    if let Some(tt) = record.remove("torrent_type") {
        let types: Vec<&str> = tt.split(',').map(|s| s.trim()).filter(|s| !s.is_empty()).collect();
        for cat in TORRENT_CATEGORIES {
            if !record.contains_key(*cat) {
                record.insert(cat.to_string(), String::new());
            }
        }
        let _ = types; // old format had no magnet links, just empty strings
    }

    // Ensure all required columns exist
    for cat in TORRENT_CATEGORIES {
        record.entry(cat.to_string()).or_default();
    }
}

fn extract_torrent_types(record: &Record) -> Vec<String> {
    if let Some(tt) = record.get("torrent_type") {
        return tt
            .split(',')
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();
    }

    let mut types = Vec::new();
    for cat in TORRENT_CATEGORIES {
        let content = record.get(*cat).map(|s| s.trim()).unwrap_or("");
        if content.is_empty() {
            continue;
        }
        if content.starts_with('[') && content.contains(']') {
            let after_bracket = content.splitn(2, ']').nth(1).unwrap_or("");
            if after_bracket.starts_with("magnet:") {
                types.push(cat.to_string());
            }
        } else if content.starts_with("magnet:") {
            types.push(cat.to_string());
        }
    }
    types
}

fn build_history_entry(record: &Record) -> Record {
    let mut entry = HashMap::new();
    entry.insert("phase".into(), record.get("phase").cloned().unwrap_or_default());
    entry.insert("video_code".into(), record.get("video_code").cloned().unwrap_or_default());
    entry.insert(
        "create_date".into(),
        record
            .get("create_date")
            .filter(|v| !v.is_empty())
            .or_else(|| record.get("parsed_date"))
            .cloned()
            .unwrap_or_default(),
    );
    entry.insert(
        "update_date".into(),
        record
            .get("update_date")
            .filter(|v| !v.is_empty())
            .or_else(|| record.get("parsed_date"))
            .cloned()
            .unwrap_or_default(),
    );

    let torrent_types = extract_torrent_types(record);
    let tt_strs: Vec<String> = torrent_types.iter().map(|s| s.to_string()).collect();
    entry.insert("torrent_types".into(), tt_strs.join(","));

    for cat in TORRENT_CATEGORIES {
        entry.insert(cat.to_string(), record.get(*cat).cloned().unwrap_or_default());
    }
    entry
}

// ── Public functions exposed to Python ───────────────────────────────────

#[pyfunction]
#[pyo3(signature = (history_file, phase=None))]
pub fn load_parsed_movies_history(
    py: Python<'_>,
    history_file: &str,
    phase: Option<i32>,
) -> PyResult<PyObject> {
    let result = py.allow_threads(|| load_history_impl(history_file, phase));

    match result {
        Ok(history) => {
            let dict = pyo3::types::PyDict::new_bound(py);
            for (href, entry) in &history {
                let inner = pyo3::types::PyDict::new_bound(py);
                for (k, v) in entry {
                    if k == "torrent_types" {
                        let types: Vec<&str> = v.split(',').filter(|s| !s.is_empty()).collect();
                        inner.set_item(k, types)?;
                    } else {
                        inner.set_item(k, v)?;
                    }
                }
                dict.set_item(href, inner)?;
            }
            Ok(dict.into())
        }
        Err(e) => {
            error!("Error loading parsed movies history: {}", e);
            let dict = pyo3::types::PyDict::new_bound(py);
            Ok(dict.into())
        }
    }
}

fn load_history_impl(
    history_file: &str,
    phase: Option<i32>,
) -> Result<HashMap<String, Record>, String> {
    let mut history: HashMap<String, Record> = HashMap::new();

    if !Path::new(history_file).exists() {
        info!("No parsed movies history found, starting fresh");
        return Ok(history);
    }

    let (_headers, records) = read_csv_records(history_file)?;

    // Dedup: keep most recent record per href
    let mut href_records: HashMap<String, Record> = HashMap::new();
    for row in &records {
        let href = row.get("href").cloned().unwrap_or_default();
        if href.is_empty() {
            continue;
        }
        if let Some(existing) = href_records.get(&href) {
            let existing_date = get_update_date(existing);
            let current_date = get_update_date(row);
            if current_date > existing_date {
                href_records.insert(href, row.clone());
            }
        } else {
            href_records.insert(href, row.clone());
        }
    }

    // Process deduplicated records
    for (href, row) in &href_records {
        let record_phase = row.get("phase").cloned().unwrap_or_default();

        let include = match phase {
            None => true,
            Some(1) => record_phase != "2",
            Some(2) => true,
            _ => true,
        };

        if include {
            history.insert(href.clone(), build_history_entry(row));
        }
    }

    // Clean up duplicates on disk
    if records.len() != href_records.len() {
        info!(
            "Found {} duplicate records, cleaning up history file",
            records.len() - href_records.len()
        );
        let _ = cleanup_history_impl(history_file, &href_records);
    }

    // Log phase counts
    let mut phase_counts: HashMap<String, usize> = HashMap::new();
    for entry in history.values() {
        let p = entry.get("phase").cloned().unwrap_or_default();
        *phase_counts.entry(p).or_insert(0) += 1;
    }
    let mut pairs: Vec<_> = phase_counts.iter().collect();
    pairs.sort_by_key(|(p, _)| (*p).clone());
    let details: Vec<String> = pairs.iter().map(|(p, c)| format!("phase {}: {}", p, c)).collect();

    if phase.is_none() {
        info!(
            "Loaded {} previously parsed movies from history ({})",
            history.len(),
            details.join(", ")
        );
    }

    Ok(history)
}

#[pyfunction]
pub fn cleanup_history_file(
    py: Python<'_>,
    history_file: &str,
    href_records: HashMap<String, HashMap<String, String>>,
) -> PyResult<()> {
    py.allow_threads(|| {
        let _ = cleanup_history_impl(history_file, &href_records);
    });
    Ok(())
}

fn cleanup_history_impl(
    history_file: &str,
    href_records: &HashMap<String, Record>,
) -> Result<(), String> {
    let mut sorted_records: Vec<Record> = href_records.values().cloned().collect();
    sorted_records.sort_by(|a, b| get_update_date(b).cmp(&get_update_date(a)));

    for rec in &mut sorted_records {
        normalize_record(rec);
    }

    write_csv_records(history_file, &sorted_records)?;
    info!(
        "Cleaned up history file: removed duplicates, kept {} unique records",
        sorted_records.len()
    );
    Ok(())
}

#[pyfunction]
#[pyo3(signature = (history_file, max_records=1000))]
pub fn maintain_history_limit(
    py: Python<'_>,
    history_file: &str,
    max_records: usize,
) -> PyResult<()> {
    py.allow_threads(|| {
        if let Err(e) = maintain_history_limit_impl(history_file, max_records) {
            error!("Error maintaining history limit: {}", e);
        }
    });
    Ok(())
}

fn maintain_history_limit_impl(history_file: &str, max_records: usize) -> Result<(), String> {
    if !Path::new(history_file).exists() {
        return Ok(());
    }

    let (_headers, records) = read_csv_records(history_file)?;
    if records.len() <= max_records {
        return Ok(());
    }

    let mut sorted = records;
    sorted.sort_by(|a, b| get_update_date(a).cmp(&get_update_date(b)));
    let skip_count = sorted.len().saturating_sub(max_records);
    let kept: Vec<Record> = sorted.into_iter().skip(skip_count).collect();

    let mut normalised: Vec<Record> = kept;
    for rec in &mut normalised {
        normalize_record(rec);
    }

    write_csv_records(history_file, &normalised)?;
    info!(
        "Maintained history limit: kept {} newest records, removed oldest entries",
        normalised.len()
    );
    Ok(())
}

#[pyfunction]
#[pyo3(signature = (history_file, href, phase, video_code, magnet_links=None))]
pub fn save_parsed_movie_to_history(
    py: Python<'_>,
    history_file: &str,
    href: &str,
    phase: &Bound<'_, pyo3::types::PyAny>,
    video_code: &str,
    magnet_links: Option<HashMap<String, String>>,
) -> PyResult<()> {
    let phase_str = phase.str()?.to_string();
    let links = magnet_links.unwrap_or_else(|| {
        let mut m = HashMap::new();
        m.insert("no_subtitle".into(), String::new());
        m
    });

    py.allow_threads(|| {
        if let Err(e) = save_history_impl(history_file, href, &phase_str, video_code, &links) {
            error!("Error writing to history file: {}", e);
        }
    });
    Ok(())
}

fn save_history_impl(
    history_file: &str,
    href: &str,
    phase: &str,
    video_code: &str,
    magnet_links: &HashMap<String, String>,
) -> Result<(), String> {
    let current_time = Local::now().format("%Y-%m-%d %H:%M:%S").to_string();
    let current_date = Local::now().format("%Y-%m-%d").to_string();

    let mut records: Vec<Record> = Vec::new();
    let mut existing_count = 0u32;
    let mut updated_record: Option<Record> = None;

    if Path::new(history_file).exists() {
        let (_headers, existing) = read_csv_records(history_file).unwrap_or_default();
        for mut row in existing {
            if row.get("href").map(|s| s.as_str()) == Some(href) {
                existing_count += 1;
                update_existing_record(&mut row, phase, magnet_links, &current_time, &current_date);
                apply_priority_cleanup(&mut row);
                updated_record = Some(row);
            } else {
                records.push(row);
            }
        }
    }

    if existing_count == 0 {
        let new_rec = create_new_record(href, phase, video_code, magnet_links, &current_time, &current_date);
        records.insert(0, new_rec);
        debug!("Added new record for {} with magnet links", href);
    } else {
        if let Some(rec) = updated_record {
            records.insert(0, rec);
        }
        if existing_count > 1 {
            warn!(
                "Found {} existing records for {}, keeping the updated one",
                existing_count, href
            );
        }
    }

    // Normalize all records before writing
    for rec in &mut records {
        normalize_record(rec);
    }

    write_csv_records(history_file, &records)?;
    debug!(
        "Updated history for {} (total records: {})",
        href,
        records.len()
    );
    Ok(())
}

fn update_existing_record(
    row: &mut Record,
    phase: &str,
    magnet_links: &HashMap<String, String>,
    current_time: &str,
    current_date: &str,
) {
    if row.contains_key("torrent_type") {
        // Old format
        let existing_str = row.get("torrent_type").cloned().unwrap_or_default();
        let mut existing_types: Vec<String> = existing_str
            .split(',')
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();
        for key in magnet_links.keys() {
            if !existing_types.contains(key) {
                existing_types.push(key.clone());
            }
        }
        existing_types.sort();
        row.insert("torrent_type".into(), existing_types.join(","));
        row.insert("update_date".into(), current_time.into());
        row.insert("phase".into(), phase.into());
    } else {
        // New format: apply filtered links with priority
        let mut filtered = HashMap::new();
        if magnet_links.get("hacked_subtitle").map(|s| !s.is_empty()).unwrap_or(false) {
            filtered.insert("hacked_subtitle", magnet_links["hacked_subtitle"].clone());
            filtered.insert("hacked_no_subtitle", String::new());
        } else {
            filtered.insert("hacked_subtitle", String::new());
            filtered.insert(
                "hacked_no_subtitle",
                magnet_links.get("hacked_no_subtitle").cloned().unwrap_or_default(),
            );
        }
        if magnet_links.get("subtitle").map(|s| !s.is_empty()).unwrap_or(false) {
            filtered.insert("subtitle", magnet_links["subtitle"].clone());
            filtered.insert("no_subtitle", String::new());
        } else {
            filtered.insert("subtitle", String::new());
            filtered.insert(
                "no_subtitle",
                magnet_links.get("no_subtitle").cloned().unwrap_or_default(),
            );
        }

        for (torrent_type, magnet_link) in &filtered {
            if !TORRENT_CATEGORIES.contains(&&**torrent_type) {
                continue;
            }
            if magnet_link.is_empty() {
                continue;
            }
            let old_content = row.get(*torrent_type).cloned().unwrap_or_default();
            let old_date = extract_date_from_content(&old_content);

            if let Some(od) = old_date {
                if current_date > od.as_str() {
                    row.insert(torrent_type.to_string(), format!("[{}]{}", current_date, magnet_link));
                }
            } else {
                row.insert(torrent_type.to_string(), format!("[{}]{}", current_date, magnet_link));
            }
        }

        row.insert("update_date".into(), current_time.into());
        row.insert("phase".into(), phase.into());
    }
}

fn create_new_record(
    href: &str,
    phase: &str,
    video_code: &str,
    magnet_links: &HashMap<String, String>,
    current_time: &str,
    current_date: &str,
) -> Record {
    let mut rec = HashMap::new();
    rec.insert("href".into(), href.into());
    rec.insert("phase".into(), phase.into());
    rec.insert("video_code".into(), video_code.into());
    rec.insert("create_date".into(), current_time.into());
    rec.insert("update_date".into(), current_time.into());

    for cat in TORRENT_CATEGORIES {
        let link = magnet_links.get(*cat).cloned().unwrap_or_default();
        if !link.is_empty() {
            rec.insert(cat.to_string(), format!("[{}]{}", current_date, link));
        } else {
            rec.insert(cat.to_string(), String::new());
        }
    }

    apply_priority_cleanup(&mut rec);
    rec
}

fn apply_priority_cleanup(record: &mut Record) {
    if record
        .get("hacked_subtitle")
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false)
    {
        record.insert("hacked_no_subtitle".into(), String::new());
    }
    if record
        .get("subtitle")
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false)
    {
        record.insert("no_subtitle".into(), String::new());
    }
}

fn extract_date_from_content(content: &str) -> Option<String> {
    let trimmed = content.trim();
    if trimmed.starts_with('[') && trimmed.contains(']') {
        Some(trimmed[1..trimmed.find(']').unwrap()].to_string())
    } else {
        None
    }
}

#[pyfunction]
pub fn validate_history_file(py: Python<'_>, history_file: &str) -> PyResult<bool> {
    Ok(py.allow_threads(|| validate_history_impl(history_file)))
}

fn validate_history_impl(history_file: &str) -> bool {
    if !Path::new(history_file).exists() {
        return true;
    }

    let (_headers, records) = match read_csv_records(history_file) {
        Ok(v) => v,
        Err(e) => {
            error!("Error validating history file: {}", e);
            return false;
        }
    };

    let needs_conversion = records
        .iter()
        .any(|r| r.contains_key("torrent_type") && !r.contains_key("hacked_subtitle"));

    if !needs_conversion {
        return true;
    }

    info!("Converting history file from old format to new format");
    let mut converted: Vec<Record> = records;
    for rec in &mut converted {
        normalize_record(rec);
    }

    match write_csv_records(history_file, &converted) {
        Ok(()) => {
            info!("Successfully converted history file to new format");
            true
        }
        Err(e) => {
            error!("Error validating history file: {}", e);
            false
        }
    }
}

// ── Pure logic functions ─────────────────────────────────────────────────

#[pyfunction]
pub fn determine_torrent_types(magnet_links: HashMap<String, String>) -> Vec<String> {
    let mut types: Vec<String> = magnet_links
        .iter()
        .filter(|(_, v)| !v.trim().is_empty())
        .filter(|(k, _)| TORRENT_CATEGORIES.contains(&k.as_str()))
        .map(|(k, _)| k.clone())
        .collect();
    types.sort();
    types.dedup();
    types
}

#[pyfunction]
pub fn determine_torrent_type(magnet_links: HashMap<String, String>) -> String {
    let types = determine_torrent_types(magnet_links);
    types.into_iter().next().unwrap_or_else(|| "no_subtitle".into())
}

#[pyfunction]
pub fn get_missing_torrent_types(
    history_torrent_types: Vec<String>,
    current_torrent_types: Vec<String>,
) -> Vec<String> {
    let mut missing = Vec::new();

    let hist_has = |t: &str| history_torrent_types.iter().any(|s| s == t);
    let curr_has = |t: &str| current_torrent_types.iter().any(|s| s == t);

    // Hacked category: prefer hacked_subtitle over hacked_no_subtitle
    if curr_has("hacked_subtitle") && !hist_has("hacked_subtitle") {
        missing.push("hacked_subtitle".into());
    } else if curr_has("hacked_no_subtitle")
        && !hist_has("hacked_no_subtitle")
        && !hist_has("hacked_subtitle")
    {
        missing.push("hacked_no_subtitle".into());
    }

    // Subtitle category: prefer subtitle over no_subtitle
    if curr_has("subtitle") && !hist_has("subtitle") {
        missing.push("subtitle".into());
    } else if curr_has("no_subtitle")
        && !hist_has("no_subtitle")
        && !hist_has("subtitle")
    {
        missing.push("no_subtitle".into());
    }

    missing
}

#[pyfunction]
#[pyo3(signature = (href, history_data=None))]
pub fn has_complete_subtitles(
    href: &str,
    history_data: Option<&Bound<'_, PyDict>>,
) -> PyResult<bool> {
    let history_data = match history_data {
        Some(d) => d,
        None => return Ok(false),
    };
    let entry = match history_data.get_item(href)? {
        Some(v) => v,
        None => return Ok(false),
    };
    let entry_dict: &Bound<'_, PyDict> = entry.downcast()?;
    let types: Vec<String> = match entry_dict.get_item("torrent_types")? {
        Some(v) => v.extract::<Vec<String>>()?,
        None => Vec::new(),
    };

    Ok(types.contains(&"subtitle".to_string()) && types.contains(&"hacked_subtitle".to_string()))
}

#[pyfunction]
pub fn should_process_movie(
    py: Python<'_>,
    href: &str,
    history_data: &Bound<'_, PyDict>,
    phase: i32,
    magnet_links: HashMap<String, String>,
) -> PyResult<(bool, PyObject)> {
    let entry = match history_data.get_item(href)? {
        Some(v) => v,
        None => {
            debug!("New movie {}: should process", href);
            return Ok((true, py.None()));
        }
    };

    let entry_dict: &Bound<'_, PyDict> = entry.downcast()?;
    let history_types: Vec<String> = match entry_dict.get_item("torrent_types")? {
        Some(v) => v.extract::<Vec<String>>()?,
        None => vec!["no_subtitle".to_string()],
    };

    let current_types = determine_torrent_types(magnet_links);
    let missing = get_missing_torrent_types(history_types.clone(), current_types.clone());

    let hist_list = pyo3::types::PyList::new_bound(py, &history_types);
    let hist_obj = hist_list.into();

    if phase == 1 {
        if !missing.is_empty() {
            debug!("Phase 1: missing types {:?} -> should process", missing);
            return Ok((true, hist_obj));
        }
        debug!("Phase 1: no missing types -> should not process");
        return Ok((false, hist_obj));
    }

    if phase == 2 {
        let hist_has_no_sub = current_types.contains(&"hacked_no_subtitle".to_string());
        let hist_has_no_subtitle_only = history_types.contains(&"no_subtitle".to_string());

        if hist_has_no_subtitle_only && hist_has_no_sub {
            debug!("Phase 2: upgrading no_subtitle to hacked_no_subtitle -> should process");
            return Ok((true, hist_obj));
        }
        if !missing.is_empty() {
            debug!("Phase 2: missing types {:?} -> should process", missing);
            return Ok((true, hist_obj));
        }
        debug!("Phase 2: no upgrade possible -> should not process");
        return Ok((false, hist_obj));
    }

    Ok((false, hist_obj))
}

#[pyfunction]
pub fn check_torrent_in_history(
    py: Python<'_>,
    history_file: &str,
    href: &str,
    torrent_type: &str,
) -> PyResult<bool> {
    Ok(py.allow_threads(|| check_torrent_impl(history_file, href, torrent_type)))
}

fn check_torrent_impl(history_file: &str, href: &str, torrent_type: &str) -> bool {
    if !Path::new(history_file).exists() {
        return false;
    }

    let records = match read_csv_records(history_file) {
        Ok((_, r)) => r,
        Err(e) => {
            error!("Error checking torrent in history: {}", e);
            return false;
        }
    };

    for row in &records {
        if row.get("href").map(|s| s.as_str()) != Some(href) {
            continue;
        }

        // Old format
        if let Some(tt) = row.get("torrent_type") {
            let types: Vec<&str> = tt.split(',').map(|s| s.trim()).collect();
            return types.contains(&torrent_type);
        }

        // New format
        let content = row.get(torrent_type).map(|s| s.trim()).unwrap_or("");
        if content.is_empty() {
            return false;
        }
        if content.starts_with('[') && content.contains(']') {
            let after = content.splitn(2, ']').nth(1).unwrap_or("");
            return after.starts_with("magnet:");
        }
        return content.starts_with("magnet:");
    }

    false
}

#[pyfunction]
pub fn add_downloaded_indicator_to_csv(
    py: Python<'_>,
    csv_file: &str,
    history_file: &str,
) -> PyResult<bool> {
    Ok(py.allow_threads(|| add_downloaded_impl(csv_file, history_file)))
}

fn add_downloaded_impl(csv_file: &str, history_file: &str) -> bool {
    if !Path::new(csv_file).exists() {
        error!("CSV file not found: {}", csv_file);
        return false;
    }

    let (headers, mut rows) = match read_csv_records(csv_file) {
        Ok(v) => v,
        Err(e) => {
            error!("Error reading CSV file: {}", e);
            return false;
        }
    };

    let mut modified = false;
    for row in &mut rows {
        let href = row.get("href").cloned().unwrap_or_default();
        for col in TORRENT_CATEGORIES {
            let content = row.get(*col).cloned().unwrap_or_default();
            if content.trim().is_empty() {
                continue;
            }
            if check_torrent_impl(history_file, &href, col) {
                if content.trim() != "[DOWNLOADED PREVIOUSLY]" {
                    row.insert(col.to_string(), "[DOWNLOADED PREVIOUSLY]".into());
                    modified = true;
                    debug!("Set downloaded indicator only for {} - {}", href, col);
                }
            }
        }
    }

    if modified {
        // Write back using original headers to preserve any extra columns
        let bom = b"\xef\xbb\xbf";
        let result = (|| -> Result<(), String> {
            let mut file = fs::File::create(csv_file).map_err(|e| e.to_string())?;
            file.write_all(bom).map_err(|e| e.to_string())?;
            let mut writer = csv::Writer::from_writer(file);
            writer.write_record(&headers).map_err(|e| e.to_string())?;
            for row in &rows {
                let fields: Vec<String> = headers
                    .iter()
                    .map(|h| row.get(h).cloned().unwrap_or_default())
                    .collect();
                writer.write_record(&fields).map_err(|e| e.to_string())?;
            }
            writer.flush().map_err(|e| e.to_string())?;
            Ok(())
        })();

        match result {
            Ok(()) => {
                info!("Added downloaded indicators to {}", csv_file);
                true
            }
            Err(e) => {
                error!("Error adding downloaded indicators to CSV: {}", e);
                false
            }
        }
    } else {
        info!("No downloaded torrents found in {}", csv_file);
        true
    }
}

#[pyfunction]
pub fn is_downloaded_torrent(torrent_content: &str) -> bool {
    torrent_content
        .trim()
        .starts_with("[DOWNLOADED PREVIOUSLY]")
}

#[pyfunction]
pub fn mark_torrent_as_downloaded(
    py: Python<'_>,
    history_file: &str,
    href: &str,
    video_code: &str,
    torrent_type: &str,
) -> PyResult<bool> {
    let mut links = HashMap::new();
    links.insert(torrent_type.to_string(), String::new());

    let result = py.allow_threads(|| save_history_impl(history_file, href, "2", video_code, &links));

    match result {
        Ok(()) => {
            debug!(
                "Marked {} as downloaded for {} ({})",
                torrent_type, video_code, href
            );
            Ok(true)
        }
        Err(e) => {
            error!("Error marking torrent as downloaded: {}", e);
            Ok(false)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_determine_torrent_types() {
        let mut links = HashMap::new();
        links.insert("subtitle".into(), "magnet:abc".into());
        links.insert("no_subtitle".into(), "".into());
        let types = determine_torrent_types(links);
        assert_eq!(types, vec!["subtitle"]);
    }

    #[test]
    fn test_get_missing_torrent_types() {
        let history = vec!["no_subtitle".to_string()];
        let current = vec!["subtitle".to_string(), "no_subtitle".to_string()];
        let missing = get_missing_torrent_types(history, current);
        assert_eq!(missing, vec!["subtitle"]);
    }

    #[test]
    fn test_is_downloaded_torrent() {
        assert!(is_downloaded_torrent("[DOWNLOADED PREVIOUSLY]"));
        assert!(is_downloaded_torrent("  [DOWNLOADED PREVIOUSLY]  "));
        assert!(!is_downloaded_torrent("magnet:?xt=urn:btih:abc"));
    }

    #[test]
    fn test_extract_date_from_content() {
        assert_eq!(
            extract_date_from_content("[2025-01-15]magnet:abc"),
            Some("2025-01-15".to_string())
        );
        assert_eq!(extract_date_from_content("magnet:abc"), None);
        assert_eq!(extract_date_from_content(""), None);
    }

    #[test]
    fn test_priority_cleanup() {
        let mut rec = HashMap::new();
        rec.insert("hacked_subtitle".into(), "[2025-01-01]magnet:abc".into());
        rec.insert("hacked_no_subtitle".into(), "[2025-01-01]magnet:def".into());
        rec.insert("subtitle".into(), "[2025-01-01]magnet:ghi".into());
        rec.insert("no_subtitle".into(), "[2025-01-01]magnet:jkl".into());
        apply_priority_cleanup(&mut rec);
        assert_eq!(rec["hacked_no_subtitle"], "");
        assert_eq!(rec["no_subtitle"], "");
    }
}
