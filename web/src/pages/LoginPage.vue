<template>
  <div class="login-minimal">
    <h1 class="login-brand">JAVDB Spider</h1>
    <form class="login-form" @submit.prevent="submit">
      <label class="login-field">
        <span class="login-field__label">用户名</span>
        <input
          v-model="username"
          class="login-input"
          type="text"
          name="username"
          placeholder=""
          autocomplete="username"
        />
      </label>
      <label class="login-field">
        <span class="login-field__label">密码</span>
        <input
          v-model="password"
          class="login-input"
          type="password"
          name="password"
          placeholder=""
          autocomplete="current-password"
        />
      </label>
      <p v-if="error" class="form-error login-error" role="alert">{{ error }}</p>
      <button type="submit" class="login-submit">登录</button>
    </form>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { apiFetch } from "../lib/api";
import { useAuthStore } from "../stores/auth";

const router = useRouter();
const auth = useAuthStore();
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
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : "登录失败";
  }
}
</script>
