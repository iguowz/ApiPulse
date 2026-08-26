export const USER_ROLES = Object.freeze({
  ADMIN: 'admin',
  EDITOR: 'editor',
  VIEWER: 'viewer',
  MONITOR_ADMIN: 'monitor_admin',
})

export const ROLE_VALUES = Object.freeze(Object.values(USER_ROLES))

const LEGACY_ROLE_ALIASES = Object.freeze({
  user: USER_ROLES.VIEWER,
})

export const ROLE_META = Object.freeze({
  [USER_ROLES.ADMIN]: {
    labelKey: 'auth.role_admin',
    tagType: 'danger',
  },
  [USER_ROLES.EDITOR]: {
    labelKey: 'auth.role_editor',
    tagType: 'warning',
  },
  [USER_ROLES.VIEWER]: {
    labelKey: 'auth.role_viewer',
    tagType: 'info',
  },
  [USER_ROLES.MONITOR_ADMIN]: {
    labelKey: 'auth.role_monitor_admin',
    tagType: 'success',
  },
})

export const ROLE_OPTIONS = Object.freeze(
  ROLE_VALUES.map(value => ({ value, ...ROLE_META[value] }))
)

export const ROLE_PERMISSIONS = Object.freeze({
  [USER_ROLES.ADMIN]: new Set([
    'api:read', 'api:create', 'api:update', 'api:delete', 'api:run', 'api:analyze',
    'scenario:read', 'scenario:create', 'scenario:update', 'scenario:delete', 'scenario:run',
    'monitor:read', 'monitor:create', 'monitor:update', 'monitor:delete',
    'factory:read', 'factory:create', 'factory:update', 'factory:delete',
    'knowledge:read', 'knowledge:create', 'knowledge:update', 'knowledge:delete',
    'environment:read', 'environment:create', 'environment:update', 'environment:delete',
    'generation:read', 'generation:review',
    'prompt:read', 'prompt:manage',
    'settings:read', 'settings:update',
    'alert_channel:read', 'alert_channel:manage',
    'capture:read', 'capture:manage',
    'mock_service:read', 'mock_service:manage',
    'traffic:read', 'traffic:manage',
    'database_service:read', 'database_service:manage', 'sql:run',
    'audit:read', 'ai_log:read', 'dlq:manage',
    'ai_chat:use',
    'project:read', 'project:create', 'project:update', 'project:delete',
    'har:upload', 'stats:read', 'user:manage',
  ]),
  [USER_ROLES.EDITOR]: new Set([
    'api:read', 'api:create', 'api:update', 'api:delete', 'api:run', 'api:analyze',
    'scenario:read', 'scenario:create', 'scenario:update', 'scenario:delete', 'scenario:run',
    'monitor:read', 'monitor:create', 'monitor:update', 'monitor:delete',
    'factory:read', 'factory:create', 'factory:update', 'factory:delete',
    'knowledge:read', 'knowledge:create', 'knowledge:update', 'knowledge:delete',
    'environment:read', 'environment:create', 'environment:update', 'environment:delete',
    'generation:read', 'generation:review',
    'prompt:read',
    'settings:read',
    'alert_channel:read', 'alert_channel:manage',
    'capture:read', 'capture:manage',
    'mock_service:read', 'mock_service:manage',
    'traffic:read', 'traffic:manage',
    'database_service:read', 'database_service:manage', 'sql:run',
    'ai_chat:use',
    'project:read', 'har:upload', 'stats:read',
  ]),
  [USER_ROLES.MONITOR_ADMIN]: new Set([
    'api:read', 'api:run',
    'scenario:read', 'scenario:run',
    'monitor:read', 'monitor:create', 'monitor:update', 'monitor:delete',
    'generation:read',
    'alert_channel:read', 'alert_channel:manage',
    'mock_service:read',
    'traffic:read',
    'database_service:read', 'sql:run',
    'ai_chat:use',
    'project:read', 'stats:read',
  ]),
  [USER_ROLES.VIEWER]: new Set([
    'api:read',
    'scenario:read',
    'monitor:read',
    'factory:read',
    'knowledge:read',
    'environment:read',
    'generation:read',
    'settings:read',
    'alert_channel:read',
    'mock_service:read',
    'traffic:read',
    'database_service:read',
    'ai_chat:use',
    'project:read', 'stats:read',
  ]),
})

export function normalizeRole(role) {
  const value = String(role || '').trim()
  const normalized = LEGACY_ROLE_ALIASES[value] || value
  return ROLE_VALUES.includes(normalized) ? normalized : USER_ROLES.VIEWER
}

export function normalizeUser(user) {
  if (!user) return null
  return { ...user, role: normalizeRole(user.role) }
}

export function hasPermission(role, permission) {
  if (!permission) return true
  return !!ROLE_PERMISSIONS[normalizeRole(role)]?.has(permission)
}

export function roleLabelKey(role) {
  return ROLE_META[normalizeRole(role)]?.labelKey || 'auth.role_viewer'
}

export function roleTagType(role) {
  return ROLE_META[normalizeRole(role)]?.tagType || 'info'
}
