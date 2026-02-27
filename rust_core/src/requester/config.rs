use pyo3::prelude::*;

#[pyclass(name = "RustRequestConfig")]
#[derive(Clone, Debug)]
pub struct RequestConfig {
    #[pyo3(get, set)]
    pub base_url: String,
    #[pyo3(get, set)]
    pub cf_bypass_service_port: u16,
    #[pyo3(get, set)]
    pub cf_bypass_enabled: bool,
    #[pyo3(get, set)]
    pub cf_bypass_max_failures: u32,
    #[pyo3(get, set)]
    pub cf_turnstile_cooldown: u64,
    #[pyo3(get, set)]
    pub fallback_cooldown: u64,
    #[pyo3(get, set)]
    pub javdb_session_cookie: Option<String>,
    #[pyo3(get, set)]
    pub proxy_http: Option<String>,
    #[pyo3(get, set)]
    pub proxy_https: Option<String>,
    #[pyo3(get, set)]
    pub proxy_modules: Vec<String>,
    #[pyo3(get, set)]
    pub proxy_mode: String,
    #[pyo3(get, set)]
    pub use_curl_cffi: bool,
    #[pyo3(get, set)]
    pub curl_cffi_impersonate: String,
}

#[pymethods]
impl RequestConfig {
    #[new]
    #[pyo3(signature = (
        base_url="https://javdb.com".to_string(),
        cf_bypass_service_port=8000,
        cf_bypass_enabled=true,
        cf_bypass_max_failures=3,
        cf_turnstile_cooldown=10,
        fallback_cooldown=30,
        javdb_session_cookie=None,
        proxy_http=None,
        proxy_https=None,
        proxy_modules=None,
        proxy_mode="single".to_string(),
        use_curl_cffi=true,
        curl_cffi_impersonate="chrome131".to_string(),
    ))]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        base_url: String,
        cf_bypass_service_port: u16,
        cf_bypass_enabled: bool,
        cf_bypass_max_failures: u32,
        cf_turnstile_cooldown: u64,
        fallback_cooldown: u64,
        javdb_session_cookie: Option<String>,
        proxy_http: Option<String>,
        proxy_https: Option<String>,
        proxy_modules: Option<Vec<String>>,
        proxy_mode: String,
        use_curl_cffi: bool,
        curl_cffi_impersonate: String,
    ) -> Self {
        Self {
            base_url,
            cf_bypass_service_port,
            cf_bypass_enabled,
            cf_bypass_max_failures,
            cf_turnstile_cooldown,
            fallback_cooldown,
            javdb_session_cookie,
            proxy_http,
            proxy_https,
            proxy_modules: proxy_modules.unwrap_or_else(|| vec!["all".to_string()]),
            proxy_mode,
            use_curl_cffi,
            curl_cffi_impersonate,
        }
    }
}

impl Default for RequestConfig {
    fn default() -> Self {
        Self {
            base_url: "https://javdb.com".to_string(),
            cf_bypass_service_port: 8000,
            cf_bypass_enabled: true,
            cf_bypass_max_failures: 3,
            cf_turnstile_cooldown: 10,
            fallback_cooldown: 30,
            javdb_session_cookie: None,
            proxy_http: None,
            proxy_https: None,
            proxy_modules: vec!["all".to_string()],
            proxy_mode: "single".to_string(),
            use_curl_cffi: true,
            curl_cffi_impersonate: "chrome131".to_string(),
        }
    }
}
