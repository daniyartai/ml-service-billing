"""Конфигурация pytest.

Наличие этого файла в корне исходников (в контейнере — /app) добавляет
директорию в sys.path, поэтому тесты видят модули приложения
(database, db_models, services и т.д.) при запуске командой `pytest`.

Здесь же тесты жёстко переключаются на собственную базу. Без этого
`pytest`, запущенный внутри контейнера (`docker compose exec app pytest`),
унаследовал бы DATABASE_URL рабочего приложения и создавал бы тестовых
пользователей и транзакции в боевой базе. Подменять переменную нужно до
импорта database.py — он читает её на уровне модуля, поэтому код стоит
здесь, а не в фикстуре.

Прогнать тесты на настоящей СУБД можно, задав TEST_DATABASE_URL явно.
"""

import os
import tempfile
from pathlib import Path

_TEST_DB = Path(tempfile.gettempdir()) / "ml_service_tests.db"

if os.getenv("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
else:
    _TEST_DB.unlink(missing_ok=True)  # каждый прогон — с чистого листа
    os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"

# воркер публикует задачи в очередь; в тестах она подменяется, но переменная
# нужна на этапе импорта mq.py
os.environ.setdefault("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
