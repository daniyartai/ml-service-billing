"""ML-воркер (Задание №5): consumer очереди RabbitMQ.

Несколько экземпляров (docker compose, deploy.replicas: 2) подписаны на одну
durable-очередь. RabbitMQ раздаёт сообщения по кругу (round-robin), а
prefetch_count=1 делает распределение честным: воркер не берёт новую задачу,
пока не подтвердил текущую. basic_ack отправляется ПОСЛЕ обработки, поэтому
при падении воркера сообщение не теряется — брокер отдаст его другому.

Для каждого сообщения: парсинг JSON -> валидация и предикт через
services.execute_prediction_task (та же бизнес-логика, что в этапах 3-4) ->
результат сохраняется в БД напрямую и логируется в формате задания.
"""

import json
import logging
import os
import socket
import time

import pika

from database import SessionLocal
from domain import InsufficientBalanceError
from init_db import init_db
from mq import QUEUE_NAME, RABBITMQ_URL
from services import execute_prediction_task, mark_task_failed, refund_task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - worker - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

WORKER_ID = os.getenv("WORKER_ID") or f"worker-{socket.gethostname()}"


def _connect(retries: int = 30, delay: float = 2.0) -> pika.BlockingConnection:
    """Подключение к RabbitMQ с ожиданием готовности брокера."""
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            return pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.info("RabbitMQ ещё недоступен, повтор через %.0f с...", delay)
            time.sleep(delay)
    raise RuntimeError(f"RabbitMQ недоступен: {last_error}")


def _init_db_with_retry(retries: int = 10, delay: float = 3.0) -> None:
    """Схема и демо-данные (идемпотентно); ретраи на случай гонки со стартом app."""
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            init_db()
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("Инициализация БД не удалась (%s), повтор...", exc)
            time.sleep(delay)
    raise RuntimeError(f"Не удалось инициализировать БД: {last_error}")


def handle_message(channel, method, properties, body) -> None:
    """Обработать одно сообщение очереди и подтвердить его."""
    task_id: str | None = None
    prediction = None
    status = "error"
    try:
        message = json.loads(body)
        task_id = message["task_id"]
        logger.info(
            "Воркер %s получил задачу %s (модель '%s')",
            WORKER_ID, task_id, message.get("model"),
        )
        with SessionLocal() as session:
            task = execute_prediction_task(session, task_id)
        status = task.status.value
        prediction = task.result
    except InsufficientBalanceError as exc:
        status = "insufficient_balance"
        logger.warning("Задача %s отклонена: %s", task_id, exc)
    except Exception:  # noqa: BLE001 — ошибка логируется, средства возвращаются
        logger.exception("Ошибка обработки сообщения: %r", body)
        if task_id:
            # работа не выполнена -> зарезервированные средства возвращаются
            try:
                with SessionLocal() as session:
                    mark_task_failed(session, task_id)
                    refund_task(session, task_id, "ошибка обработки в воркере")
            except Exception:  # noqa: BLE001
                logger.exception("Не удалось вернуть средства по задаче %s", task_id)
    result = {
        "task_id": task_id,
        "prediction": prediction,
        "worker_id": WORKER_ID,
        "status": status,
    }
    logger.info("Результат обработки: %s", json.dumps(result, ensure_ascii=False))
    # ack после обработки: при падении воркера сообщение вернётся в очередь
    channel.basic_ack(delivery_tag=method.delivery_tag)


def main() -> None:
    _init_db_with_retry()
    connection = _connect()
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=True)
    channel.basic_qos(prefetch_count=1)  # честный round-robin между воркерами
    channel.basic_consume(queue=QUEUE_NAME, on_message_callback=handle_message)
    logger.info("Воркер %s подписан на очередь '%s' и ждёт задачи", WORKER_ID, QUEUE_NAME)
    channel.start_consuming()


if __name__ == "__main__":
    main()
