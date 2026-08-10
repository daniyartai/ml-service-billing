"""Эндпоинты /predict: ML-предсказания (Задания №4-5).

С этапа №5 обработка асинхронная: POST /predict проверяет баланс, создаёт
задачу, публикует её в очередь RabbitMQ и сразу возвращает task_id со
статусом 'new'. Валидацию, предикт и списание выполняет воркер; результат
забирается через GET /predict/{task_id} или /history/predictions.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

import mq
import services
from database import get_db
from db_models import UserORM
from schemas import PredictRequest, TaskResponse
from security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/predict", tags=["predict"])


@router.post(
    "",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Поставить задачу предсказания в очередь",
)
def create_prediction(
    payload: PredictRequest,
    current: UserORM = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> TaskResponse:
    try:
        # проверка баланса ДО постановки; нехватка кредитов -> 402 (обработчик в main)
        task = services.create_prediction_task(
            session, current.id, payload.model, payload.rows
        )
    except ValueError as exc:  # модель не найдена в каталоге
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    try:
        mq.publish_task(task)  # publisher -> RabbitMQ -> воркеры
    except Exception as exc:  # noqa: BLE001 — брокер недоступен
        # работа не будет выполнена -> возвращаем зарезервированные средства
        services.refund_task(session, task.id, "не удалось поставить в очередь")
        services.mark_task_failed(session, task.id)
        # подробности отказа брокера — в лог; пользователю адреса и коды ошибок
        # инфраструктуры не нужны и ни о чём ему не говорят
        logger.error("Не удалось опубликовать задачу %s: %s", task.id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Сервис обработки временно недоступен, кредиты возвращены "
                "на баланс. Попробуйте отправить запрос позже."
            ),
        )
    # status='new'; результат появится после обработки воркером
    return TaskResponse.from_task(task)


@router.get(
    "/{task_id}",
    response_model=TaskResponse,
    summary="Задача по id (опрос результата)",
)
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
