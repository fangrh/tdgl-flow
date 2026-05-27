FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client rsync && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir boto3

COPY services/triton-runner/runner.py /app/runner.py

CMD ["python", "/app/runner.py"]
