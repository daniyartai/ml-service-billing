"""Эндпоинты /history: история ML-запросов и транзакций (Задание №4)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import services
from database import get_db
from db_models import UserORM
from schemas import TaskResponse, TransactionResponse
from security import get_current_user

router = APIRouter(prefix="/history", tags=["history"])


@router.get(
    "/predictions",
    response_model=list[TaskResponse],
    summary="История ML-запросов (дата, статус, списанные кредиты)",
)
def predictions_history(
    current: UserORM = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[TaskResponse]:
    tasks = services.get_user_tasks(session, current.id)  # новые сверху
    return [TaskResponse.from_task(t) for t in tasks]


@router.get(
    "/transactions",
    response_model=list[TransactionResponse],
    summary="История транзакций пользователя",
)
def transactions_history(
    current: UserORM = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[TransactionResponse]:
    txs = services.get_user_transactions(session, current.id)  # новые сверху
    return [TransactionResponse.from_transaction(t) for t in txs]
