import { createI18n } from "vue-i18n";
import { watch } from "vue";
import en from "../locales/en";
import zhCN from "../locales/zh-CN";

export const LOCALE_STORAGE_KEY = "javdb_ui_locale";

export type AppLocale = "zh-CN" | "en";

export function detectInitialLocale(): AppLocale {
  if (typeof localStorage === "undefined") return "en";
  const saved = localStorage.getItem(LOCALE_STORAGE_KEY);
  if (saved === "zh-CN" || saved === "en") return saved;
  if (typeof navigator === "undefined") return "en";
  const nav = (navigator.language || "").toLowerCase();
  if (nav.startsWith("zh")) return "zh-CN";
  return "en";
}

export const i18n = createI18n({
  legacy: false,
  locale: detectInitialLocale(),
  fallbackLocale: "en",
  globalInjection: true,
  messages: {
    "zh-CN": zhCN,
    en,
  },
});

/** Persist manual locale choice and keep <html lang> in sync. */
export function syncLocaleDocumentAndStorage(): void {
  watch(
    () => i18n.global.locale.value,
    (loc) => {
      const code = String(loc);
      if (typeof localStorage !== "undefined") {
        localStorage.setItem(LOCALE_STORAGE_KEY, code);
      }
      if (typeof document !== "undefined") {
        document.documentElement.lang = code === "zh-CN" ? "zh-CN" : "en";
      }
    },
    { immediate: true },
  );
}
