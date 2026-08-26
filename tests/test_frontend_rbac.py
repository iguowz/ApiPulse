from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend/src"


def _source(path: str) -> str:
    return (FRONTEND / path).read_text(encoding="utf-8")


def test_frontend_rbac_defines_backend_roles_and_legacy_user_alias():
    source = _source("utils/rbac.js")

    for role in ("admin", "editor", "viewer", "monitor_admin"):
        assert role in source

    assert "user: USER_ROLES.VIEWER" in source
    assert "'user:manage'" in source
    assert "ROLE_OPTIONS" in source


def test_admin_users_page_uses_shared_role_options_and_labels():
    source = _source("views/admin/Users.vue")

    assert "v-for=\"role in ROLE_OPTIONS\"" in source
    assert "$t(role.labelKey)" in source
    assert "roleTagType(row.role)" in source
    assert "normalizeRole(user.role)" in source
    assert "role_user" not in source
    assert "value=\"user\"" not in source


def test_route_and_sidebar_use_permissions_instead_of_admin_flags():
    router_source = _source("router/index.js")
    app_source = _source("App.vue")

    assert "meta: { permission: 'user:manage' }" in router_source
    assert "hasPermission(user.role, requiredPermission)" in router_source
    assert "meta.admin" not in router_source

    assert "permission: 'user:manage'" in app_source
    assert "authStore.hasPermission(item.permission)" in app_source
    assert "adminOnly" not in app_source


def test_auth_store_normalizes_stored_and_fetched_users():
    source = _source("stores/auth.js")

    assert "normalizeUser(_parsedUser)" in source
    assert "normalizeUser(u)" in source
    assert "JSON.stringify(normalizedUser)" in source
    assert "normalizeRole(user.value?.role)" in source
    assert "roleHasPermission(role.value, 'user:manage')" in source
