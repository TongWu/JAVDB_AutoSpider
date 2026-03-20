<template>
  <div class="app-root" :class="{ 'app-root--authed': isAuthed }">
    <aside v-if="isAuthed" class="side-nav" :aria-label="t('app.navAria')">
      <div class="side-nav__brand">
        <span class="side-nav__title">JAVDB Spider</span>
        <span class="side-nav__ver">v0.2.0</span>
      </div>

      <nav class="side-nav__main">
        <router-link to="/" class="nav-item" active-class="nav-item--active">
          <NavIcon name="home" />
          <span>{{ t("nav.home") }}</span>
        </router-link>
        <router-link to="/daily" class="nav-item" active-class="nav-item--active">
          <NavIcon name="calendar" />
          <span>{{ t("nav.daily") }}</span>
        </router-link>
        <router-link to="/adhoc" class="nav-item" active-class="nav-item--active">
          <NavIcon name="bolt" />
          <span>{{ t("nav.adhoc") }}</span>
        </router-link>
        <router-link to="/explore" class="nav-item" active-class="nav-item--active">
          <NavIcon name="search" />
          <span>{{ t("nav.explore") }}</span>
        </router-link>
        <router-link to="/config" class="nav-item" active-class="nav-item--active">
          <NavIcon name="layers" />
          <span>{{ t("nav.config") }}</span>
        </router-link>
        <router-link to="/tasks" class="nav-item" active-class="nav-item--active">
          <NavIcon name="doc" />
          <span>{{ t("nav.tasks") }}</span>
        </router-link>
      </nav>

      <div class="side-nav__label">{{ t("app.system") }}</div>
      <div class="side-nav__system">
        <button type="button" class="nav-item nav-item--btn" disabled :title="t('app.safeModeTitle')">
          <NavIcon name="shield" />
          <span>{{ t("app.safeMode") }}</span>
        </button>
        <button type="button" class="nav-item nav-item--btn" @click="toggleTheme">
          <NavIcon name="moon" />
          <span>{{ themeLabel }}</span>
        </button>
        <div class="side-nav__lang-row">
          <LanguageSwitcher />
        </div>
        <button type="button" class="nav-item nav-item--btn" @click="logout">
          <NavIcon name="power" />
          <span>{{ t("app.logout") }}</span>
        </button>
      </div>

      <div class="side-nav__user">
        <div class="side-nav__avatar" aria-hidden="true">
          <NavIcon name="user" />
        </div>
        <span class="side-nav__username">{{ displayName }}</span>
      </div>
    </aside>

    <main class="main-area" :class="{ 'main-area--login': !isAuthed }">
      <router-view />
    </main>

    <TaskRunningFloat v-if="isAuthed" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { useAuthStore } from "./stores/auth";
import { useRunningJobStore } from "./stores/runningJob";
import NavIcon from "./components/NavIcon.vue";
import TaskRunningFloat from "./components/TaskRunningFloat.vue";
import LanguageSwitcher from "./components/LanguageSwitcher.vue";

const auth = useAuthStore();
const runningJob = useRunningJobStore();
const router = useRouter();
const { t } = useI18n();
const isAuthed = computed(() => !!auth.accessToken);
const displayName = computed(() => auth.username || t("meta.userFallback"));

const themeDark = ref(false);
const themeLabel = computed(() => (themeDark.value ? t("app.themeLight") : t("app.themeToggle")));

watch(
  isAuthed,
  (v) => {
    if (v) runningJob.restoreFromStorage();
    else runningJob.clearJob();
  },
  { immediate: true },
);

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
</script>

<style scoped>
.side-nav__lang-row {
  padding: 6px 12px 8px;
}
</style>
