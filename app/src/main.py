"""Точка входа backend-приложения ML-сервиса.

FastAPI-каркас поверх объектной модели (domain.py) и ORM-слоя (Задание №3).
Конфигурация читается из переменных окружения (.env через docker-compose).
При старте выполняется автоматическая идемпотентная инициализация БД.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from database import SessionLocal
from db_models import MLModelORM
from init_db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Автоматическая инициализация БД при старте приложения (идемпотентно)."""
    init_db()
    yield


app = FastAPI(
    title="ML Service API",
    description="Личный кабинет ML-сервиса с биллингом в условных кредитах",
    version="0.3.0",
    lifespan=lifespan,
)


@app.get("/", response_model=Dict[str, str])
async def index() -> Dict[str, str]:
    """Корневой эндпоинт: краткое описание сервиса."""
    logger.info("Вызван корневой маршрут")
    return {
        "service": "ML Service with billing",
        "environment": os.getenv("APP_ENV", "development"),
    }


@app.get("/health")
async def health_check() -> Dict[str, str]:
    """Проверка работоспособности для healthcheck и мониторинга."""
    return {"status": "healthy"}


@app.get("/models")
async def list_models() -> list[dict]:
    """Список доступных ML-моделей — теперь из базы данных (таблица ml_models)."""
    logger.info("Запрошен список моделей")
    with SessionLocal() as session:
        models = session.scalars(select(MLModelORM)).all()
    return [
        {
            "name": m.name,
            "cost_per_request": float(m.cost_per_request),
            "required_features": m.required_features,
            "description": m.description,
        }
        for m in models
    ]


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    logger.warning("HTTPException: %s для запроса %s", exc.detail, request.url)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8080")),
        reload=True,
    )
