FROM python:3.12-slim

WORKDIR /app

COPY services/worker/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY core/ /app/core/
COPY services/worker/ /app/services/worker/

ENV PYTHONPATH=/app:/app/services/worker \
    PYTHONUNBUFFERED=1

CMD ["celery", "-A", "worker.celery_app", "worker", "--loglevel=info", "-Q", "search"]
