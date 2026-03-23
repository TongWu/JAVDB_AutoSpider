<template>
  <div class="page-shell">
    <header class="page-head">
      <div>
        <h1 class="page-head__title">{{ t("explore.title") }}</h1>
        <p class="page-head__sub">{{ t("explore.subtitle") }}</p>
      </div>
    </header>

    <div class="card mdc-card">
      <div class="browser-chrome">
        <div class="browser-chrome__top">
          <div class="traffic-lights">
            <span class="tl tl--red"></span>
            <span class="tl tl--yellow"></span>
            <span class="tl tl--green"></span>
          </div>
          <div class="browser-tab">{{ t("explore.title") }} - javdb.com</div>
        </div>
        <div class="browser-chrome__bar">
          <button type="button" class="chrome-btn" :disabled="!canGoBack" @click="goBack">◀</button>
          <button type="button" class="chrome-btn" :disabled="!canGoForward" @click="goForward">▶</button>
          <button type="button" class="chrome-btn" @click="reloadFrame">↻</button>
          <input v-model.trim="urlInput" class="chrome-omnibox" type="url" :placeholder="t('explore.urlPlaceholder')" @keyup.enter="openUrl" />
          <button type="button" @click="openUrl">{{ t("explore.openAndParse") }}</button>
          <button type="button" class="ghost" :disabled="!canJumpAdhoc" @click="jumpToAdhoc(urlInput)">
            {{ t("explore.jumpToAdhoc") }}
          </button>
        </div>
      </div>
      <div class="explore-toolbar explore-toolbar--secondary">
        <input v-model.trim="cookieInput" type="text" :placeholder="t('explore.cookiePlaceholder')" />
        <button type="button" class="ghost" @click="syncCookie">{{ t("explore.syncCookie") }}</button>
        <span class="job-meta" style="margin: 0">{{ currentBrowsingUrl }}</span>
      </div>
      <p v-if="message" :class="['job-meta', messageIsError ? 'form-error' : '']">{{ message }}</p>
      <div class="explore-browser-wrap">
        <webview
          v-if="isElectron"
          ref="webviewRef"
          :src="webviewSrc"
          class="explore-browser-iframe"
          allowpopups
        />
        <iframe v-else ref="myIframeRef" :key="frameKey" :src="iframeSrc" class="explore-browser-iframe" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { apiFetch } from "../lib/api";
import { useAuthStore } from "../stores/auth";

const { t } = useI18n();
const router = useRouter();
const auth = useAuthStore();
const isElectron = !!window.desktopEnv?.isElectron;
const urlInput = ref("https://javdb.com");
const cookieInput = ref("");
const message = ref("");
const messageIsError = ref(false);
const currentBrowsingUrl = ref("");
const iframeSrc = ref("");
const frameKey = ref(0);
const webviewSrc = ref("https://javdb.com");
const webviewRef = ref<any>(null);
const myIframeRef = ref<HTMLIFrameElement | null>(null);
const historyStack = ref<string[]>([]);
const historyIndex = ref(-1);
const canJumpAdhoc = computed(() => isJavdbUrl(urlInput.value) && !isDetailUrl(urlInput.value));
const canGoBack = computed(() => historyIndex.value > 0);
const canGoForward = computed(() => historyIndex.value >= 0 && historyIndex.value < historyStack.value.length - 1);

function isJavdbUrl(url: string): boolean {
  return /^https?:\/\/([^/]+\.)?javdb\.com(\/|$)/i.test(url);
}

function isDetailUrl(url: string): boolean {
  try {
    const p = new URL(url).pathname;
    return /^\/v\/[^/]+/.test(p);
  } catch {
    return false;
  }
}

