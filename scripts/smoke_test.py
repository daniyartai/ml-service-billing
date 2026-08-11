"""Смоук-проверка развёрнутой системы (Задание №7).

Скрипт работает с уже поднятым стендом по HTTP: настоящий Postgres, настоящий
RabbitMQ, настоящие воркеры, запрос идёт через nginx. Это проверка не логики
приложения (её покрывают pytest-тесты), а того, что контейнеры собраны в
рабочую систему и связаны между собой.

Запуск при поднятом docker compose:
    python scripts/smoke_test.py
    python scripts/smoke_test.py --base-url http://localhost:8080

Код возврата 0 — все проверки прошли, 1 — есть падения.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid

import httpx

if hasattr(sys.stdout, "reconfigure"):  # кириллица в Windows-терминалах
    sys.stdout.reconfigure(encoding="utf-8")

PASSWORD = "secret123"
MODEL = "threshold-scoring"
COST = 5.0
POLL_TIMEOUT = 30.0  # сколько ждём воркера, секунд
# состояния, после которых задача больше не меняется; "running" промежуточное
TERMINAL = {"done", "validation_failed", "failed"}

OK, FAIL = "  OK  ", " FAIL "
_failures: list[str] = []


def check(title: str, condition: bool, detail: str = "") -> None:
    """Записать результат проверки в протокол."""
    mark = OK if condition else FAIL
    print(f"[{mark}] {title}" + (f" — {detail}" if detail else ""))
    if not condition:
        _failures.append(title)


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def wait_for_task(client: httpx.Client, headers: dict, task_id: str) -> dict:
    """Дождаться, пока воркер разберёт задачу из очереди."""
    deadline = time.monotonic() + POLL_TIMEOUT
    while time.monotonic() < deadline:
        task = client.get(f"/predict/{task_id}", headers=headers).json()
        if task["status"] in TERMINAL:
            return task
        time.sleep(0.4)
    raise TimeoutError(
        f"воркер не обработал задачу {task_id} за {POLL_TIMEOUT} c — "
        "проверьте, что контейнеры worker запущены и RabbitMQ доступен"
    )


def run(base_url: str) -> int:
    print(f"Смоук-проверка стенда: {base_url}")
    with httpx.Client(base_url=base_url, timeout=15.0) as client:

        # ---------------------------------------------------------------
        section("Доступность сервисов")
        health = client.get("/health")
        check("Приложение отвечает на /health", health.status_code == 200)

        models = client.get("/models")
        names = [m["name"] for m in models.json()] if models.status_code == 200 else []
        check("Каталог моделей загружен", MODEL in names, f"модели: {', '.join(names)}")

        page = client.get("/")
        check("Главная страница отдаётся", page.status_code == 200)

        # ---------------------------------------------------------------
        section("Пользователи")
        email = f"smoke-{uuid.uuid4().hex[:8]}@ml-service.com"
        resp = client.post("/auth/register", json={"email": email, "password": PASSWORD})
        check("Регистрация нового пользователя", resp.status_code == 201, email)

        resp = client.post("/auth/login", json={"email": email, "password": PASSWORD})
        check("Авторизация", resp.status_code == 200)
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

        again = client.post("/auth/login", json={"email": email, "password": PASSWORD})
        check("Повторная авторизация выдаёт новый токен", again.status_code == 200)

        resp = client.post("/auth/login", json={"email": email, "password": "wrong"})
        check("Неверный пароль отклоняется", resp.status_code == 401)

        resp = client.get("/balance", headers={"Authorization": "Bearer bad-token"})
        check("Недействительный токен отклоняется", resp.status_code == 401)

        # ---------------------------------------------------------------
        section("Баланс")
        balance = client.get("/balance", headers=headers).json()["balance"]
        check("Начальный баланс равен нулю", balance == 0.0, f"{balance}")

        resp = client.post("/balance/top-up", json={"amount": 50}, headers=headers)
        balance = resp.json().get("balance")
        check("Пополнение на 50", resp.status_code == 200 and balance == 50.0, f"{balance}")

        stored = client.get("/balance", headers=headers).json()["balance"]
        check("Баланс сохранён в БД", stored == 50.0, f"{stored}")

        # ---------------------------------------------------------------
        section("ML-запрос через очередь")
        resp = client.post(
            "/predict",
            json={"model": MODEL, "rows": [{"feature_1": 1, "feature_2": 2}]},
            headers=headers,
        )
        check("Задача принята", resp.status_code == 201)
        check("Задача поставлена в очередь", resp.json()["status"] == "new")
        task = wait_for_task(client, headers, resp.json()["task_id"])
        check("Воркер обработал задачу", task["status"] == "done", f"статус {task['status']}")
        check("Получено предсказание", task["result"] == [1], f"{task['result']}")

        balance = client.get("/balance", headers=headers).json()["balance"]
        check("Кредиты списаны", balance == 45.0, f"{balance}")

        # ---------------------------------------------------------------
        section("Валидация данных")
        rows = [
            {"feature_1": 1, "feature_2": 2},
            {"feature_1": 5},
            {"feature_1": "abc", "feature_2": 1},
            {"feature_1": 0, "feature_2": 0},
        ]
        resp = client.post("/predict", json={"model": MODEL, "rows": rows}, headers=headers)
        check("Частично валидная выборка принята", resp.status_code == 201)
        task = wait_for_task(client, headers, resp.json()["task_id"])
        check("Валидные строки обработаны", task["result"] == [1, 0], f"{task['result']}")
        check("Некорректные строки возвращены с причиной", len(task["invalid_rows"]) == 2)

        resp = client.post(
            "/predict",
            json={"model": MODEL, "rows": [{"feature_1": "n/a", "feature_2": None}]},
            headers=headers,
        )
        task = wait_for_task(client, headers, resp.json()["task_id"])
        check("Полностью некорректная выборка отклонена", task["status"] == "validation_failed")
        check("Списания за неудачу не было", task["charged"] == 0.0)

        balance_now = client.get("/balance", headers=headers).json()["balance"]
        check("Кредиты возвращены на баланс", balance_now == 40.0, f"{balance_now}")

        resp = client.post(
            "/predict",
            json={"model": "no-such-model", "rows": [{"feature_1": 1}]},
            headers=headers,
        )
        check("Несуществующая модель — 404", resp.status_code == 404)

        # ---------------------------------------------------------------
        section("Проверка баланса перед выполнением")
        spent = balance_now
        while spent >= COST:
            r = client.post(
                "/predict",
                json={"model": MODEL, "rows": [{"feature_1": 1, "feature_2": 1}]},
                headers=headers,
            )
            wait_for_task(client, headers, r.json()["task_id"])
            spent -= COST

        resp = client.post(
            "/predict",
            json={"model": MODEL, "rows": [{"feature_1": 1, "feature_2": 1}]},
            headers=headers,
        )
        check("При нехватке кредитов запрос отклонён", resp.status_code == 402)
        check("Пользователь получил объяснение", bool(resp.json().get("detail")),
              resp.json().get("detail", ""))

        # ---------------------------------------------------------------
        section("История операций")
        predictions = client.get("/history/predictions", headers=headers).json()
        check("История ML-запросов сохранена", len(predictions) >= 4, f"{len(predictions)} записей")

        statuses = {p["status"] for p in predictions}
        check("В истории есть и успешные, и отклонённые",
              {"done", "validation_failed"} <= statuses, ", ".join(sorted(statuses)))

        txs = client.get("/history/transactions", headers=headers).json()
        deposits = sum(t["amount"] for t in txs if t["type"] == "deposit")
        withdrawals = sum(t["amount"] for t in txs if t["type"] == "withdrawal")
        final = client.get("/balance", headers=headers).json()["balance"]
        check("Транзакции сходятся с балансом", deposits - withdrawals == final,
              f"{deposits} - {withdrawals} = {final}")

        refunds = [t for t in txs if t["type"] == "deposit" and t["task_id"]]
        check("Возврат за неудачную задачу записан", len(refunds) == 1)

    # -------------------------------------------------------------------
    print()
    if _failures:
        print(f"ПРОВАЛЕНО {len(_failures)}:")
        for title in _failures:
            print(f"  - {title}")
        return 1
    print("Все проверки пройдены.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://localhost",
        help="адрес поднятого стенда (по умолчанию http://localhost)",
    )
    args = parser.parse_args()
    try:
        return run(args.base_url.rstrip("/"))
    except httpx.ConnectError:
        print(f"Не удалось подключиться к {args.base_url}. Поднят ли стенд? "
              "Запустите docker compose up -d")
        return 1
    except TimeoutError as exc:
        print(f"Таймаут: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
