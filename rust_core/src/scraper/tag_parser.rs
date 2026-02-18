use log::{debug, warn};
use once_cell::sync::Lazy;
use regex::Regex;
use scraper::{ElementRef, Html, Selector};
use std::collections::{HashMap, HashSet};

use crate::models::{TagCategory, TagOption, TagPageResult};
use crate::scraper::common::{get_text_content, has_class};
use crate::scraper::index_parser::parse_index_page;

static SEL_TAGS_DIV: Lazy<Selector> = Lazy::new(|| Selector::parse("div#tags").unwrap());
static SEL_STRONG: Lazy<Selector> = Lazy::new(|| Selector::parse("strong").unwrap());
static SEL_TAG_LABELS: Lazy<Selector> =
    Lazy::new(|| Selector::parse("span.tag_labels").unwrap());
static SEL_BUTTON: Lazy<Selector> = Lazy::new(|| Selector::parse("button").unwrap());

static SAVED_FROM_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"saved from url=\(\d+\)(https?://[^\s]+)").unwrap());
static CANONICAL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"<link[^>]+rel=["']canonical["'][^>]+href=["']([^"']+)"#).unwrap()
});
static CATEGORY_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"tag-category-(\d+)").unwrap());
static CPARAM_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^c(\d+)$").unwrap());

fn extract_page_url(html_content: &str) -> String {
    let prefix = if html_content.len() > 3000 {
        &html_content[..3000]
    } else {
        html_content
    };

    if let Some(caps) = SAVED_FROM_RE.captures(prefix) {
        return caps[1].trim().to_string();
    }

    let check = if html_content.len() > 5000 {
        &html_content[..5000]
    } else {
        html_content
    };
    if let Some(caps) = CANONICAL_RE.captures(check) {
        return caps[1].to_string();
    }

    String::new()
}

fn parse_url_params(url_str: &str) -> HashMap<String, Vec<String>> {
    if let Ok(parsed) = url::Url::parse(url_str) {
        let mut result: HashMap<String, Vec<String>> = HashMap::new();
        for (key, value) in parsed.query_pairs() {
            result
                .entry(key.to_string())
                .or_default()
                .push(value.to_string());
        }
        result
    } else {
        // Try as a relative URL
        let fake_base = format!("http://example.com{}", url_str);
        if let Ok(parsed) = url::Url::parse(&fake_base) {
            let mut result: HashMap<String, Vec<String>> = HashMap::new();
            for (key, value) in parsed.query_pairs() {
                result
                    .entry(key.to_string())
                    .or_default()
                    .push(value.to_string());
            }
            result
        } else {
            HashMap::new()
        }
    }
}

fn extract_tag_id_from_href(href: &str, category_id: &str) -> String {
    if href.is_empty() || href.contains("javascript") {
        return String::new();
    }

    let params = parse_url_params(href);
    let key = format!("c{}", category_id);
    params
        .get(&key)
        .and_then(|vals| vals.first())
        .cloned()
        .unwrap_or_default()
}

fn extract_new_tag_id_from_href(
    href: &str,
    category_id: &str,
    current_selection: &str,
) -> String {
    let raw = extract_tag_id_from_href(href, category_id);
    if raw.is_empty() {
        return String::new();
    }

    let raw_ids: HashSet<&str> = raw.split(',').collect();
    let current_ids: HashSet<&str> = if current_selection.is_empty() {
        HashSet::new()
    } else {
        current_selection.split(',').collect()
    };

    let new_ids: Vec<&&str> = raw_ids.difference(&current_ids).collect();
    if new_ids.len() == 1 {
        return new_ids[0].to_string();
    }

    if current_ids.is_empty() {
        raw
    } else {
        String::new()
    }
}

