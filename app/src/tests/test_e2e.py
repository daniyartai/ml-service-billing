"""Сквозной сценарий работоспособности системы (Задание №7).

В отличие от test_api.py, где каждый случай проверяется изолированно, здесь
один пользователь последовательно проходит весь путь: регистрация ->
авторизация -> баланс -> ML-запросы -> история. Проверяется не только
каждый ответ по отдельности, но и связность: баланс сходится с суммой
операций, история содержит ровно те задачи, что отправлялись, транзакции
привязаны к своим задачам.

Сценарий разбит на отдельные тесты — по одному на проверяемую часть,
чтобы при падении сразу было видно, какой шаг сломался. Общее состояние
(аккаунт, баланс, id задач) живёт в фикстуре journey уровня класса:
сквозной сценарий тем и ценен, что состояние накапливается, и разложить
его на независимые тесты, не потеряв смысл, нельзя. Порядок выполнения
задаёт pytest — сверху вниз внутри класса.

Запуск с журналом шагов:
    docker compose exec app pytest tests/test_e2e.py -v -s
"""

import uuid
from dataclasses import dataclass, field

import pytest

MODEL = "threshold-scoring"
COST = 5.0  # стоимость одного запроса к threshold-scoring
PASSWORD = "secret123"


@dataclass
class Journey:
    """Состояние пользователя, накопленное по ходу сценария."""

    headers: dict
    email: str
    user_id: str
    balance: float = 0.0
    tasks: dict[str, str] = field(default_factory=dict)  # роль -> task_id


def step(number: str, text: str) -> None:
    """Журнал сценария — виден при запуске pytest с ключом -s."""
    print(f"\n[{number}] {text}")


@pytest.fixture(scope="class")
def journey(client):
    """Аккаунт для сквозного прохода: создаётся один раз на весь класс.

    Регистрацию проводит напрямую, а не через фикстуру make_user из
    conftest.py: make_user — function-scoped (каждый вызов заводит новый
    аккаунт для одного теста), а journey — class-scoped; pytest не
    позволяет фикстуре более широкой области зависеть от более узкой.
    """
    email = f"e2e-{uuid.uuid4().hex[:10]}@ml-service.com"
    resp = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]

    resp = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    return Journey(headers=headers, email=email, user_id=user_id)


