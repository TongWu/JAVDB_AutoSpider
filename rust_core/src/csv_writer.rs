use pyo3::prelude::*;
use std::collections::HashMap;

const DOWNLOADED_PLACEHOLDER: &str = "[DOWNLOADED PREVIOUSLY]";

#[pyfunction]
pub fn merge_row_data(
    existing_row: HashMap<String, String>,
    new_row: HashMap<String, String>,
) -> HashMap<String, String> {
    let mut merged = existing_row;

    for (key, new_value) in &new_row {
        let existing_value = merged.get(key).cloned().unwrap_or_default();

        if new_value == DOWNLOADED_PLACEHOLDER {
            if existing_value.is_empty() {
                merged.insert(key.clone(), new_value.clone());
            }
        } else if !new_value.is_empty() {
            merged.insert(key.clone(), new_value.clone());
        }
    }

    merged
}

#[pyfunction]
pub fn create_csv_row(
    href: &str,
    video_code: &str,
    page: i32,
    actor_info: &str,
    rate: &str,
    comment_number: &str,
    magnet_links: HashMap<String, String>,
    history_torrent_types: Vec<String>,
    missing_types: Vec<String>,
) -> HashMap<String, String> {
    let mut row = HashMap::new();
    row.insert("href".into(), href.into());
    row.insert("video_code".into(), video_code.into());
    row.insert("page".into(), page.to_string());
    row.insert("actor".into(), actor_info.into());
    row.insert("rate".into(), rate.into());
    row.insert("comment_number".into(), comment_number.into());

    let torrent_fields = [
        "hacked_subtitle",
        "hacked_no_subtitle",
        "subtitle",
        "no_subtitle",
    ];
    let size_fields = [
        "size_hacked_subtitle",
        "size_hacked_no_subtitle",
        "size_subtitle",
        "size_no_subtitle",
    ];

    for (tf, sf) in torrent_fields.iter().zip(size_fields.iter()) {
        let magnet = magnet_links.get(*tf).cloned().unwrap_or_default();
        let size = magnet_links.get(*sf).cloned().unwrap_or_default();

        if history_torrent_types.is_empty() {
            // New movie — apply preference rules
            let include = match *tf {
                "no_subtitle" => magnet_links.get("subtitle").map_or(true, |s| s.is_empty()),
                "hacked_no_subtitle" => {
                    magnet_links
                        .get("hacked_subtitle")
                        .map_or(true, |s| s.is_empty())
                }
                _ => true,
            };
            if include && !magnet.is_empty() {
                row.insert(tf.to_string(), magnet);
                row.insert(sf.to_string(), size);
            } else {
                row.insert(tf.to_string(), String::new());
                row.insert(sf.to_string(), String::new());
            }
        } else if missing_types.contains(&tf.to_string()) && !magnet.is_empty() {
            row.insert(tf.to_string(), magnet);
            row.insert(sf.to_string(), size);
        } else if history_torrent_types.contains(&tf.to_string()) && !magnet.is_empty() {
            row.insert(tf.to_string(), DOWNLOADED_PLACEHOLDER.into());
            row.insert(sf.to_string(), size);
        } else {
            row.insert(tf.to_string(), String::new());
            row.insert(sf.to_string(), String::new());
        }
    }

    row
}

#[pyfunction]
#[pyo3(signature = (row, include_downloaded_in_report=false))]
pub fn check_torrent_status(
    row: HashMap<String, String>,
    include_downloaded_in_report: bool,
) -> (bool, bool, bool) {
    let fields = ["hacked_subtitle", "hacked_no_subtitle", "subtitle", "no_subtitle"];
    let has_any = fields
        .iter()
        .any(|f| !row.get(*f).cloned().unwrap_or_default().is_empty());
    let has_new = fields.iter().any(|f| {
        let v = row.get(*f).cloned().unwrap_or_default();
        !v.is_empty() && v != DOWNLOADED_PLACEHOLDER
    });
    let should_include = has_new || (include_downloaded_in_report && has_any);
    (has_any, has_new, should_include)
}

#[pyfunction]
pub fn collect_new_magnet_links(
    row: HashMap<String, String>,
    magnet_links: HashMap<String, String>,
) -> (
    HashMap<String, String>,
    HashMap<String, String>,
    HashMap<String, i32>,
    HashMap<String, Option<i32>>,
) {
    let mut new_magnets: HashMap<String, String> = HashMap::new();
    let mut new_sizes: HashMap<String, String> = HashMap::new();
    let mut new_file_counts: HashMap<String, i32> = HashMap::new();
    let mut new_resolutions: HashMap<String, Option<i32>> = HashMap::new();

    for mtype in ["hacked_subtitle", "hacked_no_subtitle", "subtitle", "no_subtitle"] {
        let value = row.get(mtype).cloned().unwrap_or_default();
        if value.is_empty() || value == DOWNLOADED_PLACEHOLDER {
            continue;
        }

        new_magnets.insert(
            mtype.to_string(),
            magnet_links.get(mtype).cloned().unwrap_or_default(),
        );
        new_sizes.insert(
            mtype.to_string(),
            magnet_links
                .get(&format!("size_{mtype}"))
                .cloned()
                .unwrap_or_default(),
        );
        let fc = magnet_links
            .get(&format!("file_count_{mtype}"))
            .and_then(|s| s.parse::<i32>().ok())
            .unwrap_or(0);
        new_file_counts.insert(mtype.to_string(), fc);
        let res = magnet_links
            .get(&format!("resolution_{mtype}"))
            .and_then(|s| s.parse::<i32>().ok());
        new_resolutions.insert(mtype.to_string(), res);
    }

    (new_magnets, new_sizes, new_file_counts, new_resolutions)
}
