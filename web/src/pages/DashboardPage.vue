<template>
  <div class="page-shell">
    <header class="page-head">
      <div>
        <h1 class="page-head__title">主界面</h1>
        <p class="page-head__sub">运行状态与服务健康一览。</p>
      </div>
    </header>

    <p class="section-rule">运行状态</p>
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-card__label">定期任务</div>
        <div class="stat-card__value">{{ periodicSlot }}</div>
        <div class="stat-card__hint">{{ periodicHint }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-card__label">手动任务</div>
        <div class="stat-card__value">{{ manualSlot }}</div>
        <div class="stat-card__hint">{{ manualHint }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-card__label">API 健康</div>
        <div class="stat-card__value stat-card__value--sm">{{ healthShort }}</div>
        <div class="stat-card__hint">{{ healthDetail }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-card__label">Rust 核心</div>
        <div class="stat-card__value stat-card__value--sm">{{ rustLabel }}</div>
        <div class="stat-card__hint">解析加速模块可用性</div>
      </div>
    </div>

    <p class="section-rule">快捷说明</p>
    <div class="card mdc-card">
      <p class="dash-tip">
        在 <strong>定期任务</strong> / <strong>手动任务</strong> 提交后，可在「运行日志」Tab 查看实时输出；历史任务 ID 可在
        <router-link to="/tasks">任务日志</router-link> 查询。
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { apiFetch } from "../lib/api";

const healthShort = ref("…");
const healthDetail = ref("加载中");
const rustLabel = ref("—");

/** Placeholder slots matching reference UI (no queue stats API yet). */
const periodicSlot = ref("0/1");
const periodicHint = ref("空闲 · 成功: — · 失败: —");
const manualSlot = ref("0/1");
const manualHint = ref("空闲");

onMounted(async () => {
  try {
    const health = (await apiFetch("/api/health", { skipAuth: true })) as {
      status?: string;
      rust_core_available?: boolean;
    };
    const ok = health.status === "ok" || health.status === "healthy";
    healthShort.value = ok ? "正常" : String(health.status ?? "未知");
    healthDetail.value = `状态: ${health.status ?? "—"}`;
    rustLabel.value = health.rust_core_available ? "可用" : "未启用";
  } catch {
    healthShort.value = "不可达";
    healthDetail.value = "无法连接 API";
    rustLabel.value = "—";
  }
});
</script>

<style scoped>
.dash-tip {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--mdc-text-secondary);
}

.dash-tip a {
  color: var(--mdc-link);
  font-weight: 500;
  text-decoration: none;
}

.dash-tip a:hover {
  text-decoration: underline;
}

.stat-card__value--sm {
  font-size: 18px;
}
</style>