pub fn parse_tag_page(html_content: &str, page_num: i32) -> TagPageResult {
    let document = Html::parse_document(html_content);
    let index_result = parse_index_page(html_content, page_num);

    let page_url = extract_page_url(html_content);
    let url_params = parse_url_params(&page_url);

    // Build current_selections
    let mut current_selections: HashMap<String, String> = HashMap::new();
    for (key, values) in &url_params {
        if let Some(caps) = CPARAM_RE.captures(key) {
            let cid = caps[1].to_string();
            if let Some(val) = values.first() {
                current_selections.insert(cid, val.clone());
            }
        }
    }

    // Parse tag filter panel
    let tags_div = match document.select(&SEL_TAGS_DIV).next() {
        Some(td) => td,
        None => {
            warn!("No tag filter panel found (<div id=\"tags\">)");
            return TagPageResult {
                has_movie_list: index_result.has_movie_list,
                movies: index_result.movies,
                page_title: index_result.page_title,
                categories: Vec::new(),
                current_selections,
            };
        }
    };

    let mut categories: Vec<TagCategory> = Vec::new();

    // Find all dt elements with tag-category class
    for dt in tags_div
        .descendants()
        .filter_map(|n| ElementRef::wrap(n))
        .filter(|el| {
            el.value().name() == "dt"
                && el
                    .value()
                    .attr("class")
                    .map_or(false, |c| c.contains("tag-category"))
        })
    {
        let mut cid = dt.value().attr("data-cid").unwrap_or("").to_string();
        if cid.is_empty() {
            if let Some(dt_id) = dt.value().attr("id") {
                if let Some(caps) = CATEGORY_RE.captures(dt_id) {
                    cid = caps[1].to_string();
                }
            }
        }
        if cid.is_empty() {
            continue;
        }

        let cat_name = dt
            .select(&SEL_STRONG)
            .next()
            .map_or(String::new(), |s| get_text_content(&s).trim().to_string());

        let cat_current = current_selections.get(&cid).cloned().unwrap_or_default();

        let mut options: Vec<TagOption> = Vec::new();

        let labels_span = match dt.select(&SEL_TAG_LABELS).next() {
            Some(ls) => ls,
            None => {
                categories.push(TagCategory {
                    category_id: cid,
                    name: cat_name,
                    options,
                });
                continue;
            }
        };

        for child in labels_span.children().filter_map(ElementRef::wrap) {
            // Selected tag: <div class="tag is-info">
            if child.value().name() == "div" && has_class(&child, "is-info") {
                // Clone and remove button text
                let full_text = get_text_content(&child).trim().to_string();
                let button_text = child
                    .select(&SEL_BUTTON)
                    .next()
                    .map_or(String::new(), |b| get_text_content(&b));
                let tag_name = full_text
                    .replace(&button_text, "")
                    .trim()
                    .to_string();
                if tag_name.is_empty() {
                    continue;
                }

                let tag_id = if !cat_current.is_empty() {
                    "__selected__".to_string()
                } else {
                    String::new()
                };

                options.push(TagOption {
                    name: tag_name,
                    tag_id,
                    selected: true,
                });
            }
            // Non-selected tag: <a class="tag ...">
            else if child.value().name() == "a" && has_class(&child, "tag") {
                let tag_name = get_text_content(&child).trim().to_string();
                if tag_name.is_empty() {
                    continue;
                }

                let href = child.value().attr("href").unwrap_or("");
                let tag_id = if !href.is_empty() && !href.contains("javascript") {
                    if !cat_current.is_empty() {
                        extract_new_tag_id_from_href(href, &cid, &cat_current)
                    } else {
                        extract_tag_id_from_href(href, &cid)
                    }
                } else {
                    String::new()
                };

                options.push(TagOption {
                    name: tag_name,
                    tag_id,
                    selected: false,
                });
            }
        }

        // Second pass: resolve IDs for selected tags
        let selected_indices: Vec<usize> = options
            .iter()
            .enumerate()
            .filter(|(_, o)| o.selected)
            .map(|(i, _)| i)
            .collect();

        if !selected_indices.is_empty() && !cat_current.is_empty() {
            let current_ids: Vec<&str> = cat_current.split(',').collect();
            let non_selected_ids: HashSet<String> = options
                .iter()
                .filter(|o| !o.selected && !o.tag_id.is_empty())
                .map(|o| o.tag_id.clone())
                .collect();

            let remaining_ids: Vec<&&str> = current_ids
                .iter()
                .filter(|tid| !non_selected_ids.contains(**tid))
                .collect();

            if remaining_ids.len() == selected_indices.len() {
                for (idx, tid) in selected_indices.iter().zip(remaining_ids.iter()) {
                    options[*idx].tag_id = tid.to_string();
                }
            } else if remaining_ids.len() >= 1 && selected_indices.len() == 1 {
                options[selected_indices[0]].tag_id = remaining_ids[0].to_string();
            } else {
                for (i, idx) in selected_indices.iter().enumerate() {
                    if i < remaining_ids.len() {
                        options[*idx].tag_id = remaining_ids[i].to_string();
                    } else {
                        options[*idx].tag_id = String::new();
                    }
                }
            }
        }

        categories.push(TagCategory {
            category_id: cid,
            name: cat_name,
            options,
        });
    }

    debug!(
        "Parsed tag page: {} categories, {} total options, {} movies",
        categories.len(),
        categories.iter().map(|c| c.options.len()).sum::<usize>(),
        index_result.movies.len(),
    );

    TagPageResult {
        has_movie_list: index_result.has_movie_list,
        movies: index_result.movies,
        page_title: index_result.page_title,
        categories,
        current_selections,
    }
}
