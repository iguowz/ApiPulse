<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-header">
        <span class="logo-mark">▸</span>
        <h1>AQP</h1>
        <p class="subtitle">API Quality Platform</p>
      </div>

      <div class="login-tabs">
        <button
          :class="{ active: mode === 'login' }"
          @click="switchMode('login')"
        >{{ $t('auth.login') }}</button>
        <button
          :class="{ active: mode === 'register' }"
          @click="switchMode('register')"
        >{{ $t('auth.register') }}</button>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @keyup.enter="submit"
      >
        <el-form-item :label="$t('auth.username')" prop="username">
          <el-input
            v-model="form.username"
            :placeholder="$t('auth.username')"
            autocomplete="username"
          />
        </el-form-item>

        <el-form-item :label="$t('auth.password')" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            :placeholder="$t('auth.password')"
            show-password
            autocomplete="current-password"
          />
        </el-form-item>

        <template v-if="mode === 'register'">
          <el-form-item :label="$t('auth.displayName')" prop="display_name">
            <el-input
              v-model="form.display_name"
              :placeholder="$t('auth.displayNameOptional')"
            />
          </el-form-item>
        </template>

        <el-button
          type="primary"
          class="submit-btn"
          :loading="submitting"
          @click="submit"
        >
          {{ mode === 'login' ? $t('auth.login') : $t('auth.register') }}
        </el-button>
      </el-form>

      <p v-if="errorMsg" class="error-msg">{{ errorMsg }}</p>

      <p class="login-hint">
        {{ $t('auth.defaultAdminHint') }}
      </p>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const mode = ref('login')
const submitting = ref(false)
const errorMsg = ref('')
const formRef = ref(null)

const form = reactive({
  username: '',
  password: '',
  display_name: '',
})

// 根据当前模式动态切换验证规则：注册时需要用户名、密码（≥6位），登录时只需非空
const rules = computed(() => {
  const base = {
    username: [{ required: true, message: t('auth.fillRequired'), trigger: 'blur' }],
    password: [{ required: true, message: t('auth.fillRequired'), trigger: 'blur' }],
  }
  if (mode.value === 'register') {
    base.password.push({ min: 6, message: t('auth.passwordMinLength'), trigger: 'blur' })
  }
  return base
})

// 切换登录/注册模式时清空错误和表单验证
function switchMode(m) {
  mode.value = m
  errorMsg.value = ''
  formRef.value?.clearValidate()
}

async function submit() {
  // Element Plus 表单验证（替代手工 if 校验）
  try {
    await formRef.value?.validate()
  } catch {
    // 验证不通过时 el-form 已展示内联错误，无需额外提示
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
      await authStore.login(form.username, form.password)
    }
    const redirect = route.query.redirect || '/dashboard'
    router.replace(redirect)
  } catch (e) {
    errorMsg.value = e.message || t('auth.operationFailed')
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
