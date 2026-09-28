FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin vibetrading \
    && mkdir -p /data \
    && chown -R vibetrading:vibetrading /app /data
USER vibetrading
CMD ["python", "main.py", "status"]

