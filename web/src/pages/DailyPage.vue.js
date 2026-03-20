import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { apiFetch } from "../lib/api";
import { useRunningJobStore } from "../stores/runningJob";
const store = useRunningJobStore();
const route = useRoute();
const taskTab = ref("params");
const logEl = ref(null);
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
    if (submitError.value)
        return submitError.value;
    if (store.kind === "daily" && store.logText)
        return store.logText;
    return "提交任务后将在此显示日志…";
});
watch([() => store.logText, () => submitError.value], async () => {
    if (store.kind !== "daily" && !submitError.value)
        return;
    await nextTick();
    const el = logEl.value;
    if (el)
        el.scrollTop = el.scrollHeight;
});
watch(() => route.query.tab, (t) => {
    if (t === "log") {
        taskTab.value = "log";
        store.setDailyTaskTab("log");
        if (store.kind === "daily" && store.jobId && store.pollStopped && !isTerminal.value) {
            store.resumePolling();
        }
    }
}, { immediate: true });
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
        store.startPolling(data.job_id, "daily", true);
        taskTab.value = "log";
    }
    catch (e) {
        submitError.value = `[提交失败] ${e instanceof Error ? e.message : String(e)}`;
        taskTab.value = "log";
    }
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
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
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "page-head__sub" },
});
if (__VLS_ctx.jobForPage) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "page-head__meta" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({
        ...{ class: "meta-code" },
    });
    (__VLS_ctx.jobForPage);
    (__VLS_ctx.statusForPage || "—");
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "card mdc-card task-form-card" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "config-tabs" },
    role: "tablist",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.taskTab = 'params';
        } },
    type: "button",
    role: "tab",
    ...{ class: "config-tab" },
    ...{ class: ({ 'config-tab--active': __VLS_ctx.taskTab === 'params' }) },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.openLogTab) },
    type: "button",
    role: "tab",
    ...{ class: "config-tab" },
    ...{ class: ({ 'config-tab--active': __VLS_ctx.taskTab === 'log' }) },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "task-form-card__body" },
});
__VLS_asFunctionalDirective(__VLS_directives.vShow)(null, { ...__VLS_directiveBindingRestFields, value: (__VLS_ctx.taskTab === 'params') }, null, null);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "toolbar-row" },
    ...{ style: {} },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.submit) },
    type: "button",
});
if (__VLS_ctx.jobForPage) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.store.stopPolling) },
        type: "button",
        ...{ class: "ghost" },
    });
}
if (__VLS_ctx.jobForPage && __VLS_ctx.store.pollStopped && !__VLS_ctx.isTerminal) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.store.resumePolling) },
        type: "button",
        ...{ class: "ghost" },
    });
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "grid" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    type: "number",
    min: "1",
});
(__VLS_ctx.form.start_page);
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    type: "number",
    min: "1",
});
(__VLS_ctx.form.end_page);
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
    value: (__VLS_ctx.form.phase),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: "1",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: "2",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: "all",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
    value: (__VLS_ctx.form.mode),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: "pipeline",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: "spider",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "checkbox-row" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    type: "checkbox",
});
(__VLS_ctx.form.use_proxy);
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    type: "checkbox",
});
(__VLS_ctx.form.dry_run);
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    type: "checkbox",
});
(__VLS_ctx.form.ignore_release_date);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "task-form-card__body task-form-card__body--log" },
});
__VLS_asFunctionalDirective(__VLS_directives.vShow)(null, { ...__VLS_directiveBindingRestFields, value: (__VLS_ctx.taskTab === 'log') }, null, null);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "log-panel-wrap" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "log-panel-header" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.pre, __VLS_intrinsicElements.pre)({
    ref: "logEl",
    ...{ class: "log-live" },
});
/** @type {typeof __VLS_ctx.logEl} */ ;
(__VLS_ctx.logDisplay);
/** @type {__VLS_StyleScopedClasses['page-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head__title']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head__sub']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head__meta']} */ ;
/** @type {__VLS_StyleScopedClasses['meta-code']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['mdc-card']} */ ;
/** @type {__VLS_StyleScopedClasses['task-form-card']} */ ;
/** @type {__VLS_StyleScopedClasses['config-tabs']} */ ;
/** @type {__VLS_StyleScopedClasses['config-tab']} */ ;
/** @type {__VLS_StyleScopedClasses['config-tab']} */ ;
/** @type {__VLS_StyleScopedClasses['task-form-card__body']} */ ;
/** @type {__VLS_StyleScopedClasses['toolbar-row']} */ ;
/** @type {__VLS_StyleScopedClasses['ghost']} */ ;
/** @type {__VLS_StyleScopedClasses['ghost']} */ ;
/** @type {__VLS_StyleScopedClasses['grid']} */ ;
/** @type {__VLS_StyleScopedClasses['checkbox-row']} */ ;
/** @type {__VLS_StyleScopedClasses['task-form-card__body']} */ ;
/** @type {__VLS_StyleScopedClasses['task-form-card__body--log']} */ ;
/** @type {__VLS_StyleScopedClasses['log-panel-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['log-panel-header']} */ ;
/** @type {__VLS_StyleScopedClasses['log-live']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            store: store,
            taskTab: taskTab,
            logEl: logEl,
            form: form,
            jobForPage: jobForPage,
            statusForPage: statusForPage,
            isTerminal: isTerminal,
            logDisplay: logDisplay,
            openLogTab: openLogTab,
            submit: submit,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
