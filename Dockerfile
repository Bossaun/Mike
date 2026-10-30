FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GOLDWAY_FEATURE_PROFILE=knowledge-reminders-v1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml requirements.lock requirements-build.txt ./
COPY goldway ./goldway
COPY operations ./operations
COPY examples/knowledge.public.json ./examples/knowledge.public.json
RUN pip install --no-cache-dir -r requirements.lock -r requirements-build.txt && pip install --no-cache-dir --no-deps --no-build-isolation . && useradd --uid 10001 --create-home pilot && mkdir /data && chown pilot /data && chmod -R a+rX /app
USER pilot
CMD ["goldway", "--config", "/run/config.json", "--db", "/data/goldway.sqlite", "run"]
