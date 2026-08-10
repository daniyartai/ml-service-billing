"""Web-интерфейс личного кабинета (Задание №6).

Этот роутер отдаёт только HTML-страницы. Все данные страницы получают
из браузера через существующий REST API (/auth, /balance, /predict,
/history, /admin) — бизнес-логика не дублируется, в запросах используется
тот же bearer-токен, что и в Swagger.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["web"], include_in_schema=False)


def _page(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context)


@router.get("/", response_class=HTMLResponse, summary="Главная страница")
def index(request: Request) -> HTMLResponse:
    """Описание возможностей сервиса, доступно без авторизации."""
    return _page(request, "index.html", page="index")


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request) -> HTMLResponse:
    return _page(request, "register.html", page="register")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return _page(request, "login.html", page="login")


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request) -> HTMLResponse:
    """Личный кабинет: баланс, пополнение, отправка ML-запроса."""
    return _page(request, "dashboard.html", page="dashboard")


@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request) -> HTMLResponse:
    """История ML-запросов и транзакций."""
    return _page(request, "history.html", page="history")


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request) -> HTMLResponse:
    """Панель администратора (дополнительная часть задания)."""
    return _page(request, "admin.html", page="admin")
