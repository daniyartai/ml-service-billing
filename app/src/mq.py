"""Публикация ML-задач в RabbitMQ (Задание №5) — сторона publisher'а.

Одна durable-очередь, обменник по умолчанию (default exchange, routing_key =
имя очереди), сообщения persistent (delivery_mode=2) — задачи переживают
перезапуск брокера и не теряются.
"""

import json
import logging
import os
from typing import Any

import pika

from db_models import MLTaskORM

logger = logging.getLogger(__name__)

QUEUE_NAME = os.getenv("ML_TASKS_QUEUE", "ml_tasks")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")


def build_message(task: MLTaskORM) -> dict[str, Any]:
    """JSON-сообщение ML-задачи в формате задания №5."""
    return {
        "task_id": task.id,
        "features": task.input_data,
        "model": task.model.name,
        "timestamp": task.created_at.isoformat(),
    }


def publish_task(task: MLTaskORM) -> None:
    """Опубликовать задачу в очередь."""
    message = build_message(task)
    connection = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
    try:
        channel = connection.channel()
        channel.queue_declare(queue=QUEUE_NAME, durable=True)
        channel.basic_publish(
            exchange="",  # default exchange
            routing_key=QUEUE_NAME,
            body=json.dumps(message, ensure_ascii=False),
            properties=pika.BasicProperties(
                delivery_mode=2,  # persistent: сообщение хранится на диске
                content_type="application/json",
            ),
        )
        logger.info("Задача %s опубликована в очередь '%s'", task.id, QUEUE_NAME)
    finally:
        connection.close()
