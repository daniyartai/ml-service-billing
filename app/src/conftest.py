"""Конфигурация pytest и общие фикстуры/помощники для тестов.

Наличие этого файла в корне исходников (в контейнере — /app) добавляет
директорию в sys.path, поэтому тесты видят модули приложения
(database, db_models, services и т.д.) при запуске командой `pytest`.

Здесь же тесты жёстко переключаются на собственную базу. Без этого
`pytest`, запущенный внутри контейнера (`docker compose exec app pytest`),
унаследовал бы DATABASE_URL рабочего приложения и создавал бы тестовых
пользователей и транзакции в боевой базе. Подменять переменную нужно до
импорта database.py — он читает её на уровне модуля, поэтому код стоит
здесь, а не в фикстуре.

Прогнать тесты на настоящей СУБД можно, задав TEST_DATABASE_URL явно.
"""

import os
import tempfile
from pathlib import Path

_TEST_DB = Path(tempfile.gettempdir()) / "ml_service_tests.db"

if os.getenv("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
else:
    _TEST_DB.unlink(missing_ok=True)  # каждый прогон — с чистого листа
    os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"

# воркер публикует задачи в очередь; в тестах она подменяется, но переменная
# нужна на этапе импорта mq.py
os.environ.setdefault("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")


# ---------------------------------------------------------------------------
# Общие фикстуры и хелперы
#
# Собраны здесь, а не в тестовых файлах: pytest подхватывает conftest.py
# автоматически, поэтому фикстуры доступны всем тестам без импорта и не
# дублируются в каждом модуле (test_api.py, test_e2e.py, test_web.py).
# ---------------------------------------------------------------------------

import uuid  # noqa: E402  — после подмены переменных окружения выше

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PASSWORD = "secret123"


@pytest.fixture(scope="session")
def client():
    """HTTP-клиент приложения на весь тестовый прогон.

    Контекст запускает lifespan -> init_db() один раз; инициализация
    идемпотентна, а каждый тест работает со своим пользователем (уникальный
    email), поэтому общий клиент безопасен и не создаёт связности между
    файлами.
    """
    from main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def stub_queue(monkeypatch):
    """Публикация в RabbitMQ -> заглушка.

    Брокер в тестах не поднимается; работу воркера имитирует run_worker.
    Фикстура не autouse: тест, проверяющий отказ публикации
    (test_refund_when_publish_fails), ставит свою заглушку и не должен
    молча конфликтовать с этой.
    """
    import routers.predict as predict_router

    monkeypatch.setattr(predict_router.mq, "publish_task", lambda task: None)


@pytest.fixture
def run_worker():
    """Выполнить задачу так же, как это делает worker.py."""

    def _run(task_id: str) -> None:
        from database import SessionLocal
        from services import execute_prediction_task

        with SessionLocal() as session:
            execute_prediction_task(session, task_id)

    return _run


def unique_email(prefix: str = "test") -> str:
    # ".com", а не ".local": email-validator (используется в схемах Pydantic)
    # отклоняет ".local" как зарезервированный домен специального назначения.
    return f"{prefix}-{uuid.uuid4().hex[:10]}@ml-service.com"


@pytest.fixture
def make_user(client):
    """Создать нового пользователя и вернуть (заголовки, email, id).

    Каждый вызов заводит отдельный аккаунт с уникальным email — тесты не
    делят состояние между собой.
    """

    def _make(prefix: str = "test") -> tuple[dict, str, str]:
        email = unique_email(prefix)
        resp = client.post("/auth/register", json={"email": email, "password": PASSWORD})
        assert resp.status_code == 201, resp.text
        user_id = resp.json()["id"]
        resp = client.post("/auth/login", json={"email": email, "password": PASSWORD})
        assert resp.status_code == 200, resp.text
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        return headers, email, user_id

    return _make


@pytest.fixture
def user(make_user):
    """Заголовки авторизации нового пользователя с нулевым балансом."""
    headers, _, _ = make_user()
    return headers
