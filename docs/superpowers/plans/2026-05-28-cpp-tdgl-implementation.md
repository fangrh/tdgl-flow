# cpp-tdgl + cpp-tdgl-viewer-rust Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a standalone C++ TDGL solver and Rust viewer, producing split HDF5 output (mesh + per-step), with real-time MinIO sync and a Jupyter widget viewer.

**Architecture:** cpp-tdgl is a native C++20 binary that writes split HDF5 files per timing step and uploads them to MinIO in real-time. cpp-tdgl-viewer-rust is a separate PyO3 crate that reads raw bytes from MinIO via HTTP S3 range requests and renders a 2x2 PNG panel in an ipywidgets widget.

**Tech Stack:** C++20, Eigen3, SuiteSparse/UMFPACK, HighFive/HDF5, OpenMP, Rust, PyO3, htmx/ipywidgets, MinIO S3

---

## Phase 1: cpp-tdgl (C++ Solver)

### Task 1: Copy and Adapt cpp-tdgl Source

**Files:**
- Create: `cpp-tdgl/` (entire directory tree copied from `/mnt/c/Users/photo/Photonics_Group/Ruihuan/git-tdgl-light/cpp-tdgl/`)
- Modify: `cpp-tdgl/CMakeLists.txt` (update paths for new repo)
- Test: `cpp-tdgl/build/` compiles successfully

- [ ] **Step 1: Copy entire cpp-tdgl directory tree**

Run:
```bash
cp -r /mnt/c/Users/photo/Photonics_Group/Ruihuan/git-tdgl-light/cpp-tdgl /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/cpp-tdgl
```
Expected: `cpp-tdgl/` now exists with all subdirectories.

- [ ] **Step 2: Review CMakeLists.txt and update if needed**

Read `cpp-tdgl/CMakeLists.txt`. The existing one likely uses `project(cpp-tdgl)` and `add_subdirectory(src mesh)` etc. Verify paths work from new location. Most likely no changes needed since CMake uses relative paths.

- [ ] **Step 3: Configure and build to verify compilation works**

Run:
```bash
cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/cpp-tdgl
mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j$(nproc)
```
Expected: Binary `cpp-tdgl` produced in `build/`.

- [ ] **Step 4: Verify basic CLI works**

Run:
```bash
./cpp-tdgl --help
```
Expected: Help text showing `--mesh`, `--timing`, `--output`, `--source-current`, `--drain-current`, `--applied-field`, `--timing`, `--restart`, `--solver-options`.

- [ ] **Step 5: Commit**

```bash
git add cpp-tdgl/
git commit -m "feat: copy cpp-tdgl from git-tdgl-light"
```

---

### Task 2: Add Split-Output HDF5 Writer to cpp-tdgl

**Files:**
- Modify: `cpp-tdgl/src/solution/solution.h` — add `SolutionWriter::write_step()` method
- Modify: `cpp-tdgl/src/solution/solution.cpp`
- Modify: `cpp-tdgl/src/solver/solver.h` — add step-tracking state
- Modify: `cpp-tdgl/src/solver/solver.cpp` — call step-aware output
- Test: `cpp-tdgl/build/` compiles and `step_*.h5` files are produced

**Context:** The existing cpp-tdgl writes all frames to a single `output.h5`. We need to split output by timing step, writing `step_XXXX.h5` when each timing step completes. The mesh is written to `mesh.h5` separately.

- [ ] **Step 1: Study the existing solution/solution.h and solution.cpp**

Read `cpp-tdgl/src/solution/solution.h` and `solution.cpp`. Understand:
- `SolutionWriter` class structure
- How `write_time_step()` currently works
- What datasets it writes (psi, mu, supercurrent, normal_current, applied_A, induced_A, epsilon, dt, time)
- How the HDF5 file/group structure is created

- [ ] **Step 2: Add step-tracking state to solver**

Read `cpp-tdgl/src/solver/solver.h`. Find where timing step index is tracked during the solve loop. Add a `current_step_idx_` member and `on_step_complete(step_idx)` callback mechanism.

In `solver.h`, add:
```cpp
private:
    int current_step_idx_ = 0;

public:
    std::function<void(int step_idx)> on_step_complete;
```

In `solver.cpp`, in the time loop where a timing step ends (detect by checking if `time >= ramp_start + stable_end` for the current step), call `if (on_step_complete) on_step_complete(current_step_idx_)` and increment `current_step_idx_`.

- [ ] **Step 3: Modify SolutionWriter for split output**

In `solution.h`, add a new constructor and methods:
```cpp
class SolutionWriter {
public:
    SolutionWriter(const std::string& mesh_h5_path, const std::string& output_dir);
    void write_mesh(const Mesh& mesh, const Device& device);
    void begin_step(int step_idx, double je, double ramp_start, double stable_end);
    void write_frame(int frame_idx, const PsiResult& psi_result, const Observables& obs, double dt, double time);
    void end_step();
    void write_manifest(const std::string& run_id, double solve_time);
};
```

