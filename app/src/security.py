"""Аутентификация запросов (Задание №4).

Зависимость get_current_user проверяет заголовок Authorization: Bearer <token>
и возвращает пользователя. В Swagger UI (/docs) появляется кнопка Authorize,
куда вставляется токен из POST /auth/login.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from database import get_db
from db_models import UserORM
from services import get_user_by_token

bearer_scheme = HTTPBearer(
    auto_error=False, description="Токен из POST /auth/login"
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_db),
) -> UserORM:
    """Текущий пользователь по bearer-токену; без/с неверным токеном — 401."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Не авторизован: передайте заголовок Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = get_user_by_token(session, credentials.credentials)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный токен",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
