use pyo3::prelude::*;
use regex::Regex;
use std::sync::LazyLock;
use url::Url;

static WHITESPACE_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\s+").unwrap());
static NON_SAFE_CHAR_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\-]").unwrap());
static MULTI_UNDERSCORE_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"_+").unwrap());

fn path_prefix(url_str: &str) -> Option<String> {
    let parsed = Url::parse(url_str).ok()?;
    let path = parsed.path().trim_matches('/');
    path.split('/').next().map(|s| s.to_string())
}

#[pyfunction]
pub fn detect_url_type(url: &str) -> String {
    if url.is_empty() || !url.contains("javdb.com") {
        return "unknown".into();
    }
    match path_prefix(url).as_deref() {
        Some("actors") => "actors".into(),
        Some("makers") => "makers".into(),
        Some("publishers") => "publishers".into(),
        Some("series") => "series".into(),
        Some("directors") => "directors".into(),
        Some("video_codes") => "video_codes".into(),
        _ => "unknown".into(),
    }
}

#[pyfunction]
pub fn extract_url_identifier(url: &str) -> Option<String> {
    let parsed = Url::parse(url).ok()?;
    let path = parsed.path().trim_matches('/');
    let parts: Vec<&str> = path.split('/').collect();
    if parts.len() >= 2 {
        Some(parts[1].to_string())
    } else {
        None
    }
}

#[pyfunction]
pub fn has_magnet_filter(url: &str) -> bool {
    let parsed = match Url::parse(url) {
        Ok(u) => u,
        Err(_) => return false,
    };
    let path = parsed.path().trim_matches('/');
    let pairs: Vec<(String, String)> = parsed.query_pairs().map(|(k, v)| (k.into_owned(), v.into_owned())).collect();

    if path.starts_with("actors/") {
        for (k, v) in &pairs {
            if k == "t" {
                let parts: Vec<&str> = v.split(',').collect();
                if parts.contains(&"d") || parts.contains(&"c") {
                    return true;
                }
            }
        }
        false
    } else if path.starts_with("makers/") || path.starts_with("video_codes/") {
        pairs.iter().any(|(k, v)| k == "f" && v == "download")
    } else {
        false
    }
}

#[pyfunction]
pub fn add_magnet_filter_to_url(url: &str) -> String {
    if has_magnet_filter(url) {
        return url.to_string();
    }

    let parsed = match Url::parse(url) {
        Ok(u) => u,
        Err(_) => return url.to_string(),
    };
    let path = parsed.path().trim_matches('/');

    if path.starts_with("actors/") {
        add_actors_filter(url, &parsed)
    } else if path.starts_with("makers/") || path.starts_with("video_codes/") {
        add_download_filter(url, &parsed)
    } else {
        url.to_string()
    }
}

fn add_actors_filter(url: &str, parsed: &Url) -> String {
    if parsed.query().is_none() || parsed.query() == Some("") {
        let base = url.trim_end_matches('?');
        return format!("{base}?t=d");
    }

    let pairs: Vec<(String, String)> = parsed.query_pairs().map(|(k, v)| (k.into_owned(), v.into_owned())).collect();
    let has_t = pairs.iter().any(|(k, _)| k == "t");

    if !has_t {
        let base = url.trim_end_matches('&');
        return format!("{base}&t=d");
    }

    let mut new_pairs: Vec<(String, String)> = Vec::new();
    for (k, v) in &pairs {
        if k == "t" {
            let parts: Vec<&str> = v.split(',').collect();
            if !parts.contains(&"d") && !parts.contains(&"c") {
                new_pairs.push((k.clone(), format!("{v},d")));
            } else {
                new_pairs.push((k.clone(), v.clone()));
            }
        } else {
            new_pairs.push((k.clone(), v.clone()));
        }
    }

    let mut result = parsed.clone();
    result.set_query(None);
    let query = new_pairs
        .iter()
        .map(|(k, v)| format!("{k}={v}"))
        .collect::<Vec<_>>()
        .join("&");
    result.set_query(Some(&query));
    result.to_string()
}

fn add_download_filter(url: &str, parsed: &Url) -> String {
    if parsed.query().is_none() || parsed.query() == Some("") {
        let base = url.trim_end_matches('?');
        return format!("{base}?f=download");
    }

    let pairs: Vec<(String, String)> = parsed.query_pairs().map(|(k, v)| (k.into_owned(), v.into_owned())).collect();
    let has_f = pairs.iter().any(|(k, _)| k == "f");

    if !has_f {
        let base = url.trim_end_matches('&');
        return format!("{base}&f=download");
    }

    let mut new_pairs: Vec<(String, String)> = Vec::new();
    for (k, v) in &pairs {
        if k == "f" {
            new_pairs.push((k.clone(), "download".into()));
        } else {
            new_pairs.push((k.clone(), v.clone()));
        }
    }

    let mut result = parsed.clone();
    result.set_query(None);
    let query = new_pairs
        .iter()
        .map(|(k, v)| format!("{k}={v}"))
        .collect::<Vec<_>>()
        .join("&");
    result.set_query(Some(&query));
    result.to_string()
}

#[pyfunction]
#[pyo3(signature = (page_num, base_url, custom_url=None))]
pub fn get_page_url(page_num: i32, base_url: &str, custom_url: Option<&str>) -> String {
    if let Some(cu) = custom_url {
        if page_num == 1 {
            return cu.to_string();
        }
        let sep = if cu.contains('?') { '&' } else { '?' };
        return format!("{cu}{sep}page={page_num}");
    }

    let sep = if base_url.contains('?') { '&' } else { '?' };
    format!("{base_url}{sep}page={page_num}")
}

#[pyfunction]
#[pyo3(signature = (text, max_length=30))]
pub fn sanitize_filename_part(text: &str, max_length: usize) -> String {
    if text.is_empty() {
        return String::new();
    }

    let unsafe_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*'];
    let mut sanitized: String = text.chars().filter(|c| !unsafe_chars.contains(c)).collect();

    sanitized = WHITESPACE_RE.replace_all(&sanitized, "_").to_string();
    sanitized = NON_SAFE_CHAR_RE.replace_all(&sanitized, "").to_string();

    // Truncate by character count to match Python's text[:max_length] (not byte count)
    if sanitized.chars().count() > max_length {
        sanitized = sanitized.chars().take(max_length).collect();
    }

    sanitized
}

#[pyfunction]
pub fn extract_url_part_after_javdb(url: &str) -> String {
    const FALLBACK: &str = "custom_url";

    let domain_pos = match url.find("javdb.com") {
        Some(pos) => pos,
        None => return FALLBACK.into(),
    };

    let after = &url[domain_pos + "javdb.com".len()..];
    let after = after.trim_matches('/');

    if after.is_empty() {
        return FALLBACK.into();
    }

    let mut result = after.to_string();
    for ch in ['/', '?', '&'] {
        result = result.replace(ch, "_");
    }
    result = result.replace('=', "-");
    result = MULTI_UNDERSCORE_RE.replace_all(&result, "_").to_string();
    result = result.trim_matches('_').to_string();

    if result.is_empty() { FALLBACK.into() } else { result }
}
