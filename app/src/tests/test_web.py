"""Тесты Web-интерфейса и админ-API (Задание №6).

Страницы отдаются сервером, данные подтягиваются браузером из REST API —
поэтому здесь проверяется доступность страниц и работа админских эндпоинтов.
"""

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from main import app

    with TestClient(app) as c:
        yield c


def unique_email() -> str:
    return f"web-{uuid.uuid4().hex[:10]}@ml-service.com"


def auth_headers(client: TestClient, admin: bool = False) -> dict:
    if admin:
        from init_db import DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD

        creds = {"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD}
    else:
        creds = {"email": unique_email(), "password": "secret123"}
        client.post("/auth/register", json=creds)
    token = client.post("/auth/login", json=creds).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


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

def test_admin_endpoints_require_admin(client):
    user = auth_headers(client)
    for method, url in [
        ("get", "/admin/users"),
        ("get", "/admin/transactions"),
    ]:
        assert getattr(client, method)(url, headers=user).status_code == 403
    assert client.get("/admin/users").status_code == 401


def test_admin_lists_users_and_transactions(client):
    admin = auth_headers(client, admin=True)

    users = client.get("/admin/users", headers=admin).json()
    assert any(u["is_admin"] for u in users)
    assert all({"email", "balance", "created_at"} <= set(u) for u in users)

    txs = client.get("/admin/transactions", headers=admin).json()
    assert isinstance(txs, list)


def test_admin_tops_up_user_balance(client):
    email = unique_email()
    client.post("/auth/register", json={"email": email, "password": "secret123"})
    token = client.post(
        "/auth/login", json={"email": email, "password": "secret123"}
    ).json()["access_token"]
    user_headers = {"Authorization": f"Bearer {token}"}
    admin = auth_headers(client, admin=True)

    users = client.get("/admin/users", headers=admin).json()
    user_id = next(u["id"] for u in users if u["email"] == email)

    resp = client.post(
        f"/admin/users/{user_id}/top-up", json={"amount": 25}, headers=admin
    )
    assert resp.status_code == 200
    assert resp.json() == {"balance": 25.0}
    # пользователь видит пополнение у себя
    assert client.get("/balance", headers=user_headers).json() == {"balance": 25.0}


def test_admin_top_up_unknown_user_404(client):
    admin = auth_headers(client, admin=True)
    resp = client.post(
        "/admin/users/no-such-id/top-up", json={"amount": 10}, headers=admin
    )
    assert resp.status_code == 404
