# AEDI - IONITY GLOBAL | ESP32-MCP fleet server image
FROM python:3.12-slim

WORKDIR /app
COPY server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY server /app/server
COPY dashboard /app/dashboard

ENV PYTHONPATH=/app/server
ENV IONITY_HOST=0.0.0.0
ENV IONITY_PORT=8099
EXPOSE 8099

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8099", "--app-dir", "/app/server"]
