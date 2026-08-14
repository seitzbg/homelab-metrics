# Forge RCON -> Prometheus exporter. See exporter.py for the metrics and the
# Source RCON protocol details (implemented inline, no pip deps).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    BIND_PORT=8000 \
    SCRAPE_INTERVAL=30

WORKDIR /app
COPY exporter.py .

# Run unprivileged (nobody).
USER 65534:65534
EXPOSE 8000
ENTRYPOINT ["python", "-u", "exporter.py"]
