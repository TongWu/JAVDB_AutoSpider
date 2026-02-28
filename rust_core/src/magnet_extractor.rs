use pyo3::prelude::*;
use std::collections::HashMap;

#[derive(FromPyObject, Clone)]
pub struct MagnetInput {
    #[pyo3(item)]
    href: String,
    #[pyo3(item)]
    name: String,
    #[pyo3(item)]
    tags: Vec<String>,
    #[pyo3(item)]
    size: String,
    #[pyo3(item)]
    timestamp: String,
}

fn parse_size(size_str: &str) -> f64 {
    if size_str.is_empty() {
        return 0.0;
    }
    let upper = size_str.to_uppercase();
    if let Some(rest) = upper.strip_suffix("GB") {
        rest.trim().parse::<f64>().unwrap_or(0.0) * 1024.0 * 1024.0 * 1024.0
    } else if let Some(rest) = upper.strip_suffix("MB") {
        rest.trim().parse::<f64>().unwrap_or(0.0) * 1024.0 * 1024.0
    } else if let Some(rest) = upper.strip_suffix("KB") {
        rest.trim().parse::<f64>().unwrap_or(0.0) * 1024.0
    } else {
        0.0
    }
}

fn sort_key(m: &MagnetInput) -> (String, i64) {
    (m.timestamp.clone(), parse_size(&m.size) as i64)
}

fn sort_magnets(magnets: &mut [MagnetInput]) {
    magnets.sort_by(|a, b| sort_key(b).cmp(&sort_key(a)));
}

fn has_subtitle_tag(tags: &[String]) -> bool {
    tags.iter()
        .any(|t| t.contains("字幕") || t.contains("Subtitle"))
}

fn is_hacked_subtitle(name: &str) -> bool {
    name.contains("-UC")
        || name.contains("-CU")
        || name.contains("-C.无码破解")
        || name.contains("-U-C")
        || name.contains("-C-U")
}

fn is_hacked_no_subtitle(name: &str) -> bool {
    name.contains("-U") || name.contains(".无码破解")
}

fn is_hacked(name: &str) -> bool {
    is_hacked_subtitle(name) || is_hacked_no_subtitle(name)
}

fn best_from(magnets: &mut Vec<MagnetInput>) -> Option<MagnetInput> {
    if magnets.is_empty() {
        return None;
    }
    sort_magnets(magnets);
    Some(magnets[0].clone())
}

#[pyfunction]
pub fn extract_magnets(magnets: Vec<MagnetInput>) -> HashMap<String, String> {
    let mut result: HashMap<String, String> = HashMap::with_capacity(8);
    for key in &[
        "hacked_subtitle",
        "hacked_no_subtitle",
        "subtitle",
        "no_subtitle",
        "size_hacked_subtitle",
        "size_hacked_no_subtitle",
        "size_subtitle",
        "size_no_subtitle",
    ] {
        result.insert(key.to_string(), String::new());
    }

    // --- subtitle ---
    let mut subtitle_magnets: Vec<MagnetInput> = magnets
        .iter()
        .filter(|m| has_subtitle_tag(&m.tags) && !m.name.contains(".无码破解"))
        .cloned()
        .collect();

    if let Some(best) = best_from(&mut subtitle_magnets) {
        result.insert("subtitle".into(), best.href);
        result.insert("size_subtitle".into(), best.size);
    }

    // --- hacked_subtitle / hacked_no_subtitle ---
    let mut hacked_sub: Vec<MagnetInput> = Vec::new();
    let mut hacked_nosub: Vec<MagnetInput> = Vec::new();

    for m in &magnets {
        if is_hacked_subtitle(&m.name) {
            hacked_sub.push(m.clone());
        } else if is_hacked_no_subtitle(&m.name) {
            hacked_nosub.push(m.clone());
        }
    }

    if let Some(best) = best_from(&mut hacked_sub) {
        result.insert("hacked_subtitle".into(), best.href);
        result.insert("size_hacked_subtitle".into(), best.size);
    } else if let Some(best) = best_from(&mut hacked_nosub) {
        result.insert("hacked_no_subtitle".into(), best.href);
        result.insert("size_hacked_no_subtitle".into(), best.size);
    }

    // --- no_subtitle (prefer 4k) ---
    let mut k4: Vec<MagnetInput> = Vec::new();
    let mut normal: Vec<MagnetInput> = Vec::new();

    for m in &magnets {
        let is_sub = has_subtitle_tag(&m.tags) && !m.name.contains(".无码破解");
        if is_sub || is_hacked(&m.name) {
            continue;
        }
        if m.name.to_lowercase().contains("4k") {
            k4.push(m.clone());
        } else {
            normal.push(m.clone());
        }
    }

    if let Some(best) = best_from(&mut k4) {
        result.insert("no_subtitle".into(), best.href);
        result.insert("size_no_subtitle".into(), best.size);
    } else if let Some(best) = best_from(&mut normal) {
        result.insert("no_subtitle".into(), best.href);
        result.insert("size_no_subtitle".into(), best.size);
    }

    result
}
