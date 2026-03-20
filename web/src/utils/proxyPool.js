/**
 * 与后端 PROXY_POOL 一致：{ name?, http?, https? }[]
 */
function newId() {
    return crypto.randomUUID?.() ?? `p-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}
export function emptyProxyRow(priority = 0) {
    return {
        _id: newId(),
        name: "",
        scheme: "http",
        host: "",
        port: "8080",
        username: "",
        password: "",
        sameForHttps: true,
        httpsHost: "",
        httpsPort: "",
        priority,
    };
}
export function parseProxyUrl(urlStr) {
    const s = (urlStr ?? "").trim();
    if (!s)
        return null;
    try {
        const u = new URL(s);
        const scheme = u.protocol.replace(/:$/, "");
        const allowed = ["http", "https", "socks5", "socks5h"];
        const sc = allowed.includes(scheme) ? scheme : "http";
        const port = u.port || (sc === "https" ? "443" : "80");
        return {
            scheme: sc,
            host: u.hostname,
            port: String(port),
            username: u.username ? decodeURIComponent(u.username) : "",
            password: u.password ? decodeURIComponent(u.password) : "",
        };
    }
    catch {
        return null;
    }
}
export function wireToRow(item, index) {
    const row = emptyProxyRow(index * 10);
    row.name = typeof item.name === "string" ? item.name : "";
    const httpStr = item.http?.trim() ?? "";
    const httpsStr = item.https?.trim() ?? "";
    const primary = httpStr || httpsStr;
    const parsedP = parseProxyUrl(primary);
    if (parsedP) {
        row.scheme = parsedP.scheme ?? "http";
        row.host = parsedP.host ?? "";
        row.port = parsedP.port ?? "8080";
        row.username = parsedP.username ?? "";
        row.password = parsedP.password ?? "";
    }
    if (httpStr && httpsStr && httpStr !== httpsStr) {
        const parsedH = parseProxyUrl(httpsStr);
        row.sameForHttps = false;
        if (parsedH) {
            row.httpsHost = parsedH.host ?? "";
            row.httpsPort = parsedH.port ?? "443";
        }
    }
    return row;
}
export function poolFromWire(list) {
    if (!Array.isArray(list))
        return [emptyProxyRow(0)];
    const rows = list
        .filter((x) => x !== null && typeof x === "object")
        .map((item, i) => wireToRow(item, i));
    return rows.length ? rows : [emptyProxyRow(0)];
}
function buildUrl(scheme, host, port, username, password) {
    const h = host.trim();
    const p = (port.trim() || (scheme === "https" ? "443" : "80")).replace(/^:/, "");
    if (!h)
        return "";
    let auth = "";
    if (username || password) {
        auth = `${encodeURIComponent(username)}:${encodeURIComponent(password)}@`;
    }
    return `${scheme}://${auth}${h}:${p}`;
}
export function rowToWire(r) {
    const http = buildUrl(r.scheme, r.host, r.port, r.username, r.password);
    const hHost = (r.httpsHost || r.host).trim();
    const hPort = (r.httpsPort || r.port).trim() || "443";
    const https = r.sameForHttps ? http : buildUrl(r.scheme, hHost, hPort, r.username, r.password);
    return {
        name: r.name.trim() || undefined,
        http: http || undefined,
        https: https || undefined,
    };
}
export function poolToWire(rows) {
    return [...rows]
        .sort((a, b) => (a.priority ?? 0) - (b.priority ?? 0))
        .map(rowToWire)
        .filter((w) => w.http || w.https);
}
