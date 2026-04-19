FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY bot_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY bot_service ./bot_service

CMD ["python", "-m", "bot_service.main"]
