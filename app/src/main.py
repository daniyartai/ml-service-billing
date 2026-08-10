"""Точка входа backend-приложения ML-сервиса.

REST API (Задание №4) поверх бизнес-логики services.py и ORM (Задание №3).
Интерактивная документация и ручное тестирование — Swagger UI на /docs.
Конфигурация читается из переменных окружения (.env через docker-compose).
При старте выполняется автоматическая идемпотентная инициализация БД.
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Dict

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from database import SessionLocal
from db_models import MLModelORM
from domain import InsufficientBalanceError
from init_db import init_db
from routers import admin, auth, balance, history, predict, users, web

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
    description=(
        "Личный кабинет ML-сервиса с биллингом в условных кредитах. "
        "Авторизация: POST /auth/login -> кнопка Authorize -> Bearer-токен."
    ),
    version="0.4.0",
    lifespan=lifespan,
)

# REST API (Задание №4): тонкие контроллеры поверх services.py
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(balance.router)
app.include_router(predict.router)
app.include_router(history.router)
app.include_router(admin.router)

# Web-интерфейс личного кабинета (Задание №6): страницы и статика.
# Данные страницы получают из этого же REST API — логика не дублируется.
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
    name="static",
)
app.include_router(web.router)


@app.get("/api/info", response_model=Dict[str, str], tags=["service"])
async def service_info() -> Dict[str, str]:
    """Служебная информация о сервисе (корень «/» отдаёт главную страницу)."""
    logger.info("Запрошена информация о сервисе")
    return {
        "service": "ML Service with billing",
        "environment": os.getenv("APP_ENV", "development"),
        "docs": "/docs",
    }


@app.get("/health", tags=["service"])
async def health_check() -> Dict[str, str]:
    """Проверка работоспособности для healthcheck и мониторинга."""
    return {"status": "healthy"}


@app.get("/models", tags=["service"])
async def list_models() -> list[dict]:
    """Каталог доступных ML-моделей из базы данных (таблица ml_models)."""
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


# --- Единый формат ошибок: {"detail": ...} и корректные HTTP-коды -----------

@app.exception_handler(InsufficientBalanceError)
async def insufficient_balance_handler(
    request: Request, exc: InsufficientBalanceError
) -> JSONResponse:
    """Недостаточно кредитов -> 402 Payment Required."""
    logger.warning("Отказ по балансу: %s (%s)", exc, request.url.path)
    return JSONResponse(status_code=402, content={"detail": str(exc)})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    logger.warning("HTTPException: %s для запроса %s", exc.detail, request.url)
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers
    )


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8080")),
        reload=True,
    )
