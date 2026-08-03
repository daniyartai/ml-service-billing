"""Бизнес-операции поверх ORM (Задание №3).

Каждая операция атомарна: изменение баланса, запись транзакции и результата
выполняются одним session.commit() — «всё или ничего».
Бизнес-правила переиспользуются из domain.py (enum-ы, InsufficientBalanceError,
классы ML-моделей с полиморфным predict/validate) — смысл сущностей
задания №1 не менялся.
"""

import hashlib
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db_models import BalanceORM, MLModelORM, MLTaskORM, TransactionORM, UserORM
from domain import (
    InsufficientBalanceError,
    LinearRegressionModel,
    MLModel,
    TaskStatus,
    ThresholdScoringModel,
    TransactionType,
)

# Реестр реализаций: имя модели в БД -> класс из domain.py.
# Параметры (стоимость) берутся из БД, поведение (predict) — из domain.
MODEL_REGISTRY: dict[str, type[MLModel]] = {
    "threshold-scoring": ThresholdScoringModel,
    "linear-regression": LinearRegressionModel,
}


def hash_password(password: str) -> str:
    """Та же схема хеширования, что и в domain.User (sha256)."""
    return hashlib.sha256(password.encode()).hexdigest()


def _to_credits(amount: float | int | str | Decimal) -> Decimal:
    """Нормализовать сумму к Decimal с проверкой положительности."""
    value = Decimal(str(amount))
    if value <= 0:
        raise ValueError("Сумма должна быть положительной")
    return value.quantize(Decimal("0.01"))


def _require_user(session: Session, user_id: str) -> UserORM:
    user = session.get(UserORM, user_id)
    if user is None:
        raise ValueError(f"Пользователь {user_id} не найден")
    return user


def _lock_balance(session: Session, user_id: str) -> BalanceORM:
    """Баланс с блокировкой строки (SELECT ... FOR UPDATE) — защита от гонок."""
    balance = session.scalar(
        select(BalanceORM).where(BalanceORM.user_id == user_id).with_for_update()
    )
    if balance is None:
        raise ValueError(f"Баланс пользователя {user_id} не найден")
    return balance


# ---------------------------------------------------------------------------
# Пользователи
# ---------------------------------------------------------------------------

def create_user(
    session: Session, email: str, password: str, *, is_admin: bool = False
) -> UserORM:
    """Создать пользователя вместе с нулевым балансом (атомарно)."""
    if get_user_by_email(session, email) is not None:
        raise ValueError(f"Пользователь с email {email} уже существует")
    user = UserORM(
        email=email, password_hash=hash_password(password), is_admin=is_admin
    )
    user.balance = BalanceORM(amount=Decimal("0"))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def get_user(session: Session, user_id: str) -> UserORM | None:
    """Загрузить пользователя из БД по id."""
    return session.get(UserORM, user_id)


def get_user_by_email(session: Session, email: str) -> UserORM | None:
    """Загрузить пользователя из БД по email."""
    return session.scalar(select(UserORM).where(UserORM.email == email))


def verify_password(user: UserORM, password: str) -> bool:
    """Проверка пароля — та же логика, что в domain.User.verify_password."""
    return hash_password(password) == user.password_hash


def get_balance(session: Session, user_id: str) -> Decimal:
    """Текущий баланс пользователя."""
    user = _require_user(session, user_id)
    return user.balance.amount


# ---------------------------------------------------------------------------
# Баланс и транзакции
# ---------------------------------------------------------------------------

def top_up(
    session: Session,
    user_id: str,
    amount: float | int | str | Decimal,
    approved_by_id: str | None = None,
) -> TransactionORM:
    """Пополнение: увеличить баланс и записать deposit-транзакцию (атомарно)."""
    value = _to_credits(amount)
    user = _require_user(session, user_id)
    balance = _lock_balance(session, user.id)
    balance.amount += value
    tx = TransactionORM(
        user_id=user.id,
        type=TransactionType.DEPOSIT,
        amount=value,
        approved_by_id=approved_by_id,
    )
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return tx


