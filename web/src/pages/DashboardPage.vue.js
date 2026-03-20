import { onMounted, ref } from "vue";
import { apiFetch } from "../lib/api";
const healthShort = ref("…");
const healthDetail = ref("加载中");
const rustLabel = ref("—");
/** 与参考 UI 一致的占位槽位（后端暂无队列统计接口时） */
const periodicSlot = ref("0/1");
const periodicHint = ref("空闲 · 成功: — · 失败: —");
const manualSlot = ref("0/1");
const manualHint = ref("空闲");
onMounted(async () => {
    try {
        const health = (await apiFetch("/api/health", { skipAuth: true }));
        const ok = health.status === "ok" || health.status === "healthy";
        healthShort.value = ok ? "正常" : String(health.status ?? "未知");
        healthDetail.value = `状态: ${health.status ?? "—"}`;
        rustLabel.value = health.rust_core_available ? "可用" : "未启用";
    }
    catch {
        healthShort.value = "不可达";
        healthDetail.value = "无法连接 API";
        rustLabel.value = "—";
    }
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
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "page-head__sub" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "section-rule" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-grid" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card__label" },
});
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
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card__value stat-card__value--sm" },
});
(__VLS_ctx.rustLabel);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-card__hint" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "section-rule" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "card mdc-card" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "dash-tip" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.strong, __VLS_intrinsicElements.strong)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.strong, __VLS_intrinsicElements.strong)({});
const __VLS_0 = {}.RouterLink;
/** @type {[typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, ]} */ ;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
    to: "/tasks",
}));
const __VLS_2 = __VLS_1({
    to: "/tasks",
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
__VLS_3.slots.default;
var __VLS_3;
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
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            healthShort: healthShort,
            healthDetail: healthDetail,
            rustLabel: rustLabel,
            periodicSlot: periodicSlot,
            periodicHint: periodicHint,
            manualSlot: manualSlot,
            manualHint: manualHint,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
