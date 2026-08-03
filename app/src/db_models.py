"""ORM-модели (Задание №3): отображение объектной модели domain.py на PostgreSQL.

Соответствие сущностей и таблиц:
    User / Admin          -> users        (наследование свёрнуто во флаг is_admin)
    Balance               -> balances     (отдельная таблица 1:1 — SRP из задания №1)
    Transaction и наследники -> transactions (тип хранится enum-ом из domain)
    MLModel и наследники  -> ml_models    (параметры; реализации остаются в domain.py)
    MLTask / TaskHistory  -> ml_tasks     (история запросов и предсказаний)

Бизнес-инварианты domain.py продублированы ограничениями на уровне БД:
CHECK amount >= 0 у баланса, CHECK amount > 0 у транзакций, UNIQUE email и имя модели.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from domain import TaskStatus, TransactionType


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserORM(Base):
    """Пользователь сервиса (users). Admin — тот же пользователь с is_admin=True."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    # связи: 1:1 с балансом, 1:N с транзакциями и задачами
    balance: Mapped["BalanceORM"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    transactions: Mapped[list["TransactionORM"]] = relationship(
        back_populates="user",
        foreign_keys="TransactionORM.user_id",
        cascade="all, delete-orphan",
    )
    tasks: Mapped[list["MLTaskORM"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<UserORM {self.email} admin={self.is_admin}>"


class BalanceORM(Base):
    """Счёт пользователя в кредитах (balances), 1:1 с users."""

    __tablename__ = "balances"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="balance_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), unique=True, nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    user: Mapped[UserORM] = relationship(back_populates="balance")

    def __repr__(self) -> str:
        return f"<BalanceORM user={self.user_id} amount={self.amount}>"


class MLModelORM(Base):
    """Каталог ML-моделей (ml_models): имя, стоимость запроса, требуемые признаки."""

    __tablename__ = "ml_models"
    __table_args__ = (
        CheckConstraint("cost_per_request >= 0", name="model_cost_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    cost_per_request: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    required_features: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    tasks: Mapped[list["MLTaskORM"]] = relationship(back_populates="model")

    def __repr__(self) -> str:
        return f"<MLModelORM {self.name} cost={self.cost_per_request}>"


class MLTaskORM(Base):
    """История запросов/предсказаний (ml_tasks) — бывшие MLTask + TaskHistory."""

    __tablename__ = "ml_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    model_id: Mapped[int] = mapped_column(ForeignKey("ml_models.id"), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(
            TaskStatus,
            name="task_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=TaskStatus.NEW,
    )
    input_data: Mapped[list] = mapped_column(JSON, nullable=False)
    result: Mapped[Any | None] = mapped_column(JSON)
    invalid_rows: Mapped[Any | None] = mapped_column(JSON)
    charged: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, index=True
    )

    user: Mapped[UserORM] = relationship(back_populates="tasks")
    model: Mapped[MLModelORM] = relationship(back_populates="tasks")
    transactions: Mapped[list["TransactionORM"]] = relationship(back_populates="task")

    def __repr__(self) -> str:
        return f"<MLTaskORM {self.id} status={self.status}>"


class TransactionORM(Base):
    """История транзакций (transactions): deposit / withdrawal."""

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="transaction_amount_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    type: Mapped[TransactionType] = mapped_column(
        SAEnum(
            TransactionType,
            name="transaction_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # списание привязано к ML-задаче; пополнение может быть одобрено админом
    task_id: Mapped[str | None] = mapped_column(ForeignKey("ml_tasks.id"))
    approved_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, index=True
    )

    user: Mapped[UserORM] = relationship(
        back_populates="transactions", foreign_keys=[user_id]
    )
    task: Mapped[MLTaskORM | None] = relationship(back_populates="transactions")
    approved_by: Mapped[UserORM | None] = relationship(foreign_keys=[approved_by_id])

    def __repr__(self) -> str:
        return f"<TransactionORM {self.type.value} {self.amount} user={self.user_id}>"
