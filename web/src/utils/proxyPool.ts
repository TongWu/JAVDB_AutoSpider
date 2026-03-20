/**
 * Matches backend PROXY_POOL shape: { name?, http?, https? }[]
 */

export type ProxyWire = { name?: string; http?: string; https?: string };

export type ProxyEditorRow = {
  _id: string;
  name: string;
  scheme: "http" | "https" | "socks5" | "socks5h";
  host: string;
  port: string;
  username: string;
  password: string;
  sameForHttps: boolean;
  /** Used when HTTPS host/port differs from HTTP */
  httpsHost: string;
  httpsPort: string;
  priority: number;
};

function newId(): string {
  return crypto.randomUUID?.() ?? `p-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export function emptyProxyRow(priority = 0): ProxyEditorRow {
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

export function parseProxyUrl(urlStr: string): Partial<Pick<ProxyEditorRow, "scheme" | "host" | "port" | "username" | "password">> | null {
  const s = (urlStr ?? "").trim();
  if (!s) return null;
  try {
    const u = new URL(s);
    const scheme = u.protocol.replace(/:$/, "") as ProxyEditorRow["scheme"];
    const allowed: string[] = ["http", "https", "socks5", "socks5h"];
    const sc = allowed.includes(scheme) ? scheme : "http";
    const port = u.port || (sc === "https" ? "443" : "80");
    return {
      scheme: sc,
      host: u.hostname,
      port: String(port),
      username: u.username ? decodeURIComponent(u.username) : "",
      password: u.password ? decodeURIComponent(u.password) : "",
    };
  } catch {
    return null;
  }
}

export function wireToRow(item: ProxyWire, index: number): ProxyEditorRow {
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

export function poolFromWire(list: unknown): ProxyEditorRow[] {
  if (!Array.isArray(list)) return [emptyProxyRow(0)];
  const rows = list
    .filter((x): x is ProxyWire => x !== null && typeof x === "object")
    .map((item, i) => wireToRow(item as ProxyWire, i));
  return rows.length ? rows : [emptyProxyRow(0)];
}

function buildUrl(
  scheme: ProxyEditorRow["scheme"],
  host: string,
  port: string,
  username: string,
  password: string,
): string {
  const h = host.trim();
  const p = (port.trim() || (scheme === "https" ? "443" : "80")).replace(/^:/, "");
  if (!h) return "";
  let auth = "";
  if (username || password) {
    auth = `${encodeURIComponent(username)}:${encodeURIComponent(password)}@`;
  }
  return `${scheme}://${auth}${h}:${p}`;
}

export function rowToWire(r: ProxyEditorRow): ProxyWire {
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

export function poolToWire(rows: ProxyEditorRow[]): ProxyWire[] {
  return [...rows]
    .sort((a, b) => (a.priority ?? 0) - (b.priority ?? 0))
    .map(rowToWire)
    .filter((w) => w.http || w.https);
}