In `solution.cpp`, implement:
- `write_mesh()`: creates `mesh.h5` at `{output_dir}/mesh.h5` with the existing schema
- `begin_step()`: creates `{output_dir}/step_{step_idx:04d}.h5`, writes `/metadata/` group
- `write_frame()`: writes to `/data/step_{frame_idx:03d}/` datasets
- `end_step()`: computes and stores byte offsets for all datasets in this step, stores in local `discrete_index.json` entry
- `write_manifest()`: creates `manifest.json`

- [ ] **Step 4: Verify HDF5 byte offsets are correct**

Byte offset for a dataset = HDF5 file offset at time of creation. HighFive does not expose raw byte offsets directly. Compute offsets manually:
```cpp
// After creating a dataset, the offset is available via HighFive::DataSet::getOffset()
std::vector<hsize_t> offset = dataset.getOffset();
```
Store these in `discrete_index.json` per step.

- [ ] **Step 5: Write discrete_index.json schema**

After each `end_step()`, append to local `discrete_index.json`:
```json
{
  "version": 1,
  "run_id": "...",
  "mesh_file": "mesh.h5",
  "n_sites": N,
  "n_edges": E,
  "n_steps": total_steps,
  "steps": [
    {
      "step_idx": 0,
      "je": 0.05,
      "ramp_start": 0.0,
      "stable_end": 200.0,
      "h5_file": "step_0000.h5",
      "total_frames": 200,
      "psi_offset": 123456,
      "psi_size": 48000,
      ...
    }
  ],
  "status": "running"
}
```

- [ ] **Step 6: Build and test with a small mesh**

Run cmake + make, then test with a small mesh (use existing test mesh if available):
```bash
cd cpp-tdgl/build
./cpp-tdgl --mesh ../../tests/inputs/small_mesh.h5 --timing /tmp/timing.json --output-dir /tmp/cpp-tdgl-test
ls /tmp/cpp-tdgl-test/
```
Expected: `mesh.h5`, `step_0000.h5`, `step_0001.h5`, ..., `discrete_index.json`, `manifest.json`.

- [ ] **Step 7: Verify HDF5 structure with h5dump**

```bash
h5dump -H /tmp/cpp-tdgl-test/step_0000.h5
h5dump -d /data/step_000/psi /tmp/cpp-tdgl-test/step_0000.h5 | head -20
```

- [ ] **Step 8: Commit**

```bash
git add cpp-tdgl/src/solution/ cpp-tdgl/src/solver/
git commit -m "feat(cpp-tdgl): add split-output HDF5 writer for per-step files"
```

---

### Task 3: Add Real-Time Sync to MinIO

**Files:**
- Modify: `cpp-tdgl/src/main.cpp` — add `--sync-*` CLI flags and sync thread
- Create: `cpp-tdgl/src/sync/minio_synchronizer.h` — MinIO upload client
- Create: `cpp-tdgl/src/sync/minio_synchronizer.cpp`
- Create: `cpp-tdgl/src/sync/httplib.h` — header-only HTTP client (use curl or httplib)
- Test: Verify files upload to MinIO during solve

**Context:** After each timing step completes, the sync thread uploads `step_XXXX.h5` and `discrete_index.json` to MinIO. This is built into the cpp-tdgl binary (not a separate sidecar), similar to how py-tdgl runner has a background upload thread.

- [ ] **Step 1: Design the synchronizer interface**

Create `cpp-tdgl/src/sync/minio_synchronizer.h`:
```cpp
class MinioSynchronizer {
public:
    MinioSynchronizer(const std::string& url, const std::string& bucket,
                     const std::string& prefix, int interval_seconds);
    ~MinioSynchronizer();

    void start();
    void stop();
    void upload_step(const std::string& step_h5_path, int step_idx);
    void upload_mesh(const std::string& mesh_h5_path);
    void upload_manifest(const std::string& manifest_path);
    void upload_index(const std::string& index_path);
    bool is_running() const;

private:
    void run_loop();  // polling thread
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
```

- [ ] **Step 2: Use curl for HTTP PUT uploads**

Use libcurl (already available on Ubuntu). `MinioSynchronizer::upload_file()` does:
```cpp
CURL* curl = curl_easy_init();
curl_easy_setopt(curl, CURLOPT_URL, full_url.c_str());
curl_easy_setopt(curl, CURLOPT_UPLOAD, 1L);
curl_easy_setopt(curl, CURLOPT_READDATA, file_ptr);
curl_easy_setopt(curl, CURLOPT_INFILESIZE, file_size);
curl_easy_setopt(curl, CURLOPT_PUT, 1L);
// For authenticated MinIO: add AWS V4 signing headers
curl_easy_perform(curl);
```

MinIO uses AWS S3-compatible signing. For simplicity, if MinIO has no auth (dev mode), skip signing. For authenticated MinIO, implement AWS V4 signing or use `--sync-access-key` / `--sync-secret-key` flags.

- [ ] **Step 3: Add CLI flags to main.cpp**

In `main.cpp`, after existing flags, add:
```cpp
std::string sync_url;
std::string sync_bucket;
std::string sync_prefix;
int sync_interval = 5;
bool enable_sync = false;

CLI::AddOption("--sync-url", sync_url, "MinIO S3 endpoint URL");
CLI::AddOption("--sync-bucket", sync_bucket, "MinIO bucket name");
CLI::AddOption("--sync-prefix", sync_prefix, "MinIO key prefix for this run");
CLI::AddOption("--sync-interval", sync_interval, "Seconds between sync uploads");
CLI::AddOption("--enable-sync", enable_sync, "Enable real-time MinIO sync");
```

