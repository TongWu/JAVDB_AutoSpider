use log::debug;
use once_cell::sync::Lazy;
use regex::Regex;
use scraper::{ElementRef, Html, Selector};

use crate::models::MovieLink;

// Pre-compiled regex patterns
static RATE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(\d+\.?\d*)分").unwrap());
static COMMENT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"由(\d+)人評價").unwrap());

static PAGE_TYPE_PATTERNS: Lazy<Vec<(&str, Regex)>> = Lazy::new(|| {
    vec![
        ("top250", Regex::new(r"/rankings/top").unwrap()),
        ("top_movies", Regex::new(r"/rankings/movies").unwrap()),
        ("top_playback", Regex::new(r"/rankings/playback").unwrap()),
        ("actors", Regex::new(r"/actors/").unwrap()),
        ("makers", Regex::new(r"/makers/").unwrap()),
        ("publishers", Regex::new(r"/publishers/").unwrap()),
        ("series", Regex::new(r"/series/").unwrap()),
        ("directors", Regex::new(r"/directors/").unwrap()),
        ("video_codes", Regex::new(r"/video_codes/").unwrap()),
        ("tags", Regex::new(r"/tags").unwrap()),
    ]
});

static URL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?:href|url)=["']?(?:\(\d+\))?(https?://[^"'>\s)]+)"#).unwrap()
});

pub fn extract_rate_and_comments(score_text: &str) -> (String, String) {
    let rate = RATE_RE
        .captures(score_text)
        .and_then(|c| c.get(1))
        .map_or(String::new(), |m| m.as_str().to_string());

    let comment_count = COMMENT_RE
        .captures(score_text)
        .and_then(|c| c.get(1))
        .map_or(String::new(), |m| m.as_str().to_string());

    (rate, comment_count)
}

pub fn extract_movie_link(a_tag: &ElementRef) -> Option<MovieLink> {
    let name = get_text_content(a_tag).trim().to_string();
    if name.is_empty() {
        return None;
    }
    let href = a_tag.value().attr("href").unwrap_or("").to_string();
    Some(MovieLink { name, href })
}

pub fn extract_all_movie_links(parent: &ElementRef) -> Vec<MovieLink> {
    let sel = Selector::parse("a").unwrap();
    parent
        .select(&sel)
        .filter_map(|a| extract_movie_link(&a))
        .collect()
}

pub fn extract_video_code(a_tag: &ElementRef) -> String {
    let sel = Selector::parse("div.video-title").unwrap();
    if let Some(video_title_div) = a_tag.select(&sel).next() {
        let strong_sel = Selector::parse("strong").unwrap();
        let video_code = if let Some(strong) = video_title_div.select(&strong_sel).next() {
            get_text_content(&strong).trim().to_string()
        } else {
            get_text_content(&video_title_div).trim().to_string()
        };

        if !video_code.contains('-') {
            debug!("Skipping invalid video code (no '-'): {}", video_code);
            return String::new();
        }
        return video_code;
    }

    debug!("No video-title div found");
    String::new()
}

pub fn detect_page_type(html_content: &str) -> String {
    let prefix = if html_content.len() > 3000 {
        &html_content[..3000]
    } else {
        html_content
    };

    if let Some(caps) = URL_RE.captures(prefix) {
        let url = &caps[1];
        for (page_type, pattern) in PAGE_TYPE_PATTERNS.iter() {
            if pattern.is_match(url) {
                return page_type.to_string();
            }
        }
    }

    let check_region = if html_content.len() > 50000 {
        &html_content[..50000]
    } else {
        html_content
    };

    if check_region.contains("magnets-content") || check_region.contains("video-meta-panel") {
        return "detail".to_string();
    }

    if check_region.contains("movie-list") {
        return "index".to_string();
    }

    "unknown".to_string()
}

