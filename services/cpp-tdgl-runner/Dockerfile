# Stage 1: Compile C++ solver
FROM ubuntu:22.04 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake g++ git libeigen3-dev libhdf5-dev liblapack-dev \
    libsuitesparse-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY git-tdgl-light/cpp-tdgl /build/cpp-tdgl

RUN cd /build/cpp-tdgl && \
    cmake -B build -DCMAKE_BUILD_TYPE=Release && \
    cmake --build build -j$(nproc)

# Stage 2: Python runtime
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libhdf5-dev liblapack3 libumfpack5 && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/cpp-tdgl/build/tdgl_solve /usr/local/bin/cpp-tdgl-solve

WORKDIR /app

RUN pip install --no-cache-dir boto3 h5py numpy scipy tdgl

COPY src/tdgl_workflow/ /app/vendor/tdgl_workflow/
COPY src/tdgl_sdk/ /app/vendor/tdgl_sdk/
COPY services/cpp-tdgl-runner/ /app/

CMD ["python", "/app/runner.py"]
