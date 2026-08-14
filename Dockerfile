FROM node:24-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ARG VERSION=1.0.0
LABEL org.opencontainers.image.title="ServerSense" \
      org.opencontainers.image.description="Private server monitoring and intelligence for Unraid" \
      org.opencontainers.image.version="${VERSION}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SERVERSENSE_CONFIG_DIR=/config \
    SERVERSENSE_ARRAY_PATH=/mnt/user
RUN apt-get update && apt-get install -y --no-install-recommends smartmontools curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/ /app/backend/
RUN pip install --no-cache-dir /app/backend
COPY --from=frontend-build /build/frontend/dist /app/static
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && mkdir -p /config/logs /config/models /config/backups /config/settings
VOLUME ["/config"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
