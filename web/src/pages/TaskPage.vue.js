import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { apiFetch } from "../lib/api";
const keyword = ref("");
const tasks = ref([]);
const selectedJobId = ref("");
const statusLine = ref("");
const taskLog = ref("");
const error = ref("");
const fetched = ref(false);
const { t } = useI18n();
const filteredTasks = computed(() => {
    const kw = keyword.value.trim().toLowerCase();
    if (!kw)
        return tasks.value;
    return tasks.value.filter((t) => {
        const mode = taskModeLabel(t).toLowerCase();
        return (t.job_id.toLowerCase().includes(kw) ||
            mode.includes(kw) ||
            String(t.url || "")
                .toLowerCase()
                .includes(kw));
    });
});
function taskModeLabel(item) {
    if (item.kind === "adhoc")
        return "adhoc / pipeline";
    return `daily / ${item.mode || "pipeline"}`;
}
function formatTime(v) {
    if (!v)
        return "—";
    const d = new Date(v);
    if (Number.isNaN(d.getTime()))
        return v;
    return d.toLocaleString();
}
async function refreshTasks() {
    try {
        const data = (await apiFetch("/api/tasks?limit=500"));
        tasks.value = Array.isArray(data.tasks) ? data.tasks : [];
    }
    catch (e) {
        error.value = e instanceof Error ? e.message : String(e);
    }
}
async function fetchTask(jobId) {
    error.value = "";
    fetched.value = false;
    statusLine.value = "";
    taskLog.value = "";
    try {
        const data = (await apiFetch(`/api/tasks/${jobId.trim()}`));
        fetched.value = true;
        selectedJobId.value = jobId;
        statusLine.value = t("tasksPage.statusLine", { status: data.status ?? "—" });
        taskLog.value = data.log ?? "";
    }
    catch (e) {
        error.value = e instanceof Error ? e.message : String(e);
    }
}
function selectTask(jobId) {
    void fetchTask(jobId);
}
onMounted(async () => {
    await refreshTasks();
    if (tasks.value.length) {
        await fetchTask(tasks.value[0].job_id);
    }
});
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['task-list-row--active']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "page-shell" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.header, __VLS_intrinsicElements.header)({
    ...{ class: "page-head" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.h1, __VLS_intrinsicElements.h1)({
    ...{ class: "page-head__title" },
});
(__VLS_ctx.t("tasksPage.title"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "page-head__sub" },
});
(__VLS_ctx.t("tasksPage.subtitleList"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "card mdc-card" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "table-toolbar" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    type: "search",
    placeholder: (__VLS_ctx.t('tasksPage.filterPh')),
});
(__VLS_ctx.keyword);
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.refreshTasks) },
    type: "button",
    ...{ class: "ghost" },
});
(__VLS_ctx.t("tasksPage.refresh"));
if (__VLS_ctx.error) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "form-error" },
    });
    (__VLS_ctx.error);
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "task-list-wrap" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.table, __VLS_intrinsicElements.table)({
    ...{ class: "data-table" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.thead, __VLS_intrinsicElements.thead)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.tr, __VLS_intrinsicElements.tr)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
(__VLS_ctx.t("tasksPage.mode"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
(__VLS_ctx.t("tasksPage.status"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
(__VLS_ctx.t("tasksPage.createdAt"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
(__VLS_ctx.t("tasksPage.completedAt"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.tbody, __VLS_intrinsicElements.tbody)({});
for (const [it] of __VLS_getVForSourceType((__VLS_ctx.filteredTasks))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.tr, __VLS_intrinsicElements.tr)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.selectTask(it.job_id);
            } },
        key: (it.job_id),
        ...{ class: "task-list-row" },
        ...{ class: ({ 'task-list-row--active': __VLS_ctx.selectedJobId === it.job_id }) },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({});
    (it.job_id);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({});
    (__VLS_ctx.taskModeLabel(it));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({});
    (it.status || "unknown");
    __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({});
    (it.url || "—");
    __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({});
    (__VLS_ctx.formatTime(it.created_at));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({});
    (__VLS_ctx.formatTime(it.completed_at));
}
if (!__VLS_ctx.filteredTasks.length) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.tr, __VLS_intrinsicElements.tr)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({
        colspan: "6",
    });
    (__VLS_ctx.t("tasksPage.emptyTaskList"));
}
if (__VLS_ctx.statusLine) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "job-meta" },
    });
    (__VLS_ctx.statusLine);
}
if (__VLS_ctx.taskLog || __VLS_ctx.fetched) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "log-panel-wrap" },
        ...{ style: {} },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "log-panel-header" },
    });
    (__VLS_ctx.t("tasksPage.logHeader"));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.pre, __VLS_intrinsicElements.pre)({
        ...{ class: "log-live" },
        ...{ style: {} },
    });
    (__VLS_ctx.taskLog || __VLS_ctx.t("tasksPage.emptyLog"));
}
/** @type {__VLS_StyleScopedClasses['page-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head__title']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head__sub']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['mdc-card']} */ ;
/** @type {__VLS_StyleScopedClasses['table-toolbar']} */ ;
/** @type {__VLS_StyleScopedClasses['ghost']} */ ;
/** @type {__VLS_StyleScopedClasses['form-error']} */ ;
/** @type {__VLS_StyleScopedClasses['task-list-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['data-table']} */ ;
/** @type {__VLS_StyleScopedClasses['task-list-row']} */ ;
/** @type {__VLS_StyleScopedClasses['job-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['log-panel-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['log-panel-header']} */ ;
/** @type {__VLS_StyleScopedClasses['log-live']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            keyword: keyword,
            selectedJobId: selectedJobId,
            statusLine: statusLine,
            taskLog: taskLog,
            error: error,
            fetched: fetched,
            t: t,
            filteredTasks: filteredTasks,
            taskModeLabel: taskModeLabel,
            formatTime: formatTime,
            refreshTasks: refreshTasks,
            selectTask: selectTask,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
