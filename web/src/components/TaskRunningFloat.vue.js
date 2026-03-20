import { computed } from "vue";
import { useRouter } from "vue-router";
import { useRunningJobStore } from "../stores/runningJob";
const store = useRunningJobStore();
const router = useRouter();
const kindLabel = computed(() => (store.kind === "adhoc" ? "手动任务运行中" : "定期任务运行中"));
const statusLabel = computed(() => {
    if (store.pollStopped)
        return `${store.status || "运行中"} · 已暂停刷新`;
    return store.status || "运行中";
});
const shortId = computed(() => {
    const id = store.jobId;
    if (!id)
        return "";
    return id.length > 28 ? `${id.slice(0, 14)}…${id.slice(-10)}` : id;
});
const titleText = computed(() => `点击查看实时日志 · ${store.jobId}`);
function goToLog() {
    if (store.kind === "adhoc") {
        void router.push({ path: "/adhoc", query: { tab: "log" } });
    }
    else {
        void router.push({ path: "/daily", query: { tab: "log" } });
    }
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['task-float']} */ ;
/** @type {__VLS_StyleScopedClasses['task-float__spin']} */ ;
// CSS variable injection 
// CSS variable injection end 
if (__VLS_ctx.store.showFloatingWidget) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.goToLog) },
        type: "button",
        ...{ class: "task-float" },
        title: (__VLS_ctx.titleText),
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span)({
        ...{ class: "task-float__spin" },
        'aria-hidden': "true",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "task-float__body" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "task-float__label" },
    });
    (__VLS_ctx.kindLabel);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "task-float__status" },
    });
    (__VLS_ctx.statusLabel);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "task-float__id" },
    });
    (__VLS_ctx.shortId);
}
/** @type {__VLS_StyleScopedClasses['task-float']} */ ;
/** @type {__VLS_StyleScopedClasses['task-float__spin']} */ ;
/** @type {__VLS_StyleScopedClasses['task-float__body']} */ ;
/** @type {__VLS_StyleScopedClasses['task-float__label']} */ ;
/** @type {__VLS_StyleScopedClasses['task-float__status']} */ ;
/** @type {__VLS_StyleScopedClasses['task-float__id']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            store: store,
            kindLabel: kindLabel,
            statusLabel: statusLabel,
            shortId: shortId,
            titleText: titleText,
            goToLog: goToLog,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
