"""Тесты Web-интерфейса и админ-API (Задание №6).

Страницы отдаются сервером, данные подтягиваются браузером из REST API —
поэтому здесь проверяется доступность страниц и работа админских эндпоинтов.

Фикстуры client/make_user — общие для всех тестовых файлов, см. conftest.py.
"""

import pytest


def auth_headers(client, make_user, admin: bool = False) -> dict:
    if admin:
        from init_db import DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD

        resp = client.post(
            "/auth/login",
            json={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD},
        )
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    headers, _, _ = make_user("web")
    return headers


# ---------------------------------------------------------------------------
# Страницы
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url", ["/", "/login", "/register", "/dashboard", "/history", "/admin"]
)
def test_pages_available(client, url):
    resp = client.get(url)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_index_describes_service(client):
    body = client.get("/").text
    assert "ML-сервис" in body
    assert "/static/app.js" in body  # страница подключает клиент REST API


def test_static_assets_served(client):
    for path in ("/static/style.css", "/static/app.js"):
        assert client.get(path).status_code == 200


def test_service_info_moved_to_api(client):
    """Корень отдаёт страницу; служебный JSON доступен на /api/info."""
    data = client.get("/api/info").json()
    assert data["service"] == "ML Service with billing"


# ---------------------------------------------------------------------------
# Админ-API (дополнительная часть задания)
# ---------------------------------------------------------------------------

def test_admin_endpoints_require_admin(client, make_user):
    user = auth_headers(client, make_user)
    for method, url in [
        ("get", "/admin/users"),
        ("get", "/admin/transactions"),
    ]:
        assert getattr(client, method)(url, headers=user).status_code == 403
    assert client.get("/admin/users").status_code == 401


def test_admin_lists_users_and_transactions(client, make_user):
    admin = auth_headers(client, make_user, admin=True)

    users = client.get("/admin/users", headers=admin).json()
    assert any(u["is_admin"] for u in users)
    assert all({"email", "balance", "created_at"} <= set(u) for u in users)

    txs = client.get("/admin/transactions", headers=admin).json()
    assert isinstance(txs, list)


def test_admin_tops_up_user_balance(client, make_user):
    user_headers, email, _ = make_user("web")
    admin = auth_headers(client, make_user, admin=True)

    users = client.get("/admin/users", headers=admin).json()
    user_id = next(u["id"] for u in users if u["email"] == email)

    resp = client.post(
        f"/admin/users/{user_id}/top-up", json={"amount": 25}, headers=admin
    )
    assert resp.status_code == 200
    assert resp.json() == {"balance": 25.0}
    # пользователь видит пополнение у себя
    assert client.get("/balance", headers=user_headers).json() == {"balance": 25.0}


def test_admin_top_up_unknown_user_404(client, make_user):
    admin = auth_headers(client, make_user, admin=True)
    resp = client.post(
        "/admin/users/no-such-id/top-up", json={"amount": 10}, headers=admin
    )
    assert resp.status_code == 404
