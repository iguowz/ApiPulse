<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-header">
        <span class="logo-mark">▸</span>
        <h1>AQP</h1>
        <p class="subtitle">API Quality Platform</p>
      </div>

      <!-- 登录/注册切换 tab -->
      <div class="login-tabs">
        <button
          :class="{ active: mode === 'login' }"
          @click="mode = 'login'"
        >登录</button>
        <button
          :class="{ active: mode === 'register' }"
          @click="mode = 'register'"
        >注册</button>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        label-position="top"
        @keyup.enter="submit"
      >
        <el-form-item label="用户名" required>
          <el-input
            v-model="form.username"
            placeholder="请输入用户名"
            autocomplete="username"
          />
        </el-form-item>

        <el-form-item label="密码" required>
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            show-password
            autocomplete="current-password"
          />
        </el-form-item>

        <!-- 注册模式额外字段 -->
        <template v-if="mode === 'register'">
          <el-form-item label="显示名称">
            <el-input
              v-model="form.display_name"
              placeholder="可选，默认同用户名"
            />
          </el-form-item>
        </template>

        <el-button
          type="primary"
          class="submit-btn"
          :loading="submitting"
          @click="submit"
        >
          {{ mode === 'login' ? '登录' : '注册' }}
        </el-button>
      </el-form>

      <p v-if="errorMsg" class="error-msg">{{ errorMsg }}</p>

      <p class="login-hint">
        默认管理员: admin / admin123
      </p>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const mode = ref('login')          // 'login' | 'register'
const submitting = ref(false)
const errorMsg = ref('')

const form = reactive({
  username: '',
  password: '',
  display_name: '',
})

async function submit() {
  if (!form.username || !form.password) {
    errorMsg.value = '请填写用户名和密码'
    return
  }
  if (mode.value === 'register' && form.password.length < 6) {
    errorMsg.value = '密码至少 6 位'
    return
  }

  submitting.value = true
  errorMsg.value = ''

  try {
    if (mode.value === 'login') {
      await authStore.login(form.username, form.password)
    } else {
      await authStore.register({
        username: form.username,
        password: form.password,
        display_name: form.display_name,
      })
      // 注册成功后自动登录
      await authStore.login(form.username, form.password)
    }
    // 登录成功，跳转到原来想访问的页面或 dashboard
    const redirect = route.query.redirect || '/dashboard'
    router.replace(redirect)
  } catch (e) {
    errorMsg.value = e.message || '操作失败'
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.login-page {
  display: flex; align-items: center; justify-content: center;
  min-height: 100vh;
  background: var(--bg-1);
}

.login-card {
  width: 400px; max-width: 90vw;
  padding: 40px 36px 32px;
  background: var(--bg-2);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-2);
  box-shadow: 0 4px 24px rgba(0,0,0,.15);
}

.login-header {
  text-align: center; margin-bottom: 24px;
}
.login-header h1 {
  font-size: 28px; font-weight: 700; margin: 0;
  color: var(--text-1);
}
.logo-mark {
  display: inline-block; font-size: 36px; color: var(--accent);
  margin-bottom: 4px;
}
.subtitle {
  font-size: 12px; color: var(--text-3); margin: 4px 0 0;
}

.login-tabs {
  display: flex; gap: 0; margin-bottom: 20px;
  border-radius: var(--radius);
  overflow: hidden;
  border: 1px solid var(--border-2);
}
.login-tabs button {
  flex: 1; padding: 8px 0; border: none; cursor: pointer;
  background: transparent; color: var(--text-2); font-size: 14px;
  transition: background .2s, color .2s;
}
.login-tabs button.active {
  background: var(--accent); color: #fff;
}

.submit-btn {
  width: 100%; margin-top: 8px;
}

.error-msg {
  color: var(--red, #f56c6c); font-size: 13px;
  text-align: center; margin: 12px 0 0;
}

.login-hint {
  font-size: 11px; color: var(--text-3); text-align: center;
  margin: 16px 0 0;
}
</style>
