<template>
  <div class="page-shell config-page">
    <header class="page-head">
      <div>
        <h1 class="page-head__title">{{ t("config.title") }}</h1>
        <p class="page-head__sub">{{ t("config.subtitle") }}</p>
      </div>
    </header>

    <div v-if="loading" class="card config-loading-card">{{ t("config.loading") }}</div>

    <div v-else class="card mdc-card settings-panel">
      <div class="config-tabs-wrap">
        <div class="config-tabs" role="tablist">
          <button
            v-for="[sectionName] in sections"
            :key="sectionName"
            type="button"
            role="tab"
            class="config-tab"
            :class="{ 'config-tab--active': activeSection === sectionName }"
            @click="activeSection = sectionName"
          >
            {{ sectionTabLabel(sectionName) }}
          </button>
        </div>
      </div>

      <p
        v-if="message"
        :class="['config-msg', messageIsError ? 'config-msg--err' : '']"
      >
        {{ message }}
      </p>

      <div class="setting-rows">
        <div v-for="f in visibleCurrentFields" :key="f.key" class="setting-row">
          <div class="setting-label">
            <div class="setting-label__title">{{ fieldLabel(f.key) }}</div>
            <p class="setting-label__desc">{{ fieldDescription(f.key, f) }}</p>
            <div class="setting-label__meta">
              <span class="setting-label__key">{{ f.key }}</span>
              <span v-if="f.readonly" class="badge badge--ghost">{{ t("config.badgeReadonly") }}</span>
              <span v-if="f.sensitive" class="badge badge--warn">{{ t("config.badgeSensitive") }}</span>
              <span class="badge badge--ghost">{{ f.type }}</span>
            </div>
          </div>
          <div class="setting-control">
            <template v-if="f.type === 'bool'">
              <label class="switch">
                <input
                  :id="'cfg-' + f.key"
                  type="checkbox"
                  class="switch-input"
                  :checked="form[f.key] === true"
                  :disabled="f.readonly || !canSave"
                  @change="form[f.key] = ($event.target as HTMLInputElement).checked"
                />
                <span class="switch-track" aria-hidden="true"><span class="switch-thumb" /></span>
                <span class="switch-text">{{ form[f.key] ? t("config.on") : t("config.off") }}</span>
              </label>
            </template>
            <template v-else-if="f.key === 'PROXY_POOL'">
              <ProxyPoolEditor v-model="proxyPoolRows" :readonly="f.readonly || !canSave" />
            </template>
            <template v-else-if="f.type === 'json'">
              <template v-if="f.key === 'RCLONE_CONFIG_BASE64'">
                <div class="rclone-config-editor">
                  <textarea
                    :id="'cfg-' + f.key"
                    v-model="rclonePlaintext"
                    class="field-input field-input--code"
                    rows="8"
                    :readonly="f.readonly || !canSave"
                    spellcheck="false"
                    placeholder="[remote]\ntype = drive\nscope = drive"
                  />
                  <div class="actions">
                    <button type="button" class="ghost" :disabled="f.readonly || !canSave" @click="decodeRcloneFromCurrent">
                      {{ t("config.rcloneDecodeCurrent") }}
                    </button>
                    <button type="button" class="ghost" :disabled="f.readonly || !canSave" @click="showRcloneBase64Preview">
                      {{ t("config.rcloneShowEncoded") }}
                    </button>
                  </div>
                  <pre v-if="rcloneBase64Preview" class="rclone-base64-preview">{{ rcloneBase64Preview }}</pre>
                </div>
              </template>
              <template v-else-if="f.key === 'CF_BYPASS_PORT_MAP'">
                <div class="rclone-config-editor">
                  <div class="grid">
                    <label>
                      local
                      <input v-model="cfBypassLocalPort" class="field-input" type="number" min="1" max="65535" />
                    </label>
                    <label>
                      default
                      <input v-model="cfBypassDefaultPort" class="field-input" type="number" min="1" max="65535" />
                    </label>
                  </div>
                  <div class="grid" style="margin-top: 10px">
                    <label v-for="row in proxyPoolRows" :key="`cf-port-${row._id}`">
                      {{ row.name || row.host || row._id }}
                      <input
                        v-model="cfBypassProxyPorts[row.name || row.host || row._id]"
                        class="field-input"
                        type="number"
                        min="1"
                        max="65535"
                      />
                    </label>
                  </div>
                </div>
              </template>
              <textarea
                v-else
                :id="'cfg-' + f.key"
                v-model="form[f.key]"
                class="field-input field-input--code"
                rows="8"
                :readonly="f.readonly || !canSave"
                spellcheck="false"
              />
            </template>
            <template v-else-if="SELECT_OPTION_KEYS[f.key]">
              <select
                :id="'cfg-' + f.key"
                v-model="form[f.key]"
                class="field-input field-input--select"
                :disabled="f.readonly || !canSave"
              >
                <option v-for="o in selectOptionsFor(f.key, form[f.key])" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
            </template>
            <template v-else-if="f.type === 'int'">
              <input
                :id="'cfg-' + f.key"
                v-model="form[f.key]"
                class="field-input"
                type="number"
                step="1"
                :readonly="f.readonly || !canSave"
              />
            </template>
            <template v-else-if="f.type === 'float'">
              <input
                :id="'cfg-' + f.key"
                v-model="form[f.key]"
                class="field-input"
                type="number"
                step="any"
                :readonly="f.readonly || !canSave"
              />
            </template>
            <template v-else-if="f.sensitive">
              <input
                :id="'cfg-' + f.key"
                v-model="form[f.key]"
                class="field-input"
                type="password"
                autocomplete="new-password"
                :placeholder="sensitivePlaceholder"
                :readonly="f.readonly || !canSave"
              />
            </template>
            <template v-else-if="isPathLikeField(f.key, f.type, f.sensitive) && f.key !== 'GIT_REPO_URL'">
              <div class="path-input-group">
                <input
                  :id="'cfg-' + f.key"
                  v-model="form[f.key]"
                  class="field-input field-input--path"
                  type="text"
                  :readonly="f.readonly || !canSave"
                />
                <button
                  type="button"
                  class="btn-folder"
                  :title="t('config.pathFolderTitle')"
                  :disabled="f.readonly || !canSave"
                  @click="focusField('cfg-' + f.key)"
                >
                  <svg class="btn-folder__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
                    <path
                      stroke-width="1.75"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      d="M3 7.5V18a1.5 1.5 0 001.5 1.5h15A1.5 1.5 0 0021 18V8.25A1.5 1.5 0 0019.5 6.75h-7.72L9.9 4.5H4.5A1.5 1.5 0 003 6v1.5z"
                    />
                  </svg>
                </button>
              </div>
            </template>
            <template v-else>
              <input
                :id="'cfg-' + f.key"
                v-model="form[f.key]"
                class="field-input"
                type="text"
                :readonly="f.readonly || !canSave"
              />
            </template>
          </div>
        </div>
      </div>

      <div class="config-footer">
        <button type="button" class="btn-text-link" @click="load">{{ t("config.refresh") }}</button>
        <div class="config-footer__actions">
          <button v-if="canSave" type="button" class="config-save-btn" @click="save">{{ t("config.save") }}</button>
          <span v-else class="badge badge--warn">{{ t("config.readOnlyCannotSave") }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { apiFetch } from "../lib/api";
import { useAuthStore } from "../stores/auth";
import ProxyPoolEditor from "../components/ProxyPoolEditor.vue";
import { SELECT_OPTION_KEYS } from "../config/configFormConstants";
import { isPathLikeField, useConfigFormLabels } from "../composables/useConfigFormLabels";
import { poolFromWire, poolToWire, type ProxyEditorRow } from "../utils/proxyPool";

type FieldMeta = {
  key: string;
  section: string;
  type: string;
  sensitive: boolean;
  readonly: boolean;
};

const auth = useAuthStore();
const canSave = computed(() => auth.role === "admin");
const { t } = useI18n();
const sensitivePlaceholder = computed(() => t("config.sensitivePlaceholder"));
const { sectionTabLabel, fieldLabel, fieldDescription, selectOptionsFor } = useConfigFormLabels();

const metaFields = ref<FieldMeta[]>([]);
const form = reactive<Record<string, any>>({});
const initialJsonSnapshots = ref<Record<string, string>>({});
const message = ref("");
const messageIsError = ref(false);
const loading = ref(true);
const activeSection = ref("");

const proxyPoolRows = ref<ProxyEditorRow[]>([]);
const proxyPoolTouched = ref(false);
const syncingProxyFromApi = ref(false);
const rclonePlaintext = ref("");
const rcloneBase64Preview = ref("");
const cfBypassLocalPort = ref("");
const cfBypassDefaultPort = ref("");
const cfBypassProxyPorts = reactive<Record<string, string>>({});

const sections = computed(() => {
  const map = new Map<string, FieldMeta[]>();
  for (const f of metaFields.value) {
    if (!map.has(f.section)) map.set(f.section, []);
    map.get(f.section)!.push(f);
  }
  return Array.from(map.entries());
});

const currentFields = computed(() => {
  const hit = sections.value.find(([name]) => name === activeSection.value);
  return hit ? hit[1] : [];
});

const visibleCurrentFields = computed(() =>
  currentFields.value.filter((f) => !["PROXY_HTTP", "PROXY_HTTPS"].includes(f.key)),
);

watch(
  sections,
  (list) => {
    if (!list.length) return;
    if (!list.some(([n]) => n === activeSection.value)) {
      activeSection.value = list[0][0];
    }
  },
  { immediate: true },
);

watch(
  proxyPoolRows,
  () => {
    if (syncingProxyFromApi.value) return;
    proxyPoolTouched.value = true;
  },
  { deep: true },
);

function focusField(id: string) {
  document.getElementById(id)?.focus();
}

function decodeBase64ToText(raw: string): string {
  const v = String(raw ?? "").trim();
  if (!v) return "";
  try {
    return decodeURIComponent(
      Array.prototype.map
        .call(atob(v), (ch: string) => `%${(`00${ch.charCodeAt(0).toString(16)}`).slice(-2)}`)
        .join(""),
    );
  } catch {
    return "";
  }
}

function encodeTextToBase64(raw: string): string {
  const text = String(raw ?? "");
  const bytes = encodeURIComponent(text).replace(/%([0-9A-F]{2})/g, (_m, p1) => String.fromCharCode(parseInt(p1, 16)));
  return btoa(bytes);
}

function decodeRcloneFromCurrent() {
  rcloneBase64Preview.value = "";
  const decoded = decodeBase64ToText(String(form["RCLONE_CONFIG_BASE64"] ?? ""));
  rclonePlaintext.value = decoded || String(form["RCLONE_CONFIG_BASE64"] ?? "");
}

function showRcloneBase64Preview() {
  rcloneBase64Preview.value = encodeTextToBase64(rclonePlaintext.value);
}

function setFormFromApi(data: Record<string, unknown>) {
  const m = metaFields.value;
  const next: Record<string, unknown> = {};
  const snaps: Record<string, string> = {};
  for (const f of m) {
    const v = data[f.key];
    if (f.type === "bool") {
      next[f.key] = !!v;
    } else if (f.type === "json") {
      const fallback = f.key === "PROXY_POOL" ? [] : {};
      const s = JSON.stringify(v ?? fallback, null, 2);
      next[f.key] = s;
      snaps[f.key] = s;
    } else if (f.type === "int" || f.type === "float") {
      next[f.key] = v === null || v === undefined ? "" : String(v);
    } else if (f.sensitive && v === "********") {
      next[f.key] = "";
    } else if (v === null || v === undefined) {
      next[f.key] = "";
    } else {
      next[f.key] = String(v);
    }
  }
  for (const k of Object.keys(form)) {
    delete form[k];
  }
  Object.assign(form, next);
  initialJsonSnapshots.value = snaps;
  const rcloneDecoded = decodeBase64ToText(String(next["RCLONE_CONFIG_BASE64"] ?? ""));
  rclonePlaintext.value = rcloneDecoded || String(next["RCLONE_CONFIG_BASE64"] ?? "");
  rcloneBase64Preview.value = "";

  cfBypassLocalPort.value = "";
  cfBypassDefaultPort.value = "";
  for (const k of Object.keys(cfBypassProxyPorts)) delete cfBypassProxyPorts[k];
  try {
    const cfMap = JSON.parse(String(next["CF_BYPASS_PORT_MAP"] || "{}")) as {
      local?: number | string;
      default?: number | string;
      proxies?: Record<string, number | string>;
    };
    if (cfMap && typeof cfMap === "object") {
      cfBypassLocalPort.value = cfMap.local === undefined ? "" : String(cfMap.local);
      cfBypassDefaultPort.value = cfMap.default === undefined ? "" : String(cfMap.default);
      if (cfMap.proxies && typeof cfMap.proxies === "object") {
        for (const [name, port] of Object.entries(cfMap.proxies)) {
          cfBypassProxyPorts[name] = String(port ?? "");
        }
      }
    }
  } catch {
    /* ignore malformed json */
  }

  syncingProxyFromApi.value = true;
  try {
    const poolRaw = form["PROXY_POOL"];
    let poolParsed: unknown = [];
    if (typeof poolRaw === "string") {
      try {
        poolParsed = JSON.parse(poolRaw || "[]");
      } catch {
        poolParsed = [];
      }
    }
    proxyPoolRows.value = poolFromWire(poolParsed);
    proxyPoolTouched.value = false;
  } finally {
    void nextTick(() => {
      syncingProxyFromApi.value = false;
    });
  }
}

async function load() {
  loading.value = true;
  message.value = "";
  messageIsError.value = false;
  try {
    const [meta, data] = await Promise.all([
      apiFetch("/api/config/meta") as Promise<{ fields: FieldMeta[] }>,
      apiFetch("/api/config") as Promise<Record<string, unknown>>,
    ]);
    metaFields.value = meta.fields;
    setFormFromApi(data);
  } catch (e: unknown) {
    message.value = e instanceof Error ? e.message : String(e);
    messageIsError.value = true;
  } finally {
    loading.value = false;
  }
}

function buildPayload(): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const f of metaFields.value) {
    if (f.readonly) continue;
    const key = f.key;
    const raw = form[key];
    if (f.type === "bool") {
      payload[key] = !!raw;
      continue;
    }
    if (f.type === "int") {
      const s = String(raw ?? "").trim();
      if (s === "") continue;
      const n = parseInt(s, 10);
      if (!Number.isNaN(n)) payload[key] = n;
      continue;
    }
    if (f.type === "float") {
      const s = String(raw ?? "").trim();
      if (s === "") continue;
      const n = parseFloat(s);
      if (!Number.isNaN(n)) payload[key] = n;
      continue;
    }
    if (f.type === "json") {
      if (key === "PROXY_POOL") {
        if (!proxyPoolTouched.value) continue;
        payload[key] = poolToWire(proxyPoolRows.value);
        continue;
      }
      if (key === "RCLONE_CONFIG_BASE64") {
        const encoded = encodeTextToBase64(rclonePlaintext.value);
        const origEncoded = String(form["RCLONE_CONFIG_BASE64"] ?? "");
        if (encoded === origEncoded) continue;
        payload[key] = encoded;
        continue;
      }
      if (key === "CF_BYPASS_PORT_MAP") {
        const nextMap: Record<string, unknown> = {};
        const localPort = parseInt(String(cfBypassLocalPort.value || "").trim(), 10);
        if (!Number.isNaN(localPort)) nextMap.local = localPort;
        const defaultPort = parseInt(String(cfBypassDefaultPort.value || "").trim(), 10);
        if (!Number.isNaN(defaultPort)) nextMap.default = defaultPort;
        const proxyMap: Record<string, number> = {};
        for (const [name, value] of Object.entries(cfBypassProxyPorts)) {
          const n = parseInt(String(value || "").trim(), 10);
          if (!name || Number.isNaN(n)) continue;
          proxyMap[name] = n;
        }
        if (Object.keys(proxyMap).length) nextMap.proxies = proxyMap;
        const nextJson = JSON.stringify(nextMap, null, 2);
        const orig = initialJsonSnapshots.value[key] ?? "";
        if (nextJson !== orig) {
          payload[key] = nextMap;
        }
        continue;
      }
      const s = String(raw ?? "").trim();
      const orig = initialJsonSnapshots.value[key] ?? "";
      if (s === orig) continue;
      try {
        payload[key] = JSON.parse(s) as unknown;
      } catch (err: unknown) {
        const reason = err instanceof Error ? err.message : String(err);
        throw new Error(`Invalid JSON for ${key}: ${reason}. Raw: ${s}`);
      }
      continue;
    }
    const s = String(raw ?? "").trim();
    if (f.sensitive) {
      if (s === "") continue;
      payload[key] = s;
      continue;
    }
    payload[key] = s;
  }
  return payload;
}

async function save() {
  message.value = "";
  messageIsError.value = false;
  try {
    const payload = buildPayload();
    await apiFetch("/api/config", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    message.value = t("config.saveSuccess");
    await load();
  } catch (e: unknown) {
    message.value = e instanceof Error ? e.message : String(e);
    messageIsError.value = true;
  }
}

onMounted(load);
</script>

<style scoped>
.mono-tip {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}

.config-loading-card {
  max-width: 1280px;
  margin: 0 auto;
}

.rclone-config-editor {
  width: 100%;
}

.rclone-base64-preview {
  max-width: 100%;
  margin-top: 10px;
}
</style>