def withdraw(
    session: Session,
    user_id: str,
    amount: float | int | str | Decimal,
    task_id: str | None = None,
) -> TransactionORM:
    """Списание: проверить баланс, уменьшить его и записать withdrawal-транзакцию.

    При недостатке кредитов — InsufficientBalanceError, баланс не меняется.
    """
    value = _to_credits(amount)
    user = _require_user(session, user_id)
    balance = _lock_balance(session, user.id)
    if balance.amount < value:
        session.rollback()
        raise InsufficientBalanceError(
            f"Недостаточно кредитов: баланс {balance.amount}, требуется {value}"
        )
    balance.amount -= value
    tx = TransactionORM(
        user_id=user.id,
        type=TransactionType.WITHDRAWAL,
        amount=value,
        task_id=task_id,
    )
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return tx


def get_user_transactions(session: Session, user_id: str) -> list[TransactionORM]:
    """История транзакций пользователя, новые сверху."""
    return list(
        session.scalars(
            select(TransactionORM)
            .where(TransactionORM.user_id == user_id)
            .order_by(TransactionORM.created_at.desc())
        )
    )


# ---------------------------------------------------------------------------
# Предсказания и история запросов
# ---------------------------------------------------------------------------

def run_prediction(
    session: Session,
    user_id: str,
    model_name: str,
    rows: list[dict[str, Any]],
) -> MLTaskORM:
    """Сценарий MLService.submit из domain.py, но с сохранением в БД:

    1. проверка баланса ДО выполнения;
    2. валидация входных строк (ошибочные возвращаются пользователю);
    3. полиморфный predict();
    4. списание + результат + история — одним коммитом (атомарно).
    """
    user = _require_user(session, user_id)
    model_row = session.scalar(
        select(MLModelORM).where(MLModelORM.name == model_name)
    )
    if model_row is None:
        raise ValueError(f"ML-модель '{model_name}' не найдена")

    impl_cls = MODEL_REGISTRY.get(model_row.name)
    if impl_cls is None:
        raise ValueError(f"Нет реализации для модели '{model_row.name}'")
    impl = impl_cls(
        name=model_row.name, cost_per_request=float(model_row.cost_per_request)
    )

    task = MLTaskORM(
        user_id=user.id,
        model_id=model_row.id,
        input_data=rows,
        status=TaskStatus.NEW,
    )
    session.add(task)
    session.flush()  # присваивает task.id до привязки транзакции

    cost = model_row.cost_per_request
    balance = _lock_balance(session, user.id)

    # 1. Проверка баланса до выполнения
    if balance.amount < cost:
        task.status = TaskStatus.FAILED
        session.commit()  # отказ тоже сохраняется в истории
        raise InsufficientBalanceError(
            f"Баланс {balance.amount} < стоимости запроса {cost}"
        )

    # 2. Валидация: некорректные строки возвращаются пользователю
    validation = impl.validate(rows)
    task.invalid_rows = [
        {"row": row, "reason": reason} for row, reason in validation.invalid_rows
    ]
    if not validation.has_valid:
        task.status = TaskStatus.VALIDATION_FAILED
        session.commit()
        return task

    # 3. Полиморфный предикт
    task.status = TaskStatus.RUNNING
    try:
        result = impl.predict(validation.valid_rows)
    except Exception:
        task.status = TaskStatus.FAILED
        session.commit()
        raise

    # 4. Списание, транзакция, результат — одним коммитом
    balance.amount -= cost
    session.add(
        TransactionORM(
            user_id=user.id,
            type=TransactionType.WITHDRAWAL,
            amount=cost,
            task_id=task.id,
        )
    )
    task.result = result
    task.charged = cost
    task.status = TaskStatus.DONE
    session.commit()
    session.refresh(task)
    return task


def get_user_tasks(
    session: Session, user_id: str, newest_first: bool = True
) -> list[MLTaskORM]:
    """История предиктов пользователя с сортировкой по дате."""
    order = (
        MLTaskORM.created_at.desc() if newest_first else MLTaskORM.created_at.asc()
    )
    return list(
        session.scalars(
            select(MLTaskORM).where(MLTaskORM.user_id == user_id).order_by(order)
        )
    )