function normalizeHref(href: string): string {
  const raw = String(href || "").trim();
  if (!raw) return "https://javdb.com";
  let candidate = raw;
  if (!/^https?:\/\//i.test(candidate)) {
    if (/^(www\.)?javdb\.com(\/|$)/i.test(candidate)) {
      candidate = `https://${candidate}`;
    } else if (candidate.startsWith("/")) {
      candidate = `https://javdb.com${candidate}`;
    } else {
      candidate = `https://javdb.com/${candidate}`;
    }
  }
  try {
    const parsed = new URL(candidate);
    const isHttp = parsed.protocol === "http:" || parsed.protocol === "https:";
    const isJavdb = /(^|\.)javdb\.com$/i.test(parsed.hostname);
    if (!isHttp || !isJavdb) return "https://javdb.com";
    return parsed.toString();
  } catch {
    return "https://javdb.com";
  }
}

async function syncCookie() {
  message.value = "";
  messageIsError.value = false;
  try {
    await apiFetch("/api/explore/sync-cookie", {
      method: "POST",
      body: JSON.stringify({ cookie: cookieInput.value }),
    });
    message.value = t("explore.cookieSynced");
  } catch (e: unknown) {
    message.value = e instanceof Error ? e.message : String(e);
    messageIsError.value = true;
  }
}

function jumpToAdhoc(url: string) {
  void router.push({
    path: "/adhoc",
    query: {
      url,
    },
  });
}

function buildIframeSrc(targetUrl: string): string {
  const safe = normalizeHref(targetUrl);
  const q = new URLSearchParams({
    url: safe,
  });
  return `${auth.apiBase}/api/explore/proxy-page?${q.toString()}`;
}

function appendHistory(url: string) {
  if (!url) return;
  if (historyStack.value[historyIndex.value] === url) return;
  historyStack.value = historyStack.value.slice(0, historyIndex.value + 1);
  historyStack.value.push(url);
  historyIndex.value = historyStack.value.length - 1;
}

function openUrl() {
  const safe = normalizeHref(urlInput.value);
  if (!isJavdbUrl(safe)) {
    message.value = t("explore.urlInvalid");
    messageIsError.value = true;
    return;
  }
  message.value = "";
  messageIsError.value = false;
  urlInput.value = safe;
  if (isElectron) {
    webviewSrc.value = safe;
    appendHistory(safe);
    return;
  }
  iframeSrc.value = buildIframeSrc(safe);
  appendHistory(safe);
}

function goBack() {
  if (!canGoBack.value) return;
  if (isElectron && webviewRef.value) {
    webviewRef.value.goBack();
    return;
  }
  historyIndex.value -= 1;
  const url = historyStack.value[historyIndex.value];
  urlInput.value = url;
  iframeSrc.value = buildIframeSrc(url);
}

function goForward() {
  if (!canGoForward.value) return;
  if (isElectron && webviewRef.value) {
    webviewRef.value.goForward();
    return;
  }
  historyIndex.value += 1;
  const url = historyStack.value[historyIndex.value];
  urlInput.value = url;
  iframeSrc.value = buildIframeSrc(url);
}

function reloadFrame() {
  if (isElectron && webviewRef.value) {
    webviewRef.value.reload();
    return;
  }
  frameKey.value += 1;
}

function buildEnhancerScript() {
  return `
(() => {
  if (window.__JAVDB_EXPLORE_ENHANCED__) return;
  window.__JAVDB_EXPLORE_ENHANCED__ = true;
  // Security hardening: do not inject host auth token or privileged APIs into guest pages.
})();
`;
}

function bindWebviewEvents() {
  if (!isElectron || !webviewRef.value) return;
  const wv = webviewRef.value;
  const syncHistoryFromNavigation = (url: string) => {
    if (!url) return;
    const prev = historyStack.value[historyIndex.value - 1];
    const next = historyStack.value[historyIndex.value + 1];
    if (prev === url) {
      historyIndex.value -= 1;
      return;
    }
    if (next === url) {
      historyIndex.value += 1;
      return;
    }
    appendHistory(url);
  };
  wv.addEventListener("did-navigate", (event: any) => {
    const url = normalizeHref(String(event.url || ""));
    if (!isJavdbUrl(url)) return;
    currentBrowsingUrl.value = url;
    urlInput.value = url;
    syncHistoryFromNavigation(url);
  });
  wv.addEventListener("did-navigate-in-page", (event: any) => {
    const url = normalizeHref(String(event.url || ""));
    if (!isJavdbUrl(url)) return;
    currentBrowsingUrl.value = url;
    urlInput.value = url;
    syncHistoryFromNavigation(url);
  });
  wv.addEventListener("dom-ready", () => {
    wv.executeJavaScript(buildEnhancerScript()).catch(() => {});
  });
}

