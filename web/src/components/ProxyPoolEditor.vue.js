import { useI18n } from "vue-i18n";
import { emptyProxyRow } from "../utils/proxyPool";
const __VLS_props = defineProps();
const { t } = useI18n();
const model = defineModel({ required: true });
function addRow() {
    const nextPrio = model.value.length ? Math.max(...model.value.map((r) => r.priority)) + 10 : 0;
    model.value = [...model.value, emptyProxyRow(nextPrio)];
}
function removeRow(idx) {
    const next = model.value.filter((_, i) => i !== idx);
    model.value = next.length ? next : [emptyProxyRow(0)];
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_defaults = {};
const __VLS_modelEmit = defineEmits();
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['proxy-pool-editor__hint']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-grid']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "proxy-pool-editor" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "proxy-pool-editor__hint" },
});
(__VLS_ctx.t("proxyEditor.hint"));
for (const [row, idx] of __VLS_getVForSourceType((__VLS_ctx.model))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        key: (row._id),
        ...{ class: "proxy-card" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "proxy-card__head" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "proxy-card__title" },
    });
    (__VLS_ctx.t("proxyEditor.cardTitle", { n: idx + 1 }));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.removeRow(idx);
            } },
        type: "button",
        ...{ class: "proxy-card__remove btn-text-link" },
        disabled: (__VLS_ctx.readonly),
    });
    (__VLS_ctx.t("proxyEditor.remove"));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "proxy-grid" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "proxy-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    (__VLS_ctx.t("proxyEditor.displayName"));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        value: (row.name),
        ...{ class: "field-input" },
        type: "text",
        readonly: (__VLS_ctx.readonly),
        placeholder: (__VLS_ctx.t('proxyEditor.namePlaceholder')),
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "proxy-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    (__VLS_ctx.t("proxyEditor.priority"));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "field-input" },
        type: "number",
        step: "1",
        readonly: (__VLS_ctx.readonly),
    });
    (row.priority);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "proxy-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    (__VLS_ctx.t("proxyEditor.protocol"));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
        value: (row.scheme),
        ...{ class: "field-input field-input--select" },
        disabled: (__VLS_ctx.readonly),
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        value: "http",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        value: "https",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        value: "socks5",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        value: "socks5h",
    });
    (__VLS_ctx.t("proxyEditor.socks5h"));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "proxy-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    (__VLS_ctx.t("proxyEditor.host"));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        value: (row.host),
        ...{ class: "field-input" },
        type: "text",
        readonly: (__VLS_ctx.readonly),
        placeholder: "127.0.0.1",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "proxy-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    (__VLS_ctx.t("proxyEditor.port"));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        value: (row.port),
        ...{ class: "field-input" },
        type: "text",
        readonly: (__VLS_ctx.readonly),
        placeholder: "8080",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "proxy-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    (__VLS_ctx.t("proxyEditor.username"));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        value: (row.username),
        ...{ class: "field-input" },
        type: "text",
        readonly: (__VLS_ctx.readonly),
        autocomplete: "off",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "proxy-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    (__VLS_ctx.t("proxyEditor.password"));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "field-input" },
        type: "password",
        readonly: (__VLS_ctx.readonly),
        autocomplete: "new-password",
        placeholder: (__VLS_ctx.t('proxyEditor.passwordPlaceholder')),
    });
    (row.password);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "proxy-field proxy-field--check" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        type: "checkbox",
        disabled: (__VLS_ctx.readonly),
    });
    (row.sameForHttps);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    (__VLS_ctx.t("proxyEditor.sameHttps"));
    if (!row.sameForHttps) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
            ...{ class: "proxy-field" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        (__VLS_ctx.t("proxyEditor.httpsHost"));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
            value: (row.httpsHost),
            ...{ class: "field-input" },
            type: "text",
            readonly: (__VLS_ctx.readonly),
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
            ...{ class: "proxy-field" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        (__VLS_ctx.t("proxyEditor.httpsPort"));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
            value: (row.httpsPort),
            ...{ class: "field-input" },
            type: "text",
            readonly: (__VLS_ctx.readonly),
        });
    }
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.addRow) },
    type: "button",
    ...{ class: "ghost proxy-add" },
    disabled: (__VLS_ctx.readonly),
});
(__VLS_ctx.t("proxyEditor.add"));
/** @type {__VLS_StyleScopedClasses['proxy-pool-editor']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-pool-editor__hint']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-card']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-card__head']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-card__title']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-card__remove']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-text-link']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-field']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-field']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-field']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input--select']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-field']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-field']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-field']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-field']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-field']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-field--check']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-field']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-field']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['ghost']} */ ;
/** @type {__VLS_StyleScopedClasses['proxy-add']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            t: t,
            model: model,
            addRow: addRow,
            removeRow: removeRow,
        };
    },
    __typeEmits: {},
    __typeProps: {},
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
    __typeEmits: {},
    __typeProps: {},
});
; /* PartiallyEnd: #4569/main.vue */
