use chrono::{DateTime, Duration, Local};
use log::{debug, error, info, warn};
use parking_lot::Mutex;
use pyo3::prelude::*;
use pyo3::conversion::ToPyObject;
use std::collections::HashMap;
use std::sync::Arc;

use super::ban_manager::{get_ban_manager, ProxyBanManager};
use super::masking::mask_proxy_url_internal;

#[derive(Clone, Debug)]
pub struct ProxyInfoInner {
    pub http_url: Option<String>,
    pub https_url: Option<String>,
    pub name: String,
    pub failures: u32,
    pub last_success: Option<DateTime<Local>>,
    pub last_failure: Option<DateTime<Local>>,
    pub total_requests: u64,
    pub successful_requests: u64,
    pub is_available: bool,
    pub cooldown_until: Option<DateTime<Local>>,
}

impl ProxyInfoInner {
    pub fn get_proxies_dict(&self) -> HashMap<String, String> {
        let mut proxies = HashMap::new();
        if let Some(ref http) = self.http_url {
            proxies.insert("http".to_string(), http.clone());
        }
        if let Some(ref https) = self.https_url {
            proxies.insert("https".to_string(), https.clone());
        }
        proxies
    }

    pub fn mark_success(&mut self) {
        self.last_success = Some(Local::now());
        self.successful_requests += 1;
        self.total_requests += 1;
        self.failures = 0;
        self.is_available = true;
        self.cooldown_until = None;
    }

    pub fn mark_failure(&mut self, cooldown_seconds: i64) {
        self.last_failure = Some(Local::now());
        self.failures += 1;
        self.total_requests += 1;
        self.cooldown_until = Some(Local::now() + Duration::seconds(cooldown_seconds));
        self.is_available = false;
    }

    pub fn is_in_cooldown(&self) -> bool {
        self.cooldown_until
            .map_or(false, |until| Local::now() < until)
    }

    pub fn get_success_rate(&self) -> f64 {
        if self.total_requests == 0 {
            0.0
        } else {
            self.successful_requests as f64 / self.total_requests as f64
        }
    }
}

#[pyclass(name = "RustProxyInfo")]
#[derive(Clone, Debug)]
pub struct ProxyInfo {
    inner: ProxyInfoInner,
}

#[pymethods]
impl ProxyInfo {
    #[new]
    #[pyo3(signature = (http_url=None, https_url=None, name="Unnamed".to_string()))]
    fn new(http_url: Option<String>, https_url: Option<String>, name: String) -> Self {
        Self {
            inner: ProxyInfoInner {
                http_url,
                https_url,
                name,
                failures: 0,
                last_success: None,
                last_failure: None,
                total_requests: 0,
                successful_requests: 0,
                is_available: true,
                cooldown_until: None,
            },
        }
    }

    #[getter]
    fn name(&self) -> &str {
        &self.inner.name
    }

    #[getter]
    fn failures(&self) -> u32 {
        self.inner.failures
    }

    #[getter]
    fn is_available(&self) -> bool {
        self.inner.is_available
    }

    #[getter]
    fn total_requests(&self) -> u64 {
        self.inner.total_requests
    }

    #[getter]
    fn successful_requests(&self) -> u64 {
        self.inner.successful_requests
    }

    fn get_proxies_dict(&self) -> HashMap<String, String> {
        self.inner.get_proxies_dict()
    }

    fn get_success_rate(&self) -> f64 {
        self.inner.get_success_rate()
    }

    fn is_in_cooldown(&self) -> bool {
        self.inner.is_in_cooldown()
    }
}

struct PoolInner {
    proxies: Vec<ProxyInfoInner>,
    current_index: usize,
    no_proxy_mode: bool,
}

#[pyclass(name = "RustProxyPool")]
pub struct ProxyPool {
    inner: Mutex<PoolInner>,
    #[pyo3(get)]
    cooldown_seconds: i64,
    #[pyo3(get)]
    max_failures_before_cooldown: u32,
    ban_manager: Arc<ProxyBanManager>,
}

