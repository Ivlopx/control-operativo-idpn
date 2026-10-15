FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    IDPN_CONFIG_DIR=/data/config IDPN_SESSION_DIR=/data/sessions IDPN_UPLOAD_DIR=/data/uploads \
    IDPN_COOKIE_SECURE=true
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 idpn \
    && useradd --uid 10001 --gid idpn --create-home idpn \
    && mkdir -p /data/config /data/sessions /data/uploads \
    && chown -R idpn:idpn /data /app
COPY --chown=idpn:idpn *.py ./
COPY --chown=idpn:idpn wp_cleanup.js ./
COPY --chown=idpn:idpn templates ./templates
COPY --chown=idpn:idpn static ./static
USER idpn
EXPOSE 5001
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/healthz', timeout=3)" || exit 1
# Queues, browser ownership and the operation lock live in one process.
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "1", "--worker-class", "gthread", "--threads", "24", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
