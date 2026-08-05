"""Эндпоинты /balance: просмотр и пополнение баланса (Задание №4)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import services
from database import get_db
from db_models import UserORM
from schemas import BalanceResponse, TopUpRequest
from security import get_current_user

router = APIRouter(prefix="/balance", tags=["balance"])


@router.get("", response_model=BalanceResponse, summary="Текущий баланс")
def get_balance(
    current: UserORM = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> BalanceResponse:
    return BalanceResponse(balance=float(services.get_balance(session, current.id)))


@router.post(
    "/top-up",
    response_model=BalanceResponse,
    summary="Пополнение баланса (без эквайринга)",
)
def top_up(
    payload: TopUpRequest,
    current: UserORM = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> BalanceResponse:
    services.top_up(session, current.id, payload.amount)
    # требование задания: вернуть обновлённый баланс после операции
    return BalanceResponse(balance=float(services.get_balance(session, current.id)))
