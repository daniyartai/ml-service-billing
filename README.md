# ML-сервис с биллингом — Этап 1: объектная модель

Объектная модель личного кабинета ML-сервиса: пользователи, баланс в условных
кредитах, транзакции, ML-модели, задачи предсказания и их история.

## Структура

- `domain.py` — объектная модель сервиса
- `demo.py` — демо-сценарий: регистрация, пополнение, запрос с валидацией, списание, история

## Запуск

```bash
python demo.py
```

Зависимостей нет, требуется Python 3.10+.

## Сущности

| Класс | Назначение |
|---|---|
| `User` / `Admin` | Пользователь и администратор (наследование). Баланс и хэш пароля — приватные поля |
| `Transaction` (ABC) → `DepositTransaction`, `WithdrawalTransaction` | Пополнение и списание; полиморфный `apply()` |
| `TransactionLedger` | История транзакций, выборка по пользователю |
| `MLModel` (ABC) → `ThresholdScoringModel`, `LinearRegressionModel` | Модели с единым интерфейсом `validate()` / `predict()` и стоимостью запроса |
| `MLTask`, `TaskHistory` | Задача для модели со статусом, результатом, ошибочными строками и суммой списания |
| `MLService` | Оркестратор: проверка баланса → валидация → предикт → списание при успехе |

## Диаграмма классов

```mermaid
classDiagram
    class User {
        -id: str
        -email: str
        -__password_hash: str
        -__balance: float
        +balance: float
        +is_admin: bool
        +verify_password(password: str) bool
        +can_afford(amount: float) bool
        #_apply_delta(delta: float)
    }
    class Admin {
        +is_admin: bool
        +top_up_user(user, amount, ledger) DepositTransaction
        +view_all_transactions(ledger) list~Transaction~
    }
    class Transaction {
        <<abstract>>
        -id: str
        -user: User
        -amount: float
        -timestamp: datetime
        +type: TransactionType*
        +apply()*
    }
    class DepositTransaction {
        -approved_by: Admin
        +apply()
    }
    class WithdrawalTransaction {
        -task: MLTask
        +apply()
    }
    class MLModel {
        <<abstract>>
        -name: str
        -cost_per_request: float
        -required_features: list~str~
        +validate(rows) ValidationResult
        +predict(rows)* list
    }
    class ThresholdScoringModel {
        -__threshold: float
        +predict(rows) list~int~
    }
    class LinearRegressionModel {
        -__weights: tuple
        -__bias: float
        +predict(rows) list~float~
    }
    class MLTask {
        -id: str
        -user: User
        -model: MLModel
        -status: TaskStatus
        -result: list
        -invalid_rows: list
        -charged: float
    }
    class MLService {
        -__ledger: TransactionLedger
        -__history: TaskHistory
        +submit(user, model, rows) MLTask
    }
    class TransactionLedger {
        -__items: list~Transaction~
        +add(tx)
        +all() list~Transaction~
        +for_user(user) list~Transaction~
    }
    class TaskHistory {
        -__items: list~MLTask~
        +add(task)
        +for_user(user) list~MLTask~
    }

    User <|-- Admin
    Transaction <|-- DepositTransaction
    Transaction <|-- WithdrawalTransaction
    MLModel <|-- ThresholdScoringModel
    MLModel <|-- LinearRegressionModel
    Transaction --> User
    WithdrawalTransaction --> MLTask
    MLTask --> User
    MLTask --> MLModel
    MLService --> TransactionLedger
    MLService --> TaskHistory
```

## Принципы ООП

**Инкапсуляция.** Баланс (`User.__balance`) и хэш пароля — приватные; изменение
баланса возможно только через транзакции (`Transaction.apply()` →
`User._apply_delta()`), напрямую выставить баланс нельзя. Внутренние списки
историй закрыты, наружу отдаются копии. Состояние `MLTask` меняется только
методами оркестратора.

**Наследование.** `Admin` расширяет `User` (пополнение баланса другим
пользователям, просмотр всех транзакций); конкретные транзакции и модели
расширяют абстрактные `Transaction` и `MLModel`.

**Полиморфизм.** `MLService` вызывает `model.predict()` и `tx.apply()`, не зная
конкретного класса; `Admin.is_admin` переопределяет свойство базового класса.

## Бизнес-правила

- Запрос отклоняется при недостаточном балансе (`InsufficientBalanceError`) —
  проверка до выполнения.
- Ошибочные строки выборки возвращаются пользователю, предсказание выполняется
  над валидными.
- Кредиты списываются только при успешном выполнении предсказания.
