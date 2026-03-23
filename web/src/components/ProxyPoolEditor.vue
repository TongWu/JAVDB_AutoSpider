<template>
  <div class="proxy-pool-editor">
    <p class="proxy-pool-editor__hint">
      {{ t("proxyEditor.hint") }}
    </p>

    <div v-for="(row, idx) in model" :key="row._id" class="proxy-card">
      <div class="proxy-card__head">
        <span class="proxy-card__title">{{ t("proxyEditor.cardTitle", { n: idx + 1 }) }}</span>
        <button type="button" class="proxy-card__remove btn-text-link" :disabled="readonly" @click="removeRow(idx)">
          {{ t("proxyEditor.remove") }}
        </button>
      </div>

      <div class="proxy-grid">
        <label class="proxy-field">
          <span>{{ t("proxyEditor.displayName") }}</span>
          <input v-model="row.name" class="field-input" type="text" :readonly="readonly" :placeholder="t('proxyEditor.namePlaceholder')" />
        </label>
        <label class="proxy-field">
          <span>{{ t("proxyEditor.priority") }}</span>
          <input v-model.number="row.priority" class="field-input" type="number" step="1" :readonly="readonly" />
        </label>
        <label class="proxy-field">
          <span>{{ t("proxyEditor.protocol") }}</span>
          <select v-model="row.scheme" class="field-input field-input--select" :disabled="readonly">
            <option value="http">HTTP</option>
            <option value="https">HTTPS</option>
            <option value="socks5">SOCKS5</option>
            <option value="socks5h">{{ t("proxyEditor.socks5h") }}</option>
          </select>
        </label>
        <label class="proxy-field">
          <span>{{ t("proxyEditor.host") }}</span>
          <input v-model="row.host" class="field-input" type="text" :readonly="readonly" placeholder="127.0.0.1" />
        </label>
        <label class="proxy-field">
          <span>{{ t("proxyEditor.port") }}</span>
          <input v-model="row.port" class="field-input" type="text" :readonly="readonly" placeholder="8080" />
        </label>
        <label class="proxy-field">
          <span>{{ t("proxyEditor.username") }}</span>
          <input v-model="row.username" class="field-input" type="text" :readonly="readonly" autocomplete="off" />
        </label>
        <label class="proxy-field">
          <span>{{ t("proxyEditor.password") }}</span>
          <input
            v-model="row.password"
            class="field-input"
            type="password"
            :readonly="readonly"
            autocomplete="new-password"
            :placeholder="t('proxyEditor.passwordPlaceholder')"
          />
        </label>
        <label class="proxy-field proxy-field--check">
          <input v-model="row.sameForHttps" type="checkbox" :disabled="readonly" />
          <span>{{ t("proxyEditor.sameHttps") }}</span>
        </label>
        <template v-if="!row.sameForHttps">
          <label class="proxy-field">
            <span>{{ t("proxyEditor.httpsHost") }}</span>
            <input v-model="row.httpsHost" class="field-input" type="text" :readonly="readonly" />
          </label>
          <label class="proxy-field">
            <span>{{ t("proxyEditor.httpsPort") }}</span>
            <input v-model="row.httpsPort" class="field-input" type="text" :readonly="readonly" />
          </label>
        </template>
      </div>
    </div>

    <button type="button" class="ghost proxy-add" :disabled="readonly" @click="addRow">{{ t("proxyEditor.add") }}</button>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import type { ProxyEditorRow } from "../utils/proxyPool";
import { emptyProxyRow } from "../utils/proxyPool";

defineProps<{ readonly?: boolean }>();
const { t } = useI18n();

const model = defineModel<ProxyEditorRow[]>({ required: true });

function addRow() {
  const nextPrio = model.value.length ? Math.max(...model.value.map((r) => r.priority)) + 10 : 0;
  model.value = [...model.value, emptyProxyRow(nextPrio)];
}

function removeRow(idx: number) {
  const next = model.value.filter((_, i) => i !== idx);
  model.value = next.length ? next : [emptyProxyRow(0)];
}
</script>

<style scoped>
.proxy-pool-editor__hint {
  margin: 0 0 16px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--mdc-text-secondary);
  max-width: 52em;
}

.proxy-pool-editor__hint code {
  font-size: 11px;
  padding: 1px 4px;
  border-radius: 4px;
  background: var(--mdc-bg-subtle);
  border: 1px solid var(--mdc-border);
}

.proxy-card {
  border: 1px solid var(--mdc-border);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 14px;
  background: var(--mdc-bg-subtle);
}

.proxy-card__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}

.proxy-card__title {
  font-weight: 600;
  font-size: 13px;
}

.proxy-card__remove {
  font-size: 12px !important;
  padding: 4px 8px !important;
}

.proxy-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
}

@media (max-width: 640px) {
  .proxy-grid {
    grid-template-columns: 1fr;
  }
}

.proxy-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  font-size: 12px;
  font-weight: 500;
  color: var(--mdc-text-secondary);
}

.proxy-field--check {
  flex-direction: row;
  align-items: center;
  gap: 8px;
  grid-column: 1 / -1;
}

.proxy-add {
  margin-top: 4px;
}

.field-input--select {
  cursor: pointer;
}
</style>
