# Base template for Python microservices in the TDGL platform.
# Adapt: package list, COPY paths, CMD entry point.
FROM python:3.13

WORKDIR /app

# Install Python dependencies — add yours here
RUN pip install --no-cache-dir httpx pydantic pydantic-settings

# Copy shared library (if service uses it)
COPY src/tdgl_workflow/ /app/vendor/tdgl_workflow/

# Copy service source
COPY services/my-service/ /app/

EXPOSE 8080

CMD ["python", "/app/runner.py"]