#[pymethods]
impl ProxyPool {
    #[new]
    #[pyo3(signature = (cooldown_seconds=300, max_failures_before_cooldown=3, ban_log_file="reports/proxy_bans.csv".to_string()))]
    pub fn new(
        cooldown_seconds: i64,
        max_failures_before_cooldown: u32,
        ban_log_file: String,
    ) -> Self {
        Self {
            inner: Mutex::new(PoolInner {
                proxies: Vec::new(),
                current_index: 0,
                no_proxy_mode: false,
            }),
            cooldown_seconds,
            max_failures_before_cooldown,
            ban_manager: get_ban_manager(&ban_log_file),
        }
    }

    #[pyo3(signature = (http_url=None, https_url=None, name=None))]
    pub fn add_proxy(
        &self,
        http_url: Option<String>,
        https_url: Option<String>,
        name: Option<String>,
    ) {
        if http_url.is_none() && https_url.is_none() {
            warn!("Attempted to add proxy with no URLs, skipping");
            return;
        }

        let proxy_name = name.unwrap_or_else(|| {
            let pool = self.inner.lock();
            format!("Proxy-{}", pool.proxies.len() + 1)
        });

        if self.ban_manager.is_proxy_banned(&proxy_name) {
            warn!("Proxy '{}' is currently banned, skipping", proxy_name);
            return;
        }

        let masked_http = mask_proxy_url_internal(http_url.as_deref());
        let masked_https = mask_proxy_url_internal(https_url.as_deref());

        let proxy = ProxyInfoInner {
            http_url,
            https_url,
            name: proxy_name.clone(),
            failures: 0,
            last_success: None,
            last_failure: None,
            total_requests: 0,
            successful_requests: 0,
            is_available: true,
            cooldown_until: None,
        };

        self.inner.lock().proxies.push(proxy);
        info!(
            "Added proxy '{}' to pool (HTTP: {}, HTTPS: {})",
            proxy_name, masked_http, masked_https
        );
    }

    pub fn add_proxies_from_list(&self, proxy_list: Vec<HashMap<String, String>>) {
        for (i, config) in proxy_list.iter().enumerate() {
            let http_url = config.get("http").cloned();
            let https_url = config.get("https").cloned();
            let name = config
                .get("name")
                .cloned()
                .unwrap_or_else(|| format!("Proxy-{}", i + 1));
            self.add_proxy(http_url, https_url, Some(name));
        }
    }

    pub fn enable_no_proxy_mode(&self) {
        self.inner.lock().no_proxy_mode = true;
        info!("No-proxy mode enabled (direct connection)");
    }

    pub fn disable_no_proxy_mode(&self) {
        self.inner.lock().no_proxy_mode = false;
        info!("No-proxy mode disabled");
    }

    pub fn get_current_proxy(&self) -> Option<HashMap<String, String>> {
        let mut pool = self.inner.lock();
        if pool.no_proxy_mode {
            return None;
        }
        if pool.proxies.is_empty() {
            warn!("No proxies configured in pool");
            return None;
        }

        check_cooldowns(&mut pool.proxies);

        let len = pool.proxies.len();
        for _ in 0..len {
            let proxy = &pool.proxies[pool.current_index];
            if proxy.is_available && !proxy.is_in_cooldown() {
                return Some(proxy.get_proxies_dict());
            }
            pool.current_index = (pool.current_index + 1) % len;
        }

        warn!("All proxies are unavailable or in cooldown");
        None
    }

    pub fn get_next_proxy(&self) -> Option<HashMap<String, String>> {
        let mut pool = self.inner.lock();
        if pool.no_proxy_mode {
            return None;
        }
        if pool.proxies.is_empty() {
            warn!("No proxies configured in pool");
            return None;
        }

        check_cooldowns(&mut pool.proxies);

        let available = pool
            .proxies
            .iter()
            .filter(|p| p.is_available && !p.is_in_cooldown())
            .count();
        if available == 0 {
            warn!("All proxies are unavailable or in cooldown");
            return None;
        }

        let len = pool.proxies.len();
        for _ in 0..len {
            pool.current_index = (pool.current_index + 1) % len;
            let proxy = &pool.proxies[pool.current_index];
            if proxy.is_available && !proxy.is_in_cooldown() {
                debug!("Round-robin selected proxy: {}", proxy.name);
                return Some(proxy.get_proxies_dict());
            }
        }

        warn!("Unexpected: no available proxy found after rotation");
        None
    }