- [ ] **Step 4: Wire up synchronizer in main.cpp**

```cpp
std::unique_ptr<MinioSynchronizer> syncer;
if (enable_sync) {
    syncer = std::make_unique<MinioSynchronizer>(sync_url, sync_bucket, sync_prefix, sync_interval);
    syncer->start();
}

TdglSolver solver(mesh, device, options);
solver.on_step_complete = [&](int step_idx) {
    if (syncer) {
        syncer->upload_step(output_dir + "/step_" + fmt("%04d", step_idx) + ".h5", step_idx);
        syncer->upload_index(output_dir + "/discrete_index.json");
    }
};

solver.run();
```

- [ ] **Step 5: Build with sync support**

Ensure `curl` is linked:
```bash
target_link_libraries(cpp-tdgl curl)
```

```bash
cd cpp-tdgl/build && cmake .. && make -j$(nproc)
./cpp-tdgl --help | grep sync
```
Expected: `--enable-sync`, `--sync-url`, `--sync-bucket`, `--sync-prefix`, `--sync-interval` in help.

- [ ] **Step 6: Test upload logic (mock test without real MinIO)**

For unit testing without a real MinIO, mock the HTTP responses using a local HTTP server that just logs PUT requests. Use Python:
```python
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

class PutLogger(BaseHTTPRequestHandler):
    def do_PUT(self):
        print(f"PUT {self.path}")
        self.send_response(200)
    def log_message(self, *args): pass

server = HTTPServer(('localhost', 9000), PutLogger)
thread = threading.Thread(target=server.serve_forever)
thread.start()
# run cpp-tdgl with --enable-sync --sync-url http://localhost:9000
server.shutdown()
```

- [ ] **Step 7: Commit**

```bash
git add cpp-tdgl/src/main.cpp cpp-tdgl/src/sync/
git commit -m "feat(cpp-tdgl): add real-time MinIO sync via sync thread"
```

---

## Phase 2: Docker Runner

### Task 4: Create services/cpp-tdgl-runner

**Files:**
- Create: `services/cpp-tdgl-runner/Dockerfile`
- Create: `services/cpp-tdgl-runner/entrypoint.sh`
- Test: `docker build` succeeds locally

- [ ] **Step 1: Create Dockerfile**

```dockerfile
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    curl \
    libeigen3-dev \
    libsuitesparse-dev \
    libopenmpi-dev \
    openmpi-bin \
    libgomp1 \
    libhdf5-dev \
    hdf5-tools \
    libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Build cpp-tdgl from source (copy entire cpp-tdgl source tree)
COPY cpp-tdgl/ /app/cpp-tdgl/
RUN cd /app/cpp-tdgl && mkdir -p build && cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release && \
    make -j$(nproc) && \
    mv cpp-tdgl /app/

COPY services/cpp-tdgl-runner/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
```

- [ ] **Step 2: Create entrypoint.sh**

```bash
#!/bin/bash
set -e

echo "Starting cpp-tdgl runner..."
echo "Arguments: $@"

/app/cpp-tdgl \
  --mesh "${MESH_PATH:-/inputs/mesh.h5}" \
  --timing "${TIMING_PATH:-/inputs/timing.json}" \
  --options "${OPTIONS_PATH:-/inputs/options.json}" \
  --output-dir "${OUTPUT_DIR:-/outputs}" \
  --enable-sync \
  --sync-url "${MINIO_URL:-http://minio:9000}" \
  --sync-bucket "${MINIO_BUCKET:-tdgl-results}" \
  --sync-prefix "${MINIO_PREFIX:-}" \
  --sync-interval "${SYNC_INTERVAL:-5}"

echo "cpp-tdgl completed."
```

- [ ] **Step 3: Test docker build**

```bash
cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl
docker build -f services/cpp-tdgl-runner/Dockerfile -t cpp-tdgl-runner:dev .
docker run --rm cpp-tdgl-runner:dev --help
```
Expected: `--help` output from cpp-tdgl binary.

- [ ] **Step 4: Commit**

```bash
git add services/cpp-tdgl-runner/
git commit -m "feat: add cpp-tdgl-runner Docker image"
```

---

## Phase 3: cpp-tdgl-viewer-rust (Rust Viewer)

### Task 5: Scaffold cpp-tdgl-viewer-rust Cargo Project

**Files:**
- Create: `cpp-tdgl-viewer-rust/Cargo.toml`
- Create: `cpp-tdgl-viewer-rust/src/lib.rs` (skeleton)
- Create: `cpp-tdgl-viewer-rust/python/cpp_tdgl_viewer_rust/widget.py` (skeleton)
- Test: `cargo build` succeeds, Python import works

- [ ] **Step 1: Create Cargo.toml**

