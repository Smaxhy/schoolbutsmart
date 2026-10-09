FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home appuser && mkdir -p /app/data && chown -R appuser /app/data
USER appuser

EXPOSE 5000

# One worker on purpose: the background scheduler lives inside the process,
# so more workers would send every notification multiple times.
CMD ["sh", "-c", "exec gunicorn --workers 1 --threads 4 --bind 0.0.0.0:${PORT} app:app"]
