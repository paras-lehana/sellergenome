FROM python:3.12-slim

# Only dependencies enter the image. Application source stays at the requested
# workspace path and is mounted read-only by Compose.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && useradd --uid 1000 --create-home app \
    && mkdir -p /srv/data \
    && chown app:app /srv/data
USER app
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--ws-max-size", "8192", "--proxy-headers", "--forwarded-allow-ips", "*"]
