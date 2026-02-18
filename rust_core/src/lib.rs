use pyo3::prelude::*;

pub mod models;
pub mod proxy;
pub mod requester;
pub mod scraper;

use models::{
    CategoryPageResult, IndexPageResult, MagnetInfo, MovieDetail, MovieIndexEntry, MovieLink,
    TagCategory, TagOption, TagPageResult, TopPageResult,
};
use proxy::ban_manager::{get_global_ban_manager, ProxyBanManager};
use proxy::masking::{
    mask_email, mask_full, mask_ip_address, mask_partial, mask_proxy_url, mask_server,
    mask_username,
};
use proxy::pool::{create_proxy_pool_from_config, ProxyInfo, ProxyPool};
use requester::config::RequestConfig;
use requester::handler::{create_request_handler_from_config, RequestHandler};
use requester::helper::{create_proxy_helper_from_config, ProxyHelper};

// Python-facing wrapper functions for parsers
#[pyfunction]
#[pyo3(signature = (html_content, page_num=1))]
fn parse_index_page(html_content: &str, page_num: i32) -> IndexPageResult {
    scraper::index_parser::parse_index_page(html_content, page_num)
}

#[pyfunction]
fn parse_detail_page(html_content: &str) -> MovieDetail {
    scraper::detail_parser::parse_detail_page(html_content)
}

#[pyfunction]
#[pyo3(signature = (html_content, page_num=1))]
fn parse_category_page(html_content: &str, page_num: i32) -> CategoryPageResult {
    scraper::index_parser::parse_category_page(html_content, page_num)
}

#[pyfunction]
#[pyo3(signature = (html_content, page_num=1))]
fn parse_top_page(html_content: &str, page_num: i32) -> TopPageResult {
    scraper::index_parser::parse_top_page(html_content, page_num)
}

#[pyfunction]
#[pyo3(signature = (html_content, page_num=1))]
fn parse_tag_page(html_content: &str, page_num: i32) -> TagPageResult {
    scraper::tag_parser::parse_tag_page(html_content, page_num)
}

#[pyfunction]
fn detect_page_type(html_content: &str) -> String {
    scraper::common::detect_page_type(html_content)
}

#[pymodule]
fn javdb_rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Initialize logging bridge
    pyo3_log::init();

    // --- Models ---
    m.add_class::<MovieLink>()?;
    m.add_class::<MagnetInfo>()?;
    m.add_class::<MovieIndexEntry>()?;
    m.add_class::<MovieDetail>()?;
    m.add_class::<IndexPageResult>()?;
    m.add_class::<CategoryPageResult>()?;
    m.add_class::<TopPageResult>()?;
    m.add_class::<TagOption>()?;
    m.add_class::<TagCategory>()?;
    m.add_class::<TagPageResult>()?;

    // --- Proxy ---
    m.add_class::<ProxyInfo>()?;
    m.add_class::<ProxyPool>()?;
    m.add_class::<ProxyBanManager>()?;
    m.add_function(wrap_pyfunction!(create_proxy_pool_from_config, m)?)?;
    m.add_function(wrap_pyfunction!(get_global_ban_manager, m)?)?;

    // --- Masking ---
    m.add_function(wrap_pyfunction!(mask_full, m)?)?;
    m.add_function(wrap_pyfunction!(mask_partial, m)?)?;
    m.add_function(wrap_pyfunction!(mask_email, m)?)?;
    m.add_function(wrap_pyfunction!(mask_ip_address, m)?)?;
    m.add_function(wrap_pyfunction!(mask_proxy_url, m)?)?;
    m.add_function(wrap_pyfunction!(mask_username, m)?)?;
    m.add_function(wrap_pyfunction!(mask_server, m)?)?;

    // --- Request Handler ---
    m.add_class::<RequestConfig>()?;
    m.add_class::<RequestHandler>()?;
    m.add_class::<ProxyHelper>()?;
    m.add_function(wrap_pyfunction!(create_request_handler_from_config, m)?)?;
    m.add_function(wrap_pyfunction!(create_proxy_helper_from_config, m)?)?;

    // --- Parsers ---
    m.add_function(wrap_pyfunction!(parse_index_page, m)?)?;
    m.add_function(wrap_pyfunction!(parse_detail_page, m)?)?;
    m.add_function(wrap_pyfunction!(parse_category_page, m)?)?;
    m.add_function(wrap_pyfunction!(parse_top_page, m)?)?;
    m.add_function(wrap_pyfunction!(parse_tag_page, m)?)?;
    m.add_function(wrap_pyfunction!(detect_page_type, m)?)?;

    Ok(())
}
