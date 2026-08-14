# SensorPush cloud -> Prometheus exporter. Built in-place by the observability
# deploy (deploy-compose-app.yml -e app_build=true). See exporter.py for the
# metrics and the SensorPush Gateway Cloud API details.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    EXPORTER_PORT=9825 \
    POLL_INTERVAL=60

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY exporter.py .

# Run unprivileged (nobody). Pairs with no-new-privileges in the compose file.
USER 65534:65534
EXPOSE 9825
ENTRYPOINT ["python", "-u", "exporter.py"]
