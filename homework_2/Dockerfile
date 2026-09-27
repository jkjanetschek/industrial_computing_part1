# One image for all five services (registry, three agents, coordinator).
# They share code and dependencies; docker-compose.yml picks the process per
# service with `command:`. Same conventions as the repository root Dockerfile:
# slim base, hashed lock file, no cache.
FROM python:3.12-slim

# Unbuffered stdout so `docker compose logs` shows tool calls as they happen.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYDANTIC_AI_NO_BANNER=1

WORKDIR /app

# Dependencies first, so code edits do not invalidate this layer.
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Only the runtime packages; tests, docs and spikes stay out (.dockerignore).
COPY shared/ shared/
COPY registry_service/ registry_service/
COPY agents/ agents/
COPY coordinator/ coordinator/

# Never run as root inside the container; nothing here needs it.
RUN useradd --create-home --uid 1000 gaia
USER gaia

# Default process: the coordinator service. Compose overrides per service.
CMD ["python", "-m", "uvicorn", "coordinator.api:app", "--host", "0.0.0.0", "--port", "8080"]