    pub fn get_current_proxy_name(&self) -> String {
        let pool = self.inner.lock();
        if pool.no_proxy_mode {
            return "No-Proxy (Direct)".to_string();
        }
        if pool.proxies.is_empty() {
            return "None".to_string();
        }
        pool.proxies[pool.current_index].name.clone()
    }

    pub fn mark_success(&self) {
        let mut pool = self.inner.lock();
        if pool.no_proxy_mode || pool.proxies.is_empty() {
            return;
        }
        let idx = pool.current_index;
        pool.proxies[idx].mark_success();
        debug!(
            "Proxy '{}' marked as successful (success rate: {:.1}%)",
            pool.proxies[idx].name,
            pool.proxies[idx].get_success_rate() * 100.0
        );
    }

    pub fn mark_failure_and_switch(&self) -> bool {
        let mut pool = self.inner.lock();
        if pool.no_proxy_mode || pool.proxies.is_empty() {
            return false;
        }

        let idx = pool.current_index;
        let current_name = pool.proxies[idx].name.clone();

        if pool.proxies[idx].failures >= self.max_failures_before_cooldown {
            let proxy_url = pool.proxies[idx]
                .http_url
                .clone()
                .or_else(|| pool.proxies[idx].https_url.clone());
            self.ban_manager.add_ban(&current_name, proxy_url);
            pool.proxies[idx].mark_failure(self.cooldown_seconds);
            warn!(
                "Proxy '{}' reached {} failures, putting in cooldown for {}s (8 days)",
                current_name, pool.proxies[idx].failures, self.cooldown_seconds
            );
        } else {
            pool.proxies[idx].failures += 1;
            pool.proxies[idx].total_requests += 1;
            pool.proxies[idx].last_failure = Some(Local::now());
            warn!(
                "Proxy '{}' failed ({}/{})",
                current_name, pool.proxies[idx].failures, self.max_failures_before_cooldown
            );
        }

        let len = pool.proxies.len();
        let original_index = pool.current_index;

        for _ in 0..len {
            pool.current_index = (pool.current_index + 1) % len;
            if pool.proxies[pool.current_index].is_available
                && !pool.proxies[pool.current_index].is_in_cooldown()
            {
                info!(
                    "Switched from '{}' to '{}'",
                    current_name, pool.proxies[pool.current_index].name
                );
                return true;
            }
        }

        pool.current_index = original_index;
        error!("Failed to switch proxy: all proxies are unavailable");
        false
    }

    pub fn get_statistics(&self) -> HashMap<String, PyObject> {
        Python::with_gil(|py| {
            let mut pool = self.inner.lock();
            check_cooldowns(&mut pool.proxies);

            let mut stats = HashMap::new();
            stats.insert("total_proxies".to_string(), pool.proxies.len().to_object(py));

            let available = pool
                .proxies
                .iter()
                .filter(|p| p.is_available && !p.is_in_cooldown())
                .count();
            stats.insert("available_proxies".to_string(), available.to_object(py));

            let in_cooldown = pool.proxies.iter().filter(|p| p.is_in_cooldown()).count();
            stats.insert("in_cooldown".to_string(), in_cooldown.to_object(py));
            stats.insert("no_proxy_mode".to_string(), pool.no_proxy_mode.to_object(py));

            let proxy_stats: Vec<HashMap<String, PyObject>> = pool
                .proxies
                .iter()
                .enumerate()
                .map(|(i, proxy)| {
                    let mut ps: HashMap<String, PyObject> = HashMap::new();
                    ps.insert("name".to_string(), proxy.name.clone().to_object(py));
                    ps.insert("is_current".to_string(), (i == pool.current_index).to_object(py));
                    ps.insert("is_available".to_string(), proxy.is_available.to_object(py));
                    ps.insert("in_cooldown".to_string(), proxy.is_in_cooldown().to_object(py));
                    ps.insert("total_requests".to_string(), proxy.total_requests.to_object(py));
                    ps.insert("successful_requests".to_string(), proxy.successful_requests.to_object(py));
                    ps.insert(
                        "success_rate".to_string(),
                        format!("{:.1}%", proxy.get_success_rate() * 100.0).to_object(py),
                    );
                    ps.insert("consecutive_failures".to_string(), proxy.failures.to_object(py));
                    ps.insert(
                        "last_success".to_string(),
                        proxy
                            .last_success
                            .map_or("Never".to_string(), |t| t.format("%Y-%m-%d %H:%M:%S").to_string())
                            .to_object(py),
                    );
                    ps.insert(
                        "last_failure".to_string(),
                        proxy
                            .last_failure
                            .map_or("Never".to_string(), |t| t.format("%Y-%m-%d %H:%M:%S").to_string())
                            .to_object(py),
                    );
                    ps
                })
                .collect();

            stats.insert("proxies".to_string(), proxy_stats.to_object(py));
            stats
        })
    }

