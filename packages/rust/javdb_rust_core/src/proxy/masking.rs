use pyo3::prelude::*;
use regex::Regex;

#[pyfunction]
#[pyo3(signature = (value=None))]
pub fn mask_full(value: Option<&str>) -> String {
    match value {
        Some(v) if !v.is_empty() => "********".to_string(),
        _ => "None".to_string(),
    }
}

#[pyfunction]
#[pyo3(signature = (value, show_start=2, show_end=2, min_masked=2))]
pub fn mask_partial(
    value: Option<&str>,
    show_start: usize,
    show_end: usize,
    min_masked: usize,
) -> String {
    let v = match value {
        Some(s) if !s.is_empty() => s,
        _ => return "None".to_string(),
    };

    let chars: Vec<char> = v.chars().collect();
    let length = chars.len();
    if length <= 2 {
        return "*".repeat(length);
    }
    if length == 3 {
        return format!("{}*{}", chars[0], chars[length - 1]);
    }

    let chars_to_mask = length.saturating_sub(show_start + show_end);

    let (actual_start, actual_end, actual_mask) = if chars_to_mask < min_masked {
        let actual_masked = min_masked.min(length - 2);
        let total_visible = length - actual_masked;
        let s = show_start.min(1.max(total_visible.saturating_sub(1)));
        let e = 1.max(total_visible.saturating_sub(s));
        (s, e, length - s - e)
    } else {
        (show_start, show_end, chars_to_mask)
    };

    let start: String = chars[..actual_start].iter().collect();
    let end: String = chars[length - actual_end..].iter().collect();
    format!("{}{}{}", start, "*".repeat(actual_mask), end)
}

#[pyfunction]
#[pyo3(signature = (email=None))]
pub fn mask_email(email: Option<&str>) -> String {
    let e = match email {
        Some(s) if !s.is_empty() => s,
        _ => return "None".to_string(),
    };

    if !e.contains('@') {
        return mask_partial(Some(e), 2, 2, 2);
    }

    let parts: Vec<&str> = e.rsplitn(2, '@').collect();
    let domain = parts[0];
    let local = parts[1];

    let masked_local = mask_partial(Some(local), 2, 2, 2);
    let masked_domain = mask_partial(Some(domain), 2, 3, 2);

    format!("{masked_local}@{masked_domain}")
}

