# ML-сервис с биллингом

Личный кабинет ML-сервиса: пользователи, баланс в условных кредитах,
транзакции, ML-модели, задачи предсказания и их история.

## Структура проекта

```
project-root/
├── app/                      # backend-приложение
│   ├── src/
│   │   ├── main.py           # FastAPI: роутеры API, /health, /models, обработчики ошибок
│   │   ├── domain.py         # объектная модель сервиса (этап 1)
│   │   ├── demo.py           # демо-сценарий работы модели
│   │   ├── database.py       # подключение к БД (SQLAlchemy, DATABASE_URL из env)
│   │   ├── db_models.py      # ORM-модели: users, balances, transactions, ml_models, ml_tasks, access_tokens
│   │   ├── services.py       # бизнес-операции: пользователи, баланс, предикты, токены
│   │   ├── schemas.py        # Pydantic-схемы запросов и ответов API (этап 4)
│   │   ├── security.py       # bearer-аутентификация, зависимость get_current_user
│   │   ├── routers/          # эндпоинты по группам: auth, users, balance, predict, history
│   │   ├── mq.py             # публикация задач в очередь RabbitMQ (publisher, этап 5)
│   │   ├── worker.py         # ML-воркер: consumer очереди (этап 5)
│   │   ├── init_db.py        # идемпотентная инициализация БД и демо-данных
│   │   └── tests/            # pytest-тесты: сценарии БД (этап 3) и REST API (этап 4)
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
| `worker` ×2 | образ app | ML-воркеры (этап 5): consumers очереди `ml_tasks`, `deploy.replicas: 2`, `restart: on-failure` |

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

## База данных и ORM (этап 3)

Объектная модель этапа 1 отображена на PostgreSQL через SQLAlchemy 2.0.

| Сущность domain.py | Таблица | Связи |
|---|---|---|
| `User` / `Admin` | `users` | наследование свёрнуто во флаг `is_admin` |
| `Balance` | `balances` | 1:1 c `users` (FK + UNIQUE), CHECK `amount >= 0` |
| `Transaction` (deposit/withdrawal) | `transactions` | FK на `users`, `ml_tasks` (списание) и `users` (одобривший админ) |
| `MLModel` и наследники | `ml_models` | параметры в БД, реализации predict — в `domain.py` |
| `MLTask` / `TaskHistory` | `ml_tasks` | FK на `users` и `ml_models`; история запросов |

Бизнес-операции (`services.py`) атомарны: проверка баланса, списание,
запись транзакции и результата выполняются одним коммитом. Правила и
исключения переиспользуются из `domain.py` (`InsufficientBalanceError`,
enum-ы, полиморфный `predict`).

### Инициализация

Выполняется автоматически при старте `app` (lifespan) и идемпотентна —
повторный запуск не дублирует и не сбрасывает данные. Создаёт:

- демо-администратора `admin@ml-service.com` / `admin123`;
- демо-пользователя `demo@ml-service.com` / `demo123` со стартовым
  балансом 100 кредитов (deposit-транзакция, одобренная админом);
- базовые ML-модели `threshold-scoring` (5 кредитов) и
  `linear-regression` (10 кредитов).

Ручной запуск: `docker compose exec app python init_db.py`

### Тесты сценариев

```bash
docker compose exec app pytest -v
```

Покрыто: создание и загрузка пользователей, связь с балансом, пополнение,
списание с проверкой баланса, запись транзакций, успешный предикт со
списанием, отказ при нехватке кредитов, возврат ошибок валидации без
списания, история предиктов с сортировкой по дате, идемпотентность
инициализации.

## REST API (этап 4)

Интерфейс на FastAPI поверх бизнес-логики `services.py` — контроллеры
(`routers/`) логику не дублируют. Интерактивная документация и ручное
тестирование — Swagger UI: **http://localhost/docs**.

### Аутентификация

Bearer-токен, хранится в таблице `access_tokens` (JWT — на следующих этапах):
`POST /auth/login` возвращает `access_token`, дальше он передаётся в заголовке
`Authorization: Bearer <token>` (в Swagger — кнопка **Authorize**).
Демо-доступ: `demo@ml-service.com` / `demo123` (баланс 100 кредитов),
админ — `admin@ml-service.com` / `admin123`.

### Эндпоинты

| Метод и путь | Описание | Коды ошибок |
|---|---|---|
| `POST /auth/register` | регистрация | 409 email занят, 422 валидация |
| `POST /auth/login` | авторизация, выдача токена | 401 неверные данные |
| `GET /users/me` | текущий пользователь | 401 |
| `GET /balance` | текущий баланс | 401 |
| `POST /balance/top-up` | пополнение, возвращает обновлённый баланс | 401, 422 сумма <= 0 |
| `POST /predict` | поставить задачу в очередь RabbitMQ, вернуть `task_id` (status `new`) | 401, 402 нет кредитов, 404 нет модели, 503 брокер недоступен |
| `GET /predict/{task_id}` | задача по id | 401, 404 (в т.ч. чужая задача) |
| `GET /history/predictions` | история ML-запросов (дата, статус, кредиты) | 401 |
| `GET /history/transactions` | история транзакций | 401 |

Единый формат ошибок: `{"detail": ...}` с корректным HTTP-кодом.
Ответ `/predict` построен вокруг задачи (`task_id`, `status`, `result`);
с этапа 5 обработка асинхронная: POST возвращает статус `new`, а результат
забирается через `GET /predict/{task_id}` после обработки воркером.

### Тесты

```bash
docker compose exec app pytest -v   # 21 тест: сценарии БД, REST API, формат сообщений очереди
```

## Асинхронная обработка через RabbitMQ (этап 5)

Модель publisher → broker → consumers на одной durable-очереди `ml_tasks`
(default exchange). Полный путь задачи:

1. `POST /predict` (publisher): проверка баланса, создание задачи в БД
   (status `new`), публикация JSON-сообщения в очередь, клиенту сразу
   возвращается `task_id`;
2. RabbitMQ раздаёт сообщения воркерам по кругу (round-robin);
   `prefetch_count=1` — воркер не берёт новую задачу, пока не подтвердил
   текущую, поэтому распределение честное;
3. воркер (`worker.py`, 2 экземпляра через `deploy.replicas`): валидация →
   полиморфный предикт → списание кредитов, транзакция и результат в БД
   напрямую (одним коммитом) → лог результата → `basic_ack`.

Сообщение-задача и лог результата — в формате задания:

```json
{"task_id": "uuid", "features": [{"feature_1": 1.0, "feature_2": 1.0}],
 "model": "threshold-scoring", "timestamp": "2026-08-06T12:00:00+00:00"}
```
```json
{"task_id": "uuid", "prediction": [1], "worker_id": "worker-abc123", "status": "done"}
```

Надёжность: очередь durable, сообщения persistent (`delivery_mode=2`),
подтверждение после обработки — при падении воркера задача возвращается в
очередь и достаётся другому; повторная доставка безопасна (задача со
статусом не-`new` не обрабатывается заново).

### Как проверить

```bash
docker compose up -d --build         # поднимет и двух воркеров
docker compose ps                    # ...-worker-1 и ...-worker-2
docker compose logs -f worker        # видно, какой воркер взял какую задачу
```

Через Swagger (/docs): `POST /predict` несколько раз подряд → в логах
воркеров задачи чередуются между `worker_id` (round-robin);
`GET /predict/{task_id}` → статус меняется `new` → `done`, появляется
результат и списание. Очередь видна в management UI:
http://localhost:15672 → Queues → `ml_tasks`.

Проверка отсутствия потерь: `docker compose stop worker` → отправить
несколько задач (в UI очереди растёт Ready) → `docker compose start worker`
→ задачи разобраны, ни одна не потеряна.
