FROM python:3.11-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
ENV PYTHONPATH=/app/src
EXPOSE 5002
HEALTHCHECK --interval=15s --timeout=6s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5002/health', timeout=5)" || exit 1
CMD ["gunicorn", "--chdir", "src", "--bind", "0.0.0.0:5002", "--workers", "1", "--threads", "4", "--timeout", "300", "app:app"]

FROM runtime AS test
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests/ ./tests/
CMD ["python", "-m", "unittest", "discover", "-s", "tests"]

FROM runtime AS production
