# ML-сервис с биллингом

Личный кабинет ML-сервиса: пользователи, баланс в условных кредитах,
транзакции, ML-модели, задачи предсказания и их история.

## Структура проекта

```
project-root/
├── app/                      # backend-приложение
│   ├── src/
│   │   ├── main.py           # FastAPI: /, /health, /models
│   │   ├── domain.py         # объектная модель сервиса (этап 1)
│   │   └── demo.py           # демо-сценарий работы модели
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env                  # конфигурация приложения
├── web-proxy/                # reverse proxy
│   ├── nginx.conf
│   └── Dockerfile
├── .env.example              # шаблон корневого .env (переменные для database)
├── docker-compose.yml        # 4 сервиса: app, web-proxy, rabbitmq, database
└── README.md
```

## Запуск

```bash
cp .env.example .env       # один раз: корневой .env со значениями POSTGRES_*
docker compose up --build
```

Корневой `.env` не хранится в репозитории (см. `.gitignore`) — значения из
него подставляются в сервис `database` через `${...}` в `docker-compose.yml`.

После запуска:

- http://localhost — приложение через nginx (порты 80/443)
- http://localhost:15672 — RabbitMQ management UI (guest/guest)
- PostgreSQL — порт 5432 внутри сети `ml-service-network`

Проверка без Docker:

```bash
cd app/src && python demo.py       # демо объектной модели
```

## Архитектура сервисов

| Сервис | Образ | Назначение |
|---|---|---|
| `app` | python:3.12-slim (свой Dockerfile) | FastAPI-приложение; конфиг через `env_file`, исходники через `volumes`, портов наружу нет — доступ только через proxy |
| `web-proxy` | nginx:latest | Reverse proxy; `depends_on: app`, наружу порты 80 и 443 |
| `rabbitmq` | rabbitmq:3-management | Брокер сообщений; порты 5672 (AMQP) и 15672 (UI); данные очередей в named volume `rabbitmq_volume`; `restart: on-failure` — автоперезапуск при сбоях |
| `database` | postgres:16 | БД; конфиг через `${POSTGRES_*}` из корневого `.env` (секретов в docker-compose.yml нет); данные в named volume `postgres_volume` — переживают удаление контейнера и директории проекта |

Все сервисы объединены bridge-сетью `ml-service-network` и общаются по
именам сервисов (например, `app` подключается к базе по хосту `database`).

## Объектная модель (этап 1)

Модель находится в `app/src/domain.py`, демо-сценарий — `app/src/demo.py`.

| Класс | Назначение |
|---|---|
| `Balance` | Счёт в условных кредитах: инкапсулирует сумму и инварианты (не может уйти в минус), изменения только через `deposit()`/`withdraw()` |
| `User` / `Admin` | Пользователь и администратор (наследование). Владеет счётом `Balance` (композиция), но не управляет им; хэш пароля — приватное поле |
| `Transaction` (ABC) → `DepositTransaction`, `WithdrawalTransaction` | Пополнение и списание; полиморфный `apply()` |
| `TransactionLedger` | История транзакций, выборка по пользователю |
| `MLModel` (ABC) → `ThresholdScoringModel`, `LinearRegressionModel` | Модели с единым интерфейсом `validate()` / `predict()` и стоимостью запроса |
| `MLTask`, `TaskHistory` | Задача для модели со статусом, результатом, ошибочными строками и суммой списания |
| `MLService` | Оркестратор: проверка баланса → валидация → предикт → списание при успехе |

## Диаграмма классов

```mermaid
classDiagram
    class Balance {
        -__amount: float
        +amount: float
        +can_afford(amount: float) bool
        +deposit(amount: float)
        +withdraw(amount: float)
    }
    class User {
        -id: str
        -email: str
        -__password_hash: str
        -balance: Balance
        +is_admin: bool
        +verify_password(password: str) bool
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

    User *-- Balance
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

**Инкапсуляция.** Сумма счёта (`Balance.__amount`) и хэш пароля — приватные;
изменение баланса возможно только через `Balance.deposit()`/`Balance.withdraw()`,
которые вызываются транзакциями (`Transaction.apply()`) и защищают инвариант
неотрицательности. Внутренние списки историй закрыты, наружу отдаются копии.
Состояние `MLTask` меняется только методами оркестратора.

**Разделение ответственности (SRP).** Управление балансом — не зона
ответственности пользователя: `User` лишь владеет счётом (композиция
`User *-- Balance`), а правила операций инкапсулированы в самой сущности
`Balance`.

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
