FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends openssh-client rsync && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy SDK source and install
COPY pyproject.toml /app/
COPY src/ /app/src/
RUN pip install --no-cache-dir .

# Keep runner.py as fallback entrypoint
COPY services/triton-runner/runner.py /app/runner.py

CMD ["python", "/app/runner.py"]
