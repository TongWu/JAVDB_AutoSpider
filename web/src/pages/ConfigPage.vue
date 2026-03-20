<template>
  <div class="page-shell config-page">
    <header class="page-head">
      <div>
        <h1 class="page-head__title">配置管理</h1>
        <p class="page-head__sub">按分组编辑变量，保存后由服务器重新生成 <span class="mono-tip">config.py</span>。</p>
      </div>
    </header>

    <div v-if="loading" class="card config-loading-card">加载中…</div>

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
        :class="['config-msg', message.includes('失败') || message.includes('错误') ? 'config-msg--err' : '']"
      >
        {{ message }}
      </p>

      <div class="setting-rows">
        <div v-for="f in currentFields" :key="f.key" class="setting-row">
          <div class="setting-label">
            <div class="setting-label__title">{{ fieldLabel(f.key) }}</div>
            <p class="setting-label__desc">{{ fieldDescription(f.key, f) }}</p>
            <div class="setting-label__meta">
              <span class="setting-label__key">{{ f.key }}</span>
              <span v-if="f.readonly" class="badge badge--ghost">只读</span>
              <span v-if="f.sensitive" class="badge badge--warn">敏感</span>
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
                <span class="switch-text">{{ form[f.key] ? "开启" : "关闭" }}</span>
              </label>
            </template>
            <template v-else-if="f.key === 'PROXY_POOL'">
              <ProxyPoolEditor v-model="proxyPoolRows" :readonly="f.readonly || !canSave" />
            </template>
            <template v-else-if="f.type === 'json'">
              <textarea
                :id="'cfg-' + f.key"
                v-model="form[f.key]"
                class="field-input field-input--code"
                rows="8"
                :readonly="f.readonly || !canSave"
                spellcheck="false"
              />
            </template>
            <template v-else-if="SELECT_OPTIONS[f.key]">
              <select
                :id="'cfg-' + f.key"
                v-model="form[f.key]"
                class="field-input field-input--select"
                :disabled="f.readonly || !canSave"
              >
                <option v-for="o in selectOptionsFor(f.key)" :key="o.value" :value="o.value">{{ o.label }}</option>
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
            <template v-else-if="isPathLikeField(f.key, f.type, f.sensitive)">
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
                  title="聚焦输入框（路径请手动填写）"
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
        <button type="button" class="btn-text-link" @click="load">刷新</button>
        <div class="config-footer__actions">
          <button v-if="canSave" type="button" class="config-save-btn" @click="save">保存修改</button>
          <span v-else class="badge badge--warn">只读账号无法保存</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { apiFetch } from "../lib/api";
import { useAuthStore } from "../stores/auth";
import ProxyPoolEditor from "../components/ProxyPoolEditor.vue";
import {
  SELECT_OPTIONS,
  fieldDescription,
  fieldLabel,
  isPathLikeField,
  sectionTabLabel,
} from "../config/configUiLabels";
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
const sensitivePlaceholder = "留空则不修改已保存的敏感值";

const metaFields = ref<FieldMeta[]>([]);
const form = reactive<Record<string, any>>({});
const initialJsonSnapshots = ref<Record<string, string>>({});
const message = ref("");
const loading = ref(true);
const activeSection = ref("");

const proxyPoolRows = ref<ProxyEditorRow[]>([]);
const proxyPoolTouched = ref(false);
const syncingProxyFromApi = ref(false);

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

function selectOptionsFor(key: string) {
  const opts = SELECT_OPTIONS[key];
  if (!opts?.length) return [];
  const cur = String(form[key] ?? "");
  const has = opts.some((o) => o.value === cur);
  if (cur && !has) return [...opts, { value: cur, label: `${cur}（当前值）` }];
  return opts;
}

function focusField(id: string) {
  document.getElementById(id)?.focus();
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
  try {
    const [meta, data] = await Promise.all([
      apiFetch("/api/config/meta") as Promise<{ fields: FieldMeta[] }>,
      apiFetch("/api/config") as Promise<Record<string, unknown>>,
    ]);
    metaFields.value = meta.fields;
    setFormFromApi(data);
  } catch (e: unknown) {
    message.value = e instanceof Error ? e.message : String(e);
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
      const s = String(raw ?? "").trim();
      const orig = initialJsonSnapshots.value[key] ?? "";
      if (s === orig) continue;
      payload[key] = JSON.parse(s) as unknown;
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
  try {
    const payload = buildPayload();
    await apiFetch("/api/config", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    message.value = "保存成功";
    await load();
  } catch (e: unknown) {
    message.value = e instanceof Error ? e.message : String(e);
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
</style>
