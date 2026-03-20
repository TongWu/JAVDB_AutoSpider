import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { useAuthStore } from "./stores/auth";
import { useRunningJobStore } from "./stores/runningJob";
import NavIcon from "./components/NavIcon.vue";
import TaskRunningFloat from "./components/TaskRunningFloat.vue";
const auth = useAuthStore();
const runningJob = useRunningJobStore();
const router = useRouter();
const isAuthed = computed(() => !!auth.accessToken);
const displayName = computed(() => auth.username || "用户");
const themeDark = ref(false);
const themeLabel = computed(() => (themeDark.value ? "浅色主题" : "切换主题"));
watch(isAuthed, (v) => {
    if (v)
        runningJob.restoreFromStorage();
    else
        runningJob.clearJob();
}, { immediate: true });
function logout() {
    runningJob.clearJob();
    auth.clearSession();
    router.push("/login");
}
function toggleTheme() {
    themeDark.value = !themeDark.value;
    document.documentElement.classList.toggle("theme-dark", themeDark.value);
}
onMounted(() => {
    themeDark.value = document.documentElement.classList.contains("theme-dark");
});
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "app-root" },
    ...{ class: ({ 'app-root--authed': __VLS_ctx.isAuthed }) },
});
if (__VLS_ctx.isAuthed) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.aside, __VLS_intrinsicElements.aside)({
        ...{ class: "side-nav" },
        'aria-label': "主导航",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "side-nav__brand" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "side-nav__title" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "side-nav__ver" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.nav, __VLS_intrinsicElements.nav)({
        ...{ class: "side-nav__main" },
    });
    const __VLS_0 = {}.RouterLink;
    /** @type {[typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, ]} */ ;
    // @ts-ignore
    const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
        to: "/",
        ...{ class: "nav-item" },
        activeClass: "nav-item--active",
    }));
    const __VLS_2 = __VLS_1({
        to: "/",
        ...{ class: "nav-item" },
        activeClass: "nav-item--active",
    }, ...__VLS_functionalComponentArgsRest(__VLS_1));
    __VLS_3.slots.default;
    /** @type {[typeof NavIcon, ]} */ ;
    // @ts-ignore
    const __VLS_4 = __VLS_asFunctionalComponent(NavIcon, new NavIcon({
        name: "home",
    }));
    const __VLS_5 = __VLS_4({
        name: "home",
    }, ...__VLS_functionalComponentArgsRest(__VLS_4));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    var __VLS_3;
    const __VLS_7 = {}.RouterLink;
    /** @type {[typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, ]} */ ;
    // @ts-ignore
    const __VLS_8 = __VLS_asFunctionalComponent(__VLS_7, new __VLS_7({
        to: "/daily",
        ...{ class: "nav-item" },
        activeClass: "nav-item--active",
    }));
    const __VLS_9 = __VLS_8({
        to: "/daily",
        ...{ class: "nav-item" },
        activeClass: "nav-item--active",
    }, ...__VLS_functionalComponentArgsRest(__VLS_8));
    __VLS_10.slots.default;
    /** @type {[typeof NavIcon, ]} */ ;
    // @ts-ignore
    const __VLS_11 = __VLS_asFunctionalComponent(NavIcon, new NavIcon({
        name: "calendar",
    }));
    const __VLS_12 = __VLS_11({
        name: "calendar",
    }, ...__VLS_functionalComponentArgsRest(__VLS_11));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    var __VLS_10;
    const __VLS_14 = {}.RouterLink;
    /** @type {[typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, ]} */ ;
    // @ts-ignore
    const __VLS_15 = __VLS_asFunctionalComponent(__VLS_14, new __VLS_14({
        to: "/adhoc",
        ...{ class: "nav-item" },
        activeClass: "nav-item--active",
    }));
    const __VLS_16 = __VLS_15({
        to: "/adhoc",
        ...{ class: "nav-item" },
        activeClass: "nav-item--active",
    }, ...__VLS_functionalComponentArgsRest(__VLS_15));
    __VLS_17.slots.default;
    /** @type {[typeof NavIcon, ]} */ ;
    // @ts-ignore
    const __VLS_18 = __VLS_asFunctionalComponent(NavIcon, new NavIcon({
        name: "bolt",
    }));
    const __VLS_19 = __VLS_18({
        name: "bolt",
    }, ...__VLS_functionalComponentArgsRest(__VLS_18));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    var __VLS_17;
    const __VLS_21 = {}.RouterLink;
    /** @type {[typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, ]} */ ;
    // @ts-ignore
    const __VLS_22 = __VLS_asFunctionalComponent(__VLS_21, new __VLS_21({
        to: "/config",
        ...{ class: "nav-item" },
        activeClass: "nav-item--active",
    }));
    const __VLS_23 = __VLS_22({
        to: "/config",
        ...{ class: "nav-item" },
        activeClass: "nav-item--active",
    }, ...__VLS_functionalComponentArgsRest(__VLS_22));
    __VLS_24.slots.default;
    /** @type {[typeof NavIcon, ]} */ ;
    // @ts-ignore
    const __VLS_25 = __VLS_asFunctionalComponent(NavIcon, new NavIcon({
        name: "layers",
    }));
    const __VLS_26 = __VLS_25({
        name: "layers",
    }, ...__VLS_functionalComponentArgsRest(__VLS_25));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    var __VLS_24;
    const __VLS_28 = {}.RouterLink;
    /** @type {[typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, ]} */ ;
    // @ts-ignore
    const __VLS_29 = __VLS_asFunctionalComponent(__VLS_28, new __VLS_28({
        to: "/tasks",
        ...{ class: "nav-item" },
        activeClass: "nav-item--active",
    }));
    const __VLS_30 = __VLS_29({
        to: "/tasks",
        ...{ class: "nav-item" },
        activeClass: "nav-item--active",
    }, ...__VLS_functionalComponentArgsRest(__VLS_29));
    __VLS_31.slots.default;
    /** @type {[typeof NavIcon, ]} */ ;
    // @ts-ignore
    const __VLS_32 = __VLS_asFunctionalComponent(NavIcon, new NavIcon({
        name: "doc",
    }));
    const __VLS_33 = __VLS_32({
        name: "doc",
    }, ...__VLS_functionalComponentArgsRest(__VLS_32));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    var __VLS_31;
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "side-nav__label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "side-nav__system" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        type: "button",
        ...{ class: "nav-item nav-item--btn" },
        disabled: true,
        title: "占位",
    });
    /** @type {[typeof NavIcon, ]} */ ;
    // @ts-ignore
    const __VLS_35 = __VLS_asFunctionalComponent(NavIcon, new NavIcon({
        name: "shield",
    }));
    const __VLS_36 = __VLS_35({
        name: "shield",
    }, ...__VLS_functionalComponentArgsRest(__VLS_35));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.toggleTheme) },
        type: "button",
        ...{ class: "nav-item nav-item--btn" },
    });
    /** @type {[typeof NavIcon, ]} */ ;
    // @ts-ignore
    const __VLS_38 = __VLS_asFunctionalComponent(NavIcon, new NavIcon({
        name: "moon",
    }));
    const __VLS_39 = __VLS_38({
        name: "moon",
    }, ...__VLS_functionalComponentArgsRest(__VLS_38));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    (__VLS_ctx.themeLabel);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.logout) },
        type: "button",
        ...{ class: "nav-item nav-item--btn" },
    });
    /** @type {[typeof NavIcon, ]} */ ;
    // @ts-ignore
    const __VLS_41 = __VLS_asFunctionalComponent(NavIcon, new NavIcon({
        name: "power",
    }));
    const __VLS_42 = __VLS_41({
        name: "power",
    }, ...__VLS_functionalComponentArgsRest(__VLS_41));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "side-nav__user" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "side-nav__avatar" },
        'aria-hidden': "true",
    });
    /** @type {[typeof NavIcon, ]} */ ;
    // @ts-ignore
    const __VLS_44 = __VLS_asFunctionalComponent(NavIcon, new NavIcon({
        name: "user",
    }));
    const __VLS_45 = __VLS_44({
        name: "user",
    }, ...__VLS_functionalComponentArgsRest(__VLS_44));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "side-nav__username" },
    });
    (__VLS_ctx.displayName);
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.main, __VLS_intrinsicElements.main)({
    ...{ class: "main-area" },
    ...{ class: ({ 'main-area--login': !__VLS_ctx.isAuthed }) },
});
const __VLS_47 = {}.RouterView;
/** @type {[typeof __VLS_components.RouterView, typeof __VLS_components.routerView, ]} */ ;
// @ts-ignore
const __VLS_48 = __VLS_asFunctionalComponent(__VLS_47, new __VLS_47({}));
const __VLS_49 = __VLS_48({}, ...__VLS_functionalComponentArgsRest(__VLS_48));
if (__VLS_ctx.isAuthed) {
    /** @type {[typeof TaskRunningFloat, ]} */ ;
    // @ts-ignore
    const __VLS_51 = __VLS_asFunctionalComponent(TaskRunningFloat, new TaskRunningFloat({}));
    const __VLS_52 = __VLS_51({}, ...__VLS_functionalComponentArgsRest(__VLS_51));
}
/** @type {__VLS_StyleScopedClasses['app-root']} */ ;
/** @type {__VLS_StyleScopedClasses['side-nav']} */ ;
/** @type {__VLS_StyleScopedClasses['side-nav__brand']} */ ;
/** @type {__VLS_StyleScopedClasses['side-nav__title']} */ ;
/** @type {__VLS_StyleScopedClasses['side-nav__ver']} */ ;
/** @type {__VLS_StyleScopedClasses['side-nav__main']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-item']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-item']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-item']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-item']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-item']} */ ;
/** @type {__VLS_StyleScopedClasses['side-nav__label']} */ ;
/** @type {__VLS_StyleScopedClasses['side-nav__system']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-item']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-item--btn']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-item']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-item--btn']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-item']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-item--btn']} */ ;
/** @type {__VLS_StyleScopedClasses['side-nav__user']} */ ;
/** @type {__VLS_StyleScopedClasses['side-nav__avatar']} */ ;
/** @type {__VLS_StyleScopedClasses['side-nav__username']} */ ;
/** @type {__VLS_StyleScopedClasses['main-area']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            NavIcon: NavIcon,
            TaskRunningFloat: TaskRunningFloat,
            isAuthed: isAuthed,
            displayName: displayName,
            themeLabel: themeLabel,
            logout: logout,
            toggleTheme: toggleTheme,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
