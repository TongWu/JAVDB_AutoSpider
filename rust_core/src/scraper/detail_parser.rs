use log::debug;
use once_cell::sync::Lazy;
use regex::Regex;
use scraper::{ElementRef, Html, Selector};

use crate::models::{MagnetInfo, MovieDetail, MovieLink};
use crate::scraper::common::{
    extract_all_movie_links, extract_movie_link, extract_rate_and_comments, get_text_content,
};

static SEL_CURRENT_TITLE: Lazy<Selector> =
    Lazy::new(|| Selector::parse("strong.current-title").unwrap());
static SEL_VIDEO_META_PANEL: Lazy<Selector> =
    Lazy::new(|| Selector::parse("div.video-meta-panel").unwrap());
static SEL_PANEL_BLOCK: Lazy<Selector> =
    Lazy::new(|| Selector::parse("div.panel-block").unwrap());
static SEL_STRONG: Lazy<Selector> = Lazy::new(|| Selector::parse("strong").unwrap());
static SEL_VALUE: Lazy<Selector> = Lazy::new(|| Selector::parse("span.value").unwrap());
static SEL_A: Lazy<Selector> = Lazy::new(|| Selector::parse("a").unwrap());
static SEL_MAGNETS_CONTENT: Lazy<Selector> =
    Lazy::new(|| Selector::parse("div#magnets-content").unwrap());
static SEL_MAGNET_NAME: Lazy<Selector> =
    Lazy::new(|| Selector::parse("div.magnet-name").unwrap());
static SEL_NAME_SPAN: Lazy<Selector> = Lazy::new(|| Selector::parse("span.name").unwrap());
static SEL_META_SPAN: Lazy<Selector> = Lazy::new(|| Selector::parse("span.meta").unwrap());
static SEL_TIME_SPAN: Lazy<Selector> = Lazy::new(|| Selector::parse("span.time").unwrap());
static SEL_TAGS_DIV: Lazy<Selector> = Lazy::new(|| Selector::parse("div.tags").unwrap());
static SEL_TAG_SPAN: Lazy<Selector> = Lazy::new(|| Selector::parse("span.tag").unwrap());
static SEL_COVER_COL: Lazy<Selector> =
    Lazy::new(|| Selector::parse("div.column-video-cover").unwrap());
static SEL_COVER_IMG: Lazy<Selector> =
    Lazy::new(|| Selector::parse("img.video-cover").unwrap());
static SEL_TILE_IMAGES: Lazy<Selector> =
    Lazy::new(|| Selector::parse("div.tile-images.preview-images").unwrap());
static SEL_TILE_ITEM: Lazy<Selector> = Lazy::new(|| Selector::parse("a.tile-item").unwrap());
static SEL_PREVIEW_CONTAINER: Lazy<Selector> =
    Lazy::new(|| Selector::parse("a.preview-video-container").unwrap());
static SEL_PREVIEW_VIDEO: Lazy<Selector> =
    Lazy::new(|| Selector::parse("video#preview-video").unwrap());
static SEL_SOURCE: Lazy<Selector> = Lazy::new(|| Selector::parse("source").unwrap());
static SEL_REVIEW_TAB: Lazy<Selector> =
    Lazy::new(|| Selector::parse("a.review-tab").unwrap());
static SEL_SIZE7: Lazy<Selector> = Lazy::new(|| Selector::parse("span.is-size-7").unwrap());

static MAGNET_ITEM_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"item columns is-desktop").unwrap());
static SIZE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"([\d.]+(?:GB|MB|KB|TB))").unwrap());
static REVIEW_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"短評\((\d+)\)").unwrap());
static WANT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(\d+)人想看").unwrap());
static WATCHED_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(\d+)人看過").unwrap());

fn find_panel_block<'a>(
    panel_blocks: &[ElementRef<'a>],
    label: &str,
) -> Option<ElementRef<'a>> {
    panel_blocks.iter().find(|block| {
        block
            .select(&SEL_STRONG)
            .next()
            .map_or(false, |strong| get_text_content(&strong).contains(label))
    }).copied()
}

