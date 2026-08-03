"""Тесты сценариев Задания №3.

Запуск внутри контейнера: docker compose exec app pytest -v

Тесты работают с той же БД, что и приложение; каждый тест создаёт
собственного пользователя с уникальным email, поэтому повторные запуски
не конфликтуют друг с другом и с демо-данными.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from database import SessionLocal
from db_models import MLModelORM
from domain import InsufficientBalanceError, TaskStatus, TransactionType
from init_db import DEMO_INITIAL_CREDITS, DEMO_USER_EMAIL, init_db
from services import (
    create_user,
    get_balance,
    get_user,
    get_user_by_email,
    get_user_tasks,
    get_user_transactions,
    run_prediction,
    top_up,
    verify_password,
    withdraw,
)


@pytest.fixture(scope="session", autouse=True)
def prepared_db():
    """Схема и демо-данные готовы до запуска тестов (идемпотентно)."""
    init_db()


@pytest.fixture()
def session():
    with SessionLocal() as s:
        yield s


def unique_email() -> str:
    return f"test-{uuid.uuid4().hex[:10]}@ml-service.local"


# ---------------------------------------------------------------------------
# Пользователи
# ---------------------------------------------------------------------------

def test_create_and_load_user(session):
    email = unique_email()
    created = create_user(session, email, "secret123")

    by_email = get_user_by_email(session, email)
    by_id = get_user(session, created.id)

    assert by_email is not None and by_email.id == created.id
    assert by_id is not None and by_id.email == email
    assert by_id.is_admin is False
    assert verify_password(by_id, "secret123")
    assert not verify_password(by_id, "wrong")
    # связь пользователь <-> баланс (1:1), стартовый баланс нулевой
    assert by_id.balance.user_id == by_id.id
    assert get_balance(session, by_id.id) == Decimal("0")


def test_duplicate_email_rejected(session):
    email = unique_email()
    create_user(session, email, "x")
    with pytest.raises(ValueError):
        create_user(session, email, "y")


# ---------------------------------------------------------------------------
# Баланс и транзакции
# ---------------------------------------------------------------------------

def test_top_up_creates_transaction(session):
    user = create_user(session, unique_email(), "x")
    top_up(session, user.id, 50)

    assert get_balance(session, user.id) == Decimal("50.00")
    txs = get_user_transactions(session, user.id)
    assert len(txs) == 1
    assert txs[0].type == TransactionType.DEPOSIT
    assert txs[0].amount == Decimal("50.00")


def test_top_up_non_positive_rejected(session):
    user = create_user(session, unique_email(), "x")
    for bad in (0, -5):
        with pytest.raises(ValueError):
            top_up(session, user.id, bad)
    assert get_balance(session, user.id) == Decimal("0")


def test_withdraw_checks_balance(session):
    user = create_user(session, unique_email(), "x")
    top_up(session, user.id, 30)
    withdraw(session, user.id, 10)
    assert get_balance(session, user.id) == Decimal("20.00")

    # списание больше баланса запрещено, баланс не меняется
    with pytest.raises(InsufficientBalanceError):
        withdraw(session, user.id, 999)
    assert get_balance(session, user.id) == Decimal("20.00")

    txs = get_user_transactions(session, user.id)
    types = [t.type for t in txs]
    assert types.count(TransactionType.WITHDRAWAL) == 1  # неудачное не записано


# ---------------------------------------------------------------------------
# Предсказания и история
# ---------------------------------------------------------------------------

def test_prediction_success_charges_and_saves_history(session):
    user = create_user(session, unique_email(), "x")
    top_up(session, user.id, 20)

    task = run_prediction(
        session, user.id, "threshold-scoring", [{"feature_1": 1.0, "feature_2": 1.0}]
    )

    assert task.status == TaskStatus.DONE
    assert task.result == [1]
    assert task.charged == Decimal("5.00")
    assert get_balance(session, user.id) == Decimal("15.00")

    # списание записано и связано с задачей (Foreign Key)
    withdrawals = [
        t
        for t in get_user_transactions(session, user.id)
        if t.type == TransactionType.WITHDRAWAL
    ]
    assert len(withdrawals) == 1
    assert withdrawals[0].task_id == task.id
    assert withdrawals[0].amount == Decimal("5.00")


def test_prediction_insufficient_balance(session):
    user = create_user(session, unique_email(), "x")  # баланс 0

    with pytest.raises(InsufficientBalanceError):
        run_prediction(
            session, user.id, "linear-regression", [{"x1": 1.0, "x2": 2.0}]
        )

    # баланс не изменился, списания нет, отказ сохранён в истории
    assert get_balance(session, user.id) == Decimal("0")
    assert get_user_transactions(session, user.id) == []
    tasks = get_user_tasks(session, user.id)
    assert len(tasks) == 1
    assert tasks[0].status == TaskStatus.FAILED


def test_prediction_validation_failed_no_charge(session):
    user = create_user(session, unique_email(), "x")
    top_up(session, user.id, 20)

    task = run_prediction(
        session, user.id, "threshold-scoring", [{"feature_1": 1.0}]  # нет feature_2
    )

    assert task.status == TaskStatus.VALIDATION_FAILED
    assert task.invalid_rows  # причина ошибки возвращена пользователю
    assert get_balance(session, user.id) == Decimal("20.00")  # списания не было


def test_history_sorted_by_date(session):
    user = create_user(session, unique_email(), "x")
    top_up(session, user.id, 100)
    for _ in range(3):
        run_prediction(
            session, user.id, "threshold-scoring", [{"feature_1": 2.0, "feature_2": 2.0}]
        )

    tasks = get_user_tasks(session, user.id)  # новые сверху
    assert len(tasks) == 3
    dates = [t.created_at for t in tasks]
    assert dates == sorted(dates, reverse=True)
    # у каждой записи истории доступны сумма и связанная модель
    assert all(t.charged == Decimal("5.00") for t in tasks)
    assert all(t.model.name == "threshold-scoring" for t in tasks)


# ---------------------------------------------------------------------------
# Инициализация
# ---------------------------------------------------------------------------

def test_init_is_idempotent(session):
    init_db()
    init_db()  # повторный запуск ничего не ломает и не дублирует

    models_count = session.scalar(select(func.count()).select_from(MLModelORM))
    assert models_count == 2

    demo = get_user_by_email(session, DEMO_USER_EMAIL)
    assert demo is not None
    # стартовый баланс не начислился повторно
    assert get_balance(session, demo.id) == Decimal(DEMO_INITIAL_CREDITS)
