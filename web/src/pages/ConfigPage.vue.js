import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { apiFetch } from "../lib/api";
import { useAuthStore } from "../stores/auth";
import ProxyPoolEditor from "../components/ProxyPoolEditor.vue";
import { SELECT_OPTIONS, fieldDescription, fieldLabel, isPathLikeField, sectionTabLabel, } from "../config/configUiLabels";
import { poolFromWire, poolToWire } from "../utils/proxyPool";
const auth = useAuthStore();
const canSave = computed(() => auth.role === "admin");
const sensitivePlaceholder = "留空则不修改已保存的敏感值";
const metaFields = ref([]);
const form = reactive({});
const initialJsonSnapshots = ref({});
const message = ref("");
const loading = ref(true);
const activeSection = ref("");
const proxyPoolRows = ref([]);
const proxyPoolTouched = ref(false);
const syncingProxyFromApi = ref(false);
const sections = computed(() => {
    const map = new Map();
    for (const f of metaFields.value) {
        if (!map.has(f.section))
            map.set(f.section, []);
        map.get(f.section).push(f);
    }
    return Array.from(map.entries());
});
const currentFields = computed(() => {
    const hit = sections.value.find(([name]) => name === activeSection.value);
    return hit ? hit[1] : [];
});
watch(sections, (list) => {
    if (!list.length)
        return;
    if (!list.some(([n]) => n === activeSection.value)) {
        activeSection.value = list[0][0];
    }
}, { immediate: true });
watch(proxyPoolRows, () => {
    if (syncingProxyFromApi.value)
        return;
    proxyPoolTouched.value = true;
}, { deep: true });
function selectOptionsFor(key) {
    const opts = SELECT_OPTIONS[key];
    if (!opts?.length)
        return [];
    const cur = String(form[key] ?? "");
    const has = opts.some((o) => o.value === cur);
    if (cur && !has)
        return [...opts, { value: cur, label: `${cur}（当前值）` }];
    return opts;
}
function focusField(id) {
    document.getElementById(id)?.focus();
}
function setFormFromApi(data) {
    const m = metaFields.value;
    const next = {};
    const snaps = {};
    for (const f of m) {
        const v = data[f.key];
        if (f.type === "bool") {
            next[f.key] = !!v;
        }
        else if (f.type === "json") {
            const fallback = f.key === "PROXY_POOL" ? [] : {};
            const s = JSON.stringify(v ?? fallback, null, 2);
            next[f.key] = s;
            snaps[f.key] = s;
        }
        else if (f.type === "int" || f.type === "float") {
            next[f.key] = v === null || v === undefined ? "" : String(v);
        }
        else if (f.sensitive && v === "********") {
            next[f.key] = "";
        }
        else if (v === null || v === undefined) {
            next[f.key] = "";
        }
        else {
            next[f.key] = String(v);
        }
    }
    for (const k of Object.keys(form)) {
        delete form[k];
    }
    Object.assign(form, next);
    initialJsonSnapshots.value = snaps;
    syncingProxyFromApi.value = true;
    try {
        const poolRaw = form["PROXY_POOL"];
        let poolParsed = [];
        if (typeof poolRaw === "string") {
            try {
                poolParsed = JSON.parse(poolRaw || "[]");
            }
            catch {
                poolParsed = [];
            }
        }
        proxyPoolRows.value = poolFromWire(poolParsed);
        proxyPoolTouched.value = false;
    }
    finally {
        void nextTick(() => {
            syncingProxyFromApi.value = false;
        });
    }
}
async function load() {
    loading.value = true;
    message.value = "";
    try {
        const [meta, data] = await Promise.all([
            apiFetch("/api/config/meta"),
            apiFetch("/api/config"),
        ]);
        metaFields.value = meta.fields;
        setFormFromApi(data);
    }
    catch (e) {
        message.value = e instanceof Error ? e.message : String(e);
    }
    finally {
        loading.value = false;
    }
}
function buildPayload() {
    const payload = {};
    for (const f of metaFields.value) {
        if (f.readonly)
            continue;
        const key = f.key;
        const raw = form[key];
        if (f.type === "bool") {
            payload[key] = !!raw;
            continue;
        }
        if (f.type === "int") {
            const s = String(raw ?? "").trim();
            if (s === "")
                continue;
            const n = parseInt(s, 10);
            if (!Number.isNaN(n))
                payload[key] = n;
            continue;
        }
        if (f.type === "float") {
            const s = String(raw ?? "").trim();
            if (s === "")
                continue;
            const n = parseFloat(s);
            if (!Number.isNaN(n))
                payload[key] = n;
            continue;
        }
        if (f.type === "json") {
            if (key === "PROXY_POOL") {
                if (!proxyPoolTouched.value)
                    continue;
                payload[key] = poolToWire(proxyPoolRows.value);
                continue;
            }
            const s = String(raw ?? "").trim();
            const orig = initialJsonSnapshots.value[key] ?? "";
            if (s === orig)
                continue;
            payload[key] = JSON.parse(s);
            continue;
        }
        const s = String(raw ?? "").trim();
        if (f.sensitive) {
            if (s === "")
                continue;
            payload[key] = s;
            continue;
        }
        payload[key] = s;
    }
    return payload;
}
async function save() {
    message.value = "";
    try {
        const payload = buildPayload();
        await apiFetch("/api/config", {
            method: "PUT",
            body: JSON.stringify(payload),
        });
        message.value = "保存成功";
        await load();
    }
    catch (e) {
        message.value = e instanceof Error ? e.message : String(e);
    }
}
onMounted(load);
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "page-shell config-page" },
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
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "mono-tip" },
});
if (__VLS_ctx.loading) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "card config-loading-card" },
    });
}
else {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "card mdc-card settings-panel" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "config-tabs-wrap" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "config-tabs" },
        role: "tablist",
    });
    for (const [[sectionName]] of __VLS_getVForSourceType((__VLS_ctx.sections))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.loading))
                        return;
                    __VLS_ctx.activeSection = sectionName;
                } },
            key: (sectionName),
            type: "button",
            role: "tab",
            ...{ class: "config-tab" },
            ...{ class: ({ 'config-tab--active': __VLS_ctx.activeSection === sectionName }) },
        });
        (__VLS_ctx.sectionTabLabel(sectionName));
    }
    if (__VLS_ctx.message) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
            ...{ class: (['config-msg', __VLS_ctx.message.includes('失败') || __VLS_ctx.message.includes('错误') ? 'config-msg--err' : '']) },
        });
        (__VLS_ctx.message);
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "setting-rows" },
    });
    for (const [f] of __VLS_getVForSourceType((__VLS_ctx.currentFields))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            key: (f.key),
            ...{ class: "setting-row" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "setting-label" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "setting-label__title" },
        });
        (__VLS_ctx.fieldLabel(f.key));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
            ...{ class: "setting-label__desc" },
        });
        (__VLS_ctx.fieldDescription(f.key, f));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "setting-label__meta" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "setting-label__key" },
        });
        (f.key);
        if (f.readonly) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "badge badge--ghost" },
            });
        }
        if (f.sensitive) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "badge badge--warn" },
            });
        }
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "badge badge--ghost" },
        });
        (f.type);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "setting-control" },
        });
        if (f.type === 'bool') {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
                ...{ class: "switch" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
                ...{ onChange: (...[$event]) => {
                        if (!!(__VLS_ctx.loading))
                            return;
                        if (!(f.type === 'bool'))
                            return;
                        __VLS_ctx.form[f.key] = $event.target.checked;
                    } },
                id: ('cfg-' + f.key),
                type: "checkbox",
                ...{ class: "switch-input" },
                checked: (__VLS_ctx.form[f.key] === true),
                disabled: (f.readonly || !__VLS_ctx.canSave),
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "switch-track" },
                'aria-hidden': "true",
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span)({
                ...{ class: "switch-thumb" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "switch-text" },
            });
            (__VLS_ctx.form[f.key] ? "开启" : "关闭");
        }
        else if (f.key === 'PROXY_POOL') {
            /** @type {[typeof ProxyPoolEditor, ]} */ ;
            // @ts-ignore
            const __VLS_0 = __VLS_asFunctionalComponent(ProxyPoolEditor, new ProxyPoolEditor({
                modelValue: (__VLS_ctx.proxyPoolRows),
                readonly: (f.readonly || !__VLS_ctx.canSave),
            }));
            const __VLS_1 = __VLS_0({
                modelValue: (__VLS_ctx.proxyPoolRows),
                readonly: (f.readonly || !__VLS_ctx.canSave),
            }, ...__VLS_functionalComponentArgsRest(__VLS_0));
        }
        else if (f.type === 'json') {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.textarea)({
                id: ('cfg-' + f.key),
                value: (__VLS_ctx.form[f.key]),
                ...{ class: "field-input field-input--code" },
                rows: "8",
                readonly: (f.readonly || !__VLS_ctx.canSave),
                spellcheck: "false",
            });
        }
        else if (__VLS_ctx.SELECT_OPTIONS[f.key]) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
                id: ('cfg-' + f.key),
                value: (__VLS_ctx.form[f.key]),
                ...{ class: "field-input field-input--select" },
                disabled: (f.readonly || !__VLS_ctx.canSave),
            });
            for (const [o] of __VLS_getVForSourceType((__VLS_ctx.selectOptionsFor(f.key)))) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
                    key: (o.value),
                    value: (o.value),
                });
                (o.label);
            }
        }
        else if (f.type === 'int') {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
                id: ('cfg-' + f.key),
                ...{ class: "field-input" },
                type: "number",
                step: "1",
                readonly: (f.readonly || !__VLS_ctx.canSave),
            });
            (__VLS_ctx.form[f.key]);
        }
        else if (f.type === 'float') {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
                id: ('cfg-' + f.key),
                ...{ class: "field-input" },
                type: "number",
                step: "any",
                readonly: (f.readonly || !__VLS_ctx.canSave),
            });
            (__VLS_ctx.form[f.key]);
        }
        else if (f.sensitive) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
                id: ('cfg-' + f.key),
                ...{ class: "field-input" },
                type: "password",
                autocomplete: "new-password",
                placeholder: (__VLS_ctx.sensitivePlaceholder),
                readonly: (f.readonly || !__VLS_ctx.canSave),
            });
            (__VLS_ctx.form[f.key]);
        }
        else if (__VLS_ctx.isPathLikeField(f.key, f.type, f.sensitive)) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "path-input-group" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
                id: ('cfg-' + f.key),
                value: (__VLS_ctx.form[f.key]),
                ...{ class: "field-input field-input--path" },
                type: "text",
                readonly: (f.readonly || !__VLS_ctx.canSave),
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                ...{ onClick: (...[$event]) => {
                        if (!!(__VLS_ctx.loading))
                            return;
                        if (!!(f.type === 'bool'))
                            return;
                        if (!!(f.key === 'PROXY_POOL'))
                            return;
                        if (!!(f.type === 'json'))
                            return;
                        if (!!(__VLS_ctx.SELECT_OPTIONS[f.key]))
                            return;
                        if (!!(f.type === 'int'))
                            return;
                        if (!!(f.type === 'float'))
                            return;
                        if (!!(f.sensitive))
                            return;
                        if (!(__VLS_ctx.isPathLikeField(f.key, f.type, f.sensitive)))
                            return;
                        __VLS_ctx.focusField('cfg-' + f.key);
                    } },
                type: "button",
                ...{ class: "btn-folder" },
                title: "聚焦输入框（路径请手动填写）",
                disabled: (f.readonly || !__VLS_ctx.canSave),
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
                ...{ class: "btn-folder__icon" },
                viewBox: "0 0 24 24",
                fill: "none",
                stroke: "currentColor",
                'aria-hidden': "true",
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
                'stroke-width': "1.75",
                'stroke-linecap': "round",
                'stroke-linejoin': "round",
                d: "M3 7.5V18a1.5 1.5 0 001.5 1.5h15A1.5 1.5 0 0021 18V8.25A1.5 1.5 0 0019.5 6.75h-7.72L9.9 4.5H4.5A1.5 1.5 0 003 6v1.5z",
            });
        }
        else {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
                id: ('cfg-' + f.key),
                value: (__VLS_ctx.form[f.key]),
                ...{ class: "field-input" },
                type: "text",
                readonly: (f.readonly || !__VLS_ctx.canSave),
            });
        }
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "config-footer" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.load) },
        type: "button",
        ...{ class: "btn-text-link" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "config-footer__actions" },
    });
    if (__VLS_ctx.canSave) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (__VLS_ctx.save) },
            type: "button",
            ...{ class: "config-save-btn" },
        });
    }
    else {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "badge badge--warn" },
        });
    }
}
/** @type {__VLS_StyleScopedClasses['page-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['config-page']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head__title']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head__sub']} */ ;
/** @type {__VLS_StyleScopedClasses['mono-tip']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['config-loading-card']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['mdc-card']} */ ;
/** @type {__VLS_StyleScopedClasses['settings-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['config-tabs-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['config-tabs']} */ ;
/** @type {__VLS_StyleScopedClasses['config-tab']} */ ;
/** @type {__VLS_StyleScopedClasses['setting-rows']} */ ;
/** @type {__VLS_StyleScopedClasses['setting-row']} */ ;
/** @type {__VLS_StyleScopedClasses['setting-label']} */ ;
/** @type {__VLS_StyleScopedClasses['setting-label__title']} */ ;
/** @type {__VLS_StyleScopedClasses['setting-label__desc']} */ ;
/** @type {__VLS_StyleScopedClasses['setting-label__meta']} */ ;
/** @type {__VLS_StyleScopedClasses['setting-label__key']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['badge--ghost']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['badge--warn']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['badge--ghost']} */ ;
/** @type {__VLS_StyleScopedClasses['setting-control']} */ ;
/** @type {__VLS_StyleScopedClasses['switch']} */ ;
/** @type {__VLS_StyleScopedClasses['switch-input']} */ ;
/** @type {__VLS_StyleScopedClasses['switch-track']} */ ;
/** @type {__VLS_StyleScopedClasses['switch-thumb']} */ ;
/** @type {__VLS_StyleScopedClasses['switch-text']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input--code']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input--select']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['path-input-group']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input--path']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-folder']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-folder__icon']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['config-footer']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-text-link']} */ ;
/** @type {__VLS_StyleScopedClasses['config-footer__actions']} */ ;
/** @type {__VLS_StyleScopedClasses['config-save-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['badge--warn']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            ProxyPoolEditor: ProxyPoolEditor,
            SELECT_OPTIONS: SELECT_OPTIONS,
            fieldDescription: fieldDescription,
            fieldLabel: fieldLabel,
            isPathLikeField: isPathLikeField,
            sectionTabLabel: sectionTabLabel,
            canSave: canSave,
            sensitivePlaceholder: sensitivePlaceholder,
            form: form,
            message: message,
            loading: loading,
            activeSection: activeSection,
            proxyPoolRows: proxyPoolRows,
            sections: sections,
            currentFields: currentFields,
            selectOptionsFor: selectOptionsFor,
            focusField: focusField,
            load: load,
            save: save,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
