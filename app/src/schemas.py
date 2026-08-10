"""Pydantic-схемы REST API (Задание №4): валидация входных данных и формат ответов.

Запросы валидируются автоматически (некорректные -> 422 с описанием полей),
ответы собираются из ORM-объектов методами from_*.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from db_models import MLTaskORM, TransactionORM, UserORM


# ---------------------------------------------------------------------------
# Аутентификация и пользователи
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, description="Минимум 6 символов")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    email: str
    is_admin: bool
    created_at: datetime

    @classmethod
    def from_user(cls, user: UserORM) -> "UserResponse":
        return cls(
            id=user.id,
            email=user.email,
            is_admin=user.is_admin,
            created_at=user.created_at,
        )


class AdminUserResponse(BaseModel):
    """Пользователь в админ-панели: профиль вместе с текущим балансом."""

    id: str
    email: str
    is_admin: bool
    balance: float
    created_at: datetime

    @classmethod
    def from_user(cls, user: UserORM) -> "AdminUserResponse":
        return cls(
            id=user.id,
            email=user.email,
            is_admin=user.is_admin,
            balance=float(user.balance.amount) if user.balance else 0.0,
            created_at=user.created_at,
        )


# ---------------------------------------------------------------------------
# Баланс
# ---------------------------------------------------------------------------

class BalanceResponse(BaseModel):
    balance: float


class TopUpRequest(BaseModel):
    amount: float = Field(gt=0, description="Сумма пополнения в кредитах, строго > 0")


# ---------------------------------------------------------------------------
# ML-предсказания и история
# ---------------------------------------------------------------------------

class PredictRequest(BaseModel):
    """Запрос предсказания.

    Значения признаков намеренно приняты как Any, а не как float: требование
    задания — некорректные строки возвращать пользователю, а корректные всё
    равно обрабатывать. Строгий тип отклонял бы 422-й ошибкой всю выборку
    целиком из-за одной плохой ячейки. Построчный разбор (пропущенные признаки,
    нечисловые значения) выполняет MLModel.validate() из domain.py, а причины
    отклонения попадают в TaskResponse.invalid_rows.
    """

    model_config = ConfigDict(protected_namespaces=())

    model: str = Field(description="Имя ML-модели из каталога GET /models")
    rows: list[dict[str, Any]] = Field(
        min_length=1,
        description=(
            "Строки признаков для предсказания. Строки с пропущенными "
            "признаками или нечисловыми значениями отклоняются по отдельности "
            "и возвращаются в invalid_rows с причиной."
        ),
    )


class TaskResponse(BaseModel):
    """Задача предсказания. Контракт рассчитан на асинхронный этап 5:

    сейчас статус сразу 'done', позже POST /predict будет возвращать 'new',
    а результат появится в GET /predict/{task_id} после обработки воркером.
    """

    model_config = ConfigDict(protected_namespaces=())

    task_id: str
    model: str
    status: str
    input_data: Any
    result: Any | None
    invalid_rows: Any | None
    charged: float
    created_at: datetime

    @classmethod
    def from_task(cls, task: MLTaskORM) -> "TaskResponse":
        return cls(
            task_id=task.id,
            model=task.model.name,
            status=task.status.value,
            input_data=task.input_data,
            result=task.result,
            invalid_rows=task.invalid_rows,
            charged=float(task.charged),
            created_at=task.created_at,
        )


class TransactionResponse(BaseModel):
    id: str
    type: str
    amount: float
    task_id: str | None
    created_at: datetime

    @classmethod
    def from_transaction(cls, tx: TransactionORM) -> "TransactionResponse":
        return cls(
            id=tx.id,
            type=tx.type.value,
            amount=float(tx.amount),
            task_id=tx.task_id,
            created_at=tx.created_at,
        )


class AdminTransactionResponse(TransactionResponse):
    """Транзакция в админке: дополнительно email владельца операции."""

    user_email: str

    @classmethod
    def from_transaction(cls, tx: TransactionORM) -> "AdminTransactionResponse":
        return cls(
            id=tx.id,
            type=tx.type.value,
            amount=float(tx.amount),
            task_id=tx.task_id,
            created_at=tx.created_at,
            user_email=tx.user.email,
        )
