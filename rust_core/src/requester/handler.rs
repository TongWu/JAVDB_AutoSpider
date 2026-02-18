use log::{debug, error, info, warn};
use pyo3::prelude::*;
use reqwest::blocking::Client;
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use reqwest::Proxy;
use std::collections::HashMap;
use std::str::FromStr;
use std::thread;
use std::time::Duration;
use url::Url;

use super::config::RequestConfig;
use crate::proxy::masking::mask_ip_address;
use crate::proxy::pool::ProxyPool;

fn build_browser_headers() -> HashMap<String, String> {
    let mut headers = HashMap::new();
    headers.insert("User-Agent".into(), "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36".into());
    headers.insert("Accept".into(), "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7".into());
    headers.insert("Accept-Language".into(), "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7".into());
    headers.insert("Accept-Encoding".into(), "gzip, deflate".into());
    headers.insert("Connection".into(), "keep-alive".into());
    headers.insert(
        "Upgrade-Insecure-Requests".into(),
        "1".into(),
    );
    headers.insert("Sec-Fetch-Dest".into(), "document".into());
    headers.insert("Sec-Fetch-Mode".into(), "navigate".into());
    headers.insert("Sec-Fetch-Site".into(), "none".into());
    headers.insert("Sec-Fetch-User".into(), "?1".into());
    headers.insert("Sec-Ch-Ua".into(), "\"Google Chrome\";v=\"131\", \"Chromium\";v=\"131\", \"Not_A Brand\";v=\"24\"".into());
    headers.insert("Sec-Ch-Ua-Mobile".into(), "?0".into());
    headers.insert(
        "Sec-Ch-Ua-Platform".into(),
        "\"macOS\"".into(),
    );
    headers.insert("Cache-Control".into(), "max-age=0".into());
    headers
}

fn hashmap_to_headermap(headers: &HashMap<String, String>) -> HeaderMap {
    let mut hm = HeaderMap::new();
    for (k, v) in headers {
        if let (Ok(name), Ok(value)) = (HeaderName::from_str(k), HeaderValue::from_str(v)) {
            hm.insert(name, value);
        }
    }
    hm
}

fn build_client(proxies: Option<&HashMap<String, String>>) -> Result<Client, reqwest::Error> {
    let mut builder = Client::builder()
        .timeout(Duration::from_secs(30))
        .cookie_store(true)
        .gzip(true)
        .deflate(true);

    if let Some(proxy_map) = proxies {
        if let Some(https_url) = proxy_map.get("https") {
            builder = builder.proxy(Proxy::https(https_url)?);
        }
        if let Some(http_url) = proxy_map.get("http") {
            builder = builder.proxy(Proxy::http(http_url)?);
        }
    }

    builder.build()
}

fn extract_ip_from_proxy_url(proxy_url: &str) -> Option<String> {
    Url::parse(proxy_url)
        .ok()
        .and_then(|u| u.host_str().map(|h| h.to_string()))
}

fn is_cf_bypass_failure(html_content: &str) -> bool {
    let content_size = html_content.len();
    let contains_fail = html_content.to_lowercase().contains("fail");
    let is_failure = content_size < 1000 && contains_fail;
    if is_failure {
        debug!(
            "[CF Bypass] Failure detected: size={} bytes, contains_fail={}",
            content_size, contains_fail
        );
    }
    is_failure
}

fn is_turnstile_page(html_content: &str) -> bool {
    html_content.contains("Security Verification")
        && html_content.to_lowercase().contains("turnstile")
}

fn has_valid_content(html_content: &str) -> bool {
    html_content.contains("movie-list") || html_content.contains("video-detail")
}

fn has_empty_message(html_content: &str) -> bool {
    html_content.contains("empty-message")
}

fn has_age_modal(html_content: &str) -> bool {
    html_content.contains("modal is-active over18-modal")
}

#[pyclass(name = "RustRequestHandler")]
pub struct RequestHandler {
    config: RequestConfig,
    proxy_pool: Option<Py<ProxyPool>>,
    cf_bypass_failure_count: u32,
}