```toml
[package]
name = "cpp-tdgl-viewer-rust"
version = "0.1.0"
edition = "2021"

[lib]
name = "cpp_tdgl_viewer_rust"
crate-type = ["cdylib"]

[dependencies]
pyo3 = { version = "0.22", features = ["extension-module"] }
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
reqwest = { version = "0.12", features = ["blocking"] }
image = "0.25"
tokio = { version = "1.0", features = ["full"] }
notify = "7.0"
ndarray = "0.16"
thiserror = "2.0"

[profile.release]
opt-level = 3
lto = true
```

- [ ] **Step 2: Create skeleton lib.rs**

```rust
use pyo3::prelude::*;

#[pymodule]
fn cpp_tdgl_viewer_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<CppTdglViewer>()?;
    Ok(())
}

#[pyclass]
struct CppTdglViewer {
    // TODO: fields
}

#[pymethods]
impl CppTdglViewer {
    #[new]
    fn new() -> Self {
        CppTdglViewer {}
    }
}
```

- [ ] **Step 3: Build to verify PyO3 setup**

```bash
cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/cpp-tdgl-viewer-rust
cargo build --release
```
Expected: Builds successfully, produces `target/release/libcpp_tdgl_viewer_rust.so`.

- [ ] **Step 4: Create python/widget.py skeleton**

```python
from cpp_tdgl_viewer_rust import CppTdglViewer

__all__ = ["CppTdglViewer"]
```

- [ ] **Step 5: Commit**

```bash
git add cpp-tdgl-viewer-rust/
git commit -m "feat: scaffold cpp-tdgl-viewer-rust Cargo project"
```

---

### Task 6: Copy Shared Components from tdgl-viewer-rust

**Files:**
- Create: `cpp-tdgl-viewer-rust/src/minio_client.rs` (copied from tdgl-viewer-rust)
- Create: `cpp-tdgl-viewer-rust/src/renderer.rs` (copied from tdgl-viewer-rust)
- Create: `cpp-tdgl-viewer-rust/src/iv.rs` (copied from tdgl-viewer-rust)
- Create: `cpp-tdgl-viewer-rust/src/run_info.rs` (adapted from tdgl-viewer-rust)
- Test: `cargo build` still succeeds after copying

**Context:** Per the design, these are copied (not extracted as a shared crate) to maintain separation. Each file is copied verbatim or with minimal adaptation.

- [ ] **Step 1: Read and copy minio_client.rs**

Read `tdgl-viewer-rust/src/minio.rs`. Copy it to `cpp-tdgl-viewer-rust/src/minio_client.rs`. The file should work unchanged — it implements `MinioClient` with `read_range(key, offset, size) -> Vec<u8>` using reqwest blocking HTTP GET with `Range` header.

- [ ] **Step 2: Read and copy renderer.rs**

Read `tdgl-viewer-rust/src/renderer.rs`. Copy to `cpp-tdgl-viewer-rust/src/renderer.rs`. Verify imports compile (all crates should be in Cargo.toml).

- [ ] **Step 3: Read and copy iv.rs**

Read `tdgl-viewer-rust/src/iv.rs`. Copy to `cpp-tdgl-viewer-rust/src/iv.rs`.

- [ ] **Step 4: Read and adapt run_info.rs**

Read `tdgl-viewer-rust/src/run_info.rs`. Copy to `cpp-tdgl-viewer-rust/src/run_info.rs` and adapt the manifest parsing to match the cpp-tdgl `manifest.json` schema (mostly similar, may need minor field mapping).

- [ ] **Step 5: Verify cargo build**

```bash
cd cpp-tdgl-viewer-rust && cargo build --release
```
Expected: Compiles successfully.

- [ ] **Step 6: Commit**

```bash
git add cpp-tdgl-viewer-rust/src/minio_client.rs cpp-tdgl-viewer-rust/src/renderer.rs cpp-tdgl-viewer-rust/src/iv.rs cpp-tdgl-viewer-rust/src/run_info.rs
git commit -m "feat(cpp-tdgl-viewer): copy shared components from tdgl-viewer-rust"
```

---

### Task 7: Implement discrete_reader.rs and hdf5_index.rs

**Files:**
- Create: `cpp-tdgl-viewer-rust/src/hdf5_index.rs` — parses `discrete_index.json`
- Create: `cpp-tdgl-viewer-rust/src/discrete_reader.rs` — reads from `mesh.h5` + `step_XXXX.h5`
- Test: Write unit tests reading from a real `discrete_index.json`

- [ ] **Step 1: Define Hdf5Index struct in hdf5_index.rs**

```rust
#[derive(Debug, Clone, serde::Deserialize)]
pub struct Hdf5Index {
    pub version: u32,
    pub run_id: String,
    pub mesh_file: String,
    pub n_sites: usize,
    pub n_edges: usize,
    pub n_steps: usize,
    pub steps: Vec<StepInfo>,
    pub status: String,
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct StepInfo {
    pub step_idx: u32,
    pub je: f64,
    pub ramp_start: f64,
    pub stable_end: f64,
    pub h5_file: String,
    pub total_frames: usize,
    pub psi_offset: u64,
    pub psi_size: u64,
    pub mu_offset: u64,
    pub mu_size: u64,
    pub supercurrent_offset: u64,
    pub supercurrent_size: u64,
    pub normal_current_offset: u64,
    pub normal_current_size: u64,
    pub applied_A_offset: u64,
    pub applied_A_size: u64,
    pub induced_A_offset: u64,
    pub induced_A_size: u64,
    pub epsilon_offset: u64,
    pub epsilon_size: u64,
}
```