fn extract_link_from_panel(panel_blocks: &[ElementRef], label: &str) -> Option<MovieLink> {
    let block = find_panel_block(panel_blocks, label)?;
    let value_span = block.select(&SEL_VALUE).next()?;
    let a_tag = value_span.select(&SEL_A).next()?;
    extract_movie_link(&a_tag)
}

fn extract_links_from_panel(panel_blocks: &[ElementRef], label: &str) -> Vec<MovieLink> {
    let block = match find_panel_block(panel_blocks, label) {
        Some(b) => b,
        None => return Vec::new(),
    };
    let value_span = match block.select(&SEL_VALUE).next() {
        Some(v) => v,
        None => return Vec::new(),
    };
    extract_all_movie_links(&value_span)
}

fn extract_text_from_panel(panel_blocks: &[ElementRef], label: &str) -> String {
    let block = match find_panel_block(panel_blocks, label) {
        Some(b) => b,
        None => return String::new(),
    };
    block
        .select(&SEL_VALUE)
        .next()
        .map_or(String::new(), |v| get_text_content(&v).trim().to_string())
}

fn parse_magnets(document: &Html) -> (Vec<MagnetInfo>, bool) {
    let magnets_content = match document.select(&SEL_MAGNETS_CONTENT).next() {
        Some(mc) => mc,
        None => return (Vec::new(), false),
    };

    let mut magnets = Vec::new();

    // Find all magnet items by class pattern
    for item in magnets_content
        .descendants()
        .filter_map(|n| ElementRef::wrap(n))
        .filter(|el| {
            el.value().name() == "div"
                && el
                    .value()
                    .attr("class")
                    .map_or(false, |c| MAGNET_ITEM_RE.is_match(c))
        })
    {
        let magnet_name_div = match item.select(&SEL_MAGNET_NAME).next() {
            Some(d) => d,
            None => continue,
        };

        let magnet_a = match magnet_name_div.select(&SEL_A).next() {
            Some(a) => a,
            None => continue,
        };

        let magnet_href = magnet_a.value().attr("href").unwrap_or("").to_string();
        let name = magnet_a
            .select(&SEL_NAME_SPAN)
            .next()
            .map_or(String::new(), |s| get_text_content(&s).trim().to_string());

        // Size
        let size = magnet_a
            .select(&SEL_META_SPAN)
            .next()
            .and_then(|meta| {
                let meta_text = get_text_content(&meta).trim().to_string();
                SIZE_RE
                    .captures(&meta_text)
                    .and_then(|c| c.get(1))
                    .map(|m| m.as_str().to_string())
            })
            .unwrap_or_default();

        // Timestamp
        let timestamp = item
            .select(&SEL_TIME_SPAN)
            .next()
            .map_or(String::new(), |t| get_text_content(&t).trim().to_string());

        // Tags
        let mut tags = Vec::new();
        if let Some(tags_div) = magnet_a.select(&SEL_TAGS_DIV).next() {
            for span in tags_div.select(&SEL_TAG_SPAN) {
                let tag_text = get_text_content(&span).trim().to_string();
                if !tag_text.is_empty() {
                    tags.push(tag_text);
                }
            }
        }

        magnets.push(MagnetInfo {
            href: magnet_href,
            name,
            tags,
            size,
            timestamp,
        });
    }

    (magnets, true)
}

