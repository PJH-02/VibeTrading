ARG VIBETRADING_BASE_IMAGE=python:3.12-slim
FROM ${VIBETRADING_BASE_IMAGE}

ARG VIBETRADING_BUILD_REVISION=unknown
ARG VIBETRADING_CONFIG_HASH=unknown
ARG VIBETRADING_SOURCE_SHA256=unknown
ARG VIBETRADING_CONFIG_SHA256=unknown
ARG VIBETRADING_INPUT_SHA256=unknown
ARG VIBETRADING_BASE_ID=unknown
ARG VIBETRADING_IMAGE_CLASS=app
ARG VIBETRADING_REUSE_APP_BASE=0
LABEL org.opencontainers.image.revision=${VIBETRADING_BUILD_REVISION} \
      org.vibetrading.config-hash=${VIBETRADING_CONFIG_SHA256} \
      org.vibetrading.source-sha256=${VIBETRADING_SOURCE_SHA256} \
      org.vibetrading.config-sha256=${VIBETRADING_CONFIG_SHA256} \
      org.vibetrading.input-sha256=${VIBETRADING_INPUT_SHA256} \
      org.vibetrading.base-id=${VIBETRADING_BASE_ID} \
      org.vibetrading.image-class=${VIBETRADING_IMAGE_CLASS}
ENV VIBETRADING_BUILD_REVISION=${VIBETRADING_BUILD_REVISION} \
    VIBETRADING_BUILD_CONFIG_HASH=${VIBETRADING_CONFIG_SHA256}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER root
WORKDIR /app
COPY requirements.txt .
RUN if [ "$VIBETRADING_REUSE_APP_BASE" = "1" ]; then \
        python -m pip check && python -c 'import yaml,pydantic,playwright; print("verified cached base dependencies")'; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi
RUN rm -rf /app/src /app/config /app/vibetrading /app/strategies /app/services /app/shared /app/strategy /app/backtest
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