pub fn extract_category_name(document: &Html) -> (String, String) {
    let actor_sel = Selector::parse("span.actor-section-name").unwrap();
    if let Some(span) = document.select(&actor_sel).next() {
        return ("actors".to_string(), get_text_content(&span).trim().to_string());
    }

    let section_sel = Selector::parse("span.section-name").unwrap();
    if let Some(span) = document.select(&section_sel).next() {
        return (String::new(), get_text_content(&span).trim().to_string());
    }

    let title_sel = Selector::parse("title").unwrap();
    if let Some(title_tag) = document.select(&title_sel).next() {
        let title_text = get_text_content(&title_tag).trim().to_string();
        let re = Regex::new(r"\s*\|\s*JavDB.*$").unwrap();
        let cleaned = re.replace(&title_text, "").trim().to_string();
        return (String::new(), cleaned);
    }

    (String::new(), String::new())
}

/// Check whether the HTML represents a JavDB login page.
pub fn is_login_page(html_content: &str) -> bool {
    if html_content.is_empty() {
        return false;
    }
    let document = Html::parse_document(html_content);
    let title_sel = Selector::parse("title").unwrap();
    if let Some(title_tag) = document.select(&title_sel).next() {
        let title_text: String = title_tag.text().collect::<Vec<_>>().join("");
        let lower = title_text.trim().to_lowercase();
        if lower.contains("登入") || lower.contains("login") {
            return true;
        }
    }
    false
}

/// Validate index page HTML.
///
/// Returns ``(has_movie_list, is_valid_empty_page)``.
pub fn validate_index_html(html_content: &str) -> (bool, bool) {
    let document = Html::parse_document(html_content);

    // Look for movie-list container
    let div_sel = Selector::parse("div").unwrap();
    let has_movie_list = document.select(&div_sel).any(|div| {
        if class_contains_in_html(&div, "movie-list") {
            return true;
        }
        false
    });

    if has_movie_list {
        // Re-find and check item count
        for div in document.select(&div_sel) {
            if class_contains_in_html(&div, "movie-list") {
                let item_sel = Selector::parse("div.item").unwrap();
                let count = div.select(&item_sel).count();
                if count > 0 {
                    return (true, false);
                } else {
                    return (false, true);
                }
            }
        }
    }

    {
        // Check for empty-message div
        let empty_sel = Selector::parse("div.empty-message").unwrap();
        if document.select(&empty_sel).next().is_some() {
            return (false, true);
        }

        // Check for no-content text patterns
        let body_text: String = document.root_element().text().collect();
        let no_content_patterns = [
            "No content yet",
            "No result",
            "暫無內容",
            "暂无内容",
        ];
        let age_modal_sel = Selector::parse("div.modal.is-active.over18-modal").unwrap();
        let has_age_modal = document.select(&age_modal_sel).next().is_some();

        if !has_age_modal {
            for pattern in &no_content_patterns {
                if body_text.contains(pattern) {
                    return (false, true);
                }
            }
            if html_content.len() > 20000 {
                return (false, true); // large HTML without movie list
            }
        }
    }

    (false, false)
}

fn class_contains_in_html(el: &ElementRef, substr: &str) -> bool {
    el.value()
        .attr("class")
        .map_or(false, |classes| classes.contains(substr))
}

pub fn get_text_content(el: &ElementRef) -> String {
    el.text().collect::<Vec<_>>().join("")
}

pub fn has_class(el: &ElementRef, class_name: &str) -> bool {
    el.value()
        .attr("class")
        .map_or(false, |classes| classes.split_whitespace().any(|c| c == class_name))
}

pub fn class_contains(el: &ElementRef, substr: &str) -> bool {
    el.value()
        .attr("class")
        .map_or(false, |classes| classes.contains(substr))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_rate_and_comments() {
        let (rate, comments) = extract_rate_and_comments("4.47分, 由595人評價");
        assert_eq!(rate, "4.47");
        assert_eq!(comments, "595");
    }

    #[test]
    fn test_detect_page_type_detail() {
        assert_eq!(detect_page_type("<div class=\"magnets-content\">"), "detail");
    }

    #[test]
    fn test_detect_page_type_index() {
        assert_eq!(detect_page_type("<div class=\"movie-list\">"), "index");
    }
}
