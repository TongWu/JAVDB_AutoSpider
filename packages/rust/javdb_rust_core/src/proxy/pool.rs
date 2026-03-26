use chrono::{DateTime, Duration, Local, NaiveDateTime};
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

fn naive_to_local(ndt: NaiveDateTime) -> DateTime<Local> {
    ndt.and_local_timezone(Local)
        .single()
        .unwrap_or_else(Local::now)
}

fn local_to_naive(dt: DateTime<Local>) -> NaiveDateTime {
    dt.naive_local()
}

/// Shared proxy info: cloning ProxyInfo shares the same underlying data.
#[pyclass(name = "RustProxyInfo")]
#[derive(Clone, Debug)]
pub struct ProxyInfo {
    inner: Arc<Mutex<ProxyInfoInner>>,
}

impl ProxyInfo {
    pub fn new_shared(data: ProxyInfoInner) -> Self {
        Self {
            inner: Arc::new(Mutex::new(data)),
        }
    }

    pub fn arc(&self) -> Arc<Mutex<ProxyInfoInner>> {
        self.inner.clone()
    }
}

#[pymethods]
impl ProxyInfo {
    #[new]
    #[pyo3(signature = (http_url=None, https_url=None, name="Unnamed".to_string()))]
    fn py_new(http_url: Option<String>, https_url: Option<String>, name: String) -> Self {
        Self::new_shared(ProxyInfoInner {
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
        })
    }

    // --- Getters ---

    #[getter]
    fn name(&self) -> String {
        self.inner.lock().name.clone()
    }

    #[getter]
    fn http_url(&self) -> Option<String> {
        self.inner.lock().http_url.clone()
    }

    #[getter]
    fn https_url(&self) -> Option<String> {
        self.inner.lock().https_url.clone()
    }

    #[getter]
    fn failures(&self) -> u32 {
        self.inner.lock().failures
    }

    #[getter]
    fn is_available(&self) -> bool {
        self.inner.lock().is_available
    }

    #[getter]
    fn total_requests(&self) -> u64 {
        self.inner.lock().total_requests
    }

    #[getter]
    fn successful_requests(&self) -> u64 {
        self.inner.lock().successful_requests
    }

    #[getter]
    fn cooldown_until(&self) -> Option<NaiveDateTime> {
        self.inner.lock().cooldown_until.map(local_to_naive)
    }

    #[getter]
    fn last_success(&self) -> Option<NaiveDateTime> {
        self.inner.lock().last_success.map(local_to_naive)
    }

    #[getter]
    fn last_failure(&self) -> Option<NaiveDateTime> {
        self.inner.lock().last_failure.map(local_to_naive)
    }

    // --- Setters ---

    #[setter]
    fn set_failures(&self, value: u32) {
        self.inner.lock().failures = value;
    }

    #[setter]
    fn set_is_available(&self, value: bool) {
        self.inner.lock().is_available = value;
    }

    #[setter]
    fn set_total_requests(&self, value: u64) {
        self.inner.lock().total_requests = value;
    }

    #[setter]
    fn set_successful_requests(&self, value: u64) {
        self.inner.lock().successful_requests = value;
    }

    #[setter]
    fn set_cooldown_until(&self, value: Option<NaiveDateTime>) {
        self.inner.lock().cooldown_until = value.map(naive_to_local);
    }

    // --- Methods ---

    fn get_proxies_dict(&self) -> HashMap<String, String> {
        self.inner.lock().get_proxies_dict()
    }

    fn get_success_rate(&self) -> f64 {
        self.inner.lock().get_success_rate()
    }

    fn is_in_cooldown(&self) -> bool {
        self.inner.lock().is_in_cooldown()
    }

    #[pyo3(signature = ())]
    fn mark_success(&self) {
        self.inner.lock().mark_success();
    }

    #[pyo3(signature = (cooldown_seconds=300))]
    fn mark_failure(&self, cooldown_seconds: i64) {
        self.inner.lock().mark_failure(cooldown_seconds);
    }
}

struct PoolInner {
    proxies: Vec<Arc<Mutex<ProxyInfoInner>>>,
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
    ban_manager: ProxyBanManager,
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