#[pyfunction]
#[pyo3(signature = (host=None))]
pub fn mask_ip_address(host: Option<&str>) -> String {
    let h = match host {
        Some(s) if !s.is_empty() => s,
        _ => return "None".to_string(),
    };

    let ip_re = Regex::new(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$").unwrap();
    if let Some(caps) = ip_re.captures(h) {
        return format!("{}.xxx.xxx.{}", &caps[1], &caps[4]);
    }

    let url_ip_re =
        Regex::new(r"^(https?://)?(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(:\d+)?(.*)$")
            .unwrap();
    if let Some(caps) = url_ip_re.captures(h) {
        let prefix = caps.get(1).map_or("", |m| m.as_str());
        let port = caps.get(6).map_or("", |m| m.as_str());
        let suffix = caps.get(7).map_or("", |m| m.as_str());
        return format!("{prefix}{}.xxx.xxx.{}{port}{suffix}", &caps[2], &caps[5]);
    }

    mask_partial(Some(h), 2, 3, 2)
}

#[pyfunction]
#[pyo3(signature = (proxy_url=None))]
pub fn mask_proxy_url(proxy_url: Option<&str>) -> String {
    let url = match proxy_url {
        Some(s) if !s.is_empty() => s,
        _ => return "None".to_string(),
    };

    let re = Regex::new(r"^(https?://)(?:([^:]+):([^@]+)@)?([^:]+):(\d+)(.*)$").unwrap();
    if let Some(caps) = re.captures(url) {
        let protocol = &caps[1];
        let host = &caps[4];
        let port = &caps[5];
        let suffix = caps.get(6).map_or("", |m| m.as_str());

        let ip_re = Regex::new(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$").unwrap();
        let masked_host = if ip_re.is_match(host) {
            mask_ip_address(Some(host))
        } else {
            host.to_string()
        };
        format!("{protocol}{masked_host}:{port}{suffix}")
    } else {
        let cleaned = if let Some(at_pos) = url.find('@') {
            if let Some(proto_end) = url.find("://") {
                format!("{}{}", &url[..proto_end + 3], &url[at_pos + 1..])
            } else {
                url[at_pos + 1..].to_string()
            }
        } else {
            url.to_string()
        };
        mask_partial(Some(&cleaned), 10, 5, 2)
    }
}

#[pyfunction]
#[pyo3(signature = (username=None, show_start=2, show_end=2))]
pub fn mask_username(username: Option<&str>, show_start: usize, show_end: usize) -> String {
    mask_partial(username, show_start, show_end, 2)
}

#[pyfunction]
#[pyo3(signature = (server=None))]
pub fn mask_server(server: Option<&str>) -> String {
    let s = match server {
        Some(v) if !v.is_empty() => v,
        _ => return "None".to_string(),
    };

    let ip_re = Regex::new(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(:\d+)?$").unwrap();
    if let Some(caps) = ip_re.captures(s) {
        let port = caps.get(5).map_or("", |m| m.as_str());
        return format!("{}.xxx.xxx.{}{}", &caps[1], &caps[4], port);
    }

    let url_ip_re =
        Regex::new(r"^(https?://)(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(:\d+)?(.*)$")
            .unwrap();
    if let Some(caps) = url_ip_re.captures(s) {
        let prefix = &caps[1];
        let port = caps.get(6).map_or("", |m| m.as_str());
        let suffix = caps.get(7).map_or("", |m| m.as_str());
        return format!("{prefix}{}.xxx.xxx.{}{port}{suffix}", &caps[2], &caps[5]);
    }

    if s.contains('.') {
        let parts: Vec<&str> = s.splitn(2, '.').collect();
        if parts.len() == 2 {
            let prefix_chars: String = parts[0].chars().take(3).collect();
            let domain = parts[1];
            let domain_chars: Vec<char> = domain.chars().collect();
            let domain_suffix: String = if domain_chars.len() > 4 {
                domain_chars[domain_chars.len() - 4..].iter().collect()
            } else {
                domain.to_string()
            };
            return format!("{}.***.{}", prefix_chars, domain_suffix);
        }
    }

    mask_partial(Some(s), 3, 3, 2)
}

#[pyfunction]
#[pyo3(signature = (error_msg=None))]
pub fn mask_error(error_msg: Option<&str>) -> String {
    let msg = match error_msg {
        Some(s) if !s.is_empty() => s,
        _ => return "None".to_string(),
    };

    let mut result = msg.to_string();

    // 1. Mask proxy URLs (http[s]://user:pass@host:port...)
    let proxy_re = Regex::new(r"https?://[^:]+:[^@]+@[^\s/:]+:\d+").unwrap();
    result = proxy_re
        .replace_all(&result, |caps: &regex::Captures| {
            mask_proxy_url(Some(caps.get(0).unwrap().as_str()))
        })
        .to_string();

    // 2. Mask session cookie values (_jdb_session=<value>)
    let cookie_re = Regex::new(r"(_jdb_session=)\S+").unwrap();
    result = cookie_re.replace_all(&result, "${1}********").to_string();

    // 3. Mask remaining bare IP addresses (skip already-masked xxx.xxx)
    let ip_re = Regex::new(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b").unwrap();
    result = ip_re
        .replace_all(&result, |caps: &regex::Captures| {
            let ip = caps.get(0).unwrap().as_str();
            if ip.contains("xxx") {
                ip.to_string()
            } else {
                mask_ip_address(Some(ip))
            }
        })
        .to_string();

    result
}

/// Mask proxy URL for logging (used by proxy_pool)
pub fn mask_proxy_url_internal(url: Option<&str>) -> String {
    match url {
        Some(u) if !u.is_empty() => {
            let re = Regex::new(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})").unwrap();
            let mut result = u.to_string();

            if let Some(at_pos) = result.find('@') {
                if let Some(proto_end) = result.find("://") {
                    result = format!("{}{}", &result[..proto_end + 3], &result[at_pos + 1..]);
                } else {
                    result = result[at_pos + 1..].to_string();
                }
            }

            result = re
                .replace_all(&result, |caps: &regex::Captures| {
                    format!("{}.xxx.xxx.{}", &caps[1], &caps[4])
                })
                .to_string();
            result
        }
        _ => "None".to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mask_full() {
        assert_eq!(mask_full(Some("secret")), "********");
        assert_eq!(mask_full(None), "None");
        assert_eq!(mask_full(Some("")), "None");
    }

    #[test]
    fn test_mask_ip() {
        assert_eq!(mask_ip_address(Some("192.168.1.100")), "192.xxx.xxx.100");
        assert_eq!(mask_ip_address(None), "None");
    }

    #[test]
    fn test_mask_proxy_url() {
        let result = mask_proxy_url(Some("http://user:pass@192.168.1.1:8080"));
        assert!(!result.contains("@"));
        assert!(result.contains("xxx.xxx"));
        assert!(result.starts_with("http://"));
    }

    #[test]
    fn test_mask_error_preserves_structure() {
        let msg = "HTTPSConnectionPool(host='javdb.com'): Max retries exceeded";
        let result = mask_error(Some(msg));
        assert!(result.contains("HTTPSConnectionPool"));
        assert!(result.contains("Max retries exceeded"));
    }

    #[test]
    fn test_mask_error_scrubs_proxy_url() {
        let msg = "ProxyError: Cannot connect to proxy http://tedwu:secret@192.168.1.1:8080";
        let result = mask_error(Some(msg));
        assert!(result.contains("ProxyError"));
        assert!(!result.contains("tedwu"));
        assert!(!result.contains("secret"));
        assert!(result.contains("xxx.xxx"));
    }

    #[test]
    fn test_mask_error_scrubs_domain_proxy_url() {
        let msg = "ProxyError: Cannot connect to proxy http://tedwu:secret@proxy.example.com:8080";
        let result = mask_error(Some(msg));
        assert!(result.contains("ProxyError"));
        assert!(!result.contains("tedwu"));
        assert!(!result.contains("secret"));
        assert!(!result.contains("@"));
    }

    #[test]
    fn test_mask_error_scrubs_bare_ip() {
        let msg = "ConnectionError: connect to 10.0.0.5 timed out";
        let result = mask_error(Some(msg));
        assert!(result.contains("ConnectionError"));
        assert!(result.contains("xxx.xxx"));
        assert!(!result.contains("0.0.5"));
    }

    #[test]
    fn test_mask_error_scrubs_cookie() {
        let msg = "Cookie rejected: _jdb_session=abc123secret";
        let result = mask_error(Some(msg));
        assert!(result.contains("Cookie rejected"));
        assert!(!result.contains("abc123secret"));
        assert!(result.contains("_jdb_session=********"));
    }

    #[test]
    fn test_mask_error_none() {
        assert_eq!(mask_error(None), "None");
        assert_eq!(mask_error(Some("")), "None");
    }
}
