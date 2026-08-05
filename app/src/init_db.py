"""Инициализация базы данных (Задание №3): схема + демо-данные.

Идемпотентно: повторный запуск не создаёт дубликатов и не сбрасывает данные
(поиск по уникальным email/имени; стартовый баланс начисляется только
при первом создании демо-пользователя).

Запускается автоматически при старте приложения (main.py, lifespan)
или вручную: docker compose exec app python init_db.py
"""

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

import db_models  # noqa: F401 — регистрирует таблицы в Base.metadata
from database import Base, SessionLocal, engine, wait_for_db
from db_models import MLModelORM
from domain import LinearRegressionModel, MLModel, ThresholdScoringModel
from services import create_user, get_user_by_email, top_up

logger = logging.getLogger(__name__)

DEMO_ADMIN_EMAIL = "admin@ml-service.com"
DEMO_ADMIN_PASSWORD = "admin123"          # демо-данные, не для продакшена
DEMO_USER_EMAIL = "demo@ml-service.com"
DEMO_USER_PASSWORD = "demo123"
DEMO_INITIAL_CREDITS = 100

# Базовые модели берутся из объектной модели задания №1
BASE_MODELS: list[tuple[MLModel, str]] = [
    (ThresholdScoringModel(), "Скоринг: сумма признаков против порога -> класс 0/1"),
    (LinearRegressionModel(), "Линейная регрессия с фиксированными весами (демо)"),
]


def init_db() -> None:
    """Создать отсутствующие таблицы и демо-данные (идемпотентно)."""
    wait_for_db()
    Base.metadata.create_all(bind=engine)  # создаёт только отсутствующие таблицы
    with SessionLocal() as session:
        _seed_models(session)
        _seed_users(session)
    logger.info("Инициализация БД завершена")


def _seed_models(session: Session) -> None:
    """Каталог базовых ML-моделей, доступных для работы."""
    for impl, description in BASE_MODELS:
        exists = session.scalar(
            select(MLModelORM).where(MLModelORM.name == impl.name)
        )
        if exists is not None:
            continue
        session.add(
            MLModelORM(
                name=impl.name,
                description=description,
                cost_per_request=Decimal(str(impl.cost_per_request)),
                required_features=impl.required_features,
            )
        )
        session.commit()
        logger.info("Добавлена ML-модель '%s'", impl.name)


def _seed_users(session: Session) -> None:
    """Демо-администратор и демо-пользователь со стартовым балансом."""
    admin = get_user_by_email(session, DEMO_ADMIN_EMAIL)
    if admin is None:
        admin = create_user(
            session, DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD, is_admin=True
        )
        logger.info("Создан демо-администратор %s", admin.email)

    demo = get_user_by_email(session, DEMO_USER_EMAIL)
    if demo is None:
        demo = create_user(session, DEMO_USER_EMAIL, DEMO_USER_PASSWORD)
        # стартовый баланс — только при первом создании (идемпотентность),
        # начисляется как обычная deposit-транзакция, одобренная админом
        top_up(session, demo.id, DEMO_INITIAL_CREDITS, approved_by_id=admin.id)
        logger.info(
            "Создан демо-пользователь %s с балансом %s кредитов",
            demo.email,
            DEMO_INITIAL_CREDITS,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
