<template>
  <div class="page-shell">
    <header class="page-head">
      <div>
        <h1 class="page-head__title">手动任务</h1>
        <p class="page-head__sub">Adhoc Ingestion，指定 URL 与分页范围。</p>
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
            <input v-model="form.qb_category" placeholder="可选" />
          </label>
        </div>
        <div class="checkbox-row">
          <label><input v-model="form.history_filter" type="checkbox" /> history_filter</label>
          <label><input v-model="form.date_filter" type="checkbox" /> date_filter</label>
          <label><input v-model="form.use_proxy" type="checkbox" /> use_proxy</label>
          <label><input v-model="form.proxy_uploader" type="checkbox" /> proxy_uploader</label>
          <label><input v-model="form.proxy_pikpak" type="checkbox" /> proxy_pikpak</label>
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
const submitError = ref("");

const form = reactive({
  url: "",
  start_page: 1,
  end_page: 1,
  history_filter: false,
  date_filter: false,
  phase: "all",
  use_proxy: true,
  proxy_uploader: false,
  proxy_pikpak: false,
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
  return "提交任务后将在此显示日志…";
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
    if (t === "log") {
      taskTab.value = "log";
      store.setAdhocTaskTab("log");
      if (store.kind === "adhoc" && store.jobId && store.pollStopped && !isTerminal.value) {
        store.resumePolling();
      }
    }
  },
  { immediate: true },
);

watch(taskTab, (t) => {
  store.setAdhocTaskTab(t);
  if (t === "log" && store.kind === "adhoc" && store.jobId && store.pollStopped && !isTerminal.value) {
    store.resumePolling();
  }
});

function openLogTab() {
  taskTab.value = "log";
  store.setAdhocTaskTab("log");
  if (store.kind === "adhoc" && store.jobId && store.pollStopped && !isTerminal.value) {
    store.resumePolling();
  }
}

onMounted(() => {
  if (route.query.tab !== "log" && store.kind === "adhoc") {
    taskTab.value = store.adhocTaskTab;
  }
});

async function submit() {
  submitError.value = "";
  try {
    const data = await apiFetch("/api/tasks/adhoc", {
      method: "POST",
      body: JSON.stringify(form),
    });
    store.startPolling(data.job_id as string, "adhoc", true);
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
