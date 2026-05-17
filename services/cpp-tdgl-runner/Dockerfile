# cpp-tdgl simulation runner
FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir numpy httpx

COPY services/cpp-tdgl-runner/runner.py /app/runner.py

CMD ["python", "/app/runner.py"]