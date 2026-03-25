import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { i18n } from "../i18n";
import { apiFetch } from "../lib/api";

const STORAGE_KEY = "javdb-running-task-v1";

export type JobKind = "daily" | "adhoc";
export type TaskTab = "params" | "log";

type Persisted = {
  jobId: string;
  kind: JobKind;
  status: string;
  logText: string;
  logOffset: number;
  pollStopped: boolean;
  dailyTaskTab: TaskTab;
  adhocTaskTab: TaskTab;
};

function loadPersisted(): Partial<Persisted> | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as Persisted;
  } catch {
    return null;
  }
}

export const useRunningJobStore = defineStore("runningJob", () => {
  const jobId = ref("");
  const kind = ref<JobKind | null>(null);
  const status = ref("");
  const logText = ref("");
  const logOffset = ref(0);
  /** True after user clicks "stop polling"; status is still fetched once on stop/complete. */
  const pollStopped = ref(false);
  const dailyTaskTab = ref<TaskTab>("params");
  const adhocTaskTab = ref<TaskTab>("params");

  let intervalId: ReturnType<typeof setInterval> | null = null;

  const isTerminal = computed(() => status.value === "success" || status.value === "failed");

  /** Bottom-right chip: shown while a job exists and is not success/failed. */
  const showFloatingWidget = computed(() => !!jobId.value && !isTerminal.value);

  function persist() {
    const data: Persisted = {
      jobId: jobId.value,
      kind: kind.value ?? "daily",
      status: status.value,
      logText: logText.value.slice(-100000),
      logOffset: logOffset.value,
      pollStopped: pollStopped.value,
      dailyTaskTab: dailyTaskTab.value,
      adhocTaskTab: adhocTaskTab.value,
    };
    try {
      if (!jobId.value) {
        sessionStorage.removeItem(STORAGE_KEY);
        return;
      }
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    } catch {
      /* quota */
    }
  }

  function clearIntervalOnly() {
    if (intervalId !== null) {
      clearInterval(intervalId);
      intervalId = null;
    }
  }

  function setDailyTaskTab(tab: TaskTab) {
    dailyTaskTab.value = tab;
    persist();
  }

  function setAdhocTaskTab(tab: TaskTab) {
    adhocTaskTab.value = tab;
    persist();
  }

  async function fetchOnce(id: string): Promise<string> {
    const data = (await apiFetch(`/api/tasks/${id}/stream?offset=${logOffset.value}`)) as {
      status?: string;
      chunk?: string;
      next_offset?: number;
    };
    status.value = data.status ?? "";
    if (typeof data.chunk === "string" && data.chunk.length > 0) {
      logText.value += data.chunk;
      if (logText.value.length > 100000) {
        logText.value = logText.value.slice(-100000);
      }
    }
    if (typeof data.next_offset === "number" && Number.isFinite(data.next_offset)) {
      logOffset.value = Math.max(0, data.next_offset);
    }
    persist();
    return status.value;
  }

  function startPolling(id: string, k: JobKind, clearLog = true) {
    clearIntervalOnly();
    pollStopped.value = false;
    jobId.value = id;
    kind.value = k;
    if (clearLog) {
      logText.value = "";
      logOffset.value = 0;
      status.value = "running";
      if (k === "daily") dailyTaskTab.value = "log";
      else adhocTaskTab.value = "log";
    }
    persist();

    const tick = async () => {
      try {
        const st = await fetchOnce(id);
        if (st === "success" || st === "failed") {
          clearIntervalOnly();
          persist();
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        logText.value += `\n${i18n.global.t("errors.pollError", { msg })}`;
        persist();
      }
    };

    void tick();
    intervalId = setInterval(() => void tick(), 2000);
  }

  async function stopPolling() {
    clearIntervalOnly();
    pollStopped.value = true;
    persist();
    if (jobId.value) {
      try {
        await fetchOnce(jobId.value);
      } catch {
        /* ignore */
      }
    }
  }

  function resumePolling() {
    if (!jobId.value || pollStopped.value === false) return;
    if (isTerminal.value) return;
    pollStopped.value = false;
    persist();
    startPolling(jobId.value, kind.value ?? "daily", false);
  }

  /** Restore state from sessionStorage after app load or login. */
  function restoreFromStorage() {
    const d = loadPersisted();
    if (!d?.jobId || !d.kind) return;

    jobId.value = d.jobId;
    kind.value = d.kind;
    status.value = d.status ?? "";
    logText.value = d.logText ?? "";
    logOffset.value = typeof d.logOffset === "number" ? d.logOffset : 0;
    pollStopped.value = !!d.pollStopped;
    dailyTaskTab.value = d.dailyTaskTab === "log" ? "log" : "params";
    adhocTaskTab.value = d.adhocTaskTab === "log" ? "log" : "params";

    if (status.value === "success" || status.value === "failed") {
      clearIntervalOnly();
      return;
    }

    if (!pollStopped.value) {
      clearIntervalOnly();
      const id = jobId.value;
      const tick = async () => {
        try {
          const st = await fetchOnce(id);
          if (st === "success" || st === "failed") {
            clearIntervalOnly();
            persist();
          }
        } catch (e: unknown) {
          const msg = e instanceof Error ? e.message : String(e);
          logText.value += `\n${i18n.global.t("errors.pollError", { msg })}`;
          persist();
        }
      };
      void tick();
      intervalId = setInterval(() => void tick(), 2000);
    }
  }

  function clearJob() {
    clearIntervalOnly();
    jobId.value = "";
    kind.value = null;
    status.value = "";
    logText.value = "";
    logOffset.value = 0;
    pollStopped.value = false;
    dailyTaskTab.value = "params";
    adhocTaskTab.value = "params";
    sessionStorage.removeItem(STORAGE_KEY);
  }

  return {
    jobId,
    kind,
    status,
    logText,
    logOffset,
    pollStopped,
    dailyTaskTab,
    adhocTaskTab,
    isTerminal,
    showFloatingWidget,
    persist,
    fetchOnce,
    startPolling,
    stopPolling,
    resumePolling,
    restoreFromStorage,
    clearJob,
    setDailyTaskTab,
    setAdhocTaskTab,
  };
});