#[pymethods]
impl RequestHandler {
    #[new]
    #[pyo3(signature = (proxy_pool=None, config=None))]
    pub fn new(proxy_pool: Option<Py<ProxyPool>>, config: Option<RequestConfig>) -> Self {
        let cfg = config.unwrap_or_default();
        info!(
            "RustRequestHandler initialized (base_url: {})",
            cfg.base_url
        );
        Self {
            config: cfg,
            proxy_pool,
            cf_bypass_failure_count: 0,
        }
    }

    pub fn should_use_proxy_for_module(&self, module_name: &str, use_proxy_flag: bool) -> bool {
        if !use_proxy_flag {
            return false;
        }
        if self.config.proxy_modules.is_empty() {
            return false;
        }
        if self.config.proxy_modules.contains(&"all".to_string()) {
            return true;
        }
        self.config.proxy_modules.contains(&module_name.to_string())
    }

    #[pyo3(signature = (url, use_cookie=false, use_proxy=false, module_name="unknown", max_retries=3, use_cf_bypass=false))]
    pub fn get_page(
        &mut self,
        py: Python<'_>,
        url: &str,
        use_cookie: bool,
        use_proxy: bool,
        module_name: &str,
        max_retries: u32,
        use_cf_bypass: bool,
    ) -> PyResult<Option<String>> {
        py.allow_threads(|| {
            self.get_page_impl(url, use_cookie, use_proxy, module_name, max_retries, use_cf_bypass)
        })
    }

    pub fn reset_cf_bypass_state(&mut self) {
        self.cf_bypass_failure_count = 0;
    }

    #[getter]
    fn cf_bypass_failure_count(&self) -> u32 {
        self.cf_bypass_failure_count
    }
}

impl RequestHandler {
    fn get_proxies_config(
        &self,
        module_name: &str,
        use_proxy: bool,
    ) -> (Option<HashMap<String, String>>, bool) {
        if !self.should_use_proxy_for_module(module_name, use_proxy) {
            return (None, false);
        }

        if (self.config.proxy_mode == "pool" || self.config.proxy_mode == "single")
            && self.proxy_pool.is_some()
        {
            let proxies = Python::with_gil(|py| {
                self.proxy_pool
                    .as_ref()
                    .unwrap()
                    .borrow(py)
                    .get_next_proxy()
            });
            if let Some(p) = proxies {
                return (Some(p), true);
            }
            warn!(
                "[{}] Proxy mode '{}' enabled but no proxy available",
                module_name, self.config.proxy_mode
            );
            return (None, false);
        }

        if self.config.proxy_http.is_some() || self.config.proxy_https.is_some() {
            let mut proxies = HashMap::new();
            if let Some(ref http) = self.config.proxy_http {
                proxies.insert("http".to_string(), http.clone());
            }
            if let Some(ref https) = self.config.proxy_https {
                proxies.insert("https".to_string(), https.clone());
            }
            return (Some(proxies), false);
        }

        (None, false)
    }

    fn get_proxy_name(&self) -> String {
        if let Some(ref pool_py) = self.proxy_pool {
            Python::with_gil(|py| pool_py.borrow(py).get_current_proxy_name())
        } else {
            "None".to_string()
        }
    }

    fn mark_proxy_success(&self) {
        if let Some(ref pool_py) = self.proxy_pool {
            Python::with_gil(|py| pool_py.borrow(py).mark_success());
        }
    }

    fn mark_proxy_failure_and_switch(&self) -> bool {
        if let Some(ref pool_py) = self.proxy_pool {
            Python::with_gil(|py| pool_py.borrow(py).mark_failure_and_switch())
        } else {
            false
        }
    }

    fn get_current_proxy(&self) -> Option<HashMap<String, String>> {
        if let Some(ref pool_py) = self.proxy_pool {
            Python::with_gil(|py| pool_py.borrow(py).get_current_proxy())
        } else {
            None
        }
    }

