<template>
  <div class="lang-switch" role="group" :aria-label="t('app.language')">
    <button
      type="button"
      class="lang-switch__btn"
      :class="{ 'lang-switch__btn--active': locale === 'zh-CN' }"
      @click="setLocale('zh-CN')"
    >
      {{ t("app.langZh") }}
    </button>
    <span class="lang-switch__sep" aria-hidden="true">|</span>
    <button
      type="button"
      class="lang-switch__btn"
      :class="{ 'lang-switch__btn--active': locale === 'en' }"
      @click="setLocale('en')"
    >
      {{ t("app.langEn") }}
    </button>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import type { AppLocale } from "../i18n";

const { locale, t } = useI18n();

function setLocale(code: AppLocale) {
  locale.value = code;
}
</script>

<style scoped>
.lang-switch {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.lang-switch__btn {
  background: none !important;
  color: var(--mdc-text-secondary) !important;
  padding: 4px 6px !important;
  font-size: 12px !important;
  font-weight: 500;
  border: none !important;
  border-radius: 4px;
  cursor: pointer;
}

.lang-switch__btn:hover {
  color: var(--mdc-text) !important;
  background: rgb(0 0 0 / 0.04) !important;
}

.theme-dark .lang-switch__btn:hover {
  background: rgb(255 255 255 / 0.06) !important;
}

.lang-switch__btn--active {
  color: var(--mdc-text) !important;
  font-weight: 600;
}

.lang-switch__sep {
  color: var(--mdc-border-strong);
  font-size: 11px;
  user-select: none;
}
</style>
