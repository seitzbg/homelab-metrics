# SleepHQ cloud -> Prometheus exporter. See exporter.py for the metrics and
# sleephq_common.py for the SleepHQ API quirks.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    EXPORTER_PORT=9826 \
    POLL_INTERVAL=21600

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sleephq_common.py exporter.py ./
# backfill.py is deliberately NOT copied: it is a one-shot operator tool run
# from a checkout, not part of the long-running service.

# Run unprivileged (nobody). Pairs with no-new-privileges in the compose file.
USER 65534:65534
EXPOSE 9826
ENTRYPOINT ["python", "-u", "exporter.py"]
