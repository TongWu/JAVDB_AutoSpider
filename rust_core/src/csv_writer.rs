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
