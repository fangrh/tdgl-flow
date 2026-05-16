FROM python:3.13-slim

WORKDIR /app

COPY tdgl_data/__init__.py tdgl_data/
COPY tdgl_data/synthetic.py tdgl_data/
COPY tdgl_generator/ tdgl_generator/

RUN pip install --no-cache-dir numpy httpx

CMD ["python", "-m", "tdgl_generator.cli"]