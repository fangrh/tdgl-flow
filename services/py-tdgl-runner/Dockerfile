FROM python:3.13

WORKDIR /app

RUN pip install --no-cache-dir numpy httpx h5py scipy zarr tdgl sqlalchemy pydantic pydantic-settings boto3

COPY src/tdgl_workflow/ /app/vendor/tdgl_workflow/
COPY src/tdgl_sdk/ /app/vendor/tdgl_sdk/
COPY services/py-tdgl-runner/ /app/

CMD ["python", "/app/runner.py"]