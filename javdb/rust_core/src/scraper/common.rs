use log::debug;
use once_cell::sync::Lazy;
use regex::Regex;
use scraper::{ElementRef, Html, Selector};
use url::Url;

use crate::models::MovieLink;

// Pre-compiled regex patterns
static RATE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(\d+\.?\d*)分").unwrap());
static RATE_RE_EN: Lazy<Regex> = Lazy::new(|| Regex::new(r"(\d+\.?\d*),\s*by").unwrap());
static COMMENT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"由(\d+)人評價").unwrap());
static COMMENT_RE_EN: Lazy<Regex> = Lazy::new(|| Regex::new(r"by\s+(\d+)\s+users?").unwrap());
static WESTERN_STUDIO_DATE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*\.(?:\d{4}|\d{2})\.\d{2}\.\d{2}$").unwrap()
});
static MULTI_HYPHEN_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,}$").unwrap());
static NUMERIC_DATE_HYPHEN_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\d{6}-\d+$").unwrap());
static NUMERIC_DATE_UNDERSCORE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\d{6}_\d+$").unwrap());
static CLASSIC_HYPHENATED_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z]+-\d+[A-Za-z0-9]*$").unwrap());
static HYPHENLESS_STUDIO_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[A-Za-z]+\d+$").unwrap());

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

static URL_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"(?:href|url)=["']?(?:\(\d+\))?(https?://[^"'>\s)]+)"#).unwrap());

pub fn extract_rate_and_comments(score_text: &str) -> (String, String) {
    let rate = RATE_RE
        .captures(score_text)
        .or_else(|| RATE_RE_EN.captures(score_text))
        .and_then(|c| c.get(1))
        .map_or(String::new(), |m| m.as_str().to_string());

    let comment_count = COMMENT_RE
        .captures(score_text)
        .or_else(|| COMMENT_RE_EN.captures(score_text))
        .and_then(|c| c.get(1))
        .map_or(String::new(), |m| m.as_str().to_string());

    (rate, comment_count)
}

pub fn classify_video_code_family(raw: &str) -> &'static str {
    let s = raw.trim();
    if s.chars().count() < 2 {
        return "";
    }
    if WESTERN_STUDIO_DATE_RE.is_match(s) {
        return "western_studio_date";
    }
    if MULTI_HYPHEN_RE.is_match(s) {
        return "multi_hyphen";
    }
    if NUMERIC_DATE_HYPHEN_RE.is_match(s) {
        return "numeric_date_hyphen";
    }
    if NUMERIC_DATE_UNDERSCORE_RE.is_match(s) {
        return "numeric_date_underscore";
    }
    if CLASSIC_HYPHENATED_RE.is_match(s) {
        return "classic_hyphenated";
    }
    if HYPHENLESS_STUDIO_RE.is_match(s) {
        return "hyphenless_studio";
    }
    ""
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

/// Match ``api.parsers.common.normalize_javdb_href_path`` (site path ``/actors/...``).
pub fn normalize_javdb_href_path(href: &str) -> String {
    let h = href.trim();
    if h.is_empty() {
        return String::new();
    }
    if h.starts_with("http://") || h.starts_with("https://") || h.starts_with("//") {
        let full = if h.starts_with("//") {
            format!("https:{h}")
        } else {
            h.to_string()
        };
        return Url::parse(&full)
            .ok()
            .and_then(|u| {
                let p = u.path();
                if p.is_empty() {
                    None
                } else {
                    Some(p.to_string())
                }
            })
            .unwrap_or_default();
    }
    if h.starts_with('/') {
        h.to_string()
    } else {
        format!("/{h}")
    }
}

/// Heuristic validity check for a candidate video code, mirroring the intent of
/// the Python `_is_plausible_video_code` fallback. Accepts classic hyphenated
/// codes (`ABC-123`), multi-hyphen codes (`FC2-PPV-123`), and numeric date-style
/// uncensored codes whether hyphen- or underscore-separated (`062216-179`,
/// `062216_001`), plus hyphen-less studio codes (`n0656`). Rejects empty strings
/// and title text (any character outside `[A-Za-z0-9_-]`, notably whitespace).
fn is_plausible_video_code(raw: &str) -> bool {
    let s = raw.trim();
    if s.len() < 2 {
        return false;
    }
    if WESTERN_STUDIO_DATE_RE.is_match(s) {
        return true;
    }
    // A code is a compact token: ASCII alphanumerics with '-'/'_' separators
    // only. Anything else (notably whitespace) means we captured title text.
    if !s
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return false;
    }
    if !s.chars().any(|c| c.is_ascii_digit()) {
        return false;
    }
    // Accept hyphen-less studio codes (`n0656`) via the letter, and numeric
    // date-style uncensored codes (`062216-179` / `062216_001`) via the separator.
    let has_letter = s.chars().any(|c| c.is_ascii_alphabetic());
    has_letter || s.contains('-') || s.contains('_')
}