- [ ] **Step 2: Implement Hdf5Index::load()**

```rust
impl Hdf5Index {
    pub fn from_json(json: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(json)
    }

    pub fn load_from_minio(client: &MinioClient, prefix: &str) -> Result<Self, ...> {
        let key = format!("{}/discrete_index.json", prefix);
        let data = client.read_range(&key, 0, 1 << 20)?;
        let json = std::str::from_utf8(&data).map_err(...)?;
        Hdf5Index::from_json(json)
    }
}
```

- [ ] **Step 3: Define DiscreteReader in discrete_reader.rs**

```rust
pub struct DiscreteReader {
    minio_client: MinioClient,
    index: Hdf5Index,
    prefix: String,
    mesh_data: MeshData,
}

pub struct MeshData {
    pub sites: Vec<f64>,       // (n_sites, 2)
    pub elements: Vec<i64>,    // (n_elements, 3)
    pub areas: Vec<f64>,       // (n_sites,)
    pub edges: Vec<i64>,       // (n_edges, 2)
    pub edge_centers: Vec<f64>, // (n_edges, 2)
    pub edge_dirs: Vec<f64>,   // (n_edges, 2)
    pub edge_lengths: Vec<f64>,
    pub dual_edge_lengths: Vec<f64>,
}

pub struct StepFrame {
    pub psi: Vec<f64>,         // (n_sites, 2) interleaved [re, im]
    pub mu: Vec<f64>,          // (n_sites,)
    pub supercurrent: Vec<f64>, // (n_edges,)
    pub normal_current: Vec<f64>, // (n_edges,)
    pub applied_A: Vec<f64>,
    pub induced_A: Vec<f64>,
    pub epsilon: Vec<f64>,
    pub time: f64,
    pub dt: f64,
}
```

- [ ] **Step 4: Implement DiscreteReader::open()**

```rust
impl DiscreteReader {
    pub fn open(minio_url: &str, bucket: &str, prefix: &str) -> Result<Self> {
        let client = MinioClient::new(minio_url, bucket);
        let index = Hdf5Index::load_from_minio(&client, prefix)?;

        // Read mesh from mesh.h5
        let mesh_key = format!("{}/mesh.h5", prefix);
        let mesh_data = Self::read_mesh(&client, &mesh_key, &index)?;

        Ok(DiscreteReader { minio_client: client, index, prefix: prefix.to_string(), mesh_data })
    }

    fn read_mesh(client: &MinioClient, mesh_key: &str, index: &Hdf5Index) -> Result<MeshData> {
        // Read /mesh/sites, /mesh/elements, /mesh/areas, /mesh/edge_mesh/* at known offsets
        // These offsets are computed when building mesh.h5 — store them in a separate
        // mesh_index section of discrete_index.json, or read from H5 file directly
        // by parsing the HDF5 superblock/footer to find dataset offsets.
        // For simplicity: read the full mesh H5 (mesh is small, ~few MB) and parse locally.
        let mesh_bytes = client.read_range(mesh_key, 0, 1 << 30)?; // up to 1GB
        parse_mesh_hdf5(&mesh_bytes)  // use HDF5 byte-level parsing
    }
}
```

- [ ] **Step 5: Implement frame reading**

```rust
impl DiscreteReader {
    pub fn read_frame(&self, step_idx: usize, frame_idx: usize) -> Result<StepFrame> {
        let step = &self.index.steps[step_idx];
        let h5_key = format!("{}/{}", self.prefix, step.h5_file);

        // psi
        let psi_offset = step.psi_offset + frame_idx as u64 * step.psi_size;
        let psi_data = self.minio_client.read_range(&h5_key, psi_offset, step.psi_size)?;

        // mu
        let mu_offset = step.mu_offset + frame_idx as u64 * step.mu_size;
        let mu_data = self.minio_client.read_range(&h5_key, mu_offset, step.mu_size)?;

        // supercurrent, normal_current, applied_A, induced_A, epsilon similarly
        // Convert bytes to f64 via FromBytes
    }
}
```

Note: The frame size is constant per step (`psi_size = n_sites * 2 * 8`). Frames are stored consecutively in the H5 file, so frame `i` starts at `base_offset + i * frame_size`.

- [ ] **Step 6: Write unit tests**

Create `cpp-tdgl-viewer-rust/tests/test_discrete_reader.rs`:
```rust
#[test]
fn test_parse_discrete_index() {
    let json = r#"{
      "version": 1,
      "run_id": "test123",
      "mesh_file": "mesh.h5",
      "n_sites": 3000,
      "n_edges": 8090,
      "n_steps": 3,
      "steps": [
        {
          "step_idx": 0,
          "je": 0.05,
          "ramp_start": 0.0,
          "stable_end": 200.0,
          "h5_file": "step_0000.h5",
          "total_frames": 200,
          "psi_offset": 4096,
          "psi_size": 48000,
          ...
        }
      ],
      "status": "completed"
    }"#;
    let index = Hdf5Index::from_json(json).unwrap();
    assert_eq!(index.n_steps, 3);
    assert_eq!(index.steps[0].step_idx, 0);
}
```