function onWindowMessage(event: MessageEvent) {
  const expectedOrigin = (() => {
    const source = iframeSrc.value || auth.apiBase || window.location.origin;
    try {
      return new URL(source, window.location.origin).origin;
    } catch {
      return window.location.origin;
    }
  })();
  if (event.origin !== expectedOrigin) return;
  if (myIframeRef.value && event.source !== myIframeRef.value.contentWindow) return;

  const data = event.data as { type?: string; url?: string } | null;
  if (!data || typeof data !== "object") return;
  if (data.type === "explore:url" && data.url) {
    const url = normalizeHref(String(data.url));
    if (!isJavdbUrl(url)) return;
    currentBrowsingUrl.value = url;
    urlInput.value = url;
    if (historyStack.value[historyIndex.value] !== url) {
      historyStack.value = historyStack.value.slice(0, historyIndex.value + 1);
      historyStack.value.push(url);
      historyIndex.value = historyStack.value.length - 1;
    }
    return;
  }
  if (data.type === "explore:jump-adhoc" && data.url) {
    jumpToAdhoc(String(data.url));
  }
}

onMounted(() => {
  if (isElectron) {
    const initial = normalizeHref(urlInput.value);
    webviewSrc.value = initial;
    appendHistory(initial);
    void nextTick(() => {
      bindWebviewEvents();
    });
  } else {
    openUrl();
    window.addEventListener("message", onWindowMessage);
  }
});

onUnmounted(() => {
  if (!isElectron) {
    window.removeEventListener("message", onWindowMessage);
  }
});
</script>

<style scoped>
.explore-toolbar {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}

.explore-toolbar--secondary {
  margin-top: 10px;
  grid-template-columns: 1fr auto minmax(180px, 1fr);
}

.browser-chrome {
  border: 1px solid var(--mdc-border);
  border-radius: 10px;
  background: #f1f3f4;
  overflow: hidden;
}

.browser-chrome__top {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px 6px;
}

.traffic-lights {
  display: flex;
  gap: 7px;
}

.tl {
  width: 12px;
  height: 12px;
  border-radius: 999px;
  display: inline-block;
}

.tl--red { background: #ff5f57; }
.tl--yellow { background: #ffbd2e; }
.tl--green { background: #28c840; }

.browser-tab {
  min-width: 220px;
  max-width: 460px;
  padding: 6px 12px;
  border-radius: 10px 10px 0 0;
  background: #ffffff;
  border: 1px solid #dadce0;
  border-bottom: none;
  font-size: 12px;
  color: #3c4043;
}

.browser-chrome__bar {
  display: grid;
  grid-template-columns: auto auto auto 1fr auto auto;
  gap: 8px;
  align-items: center;
  padding: 8px 10px 10px;
  border-top: 1px solid #e0e0e0;
  background: #ffffff;
}

.chrome-btn {
  width: 32px;
  height: 32px;
  padding: 0;
  border-radius: 999px;
  border: 1px solid #dadce0;
  background: #fff !important;
  color: #3c4043 !important;
}

.chrome-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.chrome-omnibox {
  height: 36px;
  border-radius: 18px;
  border: 1px solid #dadce0;
  background: #f1f3f4;
}

.explore-browser-wrap {
  margin-top: 12px;
  border: 1px solid var(--mdc-border);
  border-radius: 8px;
  overflow: hidden;
}

.explore-browser-iframe {
  width: 100%;
  height: min(76vh, 900px);
  border: 0;
  background: #fff;
}
</style>
