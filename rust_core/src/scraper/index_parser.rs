use log::{debug, warn};
use once_cell::sync::Lazy;
use regex::Regex;
use scraper::{ElementRef, Html, Selector};

use crate::models::{
    CategoryPageResult, IndexPageResult, MovieIndexEntry, TopPageResult,
};
use crate::scraper::common::{
    class_contains, detect_page_type, extract_category_name, extract_rate_and_comments,
    extract_video_code, get_text_content,
};

static SEL_TITLE: Lazy<Selector> = Lazy::new(|| Selector::parse("title").unwrap());
static SEL_ITEM: Lazy<Selector> = Lazy::new(|| Selector::parse("div.item").unwrap());
static SEL_A_BOX: Lazy<Selector> = Lazy::new(|| Selector::parse("a.box").unwrap());
static SEL_A: Lazy<Selector> = Lazy::new(|| Selector::parse("a").unwrap());
static SEL_VIDEO_TITLE: Lazy<Selector> =
    Lazy::new(|| Selector::parse("div.video-title").unwrap());
static SEL_SCORE: Lazy<Selector> = Lazy::new(|| Selector::parse("div.score").unwrap());
static SEL_VALUE_SPAN: Lazy<Selector> = Lazy::new(|| Selector::parse("span.value").unwrap());
static SEL_VALUE_DIV: Lazy<Selector> = Lazy::new(|| Selector::parse("div.value").unwrap());
static SEL_META: Lazy<Selector> = Lazy::new(|| Selector::parse("div.meta").unwrap());
static SEL_TAGS_ADDONS: Lazy<Selector> =
    Lazy::new(|| Selector::parse("div.tags.has-addons").unwrap());
static SEL_TAG_SPAN: Lazy<Selector> = Lazy::new(|| Selector::parse("span.tag").unwrap());
static SEL_IMG: Lazy<Selector> = Lazy::new(|| Selector::parse("img").unwrap());
static SEL_RANKING_SPAN: Lazy<Selector> = Lazy::new(|| Selector::parse("span.ranking").unwrap());

static YEAR_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[?&]t=y(\d{4})").unwrap());
static PERIOD_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"[?&]p=(daily|weekly|monthly)").unwrap());

fn parse_movie_item(item: &ElementRef, page_num: i32) -> Option<MovieIndexEntry> {
    let a = item
        .select(&SEL_A_BOX)
        .next()
        .or_else(|| item.select(&SEL_A).next())?;

    let href = a.value().attr("href").unwrap_or("").to_string();
    if href.is_empty() {
        return None;
    }

    let video_code = extract_video_code(&a);

    // Title
    let mut title = String::new();
    if let Some(vtd) = a.select(&SEL_VIDEO_TITLE).next() {
        let full_text = get_text_content(&vtd).trim().to_string();
        if !video_code.is_empty() && full_text.starts_with(&video_code) {
            title = full_text[video_code.len()..].trim().to_string();
        } else {
            title = full_text;
        }
    }
    if title.is_empty() {
        title = a.value().attr("title").unwrap_or("").to_string();
    }

    // Rating & comment count
    let mut rate = String::new();
    let mut comment_count = String::new();
    if let Some(score_div) = a.select(&SEL_SCORE).next() {
        let value_el = score_div
            .select(&SEL_VALUE_SPAN)
            .next()
            .or_else(|| score_div.select(&SEL_VALUE_DIV).next());
        if let Some(val) = value_el {
            let score_text = get_text_content(&val).trim().to_string();
            let (r, c) = extract_rate_and_comments(&score_text);
            rate = r;
            comment_count = c;
        }
    }

    // Release date
    let release_date = a
        .select(&SEL_META)
        .next()
        .map_or(String::new(), |m| get_text_content(&m).trim().to_string());

    // Tags
    let mut tags = Vec::new();
    if let Some(tags_div) = a.select(&SEL_TAGS_ADDONS).next() {
        for span in tags_div.select(&SEL_TAG_SPAN) {
            let tag_text = get_text_content(&span).trim().to_string();
            if !tag_text.is_empty() {
                tags.push(tag_text);
            }
        }
    }

    // Cover image URL
    let mut cover_url = String::new();
    let cover_sel = Selector::parse("div[class*='cover']").ok();
    let cover_div = cover_sel.as_ref().and_then(|sel| a.select(sel).next());
    if let Some(cd) = cover_div {
        if let Some(img) = cd.select(&SEL_IMG).next() {
            cover_url = img
                .value()
                .attr("src")
                .or_else(|| img.value().attr("data-src"))
                .unwrap_or("")
                .to_string();
        }
    }

    // Ranking
    let mut ranking = None;
    if let Some(ref cd) = cover_div {
        if let Some(rank_span) = cd.select(&SEL_RANKING_SPAN).next() {
            if let Ok(r) = get_text_content(&rank_span).trim().parse::<i32>() {
                ranking = Some(r);
            }
        }
    }

    Some(MovieIndexEntry {
        href,
        video_code,
        title,
        rate,
        comment_count,
        release_date,
        tags,
        cover_url,
        page: page_num,
        ranking,
    })
}

