FROM python:3.11-slim

WORKDIR /app

# Install dependencies (via requirements.txt: unquoted ">=" in a RUN line is a
# shell redirection, which silently dropped the version constraints)
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY scripts/vertex_claude_exporter/ /app/vertex_claude_exporter/
COPY scripts/grafana_push_metrics.py /app/

# Run as non-root user
RUN useradd -r -u 1000 appuser
USER appuser

ENTRYPOINT ["python", "/app/grafana_push_metrics.py"]
