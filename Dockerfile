FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TCD_DATA_DIR=/app/data \
    TCD_DB=/app/data/state.db \
    TCD_REQUIRE_LOGIN=1

WORKDIR /app

# Install dependencies first so source edits don't reinstall packages.
COPY requirements.txt pyproject.toml README.md BUILD.md ENHANCEMENTS.md DOCKER.md REVIEW_AND_DEPLOY.md MANIFEST.in ./
COPY telegram_channel_duplicator ./telegram_channel_duplicator
RUN pip install --no-cache-dir . \
 && python -m pip check \
 && mkdir -p /app/data/workspaces

# All per-user configs, SQLite databases, Telegram sessions and logs live here.
# Mount a named volume (or bind mount) so data survives container recreation.
VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request,sys; p=os.environ.get('PORT','8000'); r=urllib.request.urlopen(f'http://127.0.0.1:{p}/api/health',timeout=3); sys.exit(0 if r.status==200 else 1)"

# Multi-user server: every account gets an isolated workspace.
# Set TCD_SECURE_COOKIES=1 behind HTTPS, and TCD_TOKEN for an extra shared secret.
CMD ["sh", "-c", "telegram-channel-duplicator --serve --host 0.0.0.0 --port \"${PORT:-8000}\" --data-dir \"${TCD_DATA_DIR:-/app/data}\""]
