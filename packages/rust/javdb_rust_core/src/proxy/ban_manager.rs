use chrono::{DateTime, Local};
use log::{debug, info};
use parking_lot::Mutex;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::sync::Arc;

const TIME_FMT: &str = "%Y-%m-%d %H:%M:%S";

/// Session-scoped ban record.  Bans are permanent for the lifetime of
/// the current process — no duration / expiry tracking needed.
#[derive(Clone, Debug)]
pub struct ProxyBanRecord {
    pub proxy_name: String,
    pub ban_time: DateTime<Local>,
    pub proxy_url: Option<String>,
}

struct BanManagerInner {
    banned_proxies: Mutex<HashMap<String, ProxyBanRecord>>,
}

/// Session-scoped proxy ban manager.  Bans are kept in-memory only and
/// are NOT persisted to disk.  Every new process starts with no bans.
/// A ban is permanent for the lifetime of the process.
#[pyclass(name = "RustProxyBanManager")]
#[derive(Clone)]
pub struct ProxyBanManager {
    inner: Arc<BanManagerInner>,
}

#[pymethods]
impl ProxyBanManager {
    #[new]
    pub fn new() -> Self {
        info!("RustProxyBanManager initialised (session-scoped, in-memory only)");
        Self {
            inner: Arc::new(BanManagerInner {
                banned_proxies: Mutex::new(HashMap::new()),
            }),
        }
    }

    pub fn is_proxy_banned(&self, proxy_name: &str) -> bool {
        let banned = self.inner.banned_proxies.lock();
        banned.contains_key(proxy_name)
    }

    #[pyo3(signature = (proxy_name, proxy_url=None))]
    pub fn add_ban(&self, proxy_name: &str, proxy_url: Option<String>) {
        let mut banned = self.inner.banned_proxies.lock();
        if banned.contains_key(proxy_name) {
            debug!("Proxy '{}' is already banned this session, not updating", proxy_name);
            return;
        }

        let record = ProxyBanRecord {
            proxy_name: proxy_name.to_string(),
            ban_time: Local::now(),
            proxy_url,
        };
        banned.insert(proxy_name.to_string(), record);

        info!(
            "Proxy '{}' banned [session-permanent, until process restart]",
            proxy_name
        );
    }

    #[pyo3(signature = (include_ip=false))]
    pub fn get_ban_summary(&self, include_ip: bool) -> String {
        let banned = self.inner.banned_proxies.lock();

        if banned.is_empty() {
            return "No proxies currently banned.".to_string();
        }

        let mut records: Vec<&ProxyBanRecord> = banned.values().collect();
        records.sort_by_key(|r| r.ban_time);

        let mut lines = vec![
            format!("Currently banned proxies: {} [session-scoped]", banned.len()),
            String::new(),
        ];

        for record in records {
            let mut line = format!("  - {}:", record.proxy_name);
            if include_ip {
                if let Some(ref url) = record.proxy_url {
                    line.push_str(&format!("\n    IP: {}", url));
                }
            }
            line.push_str(&format!(
                "\n    Banned at: {}",
                record.ban_time.format(TIME_FMT)
            ));
            line.push_str("\n    Status: banned until process restart");
            lines.push(line);
        }

        lines.join("\n")
    }

    pub fn get_banned_proxy_names(&self) -> Vec<String> {
        let banned = self.inner.banned_proxies.lock();
        banned.keys().cloned().collect()
    }

    pub fn get_banned_proxies(&self) -> Vec<HashMap<String, String>> {
        let banned = self.inner.banned_proxies.lock();
        banned
            .values()
            .map(|r| {
                let mut m = HashMap::new();
                m.insert("proxy_name".to_string(), r.proxy_name.clone());
                m.insert(
                    "ban_time".to_string(),
                    r.ban_time.format(TIME_FMT).to_string(),
                );
                m.insert(
                    "is_still_banned".to_string(),
                    "true".to_string(),
                );
                if let Some(ref url) = r.proxy_url {
                    m.insert("proxy_url".to_string(), url.clone());
                }
                m
            })
            .collect()
    }

    pub fn get_banned_count(&self) -> usize {
        self.inner.banned_proxies.lock().len()
    }
}

use once_cell::sync::OnceCell;

static GLOBAL_BAN_MANAGER: OnceCell<ProxyBanManager> = OnceCell::new();

pub fn get_ban_manager(_ban_log_file: &str) -> ProxyBanManager {
    GLOBAL_BAN_MANAGER
        .get_or_init(ProxyBanManager::new)
        .clone()
}

#[pyfunction]
pub fn get_global_ban_manager() -> ProxyBanManager {
    ProxyBanManager::new()
}
