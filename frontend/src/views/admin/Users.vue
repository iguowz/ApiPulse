<template>
  <div class="admin-users-page">
    <div class="page-header">
      <div>
        <h2>{{ $t('auth.admin_users') }}</h2>
        <p class="text-2">{{ $t('auth.admin_users_desc') }}</p>
      </div>
      <el-button @click="loadUsers" :loading="loading">
        <el-icon><Refresh /></el-icon>
        {{ $t('common.refresh') }}
      </el-button>
    </div>

    <!-- 用户列表表格 -->
    <el-card style="padding:0">
    <el-table :data="users" v-loading="loading" stripe style="width:100%">
      <el-table-column prop="username" :label="$t('auth.username')" min-width="140" />
      <el-table-column prop="email" label="Email" min-width="180">
        <template #default="{ row }">{{ row.email || '-' }}</template>
      </el-table-column>
      <el-table-column :label="$t('auth.role')" width="120" align="center">
        <template #default="{ row }">
          <el-tag :type="row.role === 'admin' ? 'danger' : 'info'" size="small" effect="dark">
            {{ row.role === 'admin' ? $t('auth.role_admin') : $t('auth.role_user') }}
          </el-tag>
        </template>
      </el-table-column>
      <!-- 用户所属项目列：admin 可查看并修改非管理员用户的可见项目 -->
      <el-table-column :label="$t('common.project')" width="100" align="center">
        <template #default="{ row }">
          <span class="text-2" style="font-size:12px">{{ projectStore.getName(row.project_id) || row.project_id || '-' }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="$t('auth.createdAt')" width="180">
        <template #default="{ row }">
          {{ row.created_at ? new Date(row.created_at).toLocaleString() : '-' }}
        </template>
      </el-table-column>
      <el-table-column :label="$t('common.actions')" width="160" align="center" fixed="right">
        <template #default="{ row }">
          <!-- 不允许编辑自己的角色，防止管理员把自己降级 -->
          <el-button
            size="small" text type="primary"
            @click="openEdit(row)"
            :disabled="row.id === authStore.user?.id"
          >{{ $t('common.edit') }}</el-button>
          <el-popconfirm
            :title="$t('auth.confirm_delete_user', { name: row.username })"
            @confirm="handleDelete(row.id)"
          >
            <template #reference>
              <el-button
                size="small" text type="danger"
                :disabled="row.id === authStore.user?.id"
              >{{ $t('common.delete') }}</el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
      <template #empty>
        <div class="empty-state">{{ $t('auth.no_users') }}</div>
      </template>
    </el-table>
    </el-card>

    <!-- 编辑用户角色 Dialog -->
    <el-dialog
      v-model="editVisible"
      :title="$t('auth.edit_user')"
      width="420px"
      :close-on-click-modal="false"
    >
      <el-form label-position="top" v-if="editUser">
        <el-form-item :label="$t('auth.username')">
          <el-input :model-value="editUser.username" disabled />
        </el-form-item>
        <el-form-item :label="$t('auth.role')">
          <el-select v-model="editRole" style="width:100%">
            <el-option value="admin" :label="$t('auth.role_admin')" />
            <el-option value="user" :label="$t('auth.role_user')" />
          </el-select>
        </el-form-item>
        <!-- 管理员可为非管理员用户设置可访问的项目，实现项目级数据隔离 -->
        <el-form-item :label="$t('common.project')">
          <el-select v-model="editProjectId" style="width:100%">
            <el-option v-for="p in projectStore.projects" :key="p.id" :value="p.id" :label="p.name" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveEdit" :loading="saving">
          {{ $t('common.save') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { Refresh } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { useProjectStore, useToastStore } from '@/stores'

const { t } = useI18n()
const authStore = useAuthStore()
const projectStore = useProjectStore()
const toastStore = useToastStore()

const users = ref([])
const loading = ref(false)
const saving = ref(false)
const editVisible = ref(false)
const editUser = ref(null)
const editRole = ref('')
// 管理员可为用户设置可访问的项目列表
const editProjectId = ref('')

async function loadUsers() {
  loading.value = true
  try {
    const res = await authStore.listUsers()
    users.value = Array.isArray(res) ? res : (res.users || [])
  } catch (e) {
    toastStore.error(t('auth.load_users_failed'))
  } finally {
    loading.value = false
  }
}

function openEdit(user) {
  editUser.value = user
  editRole.value = user.role
  // 加载用户当前所属项目，供管理员修改
  editProjectId.value = user.project_id || ''
  editVisible.value = true
}

async function saveEdit() {
  if (!editUser.value) return
  saving.value = true
  try {
    await authStore.updateUser(editUser.value.id, { role: editRole.value, project_id: editProjectId.value })
    toastStore.success(t('auth.user_updated'))
    editVisible.value = false
    await loadUsers()
  } catch (e) {
    toastStore.error(t('auth.update_failed'))
  } finally {
    saving.value = false
  }
}

async function handleDelete(userId) {
  try {
    await authStore.deleteUser(userId)
    toastStore.success(t('auth.user_deleted'))
    await loadUsers()
  } catch (e) {
    toastStore.error(t('auth.delete_failed'))
  }
}

onMounted(() => { loadUsers() })
</script>

<style scoped>
.admin-users-page {
  padding: 24px;
  max-width: 1200px;
}
.page-header {
  display: flex; align-items: flex-start; justify-content: space-between;
  margin-bottom: 20px;
}
.page-header h2 { margin: 0 0 4px; }
.text-2 { color: var(--text-2); font-size: 13px; margin: 0; }
.empty-state { padding: 40px 0; color: var(--text-3); }
</style>
