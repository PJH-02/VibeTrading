ARG VIBETRADING_BASE_IMAGE=python:3.12-slim
FROM ${VIBETRADING_BASE_IMAGE}

ARG VIBETRADING_BUILD_REVISION=unknown
ARG VIBETRADING_CONFIG_HASH=unknown
ARG VIBETRADING_REUSE_APP_BASE=0
LABEL org.opencontainers.image.revision=${VIBETRADING_BUILD_REVISION} \
      org.vibetrading.config-hash=${VIBETRADING_CONFIG_HASH}
ENV VIBETRADING_BUILD_REVISION=${VIBETRADING_BUILD_REVISION} \
    VIBETRADING_BUILD_CONFIG_HASH=${VIBETRADING_CONFIG_HASH}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER root
WORKDIR /app
COPY requirements.txt .
RUN if [ "$VIBETRADING_REUSE_APP_BASE" = "1" ]; then \
        pip install --no-cache-dir exchange-calendars==4.13.2; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi
RUN rm -rf /app/src /app/vibetrading /app/strategies /app/services /app/shared /app/strategy /app/backtest
COPY src /app/src
COPY pyproject.toml main.py /app/
COPY config /app/config
ENV PYTHONPATH=/app/src
RUN if ! id -u vibetrading >/dev/null 2>&1; then \
        useradd --uid 10001 --create-home --shell /usr/sbin/nologin vibetrading; \
    fi \
    && mkdir -p /data \
    && chown -R vibetrading:vibetrading /app /data
USER vibetrading
CMD ["python", "-m", "runtime.cli", "status"]

