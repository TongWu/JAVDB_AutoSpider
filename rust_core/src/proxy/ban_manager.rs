use chrono::{DateTime, Duration, Local, NaiveDateTime};
use log::{debug, error, info, warn};
use parking_lot::Mutex;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

const BAN_DURATION_DAYS: i64 = 7;
const COOLDOWN_DURATION_DAYS: i64 = 8;
const TIME_FMT: &str = "%Y-%m-%d %H:%M:%S";

#[derive(Clone, Debug)]
pub struct ProxyBanRecord {
    pub proxy_name: String,
    pub ban_time: DateTime<Local>,
    pub unban_time: DateTime<Local>,
    pub proxy_url: Option<String>,
}

impl ProxyBanRecord {
    pub fn is_still_banned(&self) -> bool {
        Local::now() < self.unban_time
    }

    pub fn days_until_unban(&self) -> i64 {
        let delta = self.unban_time - Local::now();
        delta.num_days().max(0)
    }

    pub fn hours_until_unban(&self) -> i64 {
        let delta = self.unban_time - Local::now();
        (delta.num_hours() % 24).max(0)
    }
}

#[pyclass(name = "RustProxyBanManager")]
pub struct ProxyBanManager {
    ban_log_file: PathBuf,
    banned_proxies: Mutex<HashMap<String, ProxyBanRecord>>,
}

#[pymethods]
impl ProxyBanManager {
    #[new]
    #[pyo3(signature = (ban_log_file="reports/proxy_bans.csv".to_string()))]
    pub fn new(ban_log_file: String) -> Self {
        let path = PathBuf::from(&ban_log_file);
        if let Some(parent) = path.parent() {
            let _ = fs::create_dir_all(parent);
        }

        let mgr = Self {
            ban_log_file: path,
            banned_proxies: Mutex::new(HashMap::new()),
        };
        mgr.load_ban_records();
        mgr.cleanup_expired_bans();
        mgr
    }

    pub fn is_proxy_banned(&self, proxy_name: &str) -> bool {
        let mut banned = self.banned_proxies.lock();
        if let Some(record) = banned.get(proxy_name) {
            if !record.is_still_banned() {
                banned.remove(proxy_name);
                self.save_ban_records_inner(&banned);
                return false;
            }
            true
        } else {
            false
        }
    }

    #[pyo3(signature = (proxy_name, proxy_url=None))]
    pub fn add_ban(&self, proxy_name: &str, proxy_url: Option<String>) {
        let mut banned = self.banned_proxies.lock();
        if let Some(existing) = banned.get(proxy_name) {
            if existing.is_still_banned() {
                warn!("Proxy '{}' is already in ban period, not updating", proxy_name);
                return;
            }
        }

        let ban_time = Local::now();
        let unban_time = ban_time + Duration::days(BAN_DURATION_DAYS);

        let record = ProxyBanRecord {
            proxy_name: proxy_name.to_string(),
            ban_time,
            unban_time,
            proxy_url,
        };
        banned.insert(proxy_name.to_string(), record);

        warn!(
            "Proxy '{}' banned until {} ({} days)",
            proxy_name,
            unban_time.format(TIME_FMT),
            BAN_DURATION_DAYS
        );

        self.save_ban_records_inner(&banned);
    }

    #[pyo3(signature = (include_ip=false))]
    pub fn get_ban_summary(&self, include_ip: bool) -> String {
        self.cleanup_expired_bans();
        let banned = self.banned_proxies.lock();

        if banned.is_empty() {
            return "No proxies currently banned.".to_string();
        }

        let mut records: Vec<&ProxyBanRecord> = banned.values().collect();
        records.sort_by_key(|r| r.unban_time);

        let mut lines = vec![format!("Currently banned proxies: {}", banned.len()), String::new()];

        for record in records {
            let days_left = record.days_until_unban();
            let hours_left = record.hours_until_unban();

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
            line.push_str(&format!(
                "\n    Will unban: {}",
                record.unban_time.format(TIME_FMT)
            ));
            line.push_str(&format!(
                "\n    Time remaining: {} days {} hours",
                days_left, hours_left
            ));
            lines.push(line);
        }

        lines.join("\n")
    }

    pub fn get_cooldown_seconds(&self) -> i64 {
        COOLDOWN_DURATION_DAYS * 24 * 3600
    }

    pub fn get_banned_proxy_names(&self) -> Vec<String> {
        self.cleanup_expired_bans();
        let banned = self.banned_proxies.lock();
        banned.keys().cloned().collect()
    }