@pytest.mark.usefixtures("stub_queue")
class TestUserJourney:
    """Путь одного пользователя: от регистрации до сверки истории.

    Тесты выполняются по порядку и опираются на состояние предыдущих —
    это осознанное свойство сквозного сценария, а не связанность по
    недосмотру. Изолированные проверки тех же эндпоинтов — в test_api.py.
    """

    # -- 1. Пользователи ------------------------------------------------

    def test_registered_user_is_authorized(self, client, journey):
        """Регистрация и авторизация: токен даёт доступ к своему профилю."""
        step("1.1", f"Пользователь {journey.email} зарегистрирован и авторизован")
        resp = client.get("/users/me", headers=journey.headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == journey.user_id
        assert resp.json()["email"] == journey.email

    def test_repeated_login_keeps_old_token_valid(self, client, journey):
        """Повторная авторизация выдаёт новый токен, не отзывая прежний."""
        step("1.2", "Повторная авторизация: старый токен продолжает работать")
        resp = client.post(
            "/auth/login", json={"email": journey.email, "password": PASSWORD}
        )
        assert resp.status_code == 200
        new_token = resp.json()["access_token"]
        assert f"Bearer {new_token}" != journey.headers["Authorization"]

        for label, auth in (
            ("прежний", journey.headers["Authorization"]),
            ("новый", f"Bearer {new_token}"),
        ):
            resp = client.get("/users/me", headers={"Authorization": auth})
            assert resp.status_code == 200, f"{label} токен должен быть действителен"

    def test_invalid_credentials_are_rejected(self, client, journey):
        """Занятый email, чужой пароль и битый токен отклоняются."""
        step("1.3", "Ошибки при неверных данных")
        assert client.post(
            "/auth/register", json={"email": journey.email, "password": PASSWORD}
        ).status_code == 409
        assert client.post(
            "/auth/login", json={"email": journey.email, "password": "wrong-password"}
        ).status_code == 401
        assert client.get(
            "/users/me", headers={"Authorization": "Bearer not-a-real-token"}
        ).status_code == 401

    # -- 2. Баланс ------------------------------------------------------

    def test_new_user_starts_with_zero_balance(self, client, journey):
        step("2.1", "Начальный баланс нового пользователя — 0")
        assert client.get("/balance", headers=journey.headers).json() == {"balance": 0.0}

    def test_top_up_accumulates(self, client, journey):
        """Два пополнения подряд складываются и сохраняются в БД."""
        step("2.2", "Пополнение на 50, затем на 20 — суммы складываются")
        resp = client.post("/balance/top-up", json={"amount": 50}, headers=journey.headers)
        assert resp.status_code == 200 and resp.json() == {"balance": 50.0}

        resp = client.post("/balance/top-up", json={"amount": 20}, headers=journey.headers)
        assert resp.json() == {"balance": 70.0}

        # значение действительно сохранено, а не только возвращено в ответе
        assert client.get("/balance", headers=journey.headers).json() == {"balance": 70.0}
        journey.balance = 70.0

    def test_non_positive_top_up_rejected(self, client, journey):
        step("2.3", "Пополнение на неположительную сумму отклоняется")
        assert client.post(
            "/balance/top-up", json={"amount": 0}, headers=journey.headers
        ).status_code == 422
        assert client.get("/balance", headers=journey.headers).json() == {
            "balance": journey.balance
        }

    # -- 3. ML-запрос и списание ----------------------------------------

    def test_prediction_returns_result_and_charges(self, client, journey, run_worker):
        """Корректный запрос: задача принимается, обрабатывается, списывается."""
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
            headers=journey.headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "new", "задача принимается асинхронно"
        journey.tasks["ok"] = resp.json()["task_id"]

        step("3.2", "Обработка воркером и получение результата")
        run_worker(journey.tasks["ok"])
        task = client.get(f"/predict/{journey.tasks['ok']}", headers=journey.headers).json()
        assert task["status"] == "done"
        assert task["result"] == [1, 0]
        assert task["charged"] == COST

        step("3.3", "Кредиты списаны ровно за один запрос")
        journey.balance -= COST
        assert client.get("/balance", headers=journey.headers).json() == {
            "balance": journey.balance
        }

    # -- 4. Валидация данных --------------------------------------------

    def test_partially_valid_rows_are_processed(self, client, journey, run_worker):
        """Годные строки обрабатываются, негодные возвращаются с причиной."""
        step("4.1", "Частично валидная выборка: 2 строки годные, 2 — нет")
        rows = [
            {"feature_1": 1.0, "feature_2": 2.0},    # валидная
            {"feature_1": 5.0},                      # нет признака feature_2
            {"feature_1": "abc", "feature_2": 1.0},  # нечисловое значение
            {"feature_1": 0.0, "feature_2": 0.0},    # валидная
        ]
        resp = client.post(
            "/predict", json={"model": MODEL, "rows": rows}, headers=journey.headers
        )
        assert resp.status_code == 201, resp.text
        journey.tasks["mixed"] = resp.json()["task_id"]

        run_worker(journey.tasks["mixed"])
        task = client.get(
            f"/predict/{journey.tasks['mixed']}", headers=journey.headers
        ).json()
        assert task["status"] == "done"
        assert task["result"] == [1, 0], "предсказание только по валидным строкам"
        assert [item["row"] for item in task["invalid_rows"]] == [rows[1], rows[2]]
        assert "отсутствуют признаки" in task["invalid_rows"][0]["reason"]
        assert "нечисловые значения" in task["invalid_rows"][1]["reason"]

        step("4.2", "Работа выполнена частично, но выполнена — списание произошло")
        journey.balance -= COST
        assert client.get("/balance", headers=journey.headers).json() == {
            "balance": journey.balance
        }

    def test_fully_invalid_rows_are_refunded(self, client, journey, run_worker):
        """Ни одна строка не прошла валидацию -> работа не выполнена, возврат."""
        step("4.3", "Полностью некорректная выборка: списания нет, кредиты возвращены")
        resp = client.post(
            "/predict",
            json={"model": MODEL, "rows": [{"feature_1": "n/a", "feature_2": None}]},
            headers=journey.headers,
        )
        journey.tasks["bad"] = resp.json()["task_id"]

        run_worker(journey.tasks["bad"])
        task = client.get(
            f"/predict/{journey.tasks['bad']}", headers=journey.headers
        ).json()
        assert task["status"] == "validation_failed"
        assert task["charged"] == 0.0
        assert client.get("/balance", headers=journey.headers).json() == {
            "balance": journey.balance
        }

    def test_unknown_model_does_not_touch_balance(self, client, journey):
        step("4.4", "Несуществующая модель — 404, средства не тронуты")
        assert client.post(
            "/predict",
            json={"model": "no-such-model", "rows": [{"feature_1": 1.0}]},
            headers=journey.headers,
        ).status_code == 404
        assert client.get("/balance", headers=journey.headers).json() == {
            "balance": journey.balance
        }

    # -- 5. Проверка баланса перед выполнением --------------------------

    def test_request_rejected_when_credits_run_out(self, client, journey, run_worker):
        """Потратив баланс, пользователь получает отказ вместо выполнения."""
        step("5.1", "Тратим остаток баланса до суммы меньше стоимости запроса")
        while journey.balance >= COST:
            resp = client.post(
                "/predict",
                json={"model": MODEL, "rows": [{"feature_1": 1.0, "feature_2": 1.0}]},
                headers=journey.headers,
            )
            assert resp.status_code == 201, resp.text
            run_worker(resp.json()["task_id"])
            journey.balance -= COST

        step("5.2", f"Баланс {journey.balance} < стоимости {COST}: отказ с 402")
        resp = client.post(
            "/predict",
            json={"model": MODEL, "rows": [{"feature_1": 1.0, "feature_2": 1.0}]},
            headers=journey.headers,
        )
        assert resp.status_code == 402
        assert resp.json().get("detail"), "пользователь получает понятное сообщение"
        assert client.get("/balance", headers=journey.headers).json() == {
            "balance": journey.balance
        }

    # -- 6. История операций --------------------------------------------

    def test_history_contains_all_submitted_tasks(self, client, journey):
        """В истории есть каждая отправленная задача со своим исходом."""
        step("6.1", "История ML-запросов содержит все отправленные задачи")
        predictions = client.get("/history/predictions", headers=journey.headers).json()
        by_id = {p["task_id"]: p for p in predictions}

        assert set(journey.tasks.values()) <= set(by_id)
        assert by_id[journey.tasks["ok"]]["status"] == "done"
        assert by_id[journey.tasks["ok"]]["result"] == [1, 0]
        assert by_id[journey.tasks["bad"]]["status"] == "validation_failed"
        assert by_id[journey.tasks["bad"]]["charged"] == 0.0

        step("6.2", "Записи отсортированы: новые сверху")
        dates = [p["created_at"] for p in predictions]
        assert dates == sorted(dates, reverse=True)

    def test_transactions_reconcile_with_balance(self, client, journey):
        """Сумма движений по счёту равна текущему остатку."""
        step("6.3", "История транзакций сходится с балансом")
        txs = client.get("/history/transactions", headers=journey.headers).json()
        deposits = sum(t["amount"] for t in txs if t["type"] == "deposit")
        withdrawals = sum(t["amount"] for t in txs if t["type"] == "withdrawal")
        assert deposits - withdrawals == journey.balance

    def test_each_task_charge_matches_its_outcome(self, client, journey):
        """Итог по каждой задаче соответствует тому, была ли выполнена работа."""
        step("6.4", "Движение средств по каждой задаче сходится с её исходом")
        txs = client.get("/history/transactions", headers=journey.headers).json()

        def net_by_task(task_id: str) -> float:
            """Итог по задаче: списания минусом, возвраты плюсом."""
            return sum(
                t["amount"] if t["type"] == "deposit" else -t["amount"]
                for t in txs
                if t["task_id"] == task_id
            )

        assert net_by_task(journey.tasks["ok"]) == -COST, "за выполненную работу списано"
        assert net_by_task(journey.tasks["mixed"]) == -COST, "частичная выборка — тоже работа"
        # по неудавшейся задаче резерв был снят и тут же возвращён: в журнале
        # обе записи (иначе возврат не был бы виден), но в сумме — ноль
        assert net_by_task(journey.tasks["bad"]) == 0.0, "за невыполненную работу не списывают"

        refunds = [
            t for t in txs
            if t["type"] == "deposit" and t["task_id"] == journey.tasks["bad"]
        ]
        assert len(refunds) == 1 and refunds[0]["amount"] == COST


@pytest.mark.usefixtures("stub_queue")
class TestDataIsolation:
    """Данные одного пользователя недоступны другому."""

    def test_foreign_task_and_history_are_hidden(self, client, make_user, run_worker):
        step("7.1", "Алиса пополняет баланс и выполняет запрос")
        alice, _, _ = make_user("e2e-alice")
        bob, _, _ = make_user("e2e-bob")

        client.post("/balance/top-up", json={"amount": 20}, headers=alice)
        task_id = client.post(
            "/predict",
            json={"model": MODEL, "rows": [{"feature_1": 1.0, "feature_2": 1.0}]},
            headers=alice,
        ).json()["task_id"]
        run_worker(task_id)

        step("7.2", "Боб не видит ни задачу Алисы, ни её историю, ни её баланс")
        assert client.get(f"/predict/{task_id}", headers=bob).status_code == 404
        assert client.get("/history/predictions", headers=bob).json() == []
        assert client.get("/balance", headers=bob).json() == {"balance": 0.0}

    def test_admin_area_denied_for_regular_user(self, client, user):
        step("7.3", "Роль администратора обычному пользователю недоступна")
        assert client.get("/admin/users", headers=user).status_code == 403
