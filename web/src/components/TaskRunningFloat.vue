<template>
  <button
    v-if="store.showFloatingWidget"
    type="button"
    class="task-float"
    :title="titleText"
    @click="goToLog"
  >
    <span class="task-float__spin" aria-hidden="true" />
    <span class="task-float__body">
      <span class="task-float__label">{{ kindLabel }}</span>
      <span class="task-float__status">{{ statusLabel }}</span>
      <span class="task-float__id">{{ shortId }}</span>
    </span>
  </button>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useRunningJobStore } from "../stores/runningJob";

const store = useRunningJobStore();
const router = useRouter();

const kindLabel = computed(() => (store.kind === "adhoc" ? "手动任务运行中" : "定期任务运行中"));

const statusLabel = computed(() => {
  if (store.pollStopped) return `${store.status || "运行中"} · 已暂停刷新`;
  return store.status || "运行中";
});

const shortId = computed(() => {
  const id = store.jobId;
  if (!id) return "";
  return id.length > 28 ? `${id.slice(0, 14)}…${id.slice(-10)}` : id;
});

const titleText = computed(() => `点击查看实时日志 · ${store.jobId}`);

function goToLog() {
  if (store.kind === "adhoc") {
    void router.push({ path: "/adhoc", query: { tab: "log" } });
  } else {
    void router.push({ path: "/daily", query: { tab: "log" } });
  }
}
</script>

<style scoped>
.task-float {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 1000;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  max-width: min(320px, calc(100vw - 40px));
  padding: 14px 16px;
  text-align: left;
  cursor: pointer;
  border: 1px solid var(--mdc-border-strong);
  border-radius: 8px;
  background: var(--mdc-bg-page);
  box-shadow: 0 4px 24px rgb(0 0 0 / 0.12);
  transition:
    box-shadow 0.15s ease,
    transform 0.15s ease;
}

.task-float:hover {
  box-shadow: 0 6px 28px rgb(0 0 0 / 0.16);
  transform: translateY(-1px);
}

.task-float__spin {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  margin-top: 2px;
  border: 2px solid var(--mdc-border-strong);
  border-top-color: #000000;
  border-radius: 50%;
  animation: task-float-spin 0.85s linear infinite;
}

.theme-dark .task-float__spin {
  border-top-color: var(--mdc-text);
}

@keyframes task-float-spin {
  to {
    transform: rotate(360deg);
  }
}

.task-float__body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.task-float__label {
  font-size: 13px;
  font-weight: 600;
  color: var(--mdc-text);
}

.task-float__status {
  font-size: 12px;
  color: var(--mdc-text-secondary);
}

.task-float__id {
  font-size: 11px;
  font-family: ui-monospace, monospace;
  color: var(--mdc-text-muted);
  word-break: break-all;
}
</style>
