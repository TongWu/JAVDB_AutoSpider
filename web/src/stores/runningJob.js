import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { apiFetch } from "../lib/api";
const STORAGE_KEY = "javdb-running-task-v1";
function loadPersisted() {
    try {
        const raw = sessionStorage.getItem(STORAGE_KEY);
        if (!raw)
            return null;
        return JSON.parse(raw);
    }
    catch {
        return null;
    }
}
export const useRunningJobStore = defineStore("runningJob", () => {
    const jobId = ref("");
    const kind = ref(null);
    const status = ref("");
    const logText = ref("");
    /** 用户点击「停止轮询」后为 true；结束时仍会拉一次状态 */
    const pollStopped = ref(false);
    const dailyTaskTab = ref("params");
    const adhocTaskTab = ref("params");
    let intervalId = null;
    const isTerminal = computed(() => status.value === "success" || status.value === "failed");
    /** 右下角悬浮：有任务且未结束（成功/失败） */
    const showFloatingWidget = computed(() => !!jobId.value && !isTerminal.value);
    function persist() {
        const data = {
            jobId: jobId.value,
            kind: kind.value ?? "daily",
            status: status.value,
            logText: logText.value.slice(-100000),
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
        }
        catch {
            /* quota */
        }
    }
    function clearIntervalOnly() {
        if (intervalId !== null) {
            clearInterval(intervalId);
            intervalId = null;
        }
    }
    function setDailyTaskTab(tab) {
        dailyTaskTab.value = tab;
        persist();
    }
    function setAdhocTaskTab(tab) {
        adhocTaskTab.value = tab;
        persist();
    }
    async function fetchOnce(id) {
        const data = (await apiFetch(`/api/tasks/${id}`));
        status.value = data.status ?? "";
        logText.value = data.log ?? "";
        persist();
        return status.value;
    }
    function startPolling(id, k, clearLog = true) {
        clearIntervalOnly();
        pollStopped.value = false;
        jobId.value = id;
        kind.value = k;
        if (clearLog) {
            logText.value = "";
            status.value = "running";
            if (k === "daily")
                dailyTaskTab.value = "log";
            else
                adhocTaskTab.value = "log";
        }
        persist();
        const tick = async () => {
            try {
                const st = await fetchOnce(id);
                if (st === "success" || st === "failed") {
                    clearIntervalOnly();
                    persist();
                }
            }
            catch (e) {
                const msg = e instanceof Error ? e.message : String(e);
                logText.value += `\n[轮询错误] ${msg}`;
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
            }
            catch {
                /* ignore */
            }
        }
    }
    function resumePolling() {
        if (!jobId.value || pollStopped.value === false)
            return;
        if (isTerminal.value)
            return;
        pollStopped.value = false;
        persist();
        startPolling(jobId.value, kind.value ?? "daily", false);
    }
    /** App 启动 / 登录后恢复 */
    function restoreFromStorage() {
        const d = loadPersisted();
        if (!d?.jobId || !d.kind)
            return;
        jobId.value = d.jobId;
        kind.value = d.kind;
        status.value = d.status ?? "";
        logText.value = d.logText ?? "";
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
                }
                catch (e) {
                    const msg = e instanceof Error ? e.message : String(e);
                    logText.value += `\n[轮询错误] ${msg}`;
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
