import { defineStore } from "pinia";

type LoginResp = {
  access_token: string;
  refresh_token: string;
  csrf_token: string;
  role: "admin" | "readonly";
  expires_in: number;
  username?: string;
};

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8100";
const DESKTOP_API_BASE = (globalThis as { desktopEnv?: { apiBase?: string } }).desktopEnv?.apiBase ?? "";

function jwtSub(token: string): string {
  try {
    const part = token.split(".")[1];
    if (!part) return "user";
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/");
    const json = JSON.parse(atob(b64)) as { sub?: string };
    return json.sub ?? "user";
  } catch {
    return "user";
  }
}

export const useAuthStore = defineStore("auth", {
  state: () => ({
    accessToken: "",
    refreshToken: "",
    csrfToken: "",
    role: (localStorage.getItem("role") as "admin" | "readonly") ?? "readonly",
    username: localStorage.getItem("username") ?? "",
    apiBase: DESKTOP_API_BASE || API_BASE,
  }),
  actions: {
    setSession(resp: LoginResp) {
      this.accessToken = resp.access_token;
      this.refreshToken = resp.refresh_token;
      this.csrfToken = resp.csrf_token;
      this.role = resp.role;
      this.username = resp.username || jwtSub(resp.access_token);
      localStorage.setItem("role", resp.role);
      localStorage.setItem("username", this.username);
    },
    clearSession() {
      this.accessToken = "";
      this.refreshToken = "";
      this.csrfToken = "";
      this.role = "readonly";
      this.username = "";
      localStorage.removeItem("role");
      localStorage.removeItem("username");
    },
  },
});
