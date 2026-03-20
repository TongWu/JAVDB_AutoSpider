import { createRouter, createWebHistory } from "vue-router";
import DashboardPage from "./pages/DashboardPage.vue";
import ConfigPage from "./pages/ConfigPage.vue";
import DailyPage from "./pages/DailyPage.vue";
import AdhocPage from "./pages/AdhocPage.vue";
import TaskPage from "./pages/TaskPage.vue";
import LoginPage from "./pages/LoginPage.vue";
import { useAuthStore } from "./stores/auth";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", component: LoginPage },
    { path: "/", component: DashboardPage },
    { path: "/config", component: ConfigPage },
    { path: "/daily", component: DailyPage },
    { path: "/adhoc", component: AdhocPage },
    { path: "/tasks", component: TaskPage },
  ],
});

router.beforeEach((to) => {
  const auth = useAuthStore();
  if (to.path !== "/login" && !auth.accessToken) {
    return "/login";
  }
  if (to.path === "/login" && auth.accessToken) {
    return "/";
  }
  return true;
});
