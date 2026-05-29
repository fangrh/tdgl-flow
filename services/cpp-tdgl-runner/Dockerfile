FROM python:3.13

WORKDIR /app

RUN pip install --no-cache-dir boto3 h5py numpy pillow scipy tdgl

COPY src/tdgl_workflow/ /app/vendor/tdgl_workflow/
COPY src/tdgl_sdk/ /app/vendor/tdgl_sdk/

# Install C++ build deps + build cpp-tdgl
RUN apt-get update && apt-get install -y \
    build-essential cmake curl \
    libeigen3-dev libsuitesparse-dev \
    libopenmpi-dev openmpi-bin libgomp1 \
    libhdf5-dev libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY cpp-tdgl/ /app/cpp-tdgl/
RUN rm -rf /app/cpp-tdgl/build && \
    cd /app/cpp-tdgl && mkdir -p build && cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release \
             -DCMAKE_POLICY_VERSION_MINIMUM=3.5 && \
    make -j$(nproc) tdgl_solve && \
    mv tdgl_solve /usr/local/bin/cpp-tdgl-solve

COPY services/cpp-tdgl-runner/ /app/

ENTRYPOINT ["python", "/app/runner.py"]