    #[pyo3(signature = (level=None))]
    #[allow(unused_variables)]
    pub fn log_statistics(&self, level: Option<i32>) {
        let mut pool = self.inner.lock();
        check_cooldowns(&mut pool.proxies);

        let total = pool.proxies.len();
        let available = pool
            .proxies
            .iter()
            .filter(|p| p.is_available && !p.is_in_cooldown())
            .count();
        let in_cooldown = pool.proxies.iter().filter(|p| p.is_in_cooldown()).count();

        info!("=== Proxy Pool Statistics ===");
        info!(
            "Total: {} | Available: {} | Cooldown: {} | No-Proxy Mode: {}",
            total, available, in_cooldown, pool.no_proxy_mode
        );

        for (i, proxy) in pool.proxies.iter().enumerate() {
            let current = if i == pool.current_index {
                " [CURRENT]"
            } else {
                ""
            };
            let status = if proxy.is_in_cooldown() {
                "COOLDOWN"
            } else if proxy.is_available {
                "AVAILABLE"
            } else {
                "UNAVAILABLE"
            };
            let last_success = proxy
                .last_success
                .map_or("Never".to_string(), |t| t.format("%H:%M:%S").to_string());
            let last_failure = proxy
                .last_failure
                .map_or("Never".to_string(), |t| t.format("%H:%M:%S").to_string());

            info!(
                "  {} [{}]{}: {}/{} requests ({:.1}%), failures={}, last_ok={}, last_fail={}",
                proxy.name,
                status,
                current,
                proxy.successful_requests,
                proxy.total_requests,
                proxy.get_success_rate() * 100.0,
                proxy.failures,
                last_success,
                last_failure,
            );
        }
        info!("=============================");
    }

    pub fn get_ban_summary(&self, include_ip: bool) -> String {
        self.ban_manager.get_ban_summary(include_ip)
    }

    pub fn get_proxy_count(&self) -> usize {
        self.inner.lock().proxies.len()
    }

    #[getter]
    fn proxies(&self) -> Vec<ProxyInfo> {
        self.inner
            .lock()
            .proxies
            .iter()
            .map(|p| ProxyInfo { inner: p.clone() })
            .collect()
    }
}

fn check_cooldowns(proxies: &mut [ProxyInfoInner]) {
    for proxy in proxies.iter_mut() {
        if proxy.is_in_cooldown() {
            continue;
        }
        if !proxy.is_available {
            proxy.is_available = true;
            info!(
                "Proxy '{}' cooldown period ended, marked as available",
                proxy.name
            );
        }
    }
}

#[pyfunction]
#[pyo3(signature = (proxy_list_config, cooldown_seconds=300, max_failures=3, ban_log_file="reports/proxy_bans.csv".to_string()))]
pub fn create_proxy_pool_from_config(
    proxy_list_config: Vec<HashMap<String, String>>,
    cooldown_seconds: i64,
    max_failures: u32,
    ban_log_file: String,
) -> ProxyPool {
    let pool = ProxyPool::new(cooldown_seconds, max_failures, ban_log_file);
    pool.add_proxies_from_list(proxy_list_config);
    pool
}