- [ ] **Step 7: Verify build**

```bash
cd cpp-tdgl-viewer-rust && cargo build --release
```

- [ ] **Step 8: Commit**

```bash
git add cpp-tdgl-viewer-rust/src/hdf5_index.rs cpp-tdgl-viewer-rust/src/discrete_reader.rs cpp-tdgl-viewer-rust/tests/
git commit -m "feat(cpp-tdgl-viewer): implement discrete_reader and hdf5_index"
```

---

### Task 8: Implement lib.rs PyO3 Interface

**Files:**
- Modify: `cpp-tdgl-viewer-rust/src/lib.rs` — implement full `CppTdglViewer` class
- Test: Python `from cpp_tdgl_viewer_rust import CppTdglViewer` works

- [ ] **Step 1: Define CppTdglViewer struct**

```rust
#[pyclass]
struct CppTdglViewer {
    reader: DiscreteReader,
    current_step: usize,
    current_frame: usize,
    rendered_image: Option<Vec<u8>>,
}
```

- [ ] **Step 2: Implement `open()` method**

```rust
#[pymethods]
impl CppTdglViewer {
    #[new]
    fn new() -> Self {
        CppTdglViewer {
            reader: todo!(), // will be set by open()
            current_step: 0,
            current_frame: 0,
            rendered_image: None,
        }
    }

    fn open(&mut self, minio_url: &str, bucket: &str, prefix: &str) -> PyResult<()> {
        self.reader = DiscreteReader::open(minio_url, bucket, prefix)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
        self.current_step = 0;
        self.current_frame = 0;
        Ok(())
    }

    fn render_frame(&mut self, step_idx: usize, frame_idx: usize) -> PyResult<Vec<u8>> {
        let frame = self.reader.read_frame(step_idx, frame_idx)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
        let mesh = self.reader.mesh_data();
        let img = render_2x2_panel(mesh, frame, step_idx, frame_idx);
        Ok(img)
    }

    fn get_step_count(&self) -> usize {
        self.reader.index().n_steps
    }

    fn get_frame_count(&self, step_idx: usize) -> usize {
        self.reader.index().steps[step_idx].total_frames
    }

    fn get_min_time(&self, step_idx: usize) -> f64 {
        self.reader.index().steps[step_idx].ramp_start
    }

    fn get_max_time(&self, step_idx: usize) -> f64 {
        self.reader.index().steps[step_idx].stable_end
    }
}
```

- [ ] **Step 3: Implement render_2x2_panel**

This calls into the renderer. The renderer takes `MeshData`, `StepFrame`, step/frame indices and returns PNG bytes. Reuse the same rendering logic as tdgl-viewer-rust.

- [ ] **Step 4: Build and verify Python import**

```bash
cd cpp-tdgl-viewer-rust && cargo build --release
# On Linux, the .so file will be in target/release/
python3 -c "from cpp_tdgl_viewer_rust import CppTdglViewer; print('OK')"
```
Expected: Prints `OK`.

- [ ] **Step 5: Commit**

```bash
git add cpp-tdgl-viewer-rust/src/lib.rs
git commit -m "feat(cpp-tdgl-viewer): implement PyO3 CppTdglViewer interface"
```

---

### Task 9: Implement widget.py ipywidgets Wrapper

**Files:**
- Modify: `cpp-tdgl-viewer-rust/python/cpp_tdgl_viewer_rust/widget.py`
- Test: Widget displays correctly in Jupyter

- [ ] **Step 1: Read existing tdgl-viewer-rust widget.py**

Read `tdgl-viewer-rust/python/tdgl_viewer_rust/widget.py` to understand the existing widget structure (dropdowns, sliders, play button, image display).

- [ ] **Step 2: Implement widget.py**

