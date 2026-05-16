FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir numpy httpx

COPY src/tdgl_data/__init__.py /app/tdgl_data/__init__.py
COPY src/tdgl_data/synthetic.py /app/tdgl_data/synthetic.py
COPY src/tdgl_generator/ /app/tdgl_generator/

CMD ["python", "-m", "tdgl_generator.cli"]