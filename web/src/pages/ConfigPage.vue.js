import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { apiFetch } from "../lib/api";
import { useAuthStore } from "../stores/auth";
import ProxyPoolEditor from "../components/ProxyPoolEditor.vue";
import { SELECT_OPTION_KEYS } from "../config/configFormConstants";
import { isPathLikeField, useConfigFormLabels } from "../composables/useConfigFormLabels";
import { poolFromWire, poolToWire } from "../utils/proxyPool";
const auth = useAuthStore();
const canSave = computed(() => auth.role === "admin");
const { t } = useI18n();
const sensitivePlaceholder = computed(() => t("config.sensitivePlaceholder"));
const { sectionTabLabel, fieldLabel, fieldDescription, selectOptionsFor } = useConfigFormLabels();
const metaFields = ref([]);
const form = reactive({});
const initialJsonSnapshots = ref({});
const message = ref("");
const messageIsError = ref(false);
const loading = ref(true);
const activeSection = ref("");
const proxyPoolRows = ref([]);
const proxyPoolTouched = ref(false);
const syncingProxyFromApi = ref(false);
const rclonePlaintext = ref("");
const rcloneBase64Preview = ref("");
const cfBypassLocalPort = ref("");
const cfBypassDefaultPort = ref("");
const cfBypassProxyPorts = reactive({});
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
const visibleCurrentFields = computed(() => currentFields.value.filter((f) => !["PROXY_HTTP", "PROXY_HTTPS"].includes(f.key)));
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
function focusField(id) {
    document.getElementById(id)?.focus();
}
function decodeBase64ToText(raw) {
    const v = String(raw ?? "").trim();
    if (!v)
        return "";
    try {
        return decodeURIComponent(Array.prototype.map
            .call(atob(v), (ch) => `%${(`00${ch.charCodeAt(0).toString(16)}`).slice(-2)}`)
            .join(""));
    }
    catch {
        return "";
    }
}
function encodeTextToBase64(raw) {
    const text = String(raw ?? "");
    const bytes = encodeURIComponent(text).replace(/%([0-9A-F]{2})/g, (_m, p1) => String.fromCharCode(parseInt(p1, 16)));
    return btoa(bytes);
}
function decodeRcloneFromCurrent() {
    rcloneBase64Preview.value = "";
    const decoded = decodeBase64ToText(String(form["RCLONE_CONFIG_BASE64"] ?? ""));
    rclonePlaintext.value = decoded || String(form["RCLONE_CONFIG_BASE64"] ?? "");
}
function showRcloneBase64Preview() {
    rcloneBase64Preview.value = encodeTextToBase64(rclonePlaintext.value);
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
    const rcloneDecoded = decodeBase64ToText(String(next["RCLONE_CONFIG_BASE64"] ?? ""));
    rclonePlaintext.value = rcloneDecoded || String(next["RCLONE_CONFIG_BASE64"] ?? "");
    rcloneBase64Preview.value = "";
    cfBypassLocalPort.value = "";
    cfBypassDefaultPort.value = "";
    for (const k of Object.keys(cfBypassProxyPorts))
        delete cfBypassProxyPorts[k];
    try {
        const cfMap = JSON.parse(String(next["CF_BYPASS_PORT_MAP"] || "{}"));
        if (cfMap && typeof cfMap === "object") {
            cfBypassLocalPort.value = cfMap.local === undefined ? "" : String(cfMap.local);
            cfBypassDefaultPort.value = cfMap.default === undefined ? "" : String(cfMap.default);
            if (cfMap.proxies && typeof cfMap.proxies === "object") {
                for (const [name, port] of Object.entries(cfMap.proxies)) {
                    cfBypassProxyPorts[name] = String(port ?? "");
                }
            }
        }
    }
    catch {
        /* ignore malformed json */
    }
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
    messageIsError.value = false;
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
        messageIsError.value = true;
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
            if (key === "RCLONE_CONFIG_BASE64") {
                const encoded = encodeTextToBase64(rclonePlaintext.value);
                const origEncoded = String(form["RCLONE_CONFIG_BASE64"] ?? "");
                if (encoded === origEncoded)
                    continue;
                payload[key] = encoded;
                continue;
            }
            if (key === "CF_BYPASS_PORT_MAP") {
                const nextMap = {};
                const localPort = parseInt(String(cfBypassLocalPort.value || "").trim(), 10);
                if (!Number.isNaN(localPort))
                    nextMap.local = localPort;
                const defaultPort = parseInt(String(cfBypassDefaultPort.value || "").trim(), 10);
                if (!Number.isNaN(defaultPort))
                    nextMap.default = defaultPort;
                const proxyMap = {};
                for (const [name, value] of Object.entries(cfBypassProxyPorts)) {
                    const n = parseInt(String(value || "").trim(), 10);
                    if (!name || Number.isNaN(n))
                        continue;
                    proxyMap[name] = n;
                }
                if (Object.keys(proxyMap).length)
                    nextMap.proxies = proxyMap;
                const nextJson = JSON.stringify(nextMap, null, 2);
                const orig = initialJsonSnapshots.value[key] ?? "";
                if (nextJson !== orig) {
                    payload[key] = nextMap;
                }
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
    messageIsError.value = false;
    try {
        const payload = buildPayload();
        await apiFetch("/api/config", {
            method: "PUT",
            body: JSON.stringify(payload),
        });
        message.value = t("config.saveSuccess");
        await load();
    }
    catch (e) {
        message.value = e instanceof Error ? e.message : String(e);
        messageIsError.value = true;
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
(__VLS_ctx.t("config.title"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "page-head__sub" },
});
(__VLS_ctx.t("config.subtitle"));
if (__VLS_ctx.loading) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "card config-loading-card" },
    });
    (__VLS_ctx.t("config.loading"));
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
            ...{ class: (['config-msg', __VLS_ctx.messageIsError ? 'config-msg--err' : '']) },
        });
        (__VLS_ctx.message);
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "setting-rows" },
    });
    for (const [f] of __VLS_getVForSourceType((__VLS_ctx.visibleCurrentFields))) {
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
            (__VLS_ctx.t("config.badgeReadonly"));
        }
        if (f.sensitive) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "badge badge--warn" },
            });
            (__VLS_ctx.t("config.badgeSensitive"));
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
            (__VLS_ctx.form[f.key] ? __VLS_ctx.t("config.on") : __VLS_ctx.t("config.off"));
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
            if (f.key === 'RCLONE_CONFIG_BASE64') {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ class: "rclone-config-editor" },
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.textarea)({
                    id: ('cfg-' + f.key),
                    value: (__VLS_ctx.rclonePlaintext),
                    ...{ class: "field-input field-input--code" },
                    rows: "8",
                    readonly: (f.readonly || !__VLS_ctx.canSave),
                    spellcheck: "false",
                    placeholder: "\u005b\u0072\u0065\u006d\u006f\u0074\u0065\u005d\u005c\u006e\u0074\u0079\u0070\u0065\u0020\u003d\u0020\u0064\u0072\u0069\u0076\u0065\u005c\u006e\u0073\u0063\u006f\u0070\u0065\u0020\u003d\u0020\u0064\u0072\u0069\u0076\u0065",
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ class: "actions" },
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                    ...{ onClick: (__VLS_ctx.decodeRcloneFromCurrent) },
                    type: "button",
                    ...{ class: "ghost" },
                    disabled: (f.readonly || !__VLS_ctx.canSave),
                });
                (__VLS_ctx.t("config.rcloneDecodeCurrent"));
                __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                    ...{ onClick: (__VLS_ctx.showRcloneBase64Preview) },
                    type: "button",
                    ...{ class: "ghost" },
                    disabled: (f.readonly || !__VLS_ctx.canSave),
                });
                (__VLS_ctx.t("config.rcloneShowEncoded"));
                if (__VLS_ctx.rcloneBase64Preview) {
                    __VLS_asFunctionalElement(__VLS_intrinsicElements.pre, __VLS_intrinsicElements.pre)({
                        ...{ class: "rclone-base64-preview" },
                    });
                    (__VLS_ctx.rcloneBase64Preview);
                }
            }
            else if (f.key === 'CF_BYPASS_PORT_MAP') {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ class: "rclone-config-editor" },
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ class: "grid" },
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({});
                __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
                    ...{ class: "field-input" },
                    type: "number",
                    min: "1",
                    max: "65535",
                });
                (__VLS_ctx.cfBypassLocalPort);
                __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({});
                __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
                    ...{ class: "field-input" },
                    type: "number",
                    min: "1",
                    max: "65535",
                });
                (__VLS_ctx.cfBypassDefaultPort);
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ class: "grid" },
                    ...{ style: {} },
                });
                for (const [row] of __VLS_getVForSourceType((__VLS_ctx.proxyPoolRows))) {
                    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
                        key: (`cf-port-${row._id}`),
                    });
                    (row.name || row.host || row._id);
                    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
                        ...{ class: "field-input" },
                        type: "number",
                        min: "1",
                        max: "65535",
                    });
                    (__VLS_ctx.cfBypassProxyPorts[row.name || row.host || row._id]);
                }
            }
            else {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.textarea)({
                    id: ('cfg-' + f.key),
                    value: (__VLS_ctx.form[f.key]),
                    ...{ class: "field-input field-input--code" },
                    rows: "8",
                    readonly: (f.readonly || !__VLS_ctx.canSave),
                    spellcheck: "false",
                });
            }
        }
        else if (__VLS_ctx.SELECT_OPTION_KEYS[f.key]) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
                id: ('cfg-' + f.key),
                value: (__VLS_ctx.form[f.key]),
                ...{ class: "field-input field-input--select" },
                disabled: (f.readonly || !__VLS_ctx.canSave),
            });
            for (const [o] of __VLS_getVForSourceType((__VLS_ctx.selectOptionsFor(f.key, __VLS_ctx.form[f.key])))) {
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
        else if (__VLS_ctx.isPathLikeField(f.key, f.type, f.sensitive) && f.key !== 'GIT_REPO_URL') {
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
                        if (!!(__VLS_ctx.SELECT_OPTION_KEYS[f.key]))
                            return;
                        if (!!(f.type === 'int'))
                            return;
                        if (!!(f.type === 'float'))
                            return;
                        if (!!(f.sensitive))
                            return;
                        if (!(__VLS_ctx.isPathLikeField(f.key, f.type, f.sensitive) && f.key !== 'GIT_REPO_URL'))
                            return;
                        __VLS_ctx.focusField('cfg-' + f.key);
                    } },
                type: "button",
                ...{ class: "btn-folder" },
                title: (__VLS_ctx.t('config.pathFolderTitle')),
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
    (__VLS_ctx.t("config.refresh"));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "config-footer__actions" },
    });
    if (__VLS_ctx.canSave) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (__VLS_ctx.save) },
            type: "button",
            ...{ class: "config-save-btn" },
        });
        (__VLS_ctx.t("config.save"));
    }
    else {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "badge badge--warn" },
        });
        (__VLS_ctx.t("config.readOnlyCannotSave"));
    }
}
/** @type {__VLS_StyleScopedClasses['page-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['config-page']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head__title']} */ ;
/** @type {__VLS_StyleScopedClasses['page-head__sub']} */ ;
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
/** @type {__VLS_StyleScopedClasses['rclone-config-editor']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input--code']} */ ;
/** @type {__VLS_StyleScopedClasses['actions']} */ ;
/** @type {__VLS_StyleScopedClasses['ghost']} */ ;
/** @type {__VLS_StyleScopedClasses['ghost']} */ ;
/** @type {__VLS_StyleScopedClasses['rclone-base64-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['rclone-config-editor']} */ ;
/** @type {__VLS_StyleScopedClasses['grid']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
/** @type {__VLS_StyleScopedClasses['grid']} */ ;
/** @type {__VLS_StyleScopedClasses['field-input']} */ ;
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
            SELECT_OPTION_KEYS: SELECT_OPTION_KEYS,
            isPathLikeField: isPathLikeField,
            canSave: canSave,
            t: t,
            sensitivePlaceholder: sensitivePlaceholder,
            sectionTabLabel: sectionTabLabel,
            fieldLabel: fieldLabel,
            fieldDescription: fieldDescription,
            selectOptionsFor: selectOptionsFor,
            form: form,
            message: message,
            messageIsError: messageIsError,
            loading: loading,
            activeSection: activeSection,
            proxyPoolRows: proxyPoolRows,
            rclonePlaintext: rclonePlaintext,
            rcloneBase64Preview: rcloneBase64Preview,
            cfBypassLocalPort: cfBypassLocalPort,
            cfBypassDefaultPort: cfBypassDefaultPort,
            cfBypassProxyPorts: cfBypassProxyPorts,
            sections: sections,
            visibleCurrentFields: visibleCurrentFields,
            focusField: focusField,
            decodeRcloneFromCurrent: decodeRcloneFromCurrent,
            showRcloneBase64Preview: showRcloneBase64Preview,
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
