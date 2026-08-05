"""Эндпоинты /users: данные текущего пользователя (Задание №4)."""

from fastapi import APIRouter, Depends

from db_models import UserORM
from schemas import UserResponse
from security import get_current_user

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse, summary="Текущий пользователь")
def me(current: UserORM = Depends(get_current_user)) -> UserResponse:
    return UserResponse.from_user(current)