```python
import ipywidgets as widgets
from IPython.display import display, Image, clear_output

class CppTdglViewer:
    def __init__(self):
        self._step_dropdown = widgets.Dropdown(
            options=[],
            description='Step:',
            disabled=False,
        )
        self._frame_slider = widgets.IntSlider(
            value=0,
            min=0,
            max=100,
            step=1,
            description='Frame:',
            continuous_update=False,
        )
        self._play_btn = widgets.Button(description='▶ Play', button_style='success')
        self._fps_input = widgets.IntText(value=10, description='FPS:', width=60)
        self._image = widgets.Image(format='png', width=760)

        self._step_dropdown.observe(self._on_step_change, names='value')
        self._frame_slider.observe(self._on_frame_change, names='value')
        self._play_btn.on_click(self._on_play)

        self._timer = None
        self._viewer = None  # cpp_tdgl_viewer_rust.CppTdglViewer

        self.widget = widgets.VBox([
            widgets.HBox([self._step_dropdown, self._frame_slider, self._play_btn, self._fps_input]),
            self._image,
        ])

    def open(self, minio_url, bucket, prefix):
        from cpp_tdgl_viewer_rust import CppTdglViewer
        self._viewer = CppTdglViewer()
        self._viewer.open(minio_url, bucket, prefix)

        n_steps = self._viewer.get_step_count()
        self._step_dropdown.options = list(range(n_steps))
        self._step_dropdown.value = 0
        self._update_frame_count(0)
        self._render_frame(0, 0)

    def _on_step_change(self, change):
        step_idx = change['new']
        self._update_frame_count(step_idx)
        self._render_frame(step_idx, 0)

    def _on_frame_change(self, change):
        frame_idx = change['new']
        step_idx = self._step_dropdown.value
        self._render_frame(step_idx, frame_idx)

    def _update_frame_count(self, step_idx):
        n_frames = self._viewer.get_frame_count(step_idx)
        self._frame_slider.max = max(0, n_frames - 1)
        self._frame_slider.value = 0

    def _render_frame(self, step_idx, frame_idx):
        img_bytes = self._viewer.render_frame(step_idx, frame_idx)
        self._image.value = img_bytes

    def _on_play(self, btn):
        if self._timer is None:
            self._start_playback()
            btn.description = '⏸ Pause'
            btn.button_style = 'warning'
        else:
            self._stop_playback()
            btn.description = '▶ Play'
            btn.button_style = 'success'

    def _start_playback(self):
        import threading
        def loop():
            while self._timer is not None:
                frame = self._frame_slider.value + 1
                if frame > self._frame_slider.max:
                    frame = 0
                self._frame_slider.value = frame
                time.sleep(1.0 / self._fps_input.value)
        self._timer = threading.Thread(target=loop)
        self._timer.start()

    def _stop_playback(self):
        self._timer = None
```

- [ ] **Step 3: Verify Python import**

```bash
python3 -c "from cpp_tdgl_viewer_rust.widget import CppTdglViewer; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add cpp-tdgl-viewer-rust/python/cpp_tdgl_viewer_rust/widget.py
git commit -m "feat(cpp-tdgl-viewer): add ipywidgets wrapper"
```

---

## Phase 4: Workflows and Notebooks

### Task 10: Create workflows/cpp-tdgl-device-builder.yaml

**Files:**
- Create: `workflows/cpp-tdgl-device-builder.yaml`
- Test: `kubectl dry-run` validates the YAML

- [ ] **Step 1: Read existing rectangle-device-builder.yaml**

Read `workflows/rectangle-device-builder.yaml` to understand the structure (inputs, outputs, container image, command).

- [ ] **Step 2: Create cpp-tdgl-device-builder.yaml**

```yaml
apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata:
  name: cpp-tdgl-device-builder
spec:
  entrypoint: build-device
  arguments:
    parameters:
      - name: device_name
        value: "rectangle"
      - name: width
        value: "1e-5"
      - name: height
        value: "0.2e-5"
      - name: dx
        value: "0.05e-6"
      - name: lambda_
        value: "0.1e-6"
  templates:
    - name: build-device
      outputs:
        artifacts:
          - name: device_artifact
            path: /outputs/device.h5
            archive:
              none: {}
      container:
        image: ghcr.io/fangrh/cpp-tdgl-runner:dev
        command: [/app/cpp-tdgl]
        args:
          - --mesh /inputs/mesh.h5
          - --build-device-only
          - --output-dir /outputs
```

Wait — cpp-tdgl does not have a `--build-device-only` flag. Instead, create a separate small tool or Python script that builds the device mesh and writes `mesh.h5`. This task should also add a `--build-device` CLI flag to cpp-tdgl's main.cpp.

Actually: reuse the existing `tdgl_sdk` Python mesh builder. The device builder workflow can remain Python-based (building mesh.pkl), and the mesh H5 is produced by a conversion step. Create a lightweight Python script that reads `device.pkl` and writes `mesh.h5`.

- [ ] **Step 3: Add mesh conversion step**

Add to `notebooks/` a script `build_cpp_tdgl_mesh.py` that:
1. Reads the mesh from the existing py-tdgl pipeline artifacts (mesh.pkl or mesh.h5)
2. Converts to `mesh.h5` format compatible with cpp-tdgl
3. Uploads to MinIO or writes to PVC

This keeps the mesh builder as Python (reuse existing code) and cpp-tdgl just reads the H5.

- [ ] **Step 4: Validate YAML**

```bash
kubectl -n tdgl apply --dry-run=server -f workflows/cpp-tdgl-device-builder.yaml
```

- [ ] **Step 5: Commit**

```bash
git add workflows/cpp-tdgl-device-builder.yaml
git commit -m "feat: add cpp-tdgl-device-builder workflow template"
```

---

### Task 11: Create notebooks/run_cpp_tdgl.py

**Files:**
- Create: `notebooks/run_cpp_tdgl.py`
- Create: `notebooks/browse_cpp_tdgl_runs.py`
- Test: `python notebooks/run_cpp_tdgl.py --help` works

- [ ] **Step 1: Read existing run_py_tdgl.py**

Read `notebooks/run_py_tdgl.py` to understand how it submits workflows, polls for completion, and launches the viewer.

- [ ] **Step 2: Create run_cpp_tdgl.py**

