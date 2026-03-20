import { ref } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import LanguageSwitcher from "../components/LanguageSwitcher.vue";
import { apiFetch } from "../lib/api";
import { useAuthStore } from "../stores/auth";
const router = useRouter();
const auth = useAuthStore();
const { t } = useI18n();
const username = ref("admin");
const password = ref("");
const error = ref("");
async function submit() {
    error.value = "";
    try {
        const data = await apiFetch("/api/auth/login", {
            method: "POST",
            body: JSON.stringify({ username: username.value, password: password.value }),
            skipAuth: true,
        });
        auth.setSession(data);
        router.push("/");
    }
    catch (e) {
        error.value = e instanceof Error ? e.message : t("login.fail");
    }
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "login-minimal" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "login-lang" },
});
/** @type {[typeof LanguageSwitcher, ]} */ ;
// @ts-ignore
const __VLS_0 = __VLS_asFunctionalComponent(LanguageSwitcher, new LanguageSwitcher({}));
const __VLS_1 = __VLS_0({}, ...__VLS_functionalComponentArgsRest(__VLS_0));
__VLS_asFunctionalElement(__VLS_intrinsicElements.h1, __VLS_intrinsicElements.h1)({
    ...{ class: "login-brand" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.form, __VLS_intrinsicElements.form)({
    ...{ onSubmit: (__VLS_ctx.submit) },
    ...{ class: "login-form" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
    ...{ class: "login-field" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "login-field__label" },
});
(__VLS_ctx.t("login.username"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    value: (__VLS_ctx.username),
    ...{ class: "login-input" },
    type: "text",
    name: "username",
    placeholder: "",
    autocomplete: "username",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
    ...{ class: "login-field" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "login-field__label" },
});
(__VLS_ctx.t("login.password"));
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    ...{ class: "login-input" },
    type: "password",
    name: "password",
    placeholder: "",
    autocomplete: "current-password",
});
(__VLS_ctx.password);
if (__VLS_ctx.error) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "form-error login-error" },
        role: "alert",
    });
    (__VLS_ctx.error);
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    type: "submit",
    ...{ class: "login-submit" },
});
(__VLS_ctx.t("login.submit"));
/** @type {__VLS_StyleScopedClasses['login-minimal']} */ ;
/** @type {__VLS_StyleScopedClasses['login-lang']} */ ;
/** @type {__VLS_StyleScopedClasses['login-brand']} */ ;
/** @type {__VLS_StyleScopedClasses['login-form']} */ ;
/** @type {__VLS_StyleScopedClasses['login-field']} */ ;
/** @type {__VLS_StyleScopedClasses['login-field__label']} */ ;
/** @type {__VLS_StyleScopedClasses['login-input']} */ ;
/** @type {__VLS_StyleScopedClasses['login-field']} */ ;
/** @type {__VLS_StyleScopedClasses['login-field__label']} */ ;
/** @type {__VLS_StyleScopedClasses['login-input']} */ ;
/** @type {__VLS_StyleScopedClasses['form-error']} */ ;
/** @type {__VLS_StyleScopedClasses['login-error']} */ ;
/** @type {__VLS_StyleScopedClasses['login-submit']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            LanguageSwitcher: LanguageSwitcher,
            t: t,
            username: username,
            password: password,
            error: error,
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