pub fn parse_detail_page(html_content: &str) -> MovieDetail {
    let document = Html::parse_document(html_content);
    let mut detail = MovieDetail::default();

    // Title
    if let Some(title_strong) = document.select(&SEL_CURRENT_TITLE).next() {
        detail.title = get_text_content(&title_strong).trim().to_string();
    }

    // Metadata panel
    let video_meta_panel = document.select(&SEL_VIDEO_META_PANEL).next();
    let panel_blocks: Vec<ElementRef> = video_meta_panel
        .map(|p| p.select(&SEL_PANEL_BLOCK).collect())
        .unwrap_or_default();

    // Video code + prefix link
    if let Some(first_block) = find_panel_block(&panel_blocks, "番號:") {
        if let Some(value_span) = first_block.select(&SEL_VALUE).next() {
            detail.video_code = get_text_content(&value_span).trim().to_string();
            if let Some(prefix_a) = value_span.select(&SEL_A).next() {
                detail.code_prefix_link =
                    prefix_a.value().attr("href").unwrap_or("").to_string();
            }
        }
    }

    // Release date, Duration
    detail.release_date = extract_text_from_panel(&panel_blocks, "日期:");
    detail.duration = extract_text_from_panel(&panel_blocks, "時長:");

    // Directors, Maker, Publisher, Series
    detail.directors = extract_links_from_panel(&panel_blocks, "導演:");
    detail.maker = extract_link_from_panel(&panel_blocks, "片商:");
    detail.publisher = extract_link_from_panel(&panel_blocks, "發行商:");
    detail.series = extract_link_from_panel(&panel_blocks, "系列:");

    // Rating & comment count
    if let Some(rating_block) = find_panel_block(&panel_blocks, "評分:") {
        if let Some(value_span) = rating_block.select(&SEL_VALUE).next() {
            let score_text = get_text_content(&value_span).trim().to_string();
            let (r, c) = extract_rate_and_comments(&score_text);
            detail.rate = r;
            detail.comment_count = c;
        }
    }

    // Tags, Actors
    detail.tags = extract_links_from_panel(&panel_blocks, "類別:");
    detail.actors = extract_links_from_panel(&panel_blocks, "演員:");

    // Poster URL
    if let Some(vmp) = video_meta_panel {
        if let Some(cover_col) = vmp.select(&SEL_COVER_COL).next() {
            if let Some(cover_img) = cover_col.select(&SEL_COVER_IMG).next() {
                detail.poster_url = cover_img
                    .value()
                    .attr("src")
                    .unwrap_or("")
                    .to_string();
            }
        }
    }

    // Fanart URLs
    if let Some(tile_images) = document.select(&SEL_TILE_IMAGES).next() {
        for tile in tile_images.select(&SEL_TILE_ITEM) {
            let href = tile.value().attr("href").unwrap_or("").to_string();
            if !href.is_empty() {
                detail.fanart_urls.push(href);
            }
        }
    }

    // Trailer URL
    if let Some(preview_container) = document.select(&SEL_PREVIEW_CONTAINER).next() {
        if let Some(video_el) = document.select(&SEL_PREVIEW_VIDEO).next() {
            let src = video_el.value().attr("src").unwrap_or("").to_string();
            if !src.is_empty() && !src.starts_with("blob:") {
                detail.trailer_url = Some(src);
            } else if let Some(source) = video_el.select(&SEL_SOURCE).next() {
                let source_src = source.value().attr("src").unwrap_or("").to_string();
                if !source_src.is_empty() {
                    detail.trailer_url = Some(source_src);
                }
            }
        }
        if detail.trailer_url.is_none() {
            let container_href = preview_container.value().attr("href").unwrap_or("");
            if container_href.contains("#preview-video") {
                detail.trailer_url = Some(container_href.to_string());
            }
        }
    }

    // Review count
    if let Some(review_tab) = document.select(&SEL_REVIEW_TAB).next() {
        let tab_text = get_text_content(&review_tab).trim().to_string();
        if let Some(caps) = REVIEW_RE.captures(&tab_text) {
            if let Ok(count) = caps[1].parse::<i32>() {
                detail.review_count = count;
            }
        }
    }

    // Want/Watched counts
    for block in &panel_blocks {
        if let Some(span) = block.select(&SEL_SIZE7).next() {
            let text = get_text_content(&span).trim().to_string();
            if let Some(caps) = WANT_RE.captures(&text) {
                if let Ok(count) = caps[1].parse::<i32>() {
                    detail.want_count = count;
                }
            }
            if let Some(caps) = WATCHED_RE.captures(&text) {
                if let Ok(count) = caps[1].parse::<i32>() {
                    detail.watched_count = count;
                }
            }
        }
    }

    // Magnets
    let (magnets, parse_success) = parse_magnets(&document);
    detail.magnets = magnets;
    detail.parse_success = parse_success;

    debug!(
        "Parsed detail: code={}, title={}, actors={}, magnets={}",
        detail.video_code,
        if detail.title.len() > 40 {
            &detail.title[..40]
        } else {
            &detail.title
        },
        detail.actors.len(),
        detail.magnets.len(),
    );

    detail
}
