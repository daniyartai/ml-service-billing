"""Бизнес-операции поверх ORM (Задания №3-4).

Каждая операция атомарна: изменение баланса, запись транзакции и результата
выполняются одним session.commit() — «всё или ничего».
Бизнес-правила переиспользуются из domain.py (enum-ы, InsufficientBalanceError,
классы ML-моделей с полиморфным predict/validate) — смысл сущностей
задания №1 не менялся. REST-контроллеры (routers/) логику не дублируют,
а вызывают функции этого модуля.

Предикт разделён на две фазы с прицелом на асинхронный этап №5:
  create_prediction_task() — сторона publisher'а (создать задачу, проверить баланс);
  execute_prediction_task() — сторона воркера (валидация, предикт, списание).
Сейчас run_prediction() вызывает их последовательно (синхронно).
"""

import hashlib
import logging
import secrets
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from db_models import (
    AccessTokenORM,
    BalanceORM,
    MLModelORM,
    MLTaskORM,
    TransactionORM,
    UserORM,
)
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
logger = logging.getLogger(__name__)

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
# Аутентификация (Задание №4): bearer-токены в БД
# ---------------------------------------------------------------------------

def authenticate(session: Session, email: str, password: str) -> UserORM:
    """Проверить пару email/пароль. Неверные данные -> ValueError."""
    user = get_user_by_email(session, email)
    if user is None or not verify_password(user, password):
        raise ValueError("Неверный email или пароль")
    return user


def create_access_token(session: Session, user_id: str) -> AccessTokenORM:
    """Выдать пользователю новый токен доступа."""
    user = _require_user(session, user_id)
    token = AccessTokenORM(token=secrets.token_hex(32), user_id=user.id)
    session.add(token)
    session.commit()
    session.refresh(token)
    return token


def get_user_by_token(session: Session, token: str) -> UserORM | None:
    """Найти пользователя по токену (None — токен недействителен)."""
    row = session.get(AccessTokenORM, token)
    return row.user if row is not None else None


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


def list_users(session: Session) -> list[UserORM]:
    """Все пользователи системы (для роли администратора)."""
    return list(session.scalars(select(UserORM).order_by(UserORM.created_at.desc())))


def list_all_transactions(session: Session) -> list[TransactionORM]:
    """Все транзакции системы (для роли администратора), новые сверху.

    Владелец подгружается сразу (selectinload): админке нужен email по каждой
    строке, без этого получился бы отдельный запрос на каждую транзакцию.
    """
    return list(
        session.scalars(
            select(TransactionORM)
            .options(selectinload(TransactionORM.user))
            .order_by(TransactionORM.created_at.desc())
        )
    )


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

def _model_impl(model_row: MLModelORM) -> MLModel:
    """Экземпляр реализации модели из domain.py по строке каталога БД."""
    impl_cls = MODEL_REGISTRY.get(model_row.name)
    if impl_cls is None:
        raise ValueError(f"Нет реализации для модели '{model_row.name}'")
    return impl_cls(
        name=model_row.name, cost_per_request=float(model_row.cost_per_request)
    )


def create_prediction_task(
    session: Session,
    user_id: str,
    model_name: str,
    rows: list[dict[str, Any]],
) -> MLTaskORM:
    """Сторона publisher'а: создать задачу и ЗАРЕЗЕРВИРОВАТЬ стоимость запроса.

    Средства списываются сразу при постановке задачи (одним коммитом с
    созданием задачи), поэтому между проверкой баланса и обработкой их нельзя
    потратить параллельным запросом. Если задачу не удалось опубликовать в
    очередь или обработка завершилась неуспешно, резерв возвращается через
    refund_task() — см. publish в routers/predict.py и worker.py.
    """
    user = _require_user(session, user_id)
    model_row = session.scalar(
        select(MLModelORM).where(MLModelORM.name == model_name)
    )
    if model_row is None:
        raise ValueError(f"ML-модель '{model_name}' не найдена")
    _model_impl(model_row)  # проверяем, что реализация существует

    task = MLTaskORM(
        user_id=user.id,
        model_id=model_row.id,
        input_data=rows,
        status=TaskStatus.NEW,
    )
    session.add(task)
    session.flush()  # присваивает task.id

    cost = model_row.cost_per_request
    balance = _lock_balance(session, user.id)
    if balance.amount < cost:
        task.status = TaskStatus.FAILED
        session.commit()  # отказ тоже сохраняется в истории
        raise InsufficientBalanceError(
            f"Баланс {balance.amount} < стоимости запроса {cost}"
        )

    # резерв: списание и запись транзакции атомарно с созданием задачи
    balance.amount -= cost
    session.add(
        TransactionORM(
            user_id=user.id,
            type=TransactionType.WITHDRAWAL,
            amount=cost,
            task_id=task.id,
        )
    )
    task.charged = cost
    session.commit()
    session.refresh(task)
    return task


