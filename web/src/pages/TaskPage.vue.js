import { ref } from "vue";
import { apiFetch } from "../lib/api";
const jobId = ref("");
const statusLine = ref("");
const taskLog = ref("");
const error = ref("");
const fetched = ref(false);
async function fetchTask() {
    error.value = "";
    fetched.value = false;
    statusLine.value = "";
    taskLog.value = "";
    if (!jobId.value.trim()) {
        error.value = "请输入 job_id";
        return;
    }
    try {
        const data = (await apiFetch(`/api/tasks/${jobId.value.trim()}`));
        fetched.value = true;
        statusLine.value = `状态: ${data.status ?? "—"}`;
        taskLog.value = data.log ?? "";
    }
    catch (e) {
        error.value = e instanceof Error ? e.message : String(e);
    }
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
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
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "card mdc-card" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "table-toolbar" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    ...{ onKeyup: (__VLS_ctx.fetchTask) },
    type: "search",
    placeholder: "输入 job_id",
});
(__VLS_ctx.jobId);
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.fetchTask) },
    type: "button",
});
if (__VLS_ctx.error) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "form-error" },
    });
    (__VLS_ctx.error);
}
else if (__VLS_ctx.statusLine) {
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
    __VLS_asFunctionalElement(__VLS_intrinsicElements.pre, __VLS_intrinsicElements.pre)({
        ...{ class: "log-live" },
        ...{ style: {} },
    });
    (__VLS_ctx.taskLog || "（无日志内容）");
}
/** @type {__VLS_StyleScopedClasses['page-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head__title']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head__sub']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['mdc-card']} */ ;
/** @type {__VLS_StyleScopedClasses['table-toolbar']} */ ;
/** @type {__VLS_StyleScopedClasses['form-error']} */ ;
/** @type {__VLS_StyleScopedClasses['job-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['log-panel-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['log-panel-header']} */ ;
/** @type {__VLS_StyleScopedClasses['log-live']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            jobId: jobId,
            statusLine: statusLine,
            taskLog: taskLog,
            error: error,
            fetched: fetched,
            fetchTask: fetchTask,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
