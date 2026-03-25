import { useAuthStore } from "../stores/auth";

type ReqInit = RequestInit & { skipAuth?: boolean };

export async function apiFetch(path: string, init: ReqInit = {}) {
  const auth = useAuthStore();
  const headers = new Headers(init.headers || {});
  headers.set("Content-Type", "application/json");
  if (!init.skipAuth && auth.accessToken) {
    headers.set("Authorization", `Bearer ${auth.accessToken}`);
    if (["POST", "PUT", "DELETE"].includes((init.method ?? "GET").toUpperCase())) {
      headers.set("X-CSRF-Token", auth.csrfToken);
    }
  }
  const res = await fetch(`${auth.apiBase}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data.detail || data.message || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}
