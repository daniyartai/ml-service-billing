"""Эндпоинты /auth: регистрация и авторизация (Задание №4)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

import services
from database import get_db
from schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация пользователя",
)
def register(payload: RegisterRequest, session: Session = Depends(get_db)) -> UserResponse:
    try:
        user = services.create_user(session, payload.email, payload.password)
    except ValueError as exc:  # email уже занят
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return UserResponse.from_user(user)


@router.post("/login", response_model=TokenResponse, summary="Авторизация, выдача токена")
def login(payload: LoginRequest, session: Session = Depends(get_db)) -> TokenResponse:
    try:
        user = services.authenticate(session, payload.email, payload.password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = services.create_access_token(session, user.id)
    return TokenResponse(access_token=token.token)
