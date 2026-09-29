FROM python:3.12-slim

WORKDIR /app

COPY services/api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY core/ /app/core/
COPY services/api/ /app/services/api/

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "services.api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
