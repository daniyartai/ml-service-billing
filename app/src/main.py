"""Точка входа backend-приложения ML-сервиса.

Минимальный FastAPI-каркас поверх объектной модели (domain.py).
Конфигурация читается из переменных окружения (.env через docker-compose).
"""

import logging
import os
from typing import Dict

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from domain import LinearRegressionModel, ThresholdScoringModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="ML Service API",
    description="Личный кабинет ML-сервиса с биллингом в условных кредитах",
    version="0.2.0",
)

# Доступные модели (позже будут храниться в базе данных)
MODELS = [ThresholdScoringModel(), LinearRegressionModel()]


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
    """Список доступных ML-моделей и стоимость запроса в кредитах."""
    logger.info("Запрошен список моделей")
    return [
        {"name": m.name, "cost_per_request": m.cost_per_request}
        for m in MODELS
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
