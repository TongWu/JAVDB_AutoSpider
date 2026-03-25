import { defineStore } from "pinia";
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8100";
const DESKTOP_API_BASE = globalThis.desktopEnv?.apiBase ?? "";
function jwtSub(token) {
    try {
        const part = token.split(".")[1];
        if (!part)
            return "user";
        let b64 = part.replace(/-/g, "+").replace(/_/g, "/");
        const remainder = b64.length % 4;
        if (remainder) {
            b64 += "=".repeat(4 - remainder);
        }
        const json = JSON.parse(atob(b64));
        return json.sub ?? "user";
    }
    catch {
        return "user";
    }
}
export const useAuthStore = defineStore("auth", {
    state: () => ({
        accessToken: "",
        refreshToken: "",
        csrfToken: "",
        role: localStorage.getItem("role") ?? "readonly",
        username: localStorage.getItem("username") ?? "",
        apiBase: DESKTOP_API_BASE || API_BASE,
    }),
    actions: {
        setSession(resp) {
            if (resp.access_token != null) {
                this.accessToken = resp.access_token;
            }
            if (resp.refresh_token != null) {
                this.refreshToken = resp.refresh_token;
            }
            if (resp.csrf_token != null) {
                this.csrfToken = resp.csrf_token;
            }
            if (resp.role != null) {
                this.role = resp.role;
                localStorage.setItem("role", resp.role);
            }
            else {
                localStorage.removeItem("role");
            }
            if (resp.username != null) {
                this.username = resp.username;
            }
            else if (resp.access_token != null) {
                this.username = jwtSub(resp.access_token);
            }
            if (this.username) {
                localStorage.setItem("username", this.username);
            }
            else {
                localStorage.removeItem("username");
            }
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
