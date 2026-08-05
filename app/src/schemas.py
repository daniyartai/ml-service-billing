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
    model_config = ConfigDict(protected_namespaces=())

    model: str = Field(description="Имя ML-модели из каталога GET /models")
    rows: list[dict[str, float]] = Field(
        min_length=1, description="Строки признаков для предсказания"
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
