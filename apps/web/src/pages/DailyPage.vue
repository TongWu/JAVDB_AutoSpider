<template>
  <div class="page-shell">
    <header class="page-head">
      <div>
        <h1 class="page-head__title">{{ t("daily.title") }}</h1>
        <p class="page-head__sub">{{ t("daily.subtitle") }}</p>
      </div>
      <span v-if="jobForPage" class="page-head__meta">
        {{ t("daily.jobMeta") }} <code class="meta-code">{{ jobForPage }}</code> · {{ statusForPage || "—" }}
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
          {{ t("daily.tabParams") }}
        </button>
        <button
          type="button"
          role="tab"
          class="config-tab"
          :class="{ 'config-tab--active': taskTab === 'log' }"
          @click="openLogTab"
        >
          {{ t("daily.tabLog") }}
        </button>
      </div>

      <div v-show="taskTab === 'params'" class="task-form-card__body">
        <div class="grid">
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
            mode
            <select v-model="form.mode">
              <option value="pipeline">pipeline</option>
              <option value="spider">spider</option>
            </select>
          </label>
        </div>
        <div class="checkbox-row">
          <label><input v-model="form.use_proxy" type="checkbox" /> use_proxy</label>
          <label><input v-model="form.dry_run" type="checkbox" /> dry_run</label>
          <label><input v-model="form.ignore_release_date" type="checkbox" /> ignore_release_date</label>
        </div>
        <div class="actions">
          <button type="button" @click="submit">{{ t("daily.submit") }}</button>
        </div>
      </div>

      <div v-show="taskTab === 'log'" class="task-form-card__body task-form-card__body--log">
        <div class="toolbar-row toolbar-row--log">
          <button v-if="jobForPage" type="button" class="ghost" @click="store.stopPolling">{{ t("daily.stopPoll") }}</button>
          <button
            v-if="jobForPage && store.pollStopped && !isTerminal"
            type="button"
            class="ghost"
            @click="store.resumePolling"
          >
            {{ t("daily.resumePoll") }}
          </button>
        </div>
        <div class="log-panel-wrap">
          <div class="log-panel-header">{{ t("daily.logHeader") }}</div>
          <pre ref="logEl" class="log-live">{{ logDisplay }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useI18n } from "vue-i18n";
import { apiFetch } from "../lib/api";
import { useRunningJobStore } from "../stores/runningJob";
import type { TaskTab } from "../stores/runningJob";

const store = useRunningJobStore();
const route = useRoute();
const { t } = useI18n();
const taskTab = ref<TaskTab>("params");
const logEl = ref<HTMLElement | null>(null);

const form = reactive({
  start_page: 1,
  end_page: 10,
  phase: "all",
  mode: "pipeline",
  use_proxy: false,
  dry_run: false,
  ignore_release_date: false,
});

const jobForPage = computed(() => (store.kind === "daily" ? store.jobId : ""));
const statusForPage = computed(() => (store.kind === "daily" ? store.status : ""));
const isTerminal = computed(() => store.status === "success" || store.status === "failed");
const submitError = ref("");

const logDisplay = computed(() => {
  if (submitError.value) return submitError.value;
  if (store.kind === "daily" && store.logText) return store.logText;
  return t("daily.logPlaceholder");
});

watch(
  [() => store.logText, () => submitError.value],
  async () => {
    if (store.kind !== "daily" && !submitError.value) return;
    await nextTick();
    const el = logEl.value;
    if (el) el.scrollTop = el.scrollHeight;
  },
);

watch(
  () => route.query.tab,
  (t) => {
    if (t === "log") {
      taskTab.value = "log";
      store.setDailyTaskTab("log");
      if (store.kind === "daily" && store.jobId && store.pollStopped && !isTerminal.value) {
        store.resumePolling();
      }
    }
  },
  { immediate: true },
);

watch(taskTab, (t) => {
  store.setDailyTaskTab(t);
  if (t === "log" && store.kind === "daily" && store.jobId && store.pollStopped && !isTerminal.value) {
    store.resumePolling();
  }
});

function openLogTab() {
  taskTab.value = "log";
  store.setDailyTaskTab("log");
  if (store.kind === "daily" && store.jobId && store.pollStopped && !isTerminal.value) {
    store.resumePolling();
  }
}

onMounted(() => {
  if (route.query.tab !== "log" && store.kind === "daily") {
    taskTab.value = store.dailyTaskTab;
  }
});

async function submit() {
  submitError.value = "";
  try {
    const data = await apiFetch("/api/tasks/daily", {
      method: "POST",
      body: JSON.stringify(form),
    });
    store.startPolling(data.job_id as string, "daily", true);
    taskTab.value = "log";
  } catch (e: unknown) {
    submitError.value = t("daily.submitFail", { msg: e instanceof Error ? e.message : String(e) });
    taskTab.value = "log";
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
