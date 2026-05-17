# Build stage - compile C++ solver
FROM python:3.13-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git \
    libeigen3-dev libhdf5-dev libsuitesparse-dev libomp-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build/cpp-tdgl
COPY src/cpp-tdgl/ .
RUN mkdir build && cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DBUILD_BENCHMARKS=OFF && \
    cmake --build . -j$(nproc)

# Runtime stage
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libhdf5-cpp-serial libsuitesparse-dev libomp5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir numpy httpx h5py

COPY --from=builder /build/cpp-tdgl/build/tdgl_solve /app/tdgl_solve
COPY services/cpp-tdgl-runner/runner.py /app/runner.py

CMD ["python", "/app/runner.py"]