    fn do_request(
        &self,
        target_url: &str,
        headers: &HashMap<String, String>,
        proxies: Option<&HashMap<String, String>>,
        timeout: u64,
        context_msg: &str,
    ) -> (Option<String>, Option<String>) {
        debug!("[{}] Requesting: {}", context_msg, target_url);

        let client = match build_client(proxies) {
            Ok(c) => c,
            Err(e) => {
                error!("[{}] Failed to build client: {}", context_msg, e);
                return (None, Some(e.to_string()));
            }
        };

        let header_map = hashmap_to_headermap(headers);

        match client
            .get(target_url)
            .headers(header_map)
            .timeout(Duration::from_secs(timeout))
            .send()
        {
            Ok(response) => {
                let status = response.status();
                if !status.is_success() {
                    error!("[{}] HTTP Error: {}", context_msg, status);
                    return (None, Some(format!("HTTP {}", status)));
                }
                match response.text() {
                    Ok(text) => {
                        debug!(
                            "[{}] Response: HTTP {}, Text-Length: {} chars",
                            context_msg,
                            status,
                            text.len()
                        );
                        (Some(text), None)
                    }
                    Err(e) => {
                        error!("[{}] Failed to read response text: {}", context_msg, e);
                        (None, Some(e.to_string()))
                    }
                }
            }
            Err(e) => {
                error!("[{}] Error: {}", context_msg, e);
                (None, Some(e.to_string()))
            }
        }
    }

    fn get_cf_bypass_url(&self, proxy_ip: Option<&str>) -> String {
        let ip = proxy_ip.unwrap_or("127.0.0.1");
        format!("http://{}:{}", ip, self.config.cf_bypass_service_port)
    }

    fn get_bypass_ip(&self, proxies: Option<&HashMap<String, String>>, force_local: bool) -> Option<String> {
        if force_local {
            return None;
        }
        let proxy_url = proxies.and_then(|p| p.get("https").or_else(|| p.get("http")));
        proxy_url.and_then(|u| extract_ip_from_proxy_url(u))
    }