    pub fn get_banned_proxies(&self) -> Vec<HashMap<String, String>> {
        self.cleanup_expired_bans();
        let banned = self.banned_proxies.lock();
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
                    "unban_time".to_string(),
                    r.unban_time.format(TIME_FMT).to_string(),
                );
                m.insert(
                    "is_still_banned".to_string(),
                    r.is_still_banned().to_string(),
                );
                m.insert(
                    "days_until_unban".to_string(),
                    r.days_until_unban().to_string(),
                );
                if let Some(ref url) = r.proxy_url {
                    m.insert("proxy_url".to_string(), url.clone());
                }
                m
            })
            .collect()
    }

    pub fn get_banned_count(&self) -> usize {
        self.cleanup_expired_bans();
        self.banned_proxies.lock().len()
    }
}

impl ProxyBanManager {
    fn load_ban_records(&self) {
        if !self.ban_log_file.exists() {
            info!("No existing ban log found at {:?}", self.ban_log_file);
            return;
        }

        match fs::File::open(&self.ban_log_file) {
            Ok(file) => {
                let mut reader = csv::Reader::from_reader(file);
                let mut banned = self.banned_proxies.lock();
                for result in reader.records() {
                    if let Ok(record) = result {
                        let proxy_name = record.get(0).unwrap_or_default().to_string();
                        let ban_time_str = record.get(1).unwrap_or_default();
                        let unban_time_str = record.get(2).unwrap_or_default();

                        if let (Ok(ban_naive), Ok(unban_naive)) = (
                            NaiveDateTime::parse_from_str(ban_time_str, TIME_FMT),
                            NaiveDateTime::parse_from_str(unban_time_str, TIME_FMT),
                        ) {
                            let ban_time = ban_naive
                                .and_local_timezone(Local)
                                .single()
                                .unwrap_or_else(Local::now);
                            let unban_time = unban_naive
                                .and_local_timezone(Local)
                                .single()
                                .unwrap_or_else(Local::now);

                            banned.insert(
                                proxy_name.clone(),
                                ProxyBanRecord {
                                    proxy_name,
                                    ban_time,
                                    unban_time,
                                    proxy_url: None,
                                },
                            );
                        }
                    }
                }
                info!(
                    "Loaded {} ban records from {:?}",
                    banned.len(),
                    self.ban_log_file
                );
            }
            Err(e) => error!("Error loading ban records: {}", e),
        }
    }

    fn save_ban_records_inner(&self, banned: &HashMap<String, ProxyBanRecord>) {
        let result = (|| -> Result<(), Box<dyn std::error::Error>> {
            let file = fs::File::create(&self.ban_log_file)?;
            let mut writer = csv::Writer::from_writer(file);
            writer.write_record(["proxy_name", "ban_time", "unban_time"])?;
            for record in banned.values() {
                writer.write_record([
                    &record.proxy_name,
                    &record.ban_time.format(TIME_FMT).to_string(),
                    &record.unban_time.format(TIME_FMT).to_string(),
                ])?;
            }
            writer.flush()?;
            debug!(
                "Saved {} ban records to {:?}",
                banned.len(),
                self.ban_log_file
            );
            Ok(())
        })();

        if let Err(e) = result {
            error!("Error saving ban records: {}", e);
        }
    }

    fn cleanup_expired_bans(&self) {
        let mut banned = self.banned_proxies.lock();
        let expired: Vec<String> = banned
            .iter()
            .filter(|(_, r)| !r.is_still_banned())
            .map(|(k, _)| k.clone())
            .collect();

        if !expired.is_empty() {
            for name in &expired {
                banned.remove(name);
                info!("Removed expired ban record for proxy '{}'", name);
            }
            self.save_ban_records_inner(&banned);
        }
    }
}

// Global singleton
use once_cell::sync::OnceCell;
use std::sync::Arc;

static GLOBAL_BAN_MANAGER: OnceCell<Arc<ProxyBanManager>> = OnceCell::new();

pub fn get_ban_manager(ban_log_file: &str) -> Arc<ProxyBanManager> {
    GLOBAL_BAN_MANAGER
        .get_or_init(|| Arc::new(ProxyBanManager::new(ban_log_file.to_string())))
        .clone()
}

#[pyfunction]
#[pyo3(signature = (ban_log_file="reports/proxy_bans.csv"))]
pub fn get_global_ban_manager(ban_log_file: &str) -> ProxyBanManager {
    ProxyBanManager::new(ban_log_file.to_string())
}
