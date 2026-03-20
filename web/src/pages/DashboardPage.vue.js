import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { apiFetch } from "../lib/api";
const { t } = useI18n();
const healthShort = ref("…");
const healthDetail = ref(t("dashboard.loading"));
const rustLabel = ref("—");
const tasks = ref([]);
const nextSchedule = ref(null);
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
    if (!s)
        return "";
    if (s.cron_pipeline)
        return `Pipeline: ${s.cron_pipeline}`;
    if (s.cron_spider)
        return `Spider: ${s.cron_spider}`;
    return "";
});
function formatTime(v) {
    if (!v)
        return "—";
    const d = new Date(v);
    if (Number.isNaN(d.getTime()))
        return v;
    return d.toLocaleString();
}
function taskModeLabel(item) {
    if (item.kind === "adhoc")
        return t("dashboard.modeAdhoc");
    return `daily / ${item.mode || "pipeline"}`;
}
async function loadTasks() {
    try {
        const [data, statData] = (await Promise.all([
            apiFetch("/api/tasks?limit=200"),
            apiFetch("/api/tasks/stats"),
        ]));
        tasks.value = Array.isArray(data.tasks) ? data.tasks : [];
        nextSchedule.value = data.next_schedule ?? null;
        stats.value = {
            daily_success: Number(statData.daily_success || 0),
            daily_failed: Number(statData.daily_failed || 0),
            daily_running: Number(statData.daily_running || 0),
            adhoc_running: Number(statData.adhoc_running || 0),
        };
    }
    catch {
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
        const health = (await apiFetch("/api/health", { skipAuth: true }));
        const ok = health.status === "ok" || health.status === "healthy";
        healthShort.value = ok ? t("dashboard.healthOk") : String(health.status ?? t("dashboard.healthUnknown"));
        healthDetail.value = t("dashboard.statusLine", { status: health.status ?? "—" });
        rustLabel.value = health.rust_core_available ? t("dashboard.rustOn") : t("dashboard.rustOff");
    }
    catch {
        healthShort.value = t("dashboard.unreachable");
        healthDetail.value = t("dashboard.apiUnreachable");
        rustLabel.value = "—";
    }
    await loadTasks();
});
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['dash-tip']} */ ;
/** @type {__VLS_StyleScopedClasses['dash-tip']} */ ;
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
(__VLS_ctx.t("dashboard.title"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "page-head__sub" },
});
(__VLS_ctx.t("dashboard.subtitle"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "section-rule" },
});
(__VLS_ctx.t("dashboard.sectionStatus"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-grid" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card__label" },
});
(__VLS_ctx.t("dashboard.periodic"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card__value" },
});
(__VLS_ctx.periodicSlot);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card__hint" },
});
(__VLS_ctx.periodicHint);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card__label" },
});
(__VLS_ctx.t("dashboard.manual"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card__value" },
});
(__VLS_ctx.manualSlot);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card__hint" },
});
(__VLS_ctx.manualHint);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card__label" },
});
(__VLS_ctx.t("dashboard.apiHealth"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card__value stat-card__value--sm" },
});
(__VLS_ctx.healthShort);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card__hint" },
});
(__VLS_ctx.healthDetail);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card__label" },
});
(__VLS_ctx.t("dashboard.rustCore"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card__value stat-card__value--sm" },
});
(__VLS_ctx.rustLabel);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card__hint" },
});
(__VLS_ctx.t("dashboard.rustHint"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "section-rule" },
});
(__VLS_ctx.t("dashboard.sectionTips"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "card mdc-card" },
});
const __VLS_0 = {}.I18nT;
/** @type {[typeof __VLS_components.I18nT, typeof __VLS_components.i18nT, typeof __VLS_components.I18nT, typeof __VLS_components.i18nT, ]} */ ;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
    keypath: "dashboard.tipFull",
    tag: "p",
    ...{ class: "dash-tip" },
}));
const __VLS_2 = __VLS_1({
    keypath: "dashboard.tipFull",
    tag: "p",
    ...{ class: "dash-tip" },
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
__VLS_3.slots.default;
{
    const { tasks: __VLS_thisSlot } = __VLS_3.slots;
    const __VLS_4 = {}.RouterLink;
    /** @type {[typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, ]} */ ;
    // @ts-ignore
    const __VLS_5 = __VLS_asFunctionalComponent(__VLS_4, new __VLS_4({
        to: "/tasks",
    }));
    const __VLS_6 = __VLS_5({
        to: "/tasks",
    }, ...__VLS_functionalComponentArgsRest(__VLS_5));
    __VLS_7.slots.default;
    (__VLS_ctx.t("nav.tasks"));
    var __VLS_7;
}
var __VLS_3;
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "section-rule" },
});
(__VLS_ctx.t("dashboard.sectionHistory"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "card mdc-card" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "dash-schedule" },
});
(__VLS_ctx.t("dashboard.nextSchedule"));
if (__VLS_ctx.nextScheduleLabel) {
    (__VLS_ctx.nextScheduleLabel);
}
else {
    (__VLS_ctx.t("dashboard.scheduleNotConfigured"));
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "task-history-table-wrap" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.table, __VLS_intrinsicElements.table)({
    ...{ class: "data-table" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.thead, __VLS_intrinsicElements.thead)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.tr, __VLS_intrinsicElements.tr)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
(__VLS_ctx.t("dashboard.mode"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
(__VLS_ctx.t("dashboard.status"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
(__VLS_ctx.t("dashboard.createdAt"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
(__VLS_ctx.t("dashboard.completedAt"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.tbody, __VLS_intrinsicElements.tbody)({});
for (const [t] of __VLS_getVForSourceType((__VLS_ctx.taskHistoryRows))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.tr, __VLS_intrinsicElements.tr)({
        key: (t.job_id),
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({});
    (t.job_id);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({});
    (__VLS_ctx.taskModeLabel(t));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({});
    (t.status || "unknown");
    __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({
        ...{ class: "task-url-cell" },
    });
    (t.url || "—");
    __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({});
    (__VLS_ctx.formatTime(t.created_at));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({});
    (__VLS_ctx.formatTime(t.completed_at));
}
if (!__VLS_ctx.taskHistoryRows.length) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.tr, __VLS_intrinsicElements.tr)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({
        colspan: "6",
        ...{ class: "task-history-empty" },
    });
    (__VLS_ctx.t("dashboard.noHistory"));
}
/** @type {__VLS_StyleScopedClasses['page-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head__title']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head__sub']} */ ;
/** @type {__VLS_StyleScopedClasses['section-rule']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card__label']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card__value']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card__hint']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card__label']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card__value']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card__hint']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card__label']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card__value']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card__value--sm']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card__hint']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card__label']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card__value']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card__value--sm']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card__hint']} */ ;
/** @type {__VLS_StyleScopedClasses['section-rule']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['mdc-card']} */ ;
/** @type {__VLS_StyleScopedClasses['dash-tip']} */ ;
/** @type {__VLS_StyleScopedClasses['section-rule']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['mdc-card']} */ ;
/** @type {__VLS_StyleScopedClasses['dash-schedule']} */ ;
/** @type {__VLS_StyleScopedClasses['task-history-table-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['data-table']} */ ;
/** @type {__VLS_StyleScopedClasses['task-url-cell']} */ ;
/** @type {__VLS_StyleScopedClasses['task-history-empty']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            t: t,
            healthShort: healthShort,
            healthDetail: healthDetail,
            rustLabel: rustLabel,
            periodicSlot: periodicSlot,
            periodicHint: periodicHint,
            manualSlot: manualSlot,
            manualHint: manualHint,
            taskHistoryRows: taskHistoryRows,
            nextScheduleLabel: nextScheduleLabel,
            formatTime: formatTime,
            taskModeLabel: taskModeLabel,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
