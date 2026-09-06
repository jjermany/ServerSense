FROM node:24-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-alpine3.24 AS runtime
ARG VERSION=1.0.0
LABEL org.opencontainers.image.title="ServerSense" \
      org.opencontainers.image.description="Private server monitoring and intelligence for Unraid" \
      org.opencontainers.image.version="${VERSION}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SERVERSENSE_CONFIG_DIR=/config \
    SERVERSENSE_ARRAY_PATH=/mnt/user
RUN apk upgrade --no-cache && apk add --no-cache smartmontools tzdata
WORKDIR /app
COPY backend/ /app/backend/
RUN apk add --no-cache --virtual .build-deps build-base linux-headers libffi-dev \
    && python -m pip install --no-cache-dir --upgrade "pip>=26.2,<27" \
    && python -m pip install --no-cache-dir /app/backend \
    && python -m pip uninstall -y pip \
    && apk del .build-deps
COPY --from=frontend-build /build/frontend/dist /app/static
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && mkdir -p /config/logs /config/models /config/backups /config/settings
VOLUME ["/config"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3).close()"]
ENTRYPOINT ["/app/entrypoint.sh"]
