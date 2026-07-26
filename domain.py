"""
Объектная модель ML-сервиса с биллингом (Задание №1).

Демонстрируются три базовых принципа ООП:
  * Инкапсуляция  — приватные поля (__balance, __password_hash), доступ через
                    property и методы; изменить баланс напрямую нельзя.
  * Наследование  — Admin наследует User; Deposit/Withdrawal наследуют Transaction;
                    конкретные модели наследуют абстрактную MLModel.
  * Полиморфизм   — Transaction.apply() и MLModel.predict()/validate() имеют
                    разные реализации в наследниках, вызываются единообразно.
"""

from __future__ import annotations

import hashlib
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Вспомогательные типы
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    NEW = "new"
    VALIDATION_FAILED = "validation_failed"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class TransactionType(str, Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"


@dataclass(frozen=True)
class ValidationResult:
    """Результат валидации входной выборки.

    valid_rows   — строки, пригодные для предсказания;
    invalid_rows — пары (строка, причина ошибки), возвращаются пользователю.
    """
    valid_rows: list[dict[str, Any]]
    invalid_rows: list[tuple[dict[str, Any], str]]

    @property
    def has_valid(self) -> bool:
        return len(self.valid_rows) > 0


# ---------------------------------------------------------------------------
# Пользователи (наследование: User -> Admin)
# ---------------------------------------------------------------------------

class User:
    """Пользователь сервиса. Баланс и хэш пароля инкапсулированы."""

    def __init__(self, email: str, password: str, user_id: str | None = None) -> None:
        self._id: str = user_id or str(uuid.uuid4())
        self._email: str = email
        self.__password_hash: str = self.__hash(password)   # private
        self.__balance: float = 0.0                          # private
        self._created_at: datetime = datetime.now(timezone.utc)

    # --- инкапсуляция: только чтение ---
    @property
    def id(self) -> str:
        return self._id

    @property
    def email(self) -> str:
        return self._email

    @property
    def balance(self) -> float:
        return self.__balance

    @property
    def is_admin(self) -> bool:
        return False

    # --- аутентификация ---
    @staticmethod
    def __hash(password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()

    def verify_password(self, password: str) -> bool:
        return self.__hash(password) == self.__password_hash

    # --- работа с балансом: единственная точка изменения __balance ---
    def _apply_delta(self, delta: float) -> None:
        """Protected: вызывается только транзакциями (Transaction.apply)."""
        if self.__balance + delta < 0:
            raise InsufficientBalanceError(
                f"Недостаточно кредитов: баланс {self.__balance}, требуется {-delta}"
            )
        self.__balance += delta

    def can_afford(self, amount: float) -> bool:
        return self.__balance >= amount > 0 or amount == 0

    def __repr__(self) -> str:
        return f"<User {self._email} balance={self.__balance}>"


class Admin(User):
    """Администратор: расширение User (наследование).

    Может пополнять баланс любому пользователю и просматривать все транзакции.
    """

    @property
    def is_admin(self) -> bool:            # полиморфизм: переопределение
        return True

    def top_up_user(self, user: User, amount: float,
                    ledger: "TransactionLedger") -> "DepositTransaction":
        tx = DepositTransaction(user=user, amount=amount, approved_by=self)
        tx.apply()
        ledger.add(tx)
        return tx

    def view_all_transactions(self, ledger: "TransactionLedger") -> list["Transaction"]:
        return ledger.all()


class InsufficientBalanceError(Exception):
    """Списание при недостаточном балансе запрещено."""


# ---------------------------------------------------------------------------
# Транзакции (наследование + полиморфизм: apply)
# ---------------------------------------------------------------------------

class Transaction(ABC):
    """Абстрактная транзакция. Наследники по-разному реализуют apply()."""

    def __init__(self, user: User, amount: float) -> None:
        if amount <= 0:
            raise ValueError("Сумма транзакции должна быть положительной")
        self._id: str = str(uuid.uuid4())
        self._user: User = user
        self._amount: float = amount
        self._timestamp: datetime = datetime.now(timezone.utc)

    @property
    def id(self) -> str:
        return self._id

    @property
    def user(self) -> User:
        return self._user

    @property
    def amount(self) -> float:
        return self._amount

    @property
    def timestamp(self) -> datetime:
        return self._timestamp

    @property
    @abstractmethod
    def type(self) -> TransactionType: ...

    @abstractmethod
    def apply(self) -> None:
        """Применить транзакцию к балансу пользователя."""

    def __repr__(self) -> str:
        return f"<{self.type.value} {self._amount} user={self._user.email}>"


class DepositTransaction(Transaction):
    """Пополнение баланса (может требовать одобрения администратором)."""

    def __init__(self, user: User, amount: float, approved_by: Admin | None = None) -> None:
        super().__init__(user, amount)
        self._approved_by: Admin | None = approved_by

    @property
    def type(self) -> TransactionType:
        return TransactionType.DEPOSIT

    def apply(self) -> None:
        self._user._apply_delta(+self._amount)


class WithdrawalTransaction(Transaction):
    """Списание кредитов за выполненную ML-задачу."""

    def __init__(self, user: User, amount: float, task: "MLTask") -> None:
        super().__init__(user, amount)
        self._task: MLTask = task

    @property
    def type(self) -> TransactionType:
        return TransactionType.WITHDRAWAL

    @property
    def task(self) -> "MLTask":
        return self._task

    def apply(self) -> None:
        self._user._apply_delta(-self._amount)


class TransactionLedger:
    """История транзакций (инкапсулирует внутренний список)."""

    def __init__(self) -> None:
        self.__items: list[Transaction] = []

    def add(self, tx: Transaction) -> None:
        self.__items.append(tx)

    def all(self) -> list[Transaction]:
        return list(self.__items)          # копия — защита от внешних изменений

    def for_user(self, user: User) -> list[Transaction]:
        return [t for t in self.__items if t.user.id == user.id]


# ---------------------------------------------------------------------------
# ML-модели (абстрактный класс + полиморфизм predict/validate)
# ---------------------------------------------------------------------------

class MLModel(ABC):
    """Абстрактная ML-модель: имя, стоимость запроса, валидация и предикт."""

    def __init__(self, name: str, cost_per_request: float,
                 required_features: list[str]) -> None:
        self._name: str = name
        self._cost: float = cost_per_request
        self._required_features: list[str] = required_features

    @property
    def name(self) -> str:
        return self._name

    @property
    def cost_per_request(self) -> float:
        return self._cost

    def validate(self, rows: list[dict[str, Any]]) -> ValidationResult:
        """Базовая валидация: наличие признаков и числовой тип значений."""
        valid, invalid = [], []
        for row in rows:
            missing = [f for f in self._required_features if f not in row]
            if missing:
                invalid.append((row, f"отсутствуют признаки: {missing}"))
                continue
            bad = [f for f in self._required_features
                   if not isinstance(row[f], (int, float)) or isinstance(row[f], bool)]
            if bad:
                invalid.append((row, f"нечисловые значения: {bad}"))
                continue
            valid.append(row)
        return ValidationResult(valid_rows=valid, invalid_rows=invalid)

    @abstractmethod
    def predict(self, rows: list[dict[str, Any]]) -> list[Any]:
        """Полиморфный метод: каждая модель реализует по-своему."""


class ThresholdScoringModel(MLModel):
    """Простейший скоринг: сумма признаков против порога -> класс 0/1."""

    def __init__(self, name: str = "threshold-scoring", cost_per_request: float = 5.0,
                 threshold: float = 1.0) -> None:
        super().__init__(name, cost_per_request, ["feature_1", "feature_2"])
        self.__threshold = threshold

    def predict(self, rows: list[dict[str, Any]]) -> list[int]:
        return [int(r["feature_1"] + r["feature_2"] > self.__threshold) for r in rows]


class LinearRegressionModel(MLModel):
    """Линейная регрессия с фиксированными весами (демо)."""

    def __init__(self, name: str = "linear-regression", cost_per_request: float = 10.0) -> None:
        super().__init__(name, cost_per_request, ["x1", "x2"])
        self.__weights = (2.0, -1.0)
        self.__bias = 0.5

    def predict(self, rows: list[dict[str, Any]]) -> list[float]:
        w1, w2 = self.__weights
        return [w1 * r["x1"] + w2 * r["x2"] + self.__bias for r in rows]


# ---------------------------------------------------------------------------
# Задача для ML-модели и сервис-оркестратор
# ---------------------------------------------------------------------------

class MLTask:
    """Задача: пользователь -> модель -> данные -> результат + списание."""

    def __init__(self, user: User, model: MLModel, rows: list[dict[str, Any]]) -> None:
        self._id: str = str(uuid.uuid4())
        self._user: User = user
        self._model: MLModel = model
        self._rows: list[dict[str, Any]] = rows
        self._status: TaskStatus = TaskStatus.NEW
        self._created_at: datetime = datetime.now(timezone.utc)
        self._result: list[Any] | None = None
        self._invalid_rows: list[tuple[dict[str, Any], str]] = []
        self._charged: float = 0.0

    # --- только чтение наружу ---
    @property
    def id(self) -> str:
        return self._id

    @property
    def user(self) -> User:
        return self._user

    @property
    def model(self) -> MLModel:
        return self._model

    @property
    def status(self) -> TaskStatus:
        return self._status

    @property
    def result(self) -> list[Any] | None:
        return self._result

    @property
    def invalid_rows(self) -> list[tuple[dict[str, Any], str]]:
        return list(self._invalid_rows)

    @property
    def charged(self) -> float:
        return self._charged

    # --- изменение состояния только через методы ---
    def _set_running(self) -> None:
        self._status = TaskStatus.RUNNING

    def _complete(self, result: list[Any], charged: float,
                  invalid: list[tuple[dict[str, Any], str]]) -> None:
        self._result = result
        self._charged = charged
        self._invalid_rows = invalid
        self._status = TaskStatus.DONE

    def _fail(self, invalid: list[tuple[dict[str, Any], str]] | None = None,
              validation: bool = False) -> None:
        if invalid:
            self._invalid_rows = invalid
        self._status = TaskStatus.VALIDATION_FAILED if validation else TaskStatus.FAILED


class TaskHistory:
    """История задач/предсказаний пользователя."""

    def __init__(self) -> None:
        self.__items: list[MLTask] = []

    def add(self, task: MLTask) -> None:
        self.__items.append(task)

    def for_user(self, user: User) -> list[MLTask]:
        return [t for t in self.__items if t.user.id == user.id]


class MLService:
    """Оркестратор: проверка баланса -> валидация -> предикт -> списание."""

    def __init__(self, ledger: TransactionLedger, history: TaskHistory) -> None:
        self.__ledger = ledger
        self.__history = history

    def submit(self, user: User, model: MLModel,
               rows: list[dict[str, Any]]) -> MLTask:
        task = MLTask(user, model, rows)
        self.__history.add(task)

        # 1. Проверка положительного баланса ДО выполнения
        if not user.can_afford(model.cost_per_request):
            task._fail()
            raise InsufficientBalanceError(
                f"Баланс {user.balance} < стоимости запроса {model.cost_per_request}"
            )

        # 2. Валидация: ошибочные строки возвращаются пользователю
        validation = model.validate(rows)
        if not validation.has_valid:
            task._fail(invalid=validation.invalid_rows, validation=True)
            return task

        # 3. Предикт (полиморфный вызов) и списание только при успехе
        task._set_running()
        try:
            result = model.predict(validation.valid_rows)
        except Exception:
            task._fail()
            raise

        tx = WithdrawalTransaction(user=user, amount=model.cost_per_request, task=task)
        tx.apply()
        self.__ledger.add(tx)
        task._complete(result=result, charged=model.cost_per_request,
                       invalid=validation.invalid_rows)
        return task