    fn fetch_with_cf_bypass(
        &self,
        url: &str,
        proxies: Option<&HashMap<String, String>>,
        context_msg: &str,
        force_local: bool,
    ) -> (Option<String>, bool, bool) {
        let proxy_ip = self.get_bypass_ip(proxies, force_local);

        if !force_local && proxies.is_none() {
            error!("[CF Bypass] {}: No proxy available", context_msg);
            return (None, false, false);
        }

        let bypass_base = self.get_cf_bypass_url(proxy_ip.as_deref());
        let encoded_url = urlencoding::encode(url);
        let bypass_url = format!("{}/html?url={}", bypass_base, encoded_url);

        let masked_ip = proxy_ip
            .as_deref()
            .map_or("127.0.0.1".to_string(), |ip| mask_ip_address(Some(ip)));
        debug!(
            "[CF Bypass] {}: {} -> http://{}:{}/html?url=...",
            context_msg, url, masked_ip, self.config.cf_bypass_service_port
        );

        let empty_headers = HashMap::new();
        let (html_content, _error) =
            self.do_request(&bypass_url, &empty_headers, None, 60, &format!("CF Bypass {}", context_msg));

        match html_content {
            Some(content) => {
                if is_cf_bypass_failure(&content) {
                    warn!(
                        "[CF Bypass] {} returned failure response (size={} bytes)",
                        context_msg,
                        content.len()
                    );
                    return (Some(content), false, false);
                }

                if is_turnstile_page(&content) {
                    warn!(
                        "[CF Bypass] {} returned Turnstile page (size={} bytes)",
                        context_msg,
                        content.len()
                    );
                    return (Some(content), false, true);
                }

                if has_empty_message(&content) {
                    debug!("[CF Bypass] {}: Valid empty page detected", context_msg);
                    return (Some(content), true, false);
                }

                if has_age_modal(&content) && !has_valid_content(&content) && !has_empty_message(&content) {
                    debug!(
                        "[CF Bypass] {}: Age modal detected without content, attempting over18 bypass via CF...",
                        context_msg
                    );

                    // Try over18 bypass using regex to extract over18 link
                    let over18_re = regex::Regex::new(r#"href="([^"]*over18[^"]*)""#).ok();
                    if let Some(re) = over18_re {
                        if let Some(caps) = re.captures(&content) {
                            let href = &caps[1];
                            let over18_url = if href.starts_with("http") {
                                href.to_string()
                            } else {
                                format!("{}{}", self.config.base_url, href)
                            };

                            let encoded_over18 = urlencoding::encode(&over18_url);
                            let bypass_over18 = format!("{}/html?url={}", bypass_base, encoded_over18);

                            let (over18_content, _) = self.do_request(
                                &bypass_over18,
                                &empty_headers,
                                None,
                                60,
                                &format!("CF Bypass Over18 {}", context_msg),
                            );

                            if over18_content.is_some() {
                                let (retry_content, _) = self.do_request(
                                    &bypass_url,
                                    &empty_headers,
                                    None,
                                    60,
                                    &format!("CF Bypass Retry {}", context_msg),
                                );

                                if let Some(retry) = retry_content {
                                    if has_valid_content(&retry) || has_empty_message(&retry) {
                                        debug!("[CF Bypass] {}: Over18 bypass successful!", context_msg);
                                        return (Some(retry), true, false);
                                    }
                                }
                            }
                        }
                    }

                    warn!("[CF Bypass] {}: Age verification bypass failed", context_msg);
                    return (Some(content), false, false);
                }

                debug!(
                    "[CF Bypass] {} SUCCESS - got valid HTML (size={} bytes)",
                    context_msg,
                    content.len()
                );
                (Some(content), true, false)
            }
            None => {
                error!("[CF Bypass] {} returned no content", context_msg);
                (None, false, false)
            }
        }
    }

    fn fetch_direct(
        &self,
        url: &str,
        proxies: Option<&HashMap<String, String>>,
        context_msg: &str,
        use_cookie: bool,
    ) -> (Option<String>, bool, bool) {
        let mut headers = build_browser_headers();
        if use_cookie {
            if let Some(ref cookie) = self.config.javdb_session_cookie {
                headers.insert("Cookie".into(), format!("_jdb_session={}", cookie));
            }
        }

        let (html_content, error) =
            self.do_request(url, &headers, proxies, 30, &format!("Direct {}", context_msg));

        match html_content {
            Some(content) => {
                if is_turnstile_page(&content) {
                    warn!(
                        "[Direct] {} returned Turnstile page (size={} bytes)",
                        context_msg,
                        content.len()
                    );
                    return (Some(content), false, true);
                }
                (Some(content), error.is_none(), false)
            }
            None => (None, false, false),
        }
    }

    fn process_html(&self, html_content: Option<&str>) -> Option<String> {
        let content = html_content?;

        if is_turnstile_page(content) {
            warn!("Cloudflare Turnstile verification page detected");
            return None;
        }

        if has_age_modal(content) && !has_valid_content(content) && !has_empty_message(content) {
            warn!("Age verification modal detected without content - page may need over18 bypass");
            return None;
        }

        Some(content.to_string())
    }

    fn refresh_bypass_cache(
        &self,
        url: &str,
        proxies: Option<&HashMap<String, String>>,
        force_local: bool,
    ) -> bool {
        let proxy_ip = self.get_bypass_ip(proxies, force_local);

        if !force_local && proxies.is_none() {
            warn!("[CF Bypass] Cannot refresh cache: no proxy available");
            return false;
        }

        let bypass_base = self.get_cf_bypass_url(proxy_ip.as_deref());
        let encoded_url = urlencoding::encode(url);
        let refresh_url = format!("{}/html?url={}", bypass_base, encoded_url);

        let mut headers = HashMap::new();
        headers.insert("x-bypass-cache".to_string(), "true".to_string());

        debug!("[CF Bypass] Refreshing bypass cache...");

        let (content, _) = self.do_request(&refresh_url, &headers, None, 120, "CF Bypass Cache Refresh");

        match content {
            Some(c) if c.len() > 10000 => {
                debug!(
                    "[CF Bypass] Cache refresh successful (size={} bytes)",
                    c.len()
                );
                true
            }
            Some(c) => {
                warn!(
                    "[CF Bypass] Cache refresh returned small response (size={} bytes)",
                    c.len()
                );
                false
            }
            None => {
                error!("[CF Bypass] Cache refresh error");
                false
            }
        }
    }

    fn get_page_impl(
        &mut self,
        url: &str,
        use_cookie: bool,
        use_proxy: bool,
        module_name: &str,
        max_retries: u32,
        use_cf_bypass: bool,
    ) -> PyResult<Option<String>> {
        let effective_cf_bypass = use_cf_bypass && self.config.cf_bypass_enabled;

        let (proxies, use_proxy_pool_mode) = self.get_proxies_config(module_name, use_proxy);
        let proxy_name = if use_proxy_pool_mode {
            self.get_proxy_name()
        } else {
            "None".to_string()
        };

        if effective_cf_bypass {
            return Ok(self.get_page_with_cf_bypass(
                url,
                use_cookie,
                use_proxy,
                module_name,
                proxies,
                use_proxy_pool_mode,
                &proxy_name,
            ));
        }

        Ok(self.get_page_direct(
            url,
            use_cookie,
            module_name,
            max_retries,
            proxies,
            use_proxy_pool_mode,
            &proxy_name,
        ))
    }

    fn get_page_with_cf_bypass(
        &mut self,
        url: &str,
        use_cookie: bool,
        use_proxy: bool,
        module_name: &str,
        mut proxies: Option<HashMap<String, String>>,
        use_proxy_pool_mode: bool,
        proxy_name: &str,
    ) -> Option<String> {
        let use_local_bypass = !use_proxy;
        let mut turnstile_detected;

        // Initial CF bypass attempt
        let (html, success, is_turnstile) = self.fetch_with_cf_bypass(
            url,
            proxies.as_ref(),
            &format!("Proxy={}", proxy_name),
            use_local_bypass,
        );
        if success {
            let result = self.process_html(html.as_deref());
            if let Some(ref r) = result {
                if r.len() >= 10000 {
                    if use_proxy_pool_mode {
                        self.mark_proxy_success();
                    }
                    self.cf_bypass_failure_count = 0;
                    return result;
                }
            }
        }
        turnstile_detected = is_turnstile;

        warn!(
            "[{}] CF Bypass initial attempt failed. Starting fallback sequence...",
            module_name
        );
        self.cf_bypass_failure_count += 1;

        if self.config.fallback_cooldown > 0 {
            thread::sleep(Duration::from_secs(self.config.fallback_cooldown));
        }

        // Step (a): Retry CF bypass
        debug!("[{}] Fallback step (a): Retry CF bypass", module_name);
        let (html, success, is_turnstile) = self.fetch_with_cf_bypass(
            url,
            proxies.as_ref(),
            &format!("Retry Proxy={}", proxy_name),
            use_local_bypass,
        );
        if success {
            let result = self.process_html(html.as_deref());
            if let Some(ref r) = result {
                if r.len() >= 10000 {
                    if use_proxy_pool_mode {
                        self.mark_proxy_success();
                    }
                    self.cf_bypass_failure_count = 0;
                    return result;
                }
            }
        }
        turnstile_detected = turnstile_detected || is_turnstile;

        if turnstile_detected {
            info!("[{}] Turnstile detected, refreshing bypass cache...", module_name);
            if self.config.fallback_cooldown > 0 {
                thread::sleep(Duration::from_secs(self.config.fallback_cooldown));
            }
            self.refresh_bypass_cache(url, proxies.as_ref(), use_local_bypass);
            turnstile_detected = false;
        }

        // Step (b): Direct with current proxy
        if use_proxy && proxies.is_some() {
            if self.config.fallback_cooldown > 0 {
                thread::sleep(Duration::from_secs(self.config.fallback_cooldown));
            }
            debug!("[{}] Fallback step (b): Direct request with current proxy", module_name);
            let (html, success, is_turnstile) = self.fetch_direct(
                url,
                proxies.as_ref(),
                &format!("Proxy={}", proxy_name),
                use_cookie,
            );
            if success {
                let result = self.process_html(html.as_deref());
                if let Some(ref r) = result {
                    if r.len() >= 10000 {
                        if use_proxy_pool_mode {
                            self.mark_proxy_success();
                        }
                        return result;
                    }
                }
            }
            turnstile_detected = turnstile_detected || is_turnstile;
        }

        // Steps (c) & (d): Try other proxies
        if use_proxy && use_proxy_pool_mode && self.config.proxy_mode == "pool" {
            let pool_size = self.proxy_pool.as_ref().map_or(0, |p| {
                Python::with_gil(|py| p.borrow(py).get_proxy_count())
            });
            let max_switches = (pool_size.saturating_sub(1)).min(5);

            for _ in 0..max_switches {
                if self.config.fallback_cooldown > 0 {
                    thread::sleep(Duration::from_secs(self.config.fallback_cooldown));
                }

                if !self.mark_proxy_failure_and_switch() {
                    warn!("[{}] No more proxies available in pool", module_name);
                    break;
                }

                proxies = self.get_current_proxy();
                let new_proxy_name = self.get_proxy_name();

                // Step (c): Direct with new proxy
                debug!("[{}] Fallback step (c): Direct with proxy={}", module_name, new_proxy_name);
                let (html, success, is_turnstile) = self.fetch_direct(
                    url,
                    proxies.as_ref(),
                    &format!("Proxy={}", new_proxy_name),
                    use_cookie,
                );
                if success {
                    let result = self.process_html(html.as_deref());
                    if let Some(ref r) = result {
                        if r.len() >= 10000 {
                            self.mark_proxy_success();
                            return result;
                        }
                    }
                }
                turnstile_detected = turnstile_detected || is_turnstile;

                if self.config.fallback_cooldown > 0 {
                    thread::sleep(Duration::from_secs(self.config.fallback_cooldown));
                }

                // Step (d): CF bypass with new proxy
                debug!("[{}] Fallback step (d): CF bypass with proxy={}", module_name, new_proxy_name);
                let (html, success, is_turnstile) = self.fetch_with_cf_bypass(
                    url,
                    proxies.as_ref(),
                    &format!("Proxy={}", new_proxy_name),
                    false,
                );
                if success {
                    let result = self.process_html(html.as_deref());
                    if let Some(ref r) = result {
                        if r.len() >= 10000 {
                            self.mark_proxy_success();
                            self.cf_bypass_failure_count = 0;
                            return result;
                        }
                    }
                }
                turnstile_detected = turnstile_detected || is_turnstile;

                if turnstile_detected {
                    info!("[{}] Turnstile detected after step (d), refreshing...", module_name);
                    if self.config.fallback_cooldown > 0 {
                        thread::sleep(Duration::from_secs(self.config.fallback_cooldown));
                    }
                    self.refresh_bypass_cache(url, proxies.as_ref(), false);
                    turnstile_detected = false;
                }
            }
        }

        error!(
            "[{}] All CF bypass fallback attempts exhausted for {}",
            module_name, url
        );
        self.cf_bypass_failure_count += 1;
        None
    }

    fn get_page_direct(
        &mut self,
        url: &str,
        use_cookie: bool,
        module_name: &str,
        max_retries: u32,
        mut proxies: Option<HashMap<String, String>>,
        use_proxy_pool_mode: bool,
        proxy_name: &str,
    ) -> Option<String> {
        let mut retry_count = 0u32;
        let mut current_proxy_name = proxy_name.to_string();

        while retry_count < max_retries {
            debug!("Fetching URL: {} (attempt {}/{})", url, retry_count + 1, max_retries);

            let ctx = if proxies.is_some() {
                format!("Proxy={}", current_proxy_name)
            } else {
                "No proxy".to_string()
            };

            let (html, success, is_turnstile) =
                self.fetch_direct(url, proxies.as_ref(), &ctx, use_cookie);

            if success {
                if use_proxy_pool_mode {
                    self.mark_proxy_success();
                }

                let result = self.process_html(html.as_deref());
                if let Some(ref r) = result {
                    if r.len() >= 10000 {
                        return result;
                    }
                    // For detail pages, small response means failure
                    if url.contains("/v/") {
                        warn!("[{}] Small response for detail page ({} bytes), retrying...", module_name, r.len());
                    } else {
                        return result;
                    }
                }
            }

            if is_turnstile {
                warn!(
                    "[{}] Turnstile detected, waiting {}s before retry...",
                    module_name, self.config.cf_turnstile_cooldown
                );
                thread::sleep(Duration::from_secs(self.config.cf_turnstile_cooldown));
            }

            if use_proxy_pool_mode && retry_count < max_retries - 1 {
                if self.mark_proxy_failure_and_switch() {
                    proxies = self.get_current_proxy();
                    current_proxy_name = self.get_proxy_name();
                    info!("[{}] Switched to proxy: {}, retrying...", module_name, current_proxy_name);
                    retry_count += 1;
                    continue;
                } else {
                    error!("[{}] Failed to switch proxy, no more proxies available", module_name);
                    break;
                }
            } else {
                retry_count += 1;
            }
        }

        None
    }
}

#[pyfunction]
#[pyo3(signature = (proxy_pool=None, config=None))]
pub fn create_request_handler_from_config(
    proxy_pool: Option<Py<ProxyPool>>,
    config: Option<RequestConfig>,
) -> RequestHandler {
    RequestHandler::new(proxy_pool, config)
}
