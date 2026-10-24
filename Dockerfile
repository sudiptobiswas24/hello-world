# The office application, built once into static files: Node is needed
# to build it, never to run it, so it stays in this first stage.
FROM node:22-slim AS office
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build && npm test

# The application server. One image runs the web process and, with a
# different command, any management command (imports, MRP) against the
# same code and settings. Backups run in the postgres image, which has
# pg_dump; nothing here needs the operating system's packages.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_ENV=production \
    DJANGO_STATIC_ROOT=/app/staticfiles

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=office /build/apps/web/static/web /app/apps/web/static/web

RUN useradd --system --home /app erp \
    && mkdir -p /app/staticfiles /app/imports /app/media \
    && chown -R erp /app/staticfiles /app/imports /app/media
USER erp

EXPOSE 8000
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", \
     "--workers", "3", "--timeout", "120", "--access-logfile", "-", \
     "--no-control-socket"]