def mark_task_failed(session: Session, task_id: str) -> MLTaskORM | None:
    """Пометить задачу как неуспешную (используется при сбое публикации)."""
    task = session.get(MLTaskORM, task_id)
    if task is not None:
        task.status = TaskStatus.FAILED
        session.commit()
    return task


def refund_task(session: Session, task_id: str, reason: str = "") -> MLTaskORM:
    """Вернуть зарезервированные средства на баланс (идемпотентно).

    Вызывается, когда работа не выполнена: задачу не удалось опубликовать в
    очередь, воркер упал с ошибкой или данные не прошли валидацию.
    Возврат оформляется отдельной deposit-транзакцией, привязанной к задаче,
    поэтому виден в истории. Повторный вызов ничего не делает: признак
    возврата — обнулённое task.charged.
    """
    task = session.get(MLTaskORM, task_id)
    if task is None:
        raise ValueError(f"Задача {task_id} не найдена")
    if task.charged <= 0:  # возврата не требуется или он уже выполнен
        return task

    amount = task.charged
    balance = _lock_balance(session, task.user_id)
    balance.amount += amount
    session.add(
        TransactionORM(
            user_id=task.user_id,
            type=TransactionType.DEPOSIT,
            amount=amount,
            task_id=task.id,
        )
    )
    task.charged = Decimal("0")
    session.commit()
    session.refresh(task)
    logger.info(
        "Возврат %s кредитов по задаче %s%s",
        amount, task.id, f": {reason}" if reason else "",
    )
    return task


def execute_prediction_task(session: Session, task_id: str) -> MLTaskORM:
    """Сторона воркера: валидация -> предикт -> списание + результат атомарно.

    На этапе №5 эту функцию будет вызывать consumer, читающий очередь.
    """
    task = session.get(MLTaskORM, task_id)
    if task is None:
        raise ValueError(f"Задача {task_id} не найдена")
    if task.status != TaskStatus.NEW:
        # повторная доставка сообщения (redelivery) — задача уже обработана
        return task
    model_row = task.model
    impl = _model_impl(model_row)

    # Средства уже зарезервированы при постановке задачи (create_prediction_task).
    # Любой неуспех ниже -> возврат резерва на баланс.

    # 1. Валидация: некорректные строки возвращаются пользователю
    validation = impl.validate(task.input_data)
    task.invalid_rows = [
        {"row": row, "reason": reason} for row, reason in validation.invalid_rows
    ]
    if not validation.has_valid:
        task.status = TaskStatus.VALIDATION_FAILED
        session.commit()
        return refund_task(session, task.id, "данные не прошли валидацию")

    # 2. Полиморфный предикт
    task.status = TaskStatus.RUNNING
    session.commit()
    try:
        result = impl.predict(validation.valid_rows)
    except Exception:
        task.status = TaskStatus.FAILED
        session.commit()
        refund_task(session, task.id, "ошибка выполнения предикта")
        raise

    # 3. Успех: результат сохраняется, резерв остаётся списанным
    task.result = result
    task.status = TaskStatus.DONE
    session.commit()
    session.refresh(task)
    return task


def run_prediction(
    session: Session,
    user_id: str,
    model_name: str,
    rows: list[dict[str, Any]],
) -> MLTaskORM:
    """Полный сценарий MLService.submit из domain.py (синхронно):

    создать задачу с проверкой баланса и сразу выполнить её.
    """
    task = create_prediction_task(session, user_id, model_name, rows)
    return execute_prediction_task(session, task.id)


def get_task(session: Session, task_id: str) -> MLTaskORM | None:
    """Задача предсказания по id (для GET /predict/{task_id})."""
    return session.get(MLTaskORM, task_id)


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
