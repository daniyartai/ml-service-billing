"""Сквозной сценарий работоспособности системы (Задание №7).

В отличие от test_api.py, где каждый случай проверяется изолированно, здесь
один пользователь последовательно проходит весь путь: регистрация ->
авторизация -> баланс -> пополнение -> ML-запросы -> история. Проверяется не
только каждый ответ по отдельности, но и связность: баланс сходится с суммой
операций, история содержит ровно те задачи, что отправлялись, транзакции
привязаны к своим задачам.

Запуск с подробным журналом сценария:
    docker compose exec app pytest tests/test_e2e.py -v -s

Очередь и воркер здесь подменяются (см. _stub_queue/_run_worker) — тесты не
требуют поднятого RabbitMQ. Проверка на живой инфраструктуре выполняется
отдельным скриптом scripts/smoke_test.py.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

PASSWORD = "secret123"
MODEL = "threshold-scoring"
COST = 5.0  # стоимость одного запроса к threshold-scoring


@pytest.fixture(scope="module")
def client():
    from main import app

    with TestClient(app) as c:  # контекст запускает lifespan -> init_db()
        yield c


@pytest.fixture(autouse=True)
def _stub_queue(monkeypatch):
    """Публикацию в RabbitMQ заменяем заглушкой, работу воркера имитируем."""
    import routers.predict as predict_router

    monkeypatch.setattr(predict_router.mq, "publish_task", lambda task: None)


def _run_worker(task_id: str) -> None:
    """Выполнить задачу так же, как это делает worker.py."""
    from database import SessionLocal
    from services import execute_prediction_task

    with SessionLocal() as session:
        execute_prediction_task(session, task_id)


def step(number: str, text: str) -> None:
    """Журнал сценария — виден при запуске pytest с ключом -s."""
    print(f"\n[{number}] {text}")


def test_full_user_journey(client):
    """Полный путь пользователя: от регистрации до сверки истории."""

    # -----------------------------------------------------------------
    # Шаг 1. Работа с пользователями
    # -----------------------------------------------------------------
    email = f"e2e-{uuid.uuid4().hex[:10]}@ml-service.com"

    step("1.1", f"Регистрация нового пользователя {email}")
    resp = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]

    step("1.2", "Авторизация: получение токена доступа")
    resp = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    step("1.3", "Повторная авторизация: старый токен продолжает работать")
    second = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert second.status_code == 200
    second_token = second.json()["access_token"]
    assert second_token != token, "каждая авторизация выдаёт новый токен"
    for name, tok in (("первый", token), ("второй", second_token)):
        resp = client.get("/users/me", headers={"Authorization": f"Bearer {tok}"})
        assert resp.status_code == 200, f"{name} токен должен быть действителен"
        assert resp.json()["id"] == user_id

    step("1.4", "Ошибки при неверных данных: занятый email, чужой пароль, битый токен")
    assert client.post(
        "/auth/register", json={"email": email, "password": PASSWORD}
    ).status_code == 409
    assert client.post(
        "/auth/login", json={"email": email, "password": "wrong-password"}
    ).status_code == 401
    assert client.get(
        "/users/me", headers={"Authorization": "Bearer not-a-real-token"}
    ).status_code == 401

    # -----------------------------------------------------------------
    # Шаг 2. Работа с балансом
    # -----------------------------------------------------------------
    step("2.1", "Начальный баланс нового пользователя — 0")
    assert client.get("/balance", headers=headers).json() == {"balance": 0.0}

    step("2.2", "Пополнение на 50 кредитов")
    resp = client.post("/balance/top-up", json={"amount": 50}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"balance": 50.0}

    step("2.3", "Повторное пополнение на 20 — суммы складываются")
    assert client.post(
        "/balance/top-up", json={"amount": 20}, headers=headers
    ).json() == {"balance": 70.0}
    assert client.get("/balance", headers=headers).json() == {"balance": 70.0}

    step("2.4", "Пополнение на неположительную сумму отклоняется")
    assert client.post(
        "/balance/top-up", json={"amount": 0}, headers=headers
    ).status_code == 422
    assert client.get("/balance", headers=headers).json() == {"balance": 70.0}

    balance = 70.0

    # -----------------------------------------------------------------
    # Шаг 3. ML-запрос и списание кредитов
    # -----------------------------------------------------------------
    step("3.1", "Корректный запрос: две строки признаков")
    resp = client.post(
        "/predict",
        json={
            "model": MODEL,
            "rows": [
                {"feature_1": 1.0, "feature_2": 2.0},  # сумма 3 -> класс 1
                {"feature_1": 0.0, "feature_2": 0.0},  # сумма 0 -> класс 0
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    ok_task = resp.json()["task_id"]
    assert resp.json()["status"] == "new", "задача принимается асинхронно"

    step("3.2", "Обработка воркером и получение результата")
    _run_worker(ok_task)
    task = client.get(f"/predict/{ok_task}", headers=headers).json()
    assert task["status"] == "done"
    assert task["result"] == [1, 0]
    assert task["charged"] == COST

    step("3.3", "Кредиты списаны ровно за один запрос")
    balance -= COST
    assert client.get("/balance", headers=headers).json() == {"balance": balance}

    # -----------------------------------------------------------------
    # Шаг 4. Обработка некорректных и частично корректных данных
    # -----------------------------------------------------------------
    step("4.1", "Частично валидная выборка: 2 строки годные, 2 — нет")
    rows = [
        {"feature_1": 1.0, "feature_2": 2.0},   # валидная
        {"feature_1": 5.0},                     # нет признака feature_2
        {"feature_1": "abc", "feature_2": 1.0},  # нечисловое значение
        {"feature_1": 0.0, "feature_2": 0.0},   # валидная
    ]
    resp = client.post("/predict", json={"model": MODEL, "rows": rows}, headers=headers)
    assert resp.status_code == 201, resp.text
    mixed_task = resp.json()["task_id"]

    _run_worker(mixed_task)
    task = client.get(f"/predict/{mixed_task}", headers=headers).json()
    assert task["status"] == "done"
    assert task["result"] == [1, 0], "предсказание только по валидным строкам"
    assert [item["row"] for item in task["invalid_rows"]] == [rows[1], rows[2]]
    assert "отсутствуют признаки" in task["invalid_rows"][0]["reason"]
    assert "нечисловые значения" in task["invalid_rows"][1]["reason"]

    step("4.2", "Работа выполнена частично, но выполнена — списание произошло")
    balance -= COST
    assert client.get("/balance", headers=headers).json() == {"balance": balance}

    step("4.3", "Полностью некорректная выборка: списания нет, кредиты возвращены")
    resp = client.post(
        "/predict",
        json={"model": MODEL, "rows": [{"feature_1": "n/a", "feature_2": None}]},
        headers=headers,
    )
    bad_task = resp.json()["task_id"]
    _run_worker(bad_task)
    task = client.get(f"/predict/{bad_task}", headers=headers).json()
    assert task["status"] == "validation_failed"
    assert task["charged"] == 0.0
    assert client.get("/balance", headers=headers).json() == {"balance": balance}

    step("4.4", "Несуществующая модель — 404, средства не тронуты")
    assert client.post(
        "/predict",
        json={"model": "no-such-model", "rows": [{"feature_1": 1.0}]},
        headers=headers,
    ).status_code == 404
    assert client.get("/balance", headers=headers).json() == {"balance": balance}

    # -----------------------------------------------------------------
    # Шаг 5. Запрет списания при недостаточном балансе
    # -----------------------------------------------------------------
    step("5.1", "Тратим остаток баланса до суммы меньше стоимости запроса")
    while balance >= COST:
        resp = client.post(
            "/predict",
            json={"model": MODEL, "rows": [{"feature_1": 1.0, "feature_2": 1.0}]},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        _run_worker(resp.json()["task_id"])
        balance -= COST
    assert client.get("/balance", headers=headers).json() == {"balance": balance}

    step("5.2", f"Баланс {balance} < стоимости {COST}: запрос отклоняется с 402")
    resp = client.post(
        "/predict",
        json={"model": MODEL, "rows": [{"feature_1": 1.0, "feature_2": 1.0}]},
        headers=headers,
    )
    assert resp.status_code == 402
    assert "detail" in resp.json(), "пользователь получает понятное сообщение"
    assert client.get("/balance", headers=headers).json() == {"balance": balance}

    # -----------------------------------------------------------------
    # Шаг 6. История операций: сверка с тем, что действительно происходило
    # -----------------------------------------------------------------
    step("6.1", "История ML-запросов содержит все отправленные задачи")
    predictions = client.get("/history/predictions", headers=headers).json()
    by_id = {p["task_id"]: p for p in predictions}
    assert ok_task in by_id and mixed_task in by_id and bad_task in by_id
    assert by_id[ok_task]["status"] == "done"
    assert by_id[ok_task]["result"] == [1, 0]
    assert by_id[bad_task]["status"] == "validation_failed"
    assert by_id[bad_task]["charged"] == 0.0

    step("6.2", "Записи отсортированы: новые сверху")
    dates = [p["created_at"] for p in predictions]
    assert dates == sorted(dates, reverse=True)

    step("6.3", "История транзакций сходится с балансом")
    txs = client.get("/history/transactions", headers=headers).json()
    deposits = sum(t["amount"] for t in txs if t["type"] == "deposit")
    withdrawals = sum(t["amount"] for t in txs if t["type"] == "withdrawal")
    assert deposits - withdrawals == balance, "движение средств сходится с остатком"

    step("6.4", "Движение средств по каждой задаче сходится с её исходом")

    def net_by_task(task_id: str) -> float:
        """Итог по задаче: списания минусом, возвраты плюсом."""
        return sum(
            t["amount"] if t["type"] == "deposit" else -t["amount"]
            for t in txs
            if t["task_id"] == task_id
        )

    assert net_by_task(ok_task) == -COST, "за выполненную работу списано"
    assert net_by_task(mixed_task) == -COST, "частично валидная выборка тоже работа"
    # по неудавшейся задаче резерв был снят и тут же возвращён: в журнале обе
    # записи (иначе возврат не был бы виден), но в сумме — ноль
    assert net_by_task(bad_task) == 0.0, "за невыполненную работу не списывают"
    refunds = [t for t in txs if t["type"] == "deposit" and t["task_id"] == bad_task]
    assert len(refunds) == 1 and refunds[0]["amount"] == COST

    step("ИТОГ", f"Сценарий пройден: {len(predictions)} запросов, "
                 f"{len(txs)} транзакций, остаток {balance} кредитов")


def test_user_data_is_isolated(client):
    """Данные одного пользователя не видны другому — проверка на двух аккаунтах."""

    def new_user() -> tuple[dict, str]:
        email = f"e2e-iso-{uuid.uuid4().hex[:8]}@ml-service.com"
        client.post("/auth/register", json={"email": email, "password": PASSWORD})
        token = client.post(
            "/auth/login", json={"email": email, "password": PASSWORD}
        ).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}, email

    alice, _ = new_user()
    bob, _ = new_user()

    step("7.1", "Алиса пополняет баланс и выполняет запрос")
    client.post("/balance/top-up", json={"amount": 20}, headers=alice)
    task_id = client.post(
        "/predict",
        json={"model": MODEL, "rows": [{"feature_1": 1.0, "feature_2": 1.0}]},
        headers=alice,
    ).json()["task_id"]
    _run_worker(task_id)

    step("7.2", "Боб не видит ни задачу Алисы, ни её историю, ни её баланс")
    assert client.get(f"/predict/{task_id}", headers=bob).status_code == 404
    assert client.get("/history/predictions", headers=bob).json() == []
    assert client.get("/balance", headers=bob).json() == {"balance": 0.0}

    step("7.3", "Роль администратора обычному пользователю недоступна")
    assert client.get("/admin/users", headers=bob).status_code == 403
