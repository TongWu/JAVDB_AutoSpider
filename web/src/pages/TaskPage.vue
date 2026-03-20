<template>
  <div class="page-shell">
    <header class="page-head">
      <div>
        <h1 class="page-head__title">任务日志</h1>
        <p class="page-head__sub">按 job_id 查询状态与完整日志。</p>
      </div>
    </header>

    <div class="card mdc-card">
      <div class="table-toolbar">
        <input v-model="jobId" type="search" placeholder="输入 job_id" @keyup.enter="fetchTask" />
        <button type="button" @click="fetchTask">查询</button>
      </div>

      <p v-if="error" class="form-error">{{ error }}</p>
      <p v-else-if="statusLine" class="job-meta">{{ statusLine }}</p>

      <div v-if="taskLog || fetched" class="log-panel-wrap" style="margin-top: 16px">
        <div class="log-panel-header">日志正文</div>
        <pre class="log-live" style="max-height: min(55vh, 480px); min-height: 160px">{{ taskLog || "（无日志内容）" }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { apiFetch } from "../lib/api";

const jobId = ref("");
const statusLine = ref("");
const taskLog = ref("");
const error = ref("");
const fetched = ref(false);

async function fetchTask() {
  error.value = "";
  fetched.value = false;
  statusLine.value = "";
  taskLog.value = "";
  if (!jobId.value.trim()) {
    error.value = "请输入 job_id";
    return;
  }
  try {
    const data = (await apiFetch(`/api/tasks/${jobId.value.trim()}`)) as {
      status?: string;
      log?: string;
    };
    fetched.value = true;
    statusLine.value = `状态: ${data.status ?? "—"}`;
    taskLog.value = data.log ?? "";
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}
</script>
