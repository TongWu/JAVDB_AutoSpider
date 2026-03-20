import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { apiFetch } from "../lib/api";
import { useRunningJobStore } from "../stores/runningJob";
const store = useRunningJobStore();
const route = useRoute();
const taskTab = ref("params");
const logEl = ref(null);
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
    if (submitError.value)
        return submitError.value;
    if (store.kind === "adhoc" && store.logText)
        return store.logText;
    return "提交任务后将在此显示日志…";
});
watch([() => store.logText, () => submitError.value], async () => {
    if (store.kind !== "adhoc" && !submitError.value)
        return;
    await nextTick();
    const el = logEl.value;
    if (el)
        el.scrollTop = el.scrollHeight;
});
watch(() => route.query.tab, (t) => {
    if (t === "log") {
        taskTab.value = "log";
        store.setAdhocTaskTab("log");
        if (store.kind === "adhoc" && store.jobId && store.pollStopped && !isTerminal.value) {
            store.resumePolling();
        }
    }
}, { immediate: true });
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
        store.startPolling(data.job_id, "adhoc", true);
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
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
    ...{ class: "span-2" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    placeholder: "https://javdb.com/actors/xxx",
});
(__VLS_ctx.form.url);
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
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    placeholder: "可选",
});
(__VLS_ctx.form.qb_category);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "checkbox-row" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    type: "checkbox",
});
(__VLS_ctx.form.history_filter);
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    type: "checkbox",
});
(__VLS_ctx.form.date_filter);
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    type: "checkbox",
});
(__VLS_ctx.form.use_proxy);
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    type: "checkbox",
});
(__VLS_ctx.form.proxy_uploader);
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    type: "checkbox",
});
(__VLS_ctx.form.proxy_pikpak);
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
/** @type {__VLS_StyleScopedClasses['span-2']} */ ;
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
