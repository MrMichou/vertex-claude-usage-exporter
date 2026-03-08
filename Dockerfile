FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir \
    google-cloud-logging>=3.5.0 \
    prometheus-client>=0.17.0

# Copy application
COPY scripts/vertex_claude_exporter/ /app/vertex_claude_exporter/
COPY scripts/grafana_push_metrics.py /app/

# Run as non-root user
RUN useradd -r -u 1000 appuser
USER appuser

ENTRYPOINT ["python", "/app/grafana_push_metrics.py"]