```python
import time
import argparse
from tdgl_sdk.pipeline import SimulationPipeline
from tdgl_sdk.client import TDGLRunStore
from cpp_tdgl_viewer_rust.widget import CppTdglViewer

def run_cpp_tdgl(device, timing, options, run_id=None,
                 minio_url="http://minio:9000",
                 bucket="tdgl-results",
                 image="ghcr.io/fangrh/cpp-tdgl-runner:dev"):
    pipeline = SimulationPipeline(image=image)
    run = pipeline.submit(
        device=device,
        timing=timing,
        options=options,
        run_id=run_id,
        workflow_name="cpp-tdgl-sim",
    )

    store = TDGLRunStore(minio_url=minio_url, bucket=bucket)

    # Poll for first step to appear
    print(f"Waiting for simulation to start (run_id={run.run_id})...")
    while not store.step_exists(run.run_id, 0):
        time.sleep(5)

    print("First step detected. Launching viewer...")
    viewer = CppTdglViewer()
    viewer.open(minio_url=minio_url, bucket=bucket,
                prefix=f"tdgl-runs/{run.run_id}/")
    display(viewer.widget)

    return run

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True, help="Path to device.pkl")
    parser.add_argument("--timing", required=True, help="Path to timing.json")
    parser.add_argument("--options", required=True, help="Path to options.json")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    run_cpp_tdgl(args.device, args.timing, args.options, args.run_id)
```

- [ ] **Step 3: Create browse_cpp_tdgl_runs.py**

```python
"""Browse past cpp-tdgl runs from MinIO."""
from tdgl_sdk.client import TDGLRunStore
store = TDGLRunStore()
runs = store.list_runs(tool="cpp-tdgl")
for run in runs:
    print(f"{run.run_id} | status={run.status} | created={run.created_at}")
```

- [ ] **Step 4: Test imports**

```bash
cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl
python3 -c "from tdgl_sdk.pipeline import SimulationPipeline; print('OK')"
python3 -c "from cpp_tdgl_viewer_rust.widget import CppTdglViewer; print('OK')"
```
Expected: both print `OK`.

- [ ] **Step 5: Commit**

```bash
git add notebooks/run_cpp_tdgl.py notebooks/browse_cpp_tdgl_runs.py
git commit -m "feat: add cpp-tdgl notebook scripts"
```

---

### Task 12: Integration Test

**Files:**
- Test against a real mesh + timing on a running MinIO

- [ ] **Step 1: Build Docker image with updated code**

```bash
cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl
docker build -f services/cpp-tdgl-runner/Dockerfile -t cpp-tdgl-runner:dev .
```

- [ ] **Step 2: Run cpp-tdgl binary directly with a test mesh**

If MinIO is not available, test locally:
```bash
cd cpp-tdgl/build
./cpp-tdgl \
  --mesh /tmp/test_mesh.h5 \
  --timing /tmp/timing.json \
  --options /tmp/options.json \
  --output-dir /tmp/cpp-tdgl-out
ls /tmp/cpp-tdgl-out/
h5dump -H /tmp/cpp-tdgl-out/step_0000.h5
```

- [ ] **Step 3: Verify discrete_index.json is correct**

```bash
python3 -c "
import json
with open('/tmp/cpp-tdgl-out/discrete_index.json') as f:
    idx = json.load(f)
print(f'n_steps={idx[\"n_steps\"]}')
for s in idx['steps']:
    print(f'  step {s[\"step_idx\"]}: {s[\"h5_file\"]}, frames={s[\"total_frames\"]}')
"
```

- [ ] **Step 4: Build cpp-tdgl-viewer-rust wheel**

```bash
cd cpp-tdgl-viewer-rust
cargo build --release
# The .so file is the extension module
```

- [ ] **Step 5: Smoke test the viewer with local files**

Test `DiscreteReader` reading from local files (not MinIO) to verify HDF5 byte offset logic:
```bash
cd cpp-tdgl-viewer-rust
cargo test
```
Expected: all tests pass.

- [ ] **Step 6: Commit final integration changes**

---

## Self-Review Checklist

- [ ] **Spec coverage:** Every section of the design doc has a corresponding task above.
  - Directory structure → Task 1
  - HDF5 schema → Task 2 (solution writer)
  - discrete_index.json → Task 2 (step tracking)
  - CLI interface → Task 3 (main.cpp flags)
  - Real-time sync → Task 3 (MinioSynchronizer)
  - Docker deployment → Task 4
  - Viewer architecture → Tasks 5-9
  - Notebooks → Task 11
  - Workflow → Task 10
- [ ] **Placeholder scan:** No "TBD", "TODO", or vague requirements in task descriptions.
- [ ] **Type consistency:** `StepInfo` struct fields match `discrete_index.json` schema exactly. `DiscreteReader::read_frame()` step/frame indices are consistent throughout.
- [ ] **No missing steps:** Every code step shows actual code, not just "implement X".
- [ ] **Dependency order:** Tasks 1-3 (cpp-tdgl) → Task 4 (Docker) → Tasks 5-9 (viewer) → Tasks 10-11 (workflow/notebooks) → Task 12 (integration). This ordering respects build dependencies.
