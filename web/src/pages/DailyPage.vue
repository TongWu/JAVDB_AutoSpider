<template>
  <div class="page-shell">
    <header class="page-head">
      <div>
        <h1 class="page-head__title">定期任务</h1>
        <p class="page-head__sub">Daily Ingestion，对应 pipeline / spider。</p>
      </div>
      <span v-if="jobForPage" class="page-head__meta">
        任务 <code class="meta-code">{{ jobForPage }}</code> · {{ statusForPage || "—" }}
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
          任务参数
        </button>
        <button
          type="button"
          role="tab"
          class="config-tab"
          :class="{ 'config-tab--active': taskTab === 'log' }"
          @click="openLogTab"
        >
          运行日志
        </button>
      </div>

      <div v-show="taskTab === 'params'" class="task-form-card__body">
        <div class="toolbar-row" style="border: none; margin-bottom: 0; padding-bottom: 0">
          <button type="button" @click="submit">提交任务</button>
          <button v-if="jobForPage" type="button" class="ghost" @click="store.stopPolling">停止轮询</button>
          <button
            v-if="jobForPage && store.pollStopped && !isTerminal"
            type="button"
            class="ghost"
            @click="store.resumePolling"
          >
            继续轮询
          </button>
        </div>
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
      </div>

      <div v-show="taskTab === 'log'" class="task-form-card__body task-form-card__body--log">
        <div class="log-panel-wrap">
          <div class="log-panel-header">实时输出（约每 2 秒刷新，自动滚到底）</div>
          <pre ref="logEl" class="log-live">{{ logDisplay }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { apiFetch } from "../lib/api";
import { useRunningJobStore } from "../stores/runningJob";
import type { TaskTab } from "../stores/runningJob";

const store = useRunningJobStore();
const route = useRoute();
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
  return "提交任务后将在此显示日志…";
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
    submitError.value = `[提交失败] ${e instanceof Error ? e.message : String(e)}`;
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
</style>
