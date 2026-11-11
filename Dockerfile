# Sourced: one image with the API, the in-process ingestion worker, the admin dashboard and the widget.
# Build:  docker build -t sourced .
# Run:    see docker-compose.yml (local) or render.yaml (free hosting) and docs/deploy.md.

FROM node:22-slim AS dashboard
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
COPY rag/ rag/
COPY backend/ backend/
COPY widget/ widget/
COPY demo/ demo/
COPY data/sample/ data/sample/
COPY deploy/start.sh deploy/start.sh
COPY --from=dashboard /src/frontend/dist frontend/dist
RUN useradd --create-home --uid 10001 sourced
USER sourced
ENV HOME=/home/sourced PORT=8000
EXPOSE 8000
CMD ["sh", "deploy/start.sh"]
