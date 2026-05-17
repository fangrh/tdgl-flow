FROM python:3.13

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake \
    libeigen3-dev libhdf5-dev libsuitesparse-dev libomp-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build/cpp-tdgl
COPY src/cpp-tdgl/ .
RUN mkdir build && cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DBUILD_BENCHMARKS=OFF && \
    cmake --build . -j$(nproc) && \
    cmake --install . --prefix /usr/local

WORKDIR /app
RUN pip install --no-cache-dir numpy httpx h5py

COPY services/cpp-tdgl-runner/runner.py /app/runner.py

CMD ["python", "/app/runner.py"]
