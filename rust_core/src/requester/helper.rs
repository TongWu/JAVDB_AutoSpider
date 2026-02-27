use log::{debug, warn};
use pyo3::prelude::*;
use pyo3::conversion::ToPyObject;
use std::collections::HashMap;

use crate::proxy::pool::ProxyPool;

#[pyclass(name = "RustProxyHelper")]
pub struct ProxyHelper {
    proxy_pool: Option<Py<ProxyPool>>,
    proxy_modules: Vec<String>,
    proxy_mode: String,
    proxy_http: Option<String>,
    proxy_https: Option<String>,
}

#[pymethods]
impl ProxyHelper {
    #[new]
    #[pyo3(signature = (proxy_pool=None, proxy_modules=None, proxy_mode="single".to_string(), proxy_http=None, proxy_https=None))]
    pub fn new(
        proxy_pool: Option<Py<ProxyPool>>,
        proxy_modules: Option<Vec<String>>,
        proxy_mode: String,
        proxy_http: Option<String>,
        proxy_https: Option<String>,
    ) -> Self {
        Self {
            proxy_pool,
            proxy_modules: proxy_modules.unwrap_or_else(|| vec!["all".to_string()]),
            proxy_mode,
            proxy_http,
            proxy_https,
        }
    }

    pub fn should_use_proxy_for_module(&self, module_name: &str, use_proxy_flag: bool) -> bool {
        if !use_proxy_flag {
            return false;
        }
        if self.proxy_modules.is_empty() {
            return false;
        }
        if self.proxy_modules.contains(&"all".to_string()) {
            return true;
        }
        self.proxy_modules.contains(&module_name.to_string())
    }

    pub fn get_proxies_dict(
        &self,
        module_name: &str,
        use_proxy_flag: bool,
    ) -> Option<HashMap<String, String>> {
        if !self.should_use_proxy_for_module(module_name, use_proxy_flag) {
            return None;
        }

        if (self.proxy_mode == "pool" || self.proxy_mode == "single")
            && self.proxy_pool.is_some()
        {
            let proxies = Python::with_gil(|py| {
                self.proxy_pool
                    .as_ref()
                    .unwrap()
                    .borrow(py)
                    .get_current_proxy()
            });
            if proxies.is_some() {
                let name = self.get_current_proxy_name();
                debug!(
                    "[{}] Using proxy mode '{}' - Current proxy: {}",
                    module_name, self.proxy_mode, name
                );
            } else {
                warn!(
                    "[{}] Proxy mode '{}' enabled but no proxy available",
                    module_name, self.proxy_mode
                );
            }
            return proxies;
        }

        if self.proxy_http.is_none() && self.proxy_https.is_none() {
            return None;
        }

        let mut proxies = HashMap::new();
        if let Some(ref http) = self.proxy_http {
            proxies.insert("http".to_string(), http.clone());
        }
        if let Some(ref https) = self.proxy_https {
            proxies.insert("https".to_string(), https.clone());
        }
        debug!("[{}] Using single proxy", module_name);
        Some(proxies)
    }

    pub fn get_current_proxy_name(&self) -> String {
        if let Some(ref pool) = self.proxy_pool {
            Python::with_gil(|py| pool.borrow(py).get_current_proxy_name())
        } else if self.proxy_http.is_some() || self.proxy_https.is_some() {
            "Legacy-Proxy".to_string()
        } else {
            "None".to_string()
        }
    }

    pub fn mark_success(&self) {
        if let Some(ref pool) = self.proxy_pool {
            Python::with_gil(|py| pool.borrow(py).mark_success());
        }
    }

    pub fn mark_failure_and_switch(&self) -> bool {
        if let Some(ref pool) = self.proxy_pool {
            Python::with_gil(|py| pool.borrow(py).mark_failure_and_switch())
        } else {
            false
        }
    }

    pub fn get_statistics(&self) -> HashMap<String, PyObject> {
        if let Some(ref pool) = self.proxy_pool {
            Python::with_gil(|py| pool.borrow(py).get_statistics())
        } else {
            Python::with_gil(|py| {
                let mut stats = HashMap::new();
                let has_legacy = self.proxy_http.is_some() || self.proxy_https.is_some();
                let count: u32 = if has_legacy { 1 } else { 0 };
                stats.insert("total_proxies".to_string(), count.to_object(py));
                stats.insert("available_proxies".to_string(), count.to_object(py));
                stats.insert("in_cooldown".to_string(), 0u32.to_object(py));
                stats.insert("no_proxy_mode".to_string(), false.to_object(py));
                let empty: Vec<HashMap<String, PyObject>> = vec![];
                stats.insert("proxies".to_string(), empty.to_object(py));
                stats
            })
        }
    }
}

#[pyfunction]
#[pyo3(signature = (proxy_pool=None, proxy_modules=None, proxy_mode="single".to_string(), proxy_http=None, proxy_https=None))]
pub fn create_proxy_helper_from_config(
    proxy_pool: Option<Py<ProxyPool>>,
    proxy_modules: Option<Vec<String>>,
    proxy_mode: String,
    proxy_http: Option<String>,
    proxy_https: Option<String>,
) -> ProxyHelper {
    ProxyHelper::new(proxy_pool, proxy_modules, proxy_mode, proxy_http, proxy_https)
}
