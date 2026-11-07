# LiveMinutes bridge image. Built and run by compose.local.yaml next to Attendee (README section 1.4).
# Built from the repo root (for the shared requirements.txt); Dockerfile.dockerignore picks the files.
FROM python:3.12-slim

# Print logs straight away; listen on all interfaces so other containers can reach us.
ENV PYTHONUNBUFFERED=1 \
    BIND_HOST=0.0.0.0

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY 1_ingestion/*.py 1_ingestion/*.html ./

# Run as a normal user, not root. certs/ and sessions/ are mounted in by compose.
RUN useradd --uid 10001 bridge && mkdir sessions && chown bridge sessions
USER bridge

EXPOSE 8775 8776
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8776/health')"
CMD ["python", "bridge.py"]
