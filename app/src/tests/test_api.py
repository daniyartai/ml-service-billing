"""Тесты REST API (Задание №4).

Запуск внутри контейнера: docker compose exec app pytest -v

Каждый тест регистрирует собственного пользователя с уникальным email.
Фикстуры client/stub_queue/run_worker/make_user — общие для всех тестовых
файлов, см. conftest.py.
"""

import pytest

from conftest import unique_email

# ---------------------------------------------------------------------------
# Регистрация и авторизация
# ---------------------------------------------------------------------------

def test_register_login_and_me(client, make_user):
    headers, email, _ = make_user("api")

    resp = client.get("/users/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == email
    assert body["is_admin"] is False

    # повторная регистрация того же email -> 409 в едином формате {"detail": ...}
    resp = client.post(
        "/auth/register", json={"email": email, "password": "secret123"}
    )
    assert resp.status_code == 409
    assert "detail" in resp.json()

    # неверный пароль -> 401
    resp = client.post("/auth/login", json={"email": email, "password": "wrong!"})
    assert resp.status_code == 401


def test_register_validation_422(client):
    # некорректный email и короткий пароль отбивает Pydantic
    resp = client.post(
        "/auth/register", json={"email": "not-an-email", "password": "123"}
    )
    assert resp.status_code == 422
    assert "detail" in resp.json()


def test_protected_endpoints_require_auth(client):
    for method, url in [
        ("get", "/users/me"),
        ("get", "/balance"),
        ("post", "/balance/top-up"),
        ("post", "/predict"),
        ("get", "/history/predictions"),
        ("get", "/history/transactions"),
    ]:
        resp = getattr(client, method)(url)
        assert resp.status_code == 401, url

    resp = client.get("/balance", headers={"Authorization": "Bearer wrong-token"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Баланс
# ---------------------------------------------------------------------------

def test_balance_top_up_returns_updated(client, make_user):
    headers, _, _ = make_user("api")

    resp = client.get("/balance", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"balance": 0.0}

    resp = client.post("/balance/top-up", json={"amount": 50}, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"balance": 50.0}  # обновлённый баланс после операции

    resp = client.get("/balance", headers=headers)
    assert resp.json() == {"balance": 50.0}


def test_top_up_non_positive_rejected_422(client, make_user):
    headers, _, _ = make_user("api")
    for bad in (0, -5):
        resp = client.post(
            "/balance/top-up", json={"amount": bad}, headers=headers
        )
        assert resp.status_code == 422
    assert client.get("/balance", headers=headers).json() == {"balance": 0.0}


# ---------------------------------------------------------------------------
# Предсказания
# ---------------------------------------------------------------------------

def test_predict_success_and_history(client, make_user, stub_queue, run_worker):
    headers, _, _ = make_user("api")
    client.post("/balance/top-up", json={"amount": 20}, headers=headers)

    # publisher: задача поставлена в очередь, ответ сразу — task_id и status=new
    resp = client.post(
        "/predict",
        json={
            "model": "threshold-scoring",
            "rows": [{"feature_1": 1.0, "feature_2": 1.0}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    task = resp.json()
    assert task["status"] == "new"
    assert task["result"] is None
    # средства зарезервированы сразу при постановке задачи
    assert task["charged"] == 5.0
    assert client.get("/balance", headers=headers).json() == {"balance": 15.0}

    # consumer: имитируем воркера
    run_worker(task["task_id"])

    # опрос результата
    resp = client.get(f"/predict/{task['task_id']}", headers=headers)
    assert resp.status_code == 200
    done = resp.json()
    assert done["status"] == "done"
    assert done["result"] == [1]
    assert done["charged"] == 5.0

    # успех: резерв остаётся списанным
    assert client.get("/balance", headers=headers).json() == {"balance": 15.0}

    # история предиктов: дата, статус, списанные кредиты
    resp = client.get("/history/predictions", headers=headers)
    assert resp.status_code == 200
    history = resp.json()
    assert len(history) == 1
    assert history[0]["status"] == "done"
    assert history[0]["charged"] == 5.0
    assert "created_at" in history[0]

    # история транзакций: пополнение и списание, привязанное к задаче
    resp = client.get("/history/transactions", headers=headers)
    txs = resp.json()
    assert {t["type"] for t in txs} == {"deposit", "withdrawal"}
    withdrawal = next(t for t in txs if t["type"] == "withdrawal")
    assert withdrawal["amount"] == 5.0
    assert withdrawal["task_id"] == task["task_id"]


def test_predict_insufficient_balance_402(client, make_user, stub_queue):
    headers, _, _ = make_user("api")  # баланс 0

    resp = client.post(
        "/predict",
        json={"model": "linear-regression", "rows": [{"x1": 1.0, "x2": 2.0}]},
        headers=headers,
    )
    assert resp.status_code == 402
    assert "detail" in resp.json()

    # баланс не изменился, отказ сохранён в истории со статусом failed
    assert client.get("/balance", headers=headers).json() == {"balance": 0.0}
    history = client.get("/history/predictions", headers=headers).json()
    assert len(history) == 1 and history[0]["status"] == "failed"


def test_predict_validation_failed_async(client, make_user, stub_queue, run_worker):
    headers, _, _ = make_user("api")
    client.post("/balance/top-up", json={"amount": 20}, headers=headers)

    resp = client.post(
        "/predict",
        json={"model": "threshold-scoring", "rows": [{"feature_1": 1.0}]},
        headers=headers,
    )
    assert resp.status_code == 201  # публикация прошла, валидация — в воркере
    task_id = resp.json()["task_id"]

    # резерв списан при постановке
    assert client.get("/balance", headers=headers).json() == {"balance": 15.0}

    run_worker(task_id)

    task = client.get(f"/predict/{task_id}", headers=headers).json()
    assert task["status"] == "validation_failed"
    assert task["invalid_rows"]  # причины ошибок по строкам
    assert task["charged"] == 0.0

    # средства возвращены на баланс
    assert client.get("/balance", headers=headers).json() == {"balance": 20.0}


def test_predict_partial_validation(client, make_user, stub_queue, run_worker):
    """Требование задания: некорректные строки возвращаются пользователю,
    корректные — обрабатываются в том же запросе."""
    headers, _, _ = make_user("api")
    client.post("/balance/top-up", json={"amount": 20}, headers=headers)

    rows = [
        {"feature_1": 1.0, "feature_2": 1.0},   # валидная -> 1
        {"feature_1": 5.0},                     # пропущен признак
        {"feature_1": "abc", "feature_2": 1.0},  # нечисловое значение
        {"feature_1": 0.0, "feature_2": 0.0},   # валидная -> 0
    ]
    resp = client.post(
        "/predict", json={"model": "threshold-scoring", "rows": rows}, headers=headers
    )
    # одна плохая ячейка не должна отклонять всю выборку целиком
    assert resp.status_code == 201, resp.text
    task_id = resp.json()["task_id"]

    run_worker(task_id)

    task = client.get(f"/predict/{task_id}", headers=headers).json()
    assert task["status"] == "done"
    assert task["result"] == [1, 0]  # предсказание только по валидным строкам

    # пользователь видит, какие именно строки отклонены и почему
    invalid = task["invalid_rows"]
    assert [item["row"] for item in invalid] == [rows[1], rows[2]]
    assert "отсутствуют признаки" in invalid[0]["reason"]
    assert "нечисловые значения" in invalid[1]["reason"]

    # работа выполнена -> резерв остаётся списанным
    assert task["charged"] == 5.0
    assert client.get("/balance", headers=headers).json() == {"balance": 15.0}


def test_predict_all_rows_non_numeric_refunded(client, make_user, stub_queue, run_worker):
    """Ни одна строка не прошла валидацию -> запрос не выполнен, кредиты возвращены."""
    headers, _, _ = make_user("api")
    client.post("/balance/top-up", json={"amount": 20}, headers=headers)

    resp = client.post(
        "/predict",
        json={
            "model": "threshold-scoring",
            "rows": [{"feature_1": "n/a", "feature_2": None}],
        },
        headers=headers,
    )
    assert resp.status_code == 201
    task_id = resp.json()["task_id"]

    run_worker(task_id)

    task = client.get(f"/predict/{task_id}", headers=headers).json()
    assert task["status"] == "validation_failed"
    assert "нечисловые значения" in task["invalid_rows"][0]["reason"]
    assert task["charged"] == 0.0
    assert client.get("/balance", headers=headers).json() == {"balance": 20.0}


def test_predict_unknown_model_404(client, make_user, stub_queue):
    headers, _, _ = make_user("api")
    client.post("/balance/top-up", json={"amount": 20}, headers=headers)
    resp = client.post(
        "/predict",
        json={"model": "no-such-model", "rows": [{"x": 1.0}]},
        headers=headers,
    )
    assert resp.status_code == 404


def test_foreign_task_hidden_404(client, make_user, stub_queue):
    headers_a, _, _ = make_user("api")
    client.post("/balance/top-up", json={"amount": 20}, headers=headers_a)
    task = client.post(
        "/predict",
        json={
            "model": "threshold-scoring",
            "rows": [{"feature_1": 1.0, "feature_2": 1.0}],
        },
        headers=headers_a,
    ).json()

    headers_b, _, _ = make_user("api")
    resp = client.get(f"/predict/{task['task_id']}", headers=headers_b)
    assert resp.status_code == 404  # чужая задача не раскрывается


# ---------------------------------------------------------------------------
# Формат сообщения очереди (Задание №5)
# ---------------------------------------------------------------------------

def test_queue_message_format(client):
    """Сообщение для RabbitMQ содержит обязательные поля задания №5."""
    from database import SessionLocal
    from mq import build_message
    from services import create_prediction_task, create_user, top_up

    with SessionLocal() as session:
        user = create_user(session, unique_email("api"), "secret123")
        top_up(session, user.id, 20)
        task = create_prediction_task(
            session,
            user.id,
            "threshold-scoring",
            [{"feature_1": 1.0, "feature_2": 1.0}],
        )
        message = build_message(task)

    assert message["task_id"] == task.id
    assert message["model"] == "threshold-scoring"
    assert message["features"] == [{"feature_1": 1.0, "feature_2": 1.0}]
    assert "timestamp" in message


# ---------------------------------------------------------------------------
# Возврат средств при неуспехе (замечание ревьюера по этапу №5)
# ---------------------------------------------------------------------------

def test_refund_when_publish_fails(client, make_user, monkeypatch):
    """Задачу не удалось поставить в очередь -> 503 и возврат средств."""
    import routers.predict as predict_router

    headers, _, _ = make_user("api")
    client.post("/balance/top-up", json={"amount": 20}, headers=headers)

    def boom(task):
        raise RuntimeError("брокер недоступен")

    monkeypatch.setattr(predict_router.mq, "publish_task", boom)

    resp = client.post(
        "/predict",
        json={
            "model": "threshold-scoring",
            "rows": [{"feature_1": 1.0, "feature_2": 1.0}],
        },
        headers=headers,
    )
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "кредиты возвращены" in detail
    # текст ошибки брокера пользователю не показываем — он уходит в лог
    assert "брокер недоступен" not in detail

    # баланс восстановлен полностью
    assert client.get("/balance", headers=headers).json() == {"balance": 20.0}

    # в истории видно и списание, и возврат
    txs = client.get("/history/transactions", headers=headers).json()
    refunds = [t for t in txs if t["type"] == "deposit" and t["task_id"]]
    assert len(refunds) == 1 and refunds[0]["amount"] == 5.0


def test_refund_when_worker_fails(client, make_user, monkeypatch, stub_queue):
    """Ошибка предикта в воркере -> задача failed и возврат средств."""
    from database import SessionLocal
    import services
    from domain import ThresholdScoringModel

    headers, _, _ = make_user("api")
    client.post("/balance/top-up", json={"amount": 20}, headers=headers)

    task_id = client.post(
        "/predict",
        json={
            "model": "threshold-scoring",
            "rows": [{"feature_1": 1.0, "feature_2": 1.0}],
        },
        headers=headers,
    ).json()["task_id"]
    assert client.get("/balance", headers=headers).json() == {"balance": 15.0}

    def boom(self, rows):
        raise RuntimeError("модель упала")

    monkeypatch.setattr(ThresholdScoringModel, "predict", boom)

    with SessionLocal() as session:
        with pytest.raises(RuntimeError):
            services.execute_prediction_task(session, task_id)

    task = client.get(f"/predict/{task_id}", headers=headers).json()
    assert task["status"] == "failed"
    assert task["charged"] == 0.0

    # средства вернулись пользователю
    assert client.get("/balance", headers=headers).json() == {"balance": 20.0}


def test_refund_is_idempotent(client, make_user, stub_queue, run_worker):
    """Повторный возврат по той же задаче не начисляет средства дважды."""
    from database import SessionLocal
    import services

    headers, _, _ = make_user("api")
    client.post("/balance/top-up", json={"amount": 20}, headers=headers)
    task_id = client.post(
        "/predict",
        json={"model": "threshold-scoring", "rows": [{"feature_1": 1.0}]},
        headers=headers,
    ).json()["task_id"]

    run_worker(task_id)  # validation_failed -> возврат
    assert client.get("/balance", headers=headers).json() == {"balance": 20.0}

    with SessionLocal() as session:
        services.refund_task(session, task_id)
        services.refund_task(session, task_id)

    assert client.get("/balance", headers=headers).json() == {"balance": 20.0}


def test_no_intermediate_state_with_unrefunded_charge(client, make_user, stub_queue):
    """Инвариант: снаружи задача никогда не видна как проваленная с
    непогашенным списанием.

    Статус неуспеха и возврат резерва обязаны попадать в БД одной
    транзакцией. Если коммитить их по отдельности, клиент, опрашивающий
    GET /predict/{id} между двумя коммитами, увидит невыполненную работу с
    уже списанными кредитами. Синхронный вызов воркера в остальных тестах
    это окно скрывает — здесь оно ловится через журнал коммитов.
    """
    from sqlalchemy import event

    from database import SessionLocal
    from db_models import MLTaskORM
    from services import execute_prediction_task

    headers, _, _ = make_user("api")
    client.post("/balance/top-up", json={"amount": 20}, headers=headers)
    task_id = client.post(
        "/predict",
        json={"model": "threshold-scoring", "rows": [{"feature_1": "n/a"}]},
        headers=headers,
    ).json()["task_id"]

    committed: list[tuple[str, float]] = []

    with SessionLocal() as session:

        @event.listens_for(session, "after_commit")
        def _snapshot(sess):  # состояние, ставшее видимым другим сессиям
            row = sess.get(MLTaskORM, task_id)
            if row is not None:
                committed.append((row.status.value, float(row.charged)))

        execute_prediction_task(session, task_id)

    assert committed, "ни одного коммита не зафиксировано"
    inconsistent = [
        snap for snap in committed
        if snap[0] in ("validation_failed", "failed") and snap[1] > 0
    ]
    assert not inconsistent, (
        f"промежуточное состояние видно снаружи: {inconsistent}"
    )
