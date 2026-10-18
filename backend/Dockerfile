FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
RUN useradd --system --uid 10001 --create-home nascon
COPY --chown=10001:10001 . .
USER 10001:10001

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host \"${HOST:-0.0.0.0}\" --port \"${PORT:-8080}\" --no-access-log --limit-concurrency 1000 --backlog 2048"]