pub fn parse_index_page(html_content: &str, page_num: i32) -> IndexPageResult {
    let document = Html::parse_document(html_content);

    let page_title = document
        .select(&SEL_TITLE)
        .next()
        .map_or(String::new(), |t| get_text_content(&t).trim().to_string());

    // Find all movie-list containers
    let all_elements: Vec<ElementRef> = document
        .root_element()
        .descendants()
        .filter_map(|node| ElementRef::wrap(node))
        .filter(|el| el.value().name() == "div" && class_contains(el, "movie-list"))
        .collect();

    if all_elements.is_empty() {
        warn!("[Page {}] No movie list found", page_num);
        return IndexPageResult {
            has_movie_list: false,
            movies: Vec::new(),
            page_title,
        };
    }

    let mut movies = Vec::new();
    for movie_list in &all_elements {
        for item in movie_list.select(&SEL_ITEM) {
            if let Some(entry) = parse_movie_item(&item, page_num) {
                movies.push(entry);
            }
        }
    }

    debug!("[Page {}] Parsed {} movie entries", page_num, movies.len());
    IndexPageResult {
        has_movie_list: true,
        movies,
        page_title,
    }
}

pub fn parse_category_page(html_content: &str, page_num: i32) -> CategoryPageResult {
    let document = Html::parse_document(html_content);
    let base = parse_index_page(html_content, page_num);

    let (mut cat_type, cat_name) = extract_category_name(&document);

    if cat_type.is_empty() {
        let page_type = detect_page_type(html_content);
        if page_type != "index" && page_type != "detail" && page_type != "unknown" {
            cat_type = page_type;
        }
    }

    CategoryPageResult {
        has_movie_list: base.has_movie_list,
        movies: base.movies,
        page_title: base.page_title,
        category_type: cat_type,
        category_name: cat_name,
    }
}

pub fn parse_top_page(html_content: &str, page_num: i32) -> TopPageResult {
    let base = parse_index_page(html_content, page_num);

    let mut top_type = String::new();
    let mut period = None;
    let page_type = detect_page_type(html_content);

    let prefix = if html_content.len() > 5000 {
        &html_content[..5000]
    } else {
        html_content
    };

    match page_type.as_str() {
        "top250" => {
            top_type = "top250".to_string();
            if let Some(caps) = YEAR_RE.captures(prefix) {
                period = Some(caps[1].to_string());
            }
        }
        "top_movies" | "top_playback" => {
            top_type = page_type;
            if let Some(caps) = PERIOD_RE.captures(prefix) {
                period = Some(caps[1].to_string());
            }
        }
        _ => {}
    }

    TopPageResult {
        has_movie_list: base.has_movie_list,
        movies: base.movies,
        page_title: base.page_title,
        top_type,
        period,
    }
}
