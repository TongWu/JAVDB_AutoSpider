<template>
  <div class="page-shell">
    <header class="page-head">
      <div>
        <h1 class="page-head__title">{{ t("dashboard.title") }}</h1>
        <p class="page-head__sub">{{ t("dashboard.subtitle") }}</p>
      </div>
    </header>

    <p class="section-rule">{{ t("dashboard.sectionStatus") }}</p>
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-card__label">{{ t("dashboard.periodic") }}</div>
        <div class="stat-card__value">{{ periodicSlot }}</div>
        <div class="stat-card__hint">{{ periodicHint }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-card__label">{{ t("dashboard.manual") }}</div>
        <div class="stat-card__value">{{ manualSlot }}</div>
        <div class="stat-card__hint">{{ manualHint }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-card__label">{{ t("dashboard.apiHealth") }}</div>
        <div class="stat-card__value stat-card__value--sm">{{ healthShort }}</div>
        <div class="stat-card__hint">{{ healthDetail }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-card__label">{{ t("dashboard.rustCore") }}</div>
        <div class="stat-card__value stat-card__value--sm">{{ rustLabel }}</div>
        <div class="stat-card__hint">{{ t("dashboard.rustHint") }}</div>
      </div>
    </div>

    <p class="section-rule">{{ t("dashboard.sectionTips") }}</p>
    <div class="card mdc-card">
      <i18n-t keypath="dashboard.tipFull" tag="p" class="dash-tip">
        <template #tasks>
          <router-link to="/tasks">{{ t("nav.tasks") }}</router-link>
        </template>
      </i18n-t>
    </div>

    <p class="section-rule">{{ t("dashboard.sectionHistory") }}</p>
    <div class="card mdc-card">
      <p class="dash-schedule">
        {{ t("dashboard.nextSchedule") }}：
        <template v-if="nextScheduleLabel">{{ nextScheduleLabel }}</template>
        <template v-else>{{ t("dashboard.scheduleNotConfigured") }}</template>
      </p>
      <div class="task-history-table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>任务ID</th>
              <th>{{ t("dashboard.mode") }}</th>
              <th>{{ t("dashboard.status") }}</th>
              <th>URL</th>
              <th>{{ t("dashboard.createdAt") }}</th>
              <th>{{ t("dashboard.completedAt") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in taskHistoryRows" :key="t.job_id">
              <td><code>{{ t.job_id }}</code></td>
              <td>{{ taskModeLabel(t) }}</td>
              <td>{{ t.status || "unknown" }}</td>
              <td class="task-url-cell">{{ t.url || "—" }}</td>
              <td>{{ formatTime(t.created_at) }}</td>
              <td>{{ formatTime(t.completed_at) }}</td>
            </tr>
            <tr v-if="!taskHistoryRows.length">
              <td colspan="6" class="task-history-empty">{{ t("dashboard.noHistory") }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { apiFetch } from "../lib/api";

const { t } = useI18n();
const healthShort = ref("…");
const healthDetail = ref(t("dashboard.loading"));
const rustLabel = ref("—");

type TaskItem = {
  job_id: string;
  kind: "daily" | "adhoc" | string;
  mode: string;
  status: string;
  url?: string;
  created_at?: string;
  completed_at?: string;
};

type TaskListResponse = {
  tasks?: TaskItem[];
  next_schedule?: {
    source?: string;
    cron_pipeline?: string;
    cron_spider?: string;
  };
};

const tasks = ref<TaskItem[]>([]);
const nextSchedule = ref<TaskListResponse["next_schedule"] | null>(null);
const stats = ref({
  daily_success: 0,
  daily_failed: 0,
  daily_running: 0,
  adhoc_running: 0,
});

/** Placeholder slots matching reference UI (no queue stats API yet). */
const dailyRunning = computed(() => stats.value.daily_running);
const adhocRunning = computed(() => stats.value.adhoc_running);
const dailySuccess = computed(() => stats.value.daily_success);
const dailyFailed = computed(() => stats.value.daily_failed);

const periodicSlot = computed(() => `${dailyRunning.value}/1`);
const periodicHint = computed(() => t("dashboard.periodicHintCount", { ok: dailySuccess.value, failed: dailyFailed.value }));
const manualSlot = computed(() => `${adhocRunning.value}/1`);
const manualHint = computed(() => (adhocRunning.value > 0 ? t("taskFloat.running") : t("dashboard.manualHint")));
const taskHistoryRows = computed(() => tasks.value.slice(0, 20));
const nextScheduleLabel = computed(() => {
  const s = nextSchedule.value;
  if (!s) return "";
  if (s.cron_pipeline) return `Pipeline: ${s.cron_pipeline}`;
  if (s.cron_spider) return `Spider: ${s.cron_spider}`;
  return "";
});

function formatTime(v?: string): string {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return d.toLocaleString();
}

function taskModeLabel(item: TaskItem): string {
  if (item.kind === "adhoc") return t("dashboard.modeAdhoc");
  return `daily / ${item.mode || "pipeline"}`;
}

async function loadTasks() {
  try {
    const [data, statData] = (await Promise.all([
      apiFetch("/api/tasks?limit=200") as Promise<TaskListResponse>,
      apiFetch("/api/tasks/stats") as Promise<{
        daily_success?: number;
        daily_failed?: number;
        daily_running?: number;
        adhoc_running?: number;
      }>,
    ])) as [TaskListResponse, { daily_success?: number; daily_failed?: number; daily_running?: number; adhoc_running?: number }];
    tasks.value = Array.isArray(data.tasks) ? data.tasks : [];
    nextSchedule.value = data.next_schedule ?? null;
    stats.value = {
      daily_success: Number(statData.daily_success || 0),
      daily_failed: Number(statData.daily_failed || 0),
      daily_running: Number(statData.daily_running || 0),
      adhoc_running: Number(statData.adhoc_running || 0),
    };
  } catch {
    tasks.value = [];
    nextSchedule.value = null;
    stats.value = {
      daily_success: 0,
      daily_failed: 0,
      daily_running: 0,
      adhoc_running: 0,
    };
  }
}

onMounted(async () => {
  try {
    const health = (await apiFetch("/api/health", { skipAuth: true })) as {
      status?: string;
      rust_core_available?: boolean;
    };
    const ok = health.status === "ok" || health.status === "healthy";
    healthShort.value = ok ? t("dashboard.healthOk") : String(health.status ?? t("dashboard.healthUnknown"));
    healthDetail.value = t("dashboard.statusLine", { status: health.status ?? "—" });
    rustLabel.value = health.rust_core_available ? t("dashboard.rustOn") : t("dashboard.rustOff");
  } catch {
    healthShort.value = t("dashboard.unreachable");
    healthDetail.value = t("dashboard.apiUnreachable");
    rustLabel.value = "—";
  }
  await loadTasks();
});
</script>

<style scoped>
.dash-tip {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--mdc-text-secondary);
}

.dash-tip a {
  color: var(--mdc-link);
  font-weight: 500;
  text-decoration: none;
}

.dash-tip a:hover {
  text-decoration: underline;
}

.stat-card__value--sm {
  font-size: 18px;
}

.dash-schedule {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--mdc-text-secondary);
}

.task-history-table-wrap {
  overflow: auto;
}

.task-url-cell {
  max-width: 360px;
  word-break: break-all;
}

.task-history-empty {
  color: var(--mdc-text-muted);
}
</style>
