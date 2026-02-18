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

    let length = v.len();
    if length <= 2 {
        return "*".repeat(length);
    }
    if length == 3 {
        return format!("{}*{}", &v[..1], &v[length - 1..]);
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

    format!(
        "{}{}{}",
        &v[..actual_start],
        "*".repeat(actual_mask),
        &v[length - actual_end..]
    )
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
        let user = caps.get(2);
        let host = &caps[4];
        let port = &caps[5];
        let suffix = caps.get(6).map_or("", |m| m.as_str());

        let masked_host = mask_ip_address(Some(host));
        if user.is_some() {
            format!("{protocol}***:***@{masked_host}:{port}{suffix}")
        } else {
            format!("{protocol}{masked_host}:{port}{suffix}")
        }
    } else {
        mask_partial(Some(url), 10, 5, 2)
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
            return format!("{}.***.{}", &parts[0][..parts[0].len().min(3)], {
                let domain = parts[1];
                if domain.len() > 4 {
                    &domain[domain.len() - 4..]
                } else {
                    domain
                }
            });
        }
    }

    mask_partial(Some(s), 3, 3, 2)
}

/// Mask proxy URL for logging (used by proxy_pool)
pub fn mask_proxy_url_internal(url: Option<&str>) -> String {
    match url {
        Some(u) if !u.is_empty() => {
            let re = Regex::new(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})").unwrap();
            let mut result = u.to_string();

            if result.contains('@') {
                if let Some(at_pos) = result.find('@') {
                    if let Some(proto_end) = result.find("://") {
                        result = format!("{}{}", &result[..proto_end + 3], &result[at_pos + 1..]);
                    }
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
        assert!(result.contains("***:***@"));
        assert!(result.contains("xxx.xxx"));
    }
}
