"""Эндпоинты /admin (Задание №6, дополнительная часть): роль администратора.

Доступ только пользователям с is_admin=True (демо-админ создаётся init_db).
Позволяет просматривать всех пользователей и все транзакции системы,
а также пополнять баланс любого пользователя.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

import services
from database import get_db
from db_models import UserORM
from schemas import (
    AdminTransactionResponse,
    AdminUserResponse,
    BalanceResponse,
    TopUpRequest,
)
from security import get_current_user

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(current: UserORM = Depends(get_current_user)) -> UserORM:
    """Доступ только для администратора."""
    if not current.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуются права администратора",
        )
    return current


@router.get(
    "/users",
    response_model=list[AdminUserResponse],
    summary="Все пользователи системы",
)
def list_users(
    _: UserORM = Depends(require_admin), session: Session = Depends(get_db)
) -> list[AdminUserResponse]:
    return [AdminUserResponse.from_user(u) for u in services.list_users(session)]


@router.post(
    "/users/{user_id}/top-up",
    response_model=BalanceResponse,
    summary="Пополнить баланс пользователя",
)
def top_up_user(
    user_id: str,
    payload: TopUpRequest,
    admin: UserORM = Depends(require_admin),
    session: Session = Depends(get_db),
) -> BalanceResponse:
    if services.get_user(session, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Пользователь {user_id} не найден",
        )
    # пополнение фиксируется как одобренное администратором
    services.top_up(session, user_id, payload.amount, approved_by_id=admin.id)
    return BalanceResponse(balance=float(services.get_balance(session, user_id)))


@router.get(
    "/transactions",
    response_model=list[AdminTransactionResponse],
    summary="Все транзакции системы",
)
def all_transactions(
    _: UserORM = Depends(require_admin), session: Session = Depends(get_db)
) -> list[AdminTransactionResponse]:
    return [
        AdminTransactionResponse.from_transaction(t)
        for t in services.list_all_transactions(session)
    ]
