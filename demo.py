"""Демо-сценарий: регистрация, пополнение, запрос к ML-сервису, история."""

import sys

# Корректный вывод кириллицы в любых терминалах (Windows cmd, Git Bash и т.д.)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from domain import (
    Admin, InsufficientBalanceError, LinearRegressionModel, MLService,
    TaskHistory, ThresholdScoringModel, TransactionLedger, User,
)


def main() -> None:
    ledger = TransactionLedger()
    history = TaskHistory()
    service = MLService(ledger, history)

    # Пользователи
    user = User(email="demo@user.kz", password="secret123")
    admin = Admin(email="admin@service.kz", password="admin123")
    print("Регистрация:", user, admin)
    print("Авторизация demo:", user.verify_password("secret123"))

    # Модели (полиморфизм: одинаковый интерфейс, разный predict)
    models = [ThresholdScoringModel(), LinearRegressionModel()]
    for m in models:
        print(f"Модель {m.name}: {m.cost_per_request} кредитов/запрос")

    # Запрос без баланса -> отказ
    try:
        service.submit(user, models[0], [{"feature_1": 1.0, "feature_2": 0.5}])
    except InsufficientBalanceError as e:
        print("Ожидаемый отказ:", e)

    # Админ пополняет баланс пользователю
    admin.top_up_user(user, 50.0, ledger)
    print("Баланс после пополнения:", user.balance)

    # Запрос со смешанными данными: валидные + ошибочные
    rows = [
        {"feature_1": 1.0, "feature_2": 0.5},        # валидная
        {"feature_1": "abc", "feature_2": 0.1},      # нечисловое значение
        {"feature_2": 2.0},                          # нет feature_1
        {"feature_1": 0.1, "feature_2": 0.2},        # валидная
    ]
    task = service.submit(user, models[0], rows)
    print("Статус задачи:", task.status.value)
    print("Предсказания по валидным строкам:", task.result)
    print("Ошибочные строки, возвращённые пользователю:")
    for row, reason in task.invalid_rows:
        print("  ", row, "->", reason)
    print("Списано кредитов:", task.charged, "| Баланс:", user.balance)

    # Истории
    print("\nИстория транзакций пользователя:")
    for tx in ledger.for_user(user):
        print("  ", tx)
    print("История задач пользователя:", len(history.for_user(user)))
    print("Админ видит все транзакции:", len(admin.view_all_transactions(ledger)))


if __name__ == "__main__":
    main()
