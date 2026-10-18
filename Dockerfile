FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/team.sqlite3
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 bot \
    && useradd --uid 10001 --gid 10001 --create-home bot \
    && mkdir -p /app/data && chown bot:bot /app/data
COPY --chown=bot:bot *.py ./
COPY .env.example ./
# Bootstrap starts as root only to repair volume ownership, then immediately drops to UID 10001.
# If a hosting panel enforces a non-root UID, the mounted volume must already be writable for it.
ENTRYPOINT ["python", "/app/docker_start.py"]
CMD ["python", "/app/bot.py"]
