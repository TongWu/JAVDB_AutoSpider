<template>
  <div class="app-root" :class="{ 'app-root--authed': isAuthed }">
    <aside v-if="isAuthed" class="side-nav" aria-label="主导航">
      <div class="side-nav__brand">
        <span class="side-nav__title">JAVDB Spider</span>
        <span class="side-nav__ver">v0.2.0</span>
      </div>

      <nav class="side-nav__main">
        <router-link to="/" class="nav-item" active-class="nav-item--active">
          <NavIcon name="home" />
          <span>主界面</span>
        </router-link>
        <router-link to="/daily" class="nav-item" active-class="nav-item--active">
          <NavIcon name="calendar" />
          <span>定期任务</span>
        </router-link>
        <router-link to="/adhoc" class="nav-item" active-class="nav-item--active">
          <NavIcon name="bolt" />
          <span>手动任务</span>
        </router-link>
        <router-link to="/config" class="nav-item" active-class="nav-item--active">
          <NavIcon name="layers" />
          <span>配置管理</span>
        </router-link>
        <router-link to="/tasks" class="nav-item" active-class="nav-item--active">
          <NavIcon name="doc" />
          <span>任务日志</span>
        </router-link>
      </nav>

      <div class="side-nav__label">系统</div>
      <div class="side-nav__system">
        <button type="button" class="nav-item nav-item--btn" disabled title="占位">
          <NavIcon name="shield" />
          <span>安全模式</span>
        </button>
        <button type="button" class="nav-item nav-item--btn" @click="toggleTheme">
          <NavIcon name="moon" />
          <span>{{ themeLabel }}</span>
        </button>
        <button type="button" class="nav-item nav-item--btn" @click="logout">
          <NavIcon name="power" />
          <span>退出</span>
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
