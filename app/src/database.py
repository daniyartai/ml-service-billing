"""Подключение к базе данных через SQLAlchemy (Задание №3).

Конфигурация берётся из переменной окружения DATABASE_URL
(app/.env -> env_file в docker-compose) — секретов в коде нет.
"""

import os
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "Переменная окружения DATABASE_URL не задана "
        "(см. app/.env и docker-compose.yml)"
    )

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    # для локальных тестов на SQLite (TestClient ходит из другого потока);
    # на PostgreSQL параметр не используется
    connect_args=(
        {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
    ),
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    """FastAPI-зависимость: сессия БД на время одного запроса."""
    with SessionLocal() as session:
        yield session


class Base(DeclarativeBase):
    """Базовый класс всех ORM-моделей."""


def wait_for_db(retries: int = 30, delay: float = 1.0) -> None:
    """Дождаться готовности БД (страховка, если app стартовал раньше postgres)."""
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001 — важен сам факт недоступности
            last_error = exc
            time.sleep(delay)
    raise RuntimeError(f"База данных недоступна: {last_error}")
