<template>
  <div class="page-shell">
    <header class="page-head">
      <div>
        <h1 class="page-head__title">{{ t("tasksPage.title") }}</h1>
        <p class="page-head__sub">{{ t("tasksPage.subtitleList") }}</p>
      </div>
    </header>

    <div class="card mdc-card">
      <div class="table-toolbar">
        <input v-model="keyword" type="search" :placeholder="t('tasksPage.filterPh')" />
        <button type="button" class="ghost" @click="refreshTasks">{{ t("tasksPage.refresh") }}</button>
      </div>

      <p v-if="error" class="form-error">{{ error }}</p>

      <div class="task-list-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>任务ID</th>
              <th>{{ t("tasksPage.mode") }}</th>
              <th>{{ t("tasksPage.status") }}</th>
              <th>URL</th>
              <th>{{ t("tasksPage.createdAt") }}</th>
              <th>{{ t("tasksPage.completedAt") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="it in filteredTasks"
              :key="it.job_id"
              class="task-list-row"
              :class="{ 'task-list-row--active': selectedJobId === it.job_id }"
              @click="selectTask(it.job_id)"
            >
              <td><code>{{ it.job_id }}</code></td>
              <td>{{ taskModeLabel(it) }}</td>
              <td>{{ it.status || "unknown" }}</td>
              <td>{{ it.url || "—" }}</td>
              <td>{{ formatTime(it.created_at) }}</td>
              <td>{{ formatTime(it.completed_at) }}</td>
            </tr>
            <tr v-if="!filteredTasks.length">
              <td colspan="6">{{ t("tasksPage.emptyTaskList") }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p v-if="statusLine" class="job-meta">{{ statusLine }}</p>

      <div v-if="taskLog || fetched" class="log-panel-wrap" style="margin-top: 16px">
        <div class="log-panel-header">{{ t("tasksPage.logHeader") }}</div>
        <pre class="log-live" style="max-height: min(55vh, 480px); min-height: 160px">{{ taskLog || t("tasksPage.emptyLog") }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { apiFetch } from "../lib/api";

type TaskItem = {
  job_id: string;
  kind: "daily" | "adhoc" | string;
  mode: string;
  status: string;
  url?: string;
  created_at?: string;
  completed_at?: string;
};

const keyword = ref("");
const tasks = ref<TaskItem[]>([]);
const selectedJobId = ref("");
const statusLine = ref("");
const taskLog = ref("");
const error = ref("");
const fetched = ref(false);
const { t } = useI18n();

const filteredTasks = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  if (!kw) return tasks.value;
  return tasks.value.filter((t) => {
    const mode = taskModeLabel(t).toLowerCase();
    return (
      t.job_id.toLowerCase().includes(kw) ||
      mode.includes(kw) ||
      String(t.url || "")
        .toLowerCase()
        .includes(kw)
    );
  });
});

function taskModeLabel(item: TaskItem): string {
  if (item.kind === "adhoc") return "adhoc / pipeline";
  return `daily / ${item.mode || "pipeline"}`;
}

function formatTime(v?: string): string {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return d.toLocaleString();
}

async function refreshTasks() {
  try {
    const data = (await apiFetch("/api/tasks?limit=500")) as { tasks?: TaskItem[] };
    tasks.value = Array.isArray(data.tasks) ? data.tasks : [];
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function fetchTask(jobId: string) {
  error.value = "";
  fetched.value = false;
  statusLine.value = "";
  taskLog.value = "";
  try {
    const data = (await apiFetch(`/api/tasks/${jobId.trim()}`)) as {
      status?: string;
      log?: string;
    };
    fetched.value = true;
    selectedJobId.value = jobId;
    statusLine.value = t("tasksPage.statusLine", { status: data.status ?? "—" });
    taskLog.value = data.log ?? "";
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

function selectTask(jobId: string) {
  void fetchTask(jobId);
}

onMounted(async () => {
  await refreshTasks();
  if (tasks.value.length) {
    await fetchTask(tasks.value[0].job_id);
  }
});
</script>

<style scoped>
.task-list-wrap {
  max-height: min(45vh, 360px);
  overflow: auto;
}

.task-list-row {
  cursor: pointer;
}

.task-list-row--active td {
  background: rgb(37 99 235 / 0.08);
}

.theme-dark .task-list-row--active td {
  background: rgb(96 165 250 / 0.2);
}
</style>
