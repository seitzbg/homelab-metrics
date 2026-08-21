# WeatherFlow Tempest cloud -> Prometheus exporter. See exporter.py for the
# metrics and the WeatherFlow REST API details.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    EXPORTER_PORT=9827 \
    POLL_INTERVAL=60

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY exporter.py .

# Run unprivileged (nobody).
USER 65534:65534
EXPOSE 9827
ENTRYPOINT ["python", "-u", "exporter.py"]