pub fn extract_video_code(a_tag: &ElementRef) -> String {
    let sel = Selector::parse("div.video-title").unwrap();
    if let Some(video_title_div) = a_tag.select(&sel).next() {
        let strong_sel = Selector::parse("strong").unwrap();
        let video_code = if let Some(strong) = video_title_div.select(&strong_sel).next() {
            get_text_content(&strong).trim().to_string()
        } else {
            // No <strong>: the div reads "CODE Title…" — the code is the first
            // whitespace-delimited token; the rest is the title.
            get_text_content(&video_title_div)
                .split_whitespace()
                .next()
                .unwrap_or("")
                .to_string()
        };

        if !is_plausible_video_code(&video_code) {
            debug!("Skipping implausible video code: {}", video_code);
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
        return (
            "actors".to_string(),
            get_text_content(&span).trim().to_string(),
        );
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
    let lower_html = html_content.to_lowercase();
    if lower_html.contains("due to copyright restrictions")
        && lower_html.contains("not available in your country")
    {
        return true;
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
        let no_content_patterns = ["No content yet", "No result", "暫無內容", "暂无内容"];
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
    // Mirror BeautifulSoup's `get_text(strip=True)`: strip each text node
    // before joining with the empty separator. Without this, adjacent
    // text nodes around inline tags (e.g. `<strong>code</strong> title`)
    // leave a stray space between code and title in index entries.
    el.text()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("")
}

pub fn has_class(el: &ElementRef, class_name: &str) -> bool {
    el.value().attr("class").map_or(false, |classes| {
        classes.split_whitespace().any(|c| c == class_name)
    })
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
        assert_eq!(
            detect_page_type("<div class=\"magnets-content\">"),
            "detail"
        );
    }

    #[test]
    fn test_detect_page_type_index() {
        assert_eq!(detect_page_type("<div class=\"movie-list\">"), "index");
    }

    #[test]
    fn test_is_login_page_copyright_restriction() {
        let html = "<html><body>Due to copyright restrictions, this page is not available in your country.</body></html>";
        assert!(is_login_page(html));
    }

    #[test]
    fn test_is_plausible_video_code_accepts_real_codes() {
        // Hyphenated, multi-hyphen, numeric date-style (hyphen and underscore),
        // and hyphen-less studio codes.
        assert!(is_plausible_video_code("ABC-123"));
        assert!(is_plausible_video_code("FC2-PPV-1234567"));
        assert!(is_plausible_video_code("062216-179"));
        assert!(is_plausible_video_code("062216_001")); // Caribbean/1pondo underscore form
        assert!(is_plausible_video_code("n0656")); // regression guard: hyphen-less
    }

    #[test]
    fn test_classify_video_code_family() {
        assert_eq!(
            classify_video_code_family("Wifey.2026.05.30"),
            "western_studio_date"
        );
        assert_eq!(
            classify_video_code_family("RKPrime.26.05.28"),
            "western_studio_date"
        );
        assert_eq!(classify_video_code_family("ABC-123"), "classic_hyphenated");
        assert_eq!(classify_video_code_family("259LUXU-1234"), "");
    }

    #[test]
    fn test_extract_video_code_is_additive() {
        for code in [
            "Wifey.2026.05.30",
            "259LUXU-1234",
            "H4610-ki220101",
            "1pondo-010120_001",
        ] {
            let doc = Html::parse_document(&index_card(
                "/v/x",
                &format!(
                    "<div class=\"video-title\"><strong>{}</strong> Title</div>",
                    code
                ),
            ));
            let a = Selector::parse("a.box").unwrap();
            let el = doc.select(&a).next().unwrap();
            assert_eq!(extract_video_code(&el), code, "regressed code: {}", code);
        }
    }

    #[test]
    fn test_is_plausible_video_code_rejects_non_codes() {
        assert!(!is_plausible_video_code(""));
        assert!(!is_plausible_video_code("X"));
        assert!(!is_plausible_video_code("XYZ-99 Some Title")); // title text leaked in
        assert!(!is_plausible_video_code("XYZ-99標題")); // CJK title glued on (no space)
        assert!(!is_plausible_video_code("123")); // digits only, no letter/separator
    }

    fn index_card(href: &str, inner: &str) -> String {
        format!(
            "<div class=\"movie-list\"><div class=\"item\"><a class=\"box\" href=\"{href}\">{inner}</a></div></div>"
        )
    }

    #[test]
    fn test_extract_video_code_hyphenless_studio_code() {
        // Regression: an uncensored studio code without a hyphen must survive.
        let doc = Html::parse_document(&index_card(
            "/v/a2",
            "<div class=\"video-title\"><strong>n0656</strong> Title</div>",
        ));
        let a = Selector::parse("a.box").unwrap();
        let el = doc.select(&a).next().unwrap();
        assert_eq!(extract_video_code(&el), "n0656");
    }

    #[test]
    fn test_extract_video_code_no_strong_keeps_code_drops_title() {
        // Without <strong>, the div text is "CODE Title"; only the leading code
        // token is kept, the trailing title is dropped.
        let doc = Html::parse_document(&index_card(
            "/v/a5",
            "<div class=\"video-title\">XYZ-99 Some Title</div>",
        ));
        let a = Selector::parse("a.box").unwrap();
        let el = doc.select(&a).next().unwrap();
        assert_eq!(extract_video_code(&el), "XYZ-99");
    }
}