    // --- Pool-level getters ---

    #[getter]
    fn current_index(&self) -> usize {
        self.inner.lock().current_index
    }

    #[getter]
    fn no_proxy_mode(&self) -> bool {
        self.inner.lock().no_proxy_mode
    }

    #[getter]
    fn ban_manager(&self) -> ProxyBanManager {
        self.ban_manager.clone()
    }

    #[getter]
    fn proxies(&self) -> Vec<ProxyInfo> {
        self.inner
            .lock()
            .proxies
            .iter()
            .map(|arc| ProxyInfo { inner: arc.clone() })
            .collect()
    }

    // --- Proxy management ---

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

        self.inner.lock().proxies.push(Arc::new(Mutex::new(proxy)));
        debug!(
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

        check_cooldowns(&pool.proxies, &self.ban_manager);

        let len = pool.proxies.len();
        for _ in 0..len {
            let proxy = pool.proxies[pool.current_index].lock();
            if proxy.is_available && !proxy.is_in_cooldown() {
                return Some(proxy.get_proxies_dict());
            }
            drop(proxy);
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

        check_cooldowns(&pool.proxies, &self.ban_manager);

        let available = pool
            .proxies
            .iter()
            .filter(|p| {
                let proxy = p.lock();
                proxy.is_available && !proxy.is_in_cooldown()
            })
            .count();
        if available == 0 {
            warn!("All proxies are unavailable or in cooldown");
            return None;
        }

        let len = pool.proxies.len();
        for _ in 0..len {
            pool.current_index = (pool.current_index + 1) % len;
            let proxy = pool.proxies[pool.current_index].lock();
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
        let name = pool.proxies[pool.current_index].lock().name.clone();
        name
    }

    pub fn mark_success(&self) {
        let pool = self.inner.lock();
        if pool.no_proxy_mode || pool.proxies.is_empty() {
            return;
        }
        let idx = pool.current_index;
        let mut proxy = pool.proxies[idx].lock();
        proxy.mark_success();
        debug!(
            "Proxy '{}' marked as successful (success rate: {:.1}%)",
            proxy.name,
            proxy.get_success_rate() * 100.0
        );
    }

    pub fn mark_failure_and_switch(&self) -> bool {
        let mut pool = self.inner.lock();
        if pool.no_proxy_mode || pool.proxies.is_empty() {
            return false;
        }

        let idx = pool.current_index;
        let current_name = pool.proxies[idx].lock().name.clone();

        {
            let mut proxy = pool.proxies[idx].lock();
            proxy.failures += 1;
            proxy.total_requests += 1;
            proxy.last_failure = Some(Local::now());

            if proxy.failures >= self.max_failures_before_cooldown {
                let proxy_url = proxy
                    .http_url
                    .clone()
                    .or_else(|| proxy.https_url.clone());
                self.ban_manager.add_ban(&current_name, proxy_url);
                proxy.cooldown_until = Some(Local::now() + Duration::seconds(self.cooldown_seconds));
                proxy.is_available = false;
                warn!(
                    "Proxy '{}' reached {} failures, putting in cooldown for {}s (8 days)",
                    current_name, proxy.failures, self.cooldown_seconds
                );
            } else {
                warn!(
                    "Proxy '{}' failed ({}/{})",
                    current_name, proxy.failures, self.max_failures_before_cooldown
                );
            }
        }

        let len = pool.proxies.len();
        let original_index = pool.current_index;

        for _ in 0..len {
            pool.current_index = (pool.current_index + 1) % len;
            let proxy = pool.proxies[pool.current_index].lock();
            if proxy.is_available && !proxy.is_in_cooldown() {
                info!(
                    "Switched from '{}' to '{}'",
                    current_name, proxy.name
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
            let pool = self.inner.lock();
            check_cooldowns(&pool.proxies, &self.ban_manager);

            let mut stats = HashMap::new();
            stats.insert("total_proxies".to_string(), pool.proxies.len().to_object(py));

            let available = pool
                .proxies
                .iter()
                .filter(|p| {
                    let proxy = p.lock();
                    proxy.is_available && !proxy.is_in_cooldown()
                })
                .count();
            stats.insert("available_proxies".to_string(), available.to_object(py));

            let in_cooldown = pool.proxies.iter().filter(|p| p.lock().is_in_cooldown()).count();
            stats.insert("in_cooldown".to_string(), in_cooldown.to_object(py));
            stats.insert("no_proxy_mode".to_string(), pool.no_proxy_mode.to_object(py));

            let proxy_stats: Vec<HashMap<String, PyObject>> = pool
                .proxies
                .iter()
                .enumerate()
                .map(|(i, arc)| {
                    let proxy = arc.lock();
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
        let pool = self.inner.lock();
        check_cooldowns(&pool.proxies, &self.ban_manager);

        let total = pool.proxies.len();
        let available = pool
            .proxies
            .iter()
            .filter(|p| {
                let proxy = p.lock();
                proxy.is_available && !proxy.is_in_cooldown()
            })
            .count();
        let in_cooldown = pool.proxies.iter().filter(|p| p.lock().is_in_cooldown()).count();
        let no_proxy_mode = pool.no_proxy_mode;

        info!("=== Proxy Pool Statistics ===");
        info!(
            "Total: {} | Available: {} | Cooldown: {} | No-Proxy Mode: {}",
            total, available, in_cooldown, no_proxy_mode
        );

        for (i, arc) in pool.proxies.iter().enumerate() {
            let proxy = arc.lock();
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

    #[pyo3(signature = (include_ip=false))]
    pub fn get_ban_summary(&self, include_ip: bool) -> String {
        self.ban_manager.get_ban_summary(include_ip)
    }

    #[pyo3(signature = (proxy_name=None))]
    pub fn ban_proxy(&self, proxy_name: Option<String>) -> bool {
        let mut pool = self.inner.lock();
        if pool.no_proxy_mode || pool.proxies.is_empty() {
            return false;
        }

        let (target_index, target_name, proxy_url) = match proxy_name {
            None => {
                let idx = pool.current_index;
                let proxy = pool.proxies[idx].lock();
                let name = proxy.name.clone();
                let url = proxy.http_url.clone().or_else(|| proxy.https_url.clone());
                (idx, name, url)
            }
            Some(ref name) => {
                let found = pool.proxies.iter().enumerate().find(|(_, arc)| {
                    arc.lock().name == *name
                });
                match found {
                    Some((idx, arc)) => {
                        let proxy = arc.lock();
                        let url = proxy.http_url.clone().or_else(|| proxy.https_url.clone());
                        (idx, name.clone(), url)
                    }
                    None => {
                        warn!("ban_proxy: proxy '{}' not found in pool", name);
                        return false;
                    }
                }
            }
        };

        self.ban_manager.add_ban(&target_name, proxy_url);
        {
            let mut proxy = pool.proxies[target_index].lock();
            proxy.cooldown_until = Some(Local::now() + Duration::seconds(self.cooldown_seconds));
            proxy.is_available = false;
            proxy.failures += 1;
            proxy.total_requests += 1;
            proxy.last_failure = Some(Local::now());
        }
        warn!(
            "Proxy '{}' immediately banned and put in cooldown for {}s",
            target_name, self.cooldown_seconds
        );

        let len = pool.proxies.len();
        let mut candidate = target_index;
        for _ in 0..len {
            candidate = (candidate + 1) % len;
            let proxy = pool.proxies[candidate].lock();
            if proxy.is_available && !proxy.is_in_cooldown() {
                pool.current_index = candidate;
                info!(
                    "Switched from '{}' to '{}'",
                    target_name, proxy.name
                );
                return true;
            }
        }

        error!("ban_proxy: all proxies are unavailable after ban");
        false
    }

    pub fn get_proxy_count(&self) -> usize {
        self.inner.lock().proxies.len()
    }
}

fn check_cooldowns(proxies: &[Arc<Mutex<ProxyInfoInner>>], ban_manager: &ProxyBanManager) {
    for arc in proxies {
        let mut proxy = arc.lock();
        if proxy.is_in_cooldown() {
            continue;
        }
        if !proxy.is_available {
            if ban_manager.is_proxy_banned(&proxy.name) {
                continue;
            }
            proxy.is_available = true;
            proxy.failures = 0;
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
