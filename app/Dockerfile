FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/

RUN pip install --upgrade pip && pip install -r /app/requirements.txt

EXPOSE 8080

# Исходники не копируем в образ: они монтируются через volumes (см. docker-compose.yml)

CMD ["python", "main.py"]
