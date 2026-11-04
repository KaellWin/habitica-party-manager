# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock ./
# CA de proxy facultative pour les builds en cloud ; jamais copiée dans l'image.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
      PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir -r requirements.lock; \
    else pip install --no-cache-dir -r requirements.lock; fi
RUN groupadd --gid 10001 hpm && useradd --uid 10001 --gid hpm --no-create-home hpm \
    && mkdir /data && chown hpm:hpm /data
COPY hpm ./hpm
RUN chmod -R a+rX /app/hpm
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "hpm.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log", "--log-level", "info", "--log-config", "/app/hpm/logging.json"]
