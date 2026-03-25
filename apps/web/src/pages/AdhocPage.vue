<template>
  <div class="page-shell">
    <header class="page-head">
      <div>
        <h1 class="page-head__title">{{ t("adhoc.title") }}</h1>
        <p class="page-head__sub">{{ t("adhoc.subtitle") }}</p>
      </div>
      <span v-if="jobForPage" class="page-head__meta">
        {{ t("adhoc.jobMeta") }} <code class="meta-code">{{ jobForPage }}</code> · {{ statusForPage || "—" }}
      </span>
    </header>

    <div class="card mdc-card task-form-card">
      <div class="config-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          class="config-tab"
          :class="{ 'config-tab--active': taskTab === 'params' }"
          @click="taskTab = 'params'"
        >
          {{ t("adhoc.tabParams") }}
        </button>
        <button
          type="button"
          role="tab"
          class="config-tab"
          :class="{ 'config-tab--active': taskTab === 'log' }"
          @click="openLogTab"
        >
          {{ t("adhoc.tabLog") }}
        </button>
      </div>

      <div v-show="taskTab === 'params'" class="task-form-card__body">
        <div class="grid">
          <label class="span-2">
            url
            <input v-model="form.url" placeholder="https://javdb.com/actors/xxx" />
          </label>
          <label>
            start_page
            <input v-model.number="form.start_page" type="number" min="1" />
          </label>
          <label>
            end_page
            <input v-model.number="form.end_page" type="number" min="1" />
          </label>
          <label>
            phase
            <select v-model="form.phase">
              <option value="1">1</option>
              <option value="2">2</option>
              <option value="all">all</option>
            </select>
          </label>
          <label>
            qb_category
            <input v-model="form.qb_category" :placeholder="t('adhoc.qbCategoryPh')" />
          </label>
          <label>
            {{ t("adhoc.proxyMode") }}
            <select v-model="form.proxy_mode">
              <option value="auto">{{ t("adhoc.proxyAuto") }}</option>
              <option value="use_proxy">{{ t("adhoc.proxyForceOn") }}</option>
              <option value="no_proxy">{{ t("adhoc.proxyForceOff") }}</option>
            </select>
          </label>
        </div>
        <div class="checkbox-row">
          <label><input v-model="form.history_filter" type="checkbox" /> history_filter</label>
          <label><input v-model="form.date_filter" type="checkbox" /> date_filter</label>
          <label><input v-model="form.dry_run" type="checkbox" /> dry_run</label>
          <label><input v-model="form.ignore_release_date" type="checkbox" /> ignore_release_date</label>
        </div>
        <div class="actions">
          <button type="button" :disabled="submitting" @click="submit">{{ t("adhoc.submit") }}</button>
        </div>
      </div>

      <div v-show="taskTab === 'log'" class="task-form-card__body task-form-card__body--log">
        <div class="toolbar-row toolbar-row--log">
          <button v-if="jobForPage" type="button" class="ghost" @click="store.stopPolling">{{ t("adhoc.stopPoll") }}</button>
          <button
            v-if="jobForPage && store.pollStopped && !isTerminal"
            type="button"
            class="ghost"
            @click="store.resumePolling"
          >
            {{ t("adhoc.resumePoll") }}
          </button>
        </div>
        <div class="log-panel-wrap">
          <div class="log-panel-header">{{ t("adhoc.logHeader") }}</div>
          <pre ref="logEl" class="log-live">{{ logDisplay }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { apiFetch } from "../lib/api";
import { useRunningJobStore } from "../stores/runningJob";
import type { TaskTab } from "../stores/runningJob";

const store = useRunningJobStore();
const route = useRoute();
const router = useRouter();
const { t } = useI18n();
const taskTab = ref<TaskTab>("params");
const logEl = ref<HTMLElement | null>(null);
const submitError = ref("");
const submitting = ref(false);

const form = reactive({
  url: "",
  start_page: 1,
  end_page: 1,
  history_filter: false,
  date_filter: false,
  phase: "all",
  proxy_mode: "auto",
  qb_category: "",
  dry_run: false,
  ignore_release_date: true,
});

const jobForPage = computed(() => (store.kind === "adhoc" ? store.jobId : ""));
const statusForPage = computed(() => (store.kind === "adhoc" ? store.status : ""));
const isTerminal = computed(() => store.status === "success" || store.status === "failed");

const logDisplay = computed(() => {
  if (submitError.value) return submitError.value;
  if (store.kind === "adhoc" && store.logText) return store.logText;
  return t("adhoc.logPlaceholder");
});

watch(
  [() => store.logText, () => submitError.value],
  async () => {
    if (store.kind !== "adhoc" && !submitError.value) return;
    await nextTick();
    const el = logEl.value;
    if (el) el.scrollTop = el.scrollHeight;
  },
);

watch(
  () => route.query.tab,
  (t) => {
    const tab = t === "log" ? "log" : "params";
    if (taskTab.value !== tab) {
      taskTab.value = tab;
    }
    store.setAdhocTaskTab(tab);
    if (tab === "log" && store.kind === "adhoc" && store.jobId && store.pollStopped && !isTerminal.value) {
      store.resumePolling();
    }
  },
  { immediate: true },
);

watch(
  () => route.query.url,
  (v) => {
    const url = String(v || "").trim();
    if (!url) return;
    form.url = url;
  },
  { immediate: true },
);

watch(taskTab, (t) => {
  store.setAdhocTaskTab(t);
  const currentQueryTab = route.query.tab === "log" ? "log" : "params";
  if (currentQueryTab !== t) {
    void router.replace({
      query: {
        ...route.query,
        tab: t,
      },
    });
  }
  if (t === "log" && store.kind === "adhoc" && store.jobId && store.pollStopped && !isTerminal.value) {
    store.resumePolling();
  }
});

function openLogTab() {
  taskTab.value = "log";
}

onMounted(() => {
  if (route.query.tab !== "log" && store.kind === "adhoc") {
    taskTab.value = store.adhocTaskTab;
  }
});

async function submit() {
  if (submitting.value) return;
  submitting.value = true;
  submitError.value = "";
  try {
    const { proxy_mode, ...rest } = form;
    const data = await apiFetch("/api/tasks/adhoc", {
      method: "POST",
      body: JSON.stringify({
        ...rest,
        use_proxy: proxy_mode === "use_proxy",
        no_proxy: proxy_mode === "no_proxy",
      }),
    });
    store.startPolling(data.job_id as string, "adhoc", true);
    taskTab.value = "log";
  } catch (e: unknown) {
    submitError.value = t("adhoc.submitFail", { msg: e instanceof Error ? e.message : String(e) });
    taskTab.value = "log";
  } finally {
    submitting.value = false;
  }
}
</script>

<style scoped>
.meta-code {
  font-size: 12px;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--mdc-bg-subtle);
  border: 1px solid var(--mdc-border);
}

.toolbar-row--log {
  margin: 0;
  padding: 12px 20px;
  border-bottom: 1px solid var(--mdc-border);
}
</style>
