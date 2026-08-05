"""Эндпоинты /predict: ML-предсказания (Задание №4).

Контракт рассчитан на асинхронный этап №5: ответ строится вокруг задачи
(task_id + status), а GET /predict/{task_id} станет эндпоинтом опроса
результата, когда обработку заберут воркеры RabbitMQ.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

import services
from database import get_db
from db_models import UserORM
from domain import TaskStatus
from schemas import PredictRequest, TaskResponse
from security import get_current_user

router = APIRouter(prefix="/predict", tags=["predict"])


@router.post(
    "",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Отправить данные для предсказания",
)
def create_prediction(
    payload: PredictRequest,
    current: UserORM = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> TaskResponse:
    try:
        # проверка баланса до выполнения; нехватка кредитов -> 402 (обработчик в main)
        task = services.create_prediction_task(
            session, current.id, payload.model, payload.rows
        )
    except ValueError as exc:  # модель не найдена в каталоге
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    task = services.execute_prediction_task(session, task.id)

    if task.status == TaskStatus.VALIDATION_FAILED:
        # ни одной корректной строки — ошибка с перечнем причин по строкам
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Входные данные не прошли валидацию",
                "task_id": task.id,
                "invalid_rows": task.invalid_rows,
            },
        )
    return TaskResponse.from_task(task)


@router.get("/{task_id}", response_model=TaskResponse, summary="Задача по id")
def get_prediction(
    task_id: str,
    current: UserORM = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> TaskResponse:
    task = services.get_task(session, task_id)
    if task is None or task.user_id != current.id:  # чужие задачи не раскрываем
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Задача {task_id} не найдена",
        )
    return TaskResponse.from_task(task)
