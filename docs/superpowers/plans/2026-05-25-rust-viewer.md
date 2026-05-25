# tdgl-viewer-rust Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Rust-based TDGL simulation viewer (`tdgl_viewer_rust`) that reads HDF5 from MinIO via HTTP range requests, renders 2x2 panels in Rust, and displays in Jupyter via ipywidgets — replacing the performance-limited Python viewer.

**Architecture:** PyO3/maturin Python extension. Rust handles MinIO HTTP access, HDF5 binary parsing, griddata interpolation, colormap rendering, frame buffering with native threads. Python manages ipywidgets UI only.

**Tech Stack:** Rust (PyO3 0.22, reqwest, image, rayon), maturin build system, Python ipywidgets

**Design spec:** `docs/superpowers/specs/2026-05-25-rust-viewer-design.md`

---

## File Map

```
tdgl-viewer-rust/
├── Cargo.toml                         # PyO3 + dependencies
├── pyproject.toml                     # maturin build config
├── src/
│   ├── lib.rs                         # PyO3 module: TdglViewer class
│   ├── minio.rs                       # HTTP client: manifests, range reads
│   ├── hdf5_index.rs                  # HDF5 superblock + B-tree parsing
│   ├── frame_reader.rs                # Per-frame psi/mu reading
│   ├── renderer.rs                    # Interpolation, colormaps, composite, PNG
│   ├── buffer.rs                      # Ring buffer + prefetch thread
│   ├── iv.rs                          # V and I computation
│   ├── run_info.rs                    # RunInfo struct, manifest parsing
│   └── colormaps.rs                   # Inferno + RdBu_r LUT tables
├── python/
│   └── tdgl_viewer_rust/
│       └── __init__.py                # from tdgl_viewer_rust import TdglViewer
└── tests/
    ├── test_minio.rs
    ├── test_hdf5_index.rs
    ├── test_renderer.rs
    └── test_integration.py
```

---

### Task 1: Project Scaffold

**Files:**
- Create: `tdgl-viewer-rust/Cargo.toml`
- Create: `tdgl-viewer-rust/pyproject.toml`
- Create: `tdgl-viewer-rust/src/lib.rs`
- Create: `tdgl-viewer-rust/python/tdgl_viewer_rust/__init__.py`

- [ ] **Step 1: Create project directory and Cargo.toml**

```bash
mkdir -p tdgl-viewer-rust/src tdgl-viewer-rust/python/tdgl_viewer_rust tdgl-viewer-rust/tests
```

`tdgl-viewer-rust/Cargo.toml`:
```toml
[package]
name = "tdgl-viewer-rust"
version = "0.1.0"
edition = "2021"

[lib]
name = "tdgl_viewer_rust"
crate-type = ["cdylib"]

[dependencies]
pyo3 = { version = "0.22", features = ["extension-module"] }
numpy = "0.22"
reqwest = { version = "0.12", features = ["blocking"] }
tokio = { version = "1", features = ["rt-multi-thread"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
image = "0.25"
rayon = "1.10"
```

`tdgl-viewer-rust/pyproject.toml`:
```toml
[build-system]
requires = ["maturin>=1.0,<2.0"]
build-backend = "maturin"

[project]
name = "tdgl-viewer-rust"
requires-python = ">=3.9"

[tool.maturin]
features = ["pyo3/extension-module"]
```

- [ ] **Step 2: Create minimal PyO3 entry point**

`tdgl-viewer-rust/src/lib.rs`:
```rust
use pyo3::prelude::*;

#[pyclass]
struct TdglViewer {
    minio_url: String,
}

#[pymethods]
impl TdglViewer {
    #[new]
    fn new(minio_url: String) -> Self {
        TdglViewer { minio_url }
    }

    fn open(&mut self, run_id: Option<&str>, run_index: Option<usize>) -> PyResult<()> {
        Ok(())
    }

    fn display(&self) -> PyResult<()> {
        Ok(())
    }
}

#[pymodule]
fn tdgl_viewer_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TdglViewer>()?;
    Ok(())
}
```

`tdgl-viewer-rust/python/tdgl_viewer_rust/__init__.py`:
```python
from tdgl_viewer_rust.tdgl_viewer_rust import TdglViewer

__all__ = ["TdglViewer"]
```

- [ ] **Step 3: Build and verify import**

```bash
cd tdgl-viewer-rust && maturin develop
python -c "from tdgl_viewer_rust import TdglViewer; v = TdglViewer('http://localhost:30900'); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add tdgl-viewer-rust/
git commit -m "feat: scaffold tdgl-viewer-rust PyO3 project"
```

---

### Task 2: MinIO HTTP Client

**Files:**
- Create: `tdgl-viewer-rust/src/minio.rs`
- Create: `tdgl-viewer-rust/src/run_info.rs`
- Modify: `tdgl-viewer-rust/src/lib.rs`
- Test: `tdgl-viewer-rust/tests/test_minio.rs`

- [ ] **Step 1: Define RunInfo struct and manifest parsing**

`tdgl-viewer-rust/src/run_info.rs`:
```rust
use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub struct RunInfo {
    pub run_id: String,
    pub status: String,
    pub created_at: String,
    pub n_sites: Option<u64>,
    pub n_frames: Option<u64>,
    pub device_params: Option<DeviceParams>,
    pub timing_params: Option<TimingSummary>,
    pub raw_timing_params: Option<serde_json::Value>,
    pub timing_steps: Option<Vec<TimingStep>>,
    pub solver_options: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct DeviceParams {
    pub film_width: Option<f64>,
    pub film_height: Option<f64>,
    pub elec_width: Option<f64>,
    pub elec_height: Option<f64>,
    pub max_edge_length: Option<f64>,
    pub smooth: Option<f64>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct TimingSummary {
    pub mode: Option<String>,
    pub n_steps: Option<u64>,
    pub solve_time: Option<f64>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct TimingStep {
    pub ramp_start: f64,
    pub ramp_end: f64,
    pub stable_end: f64,
    #[serde(default)]
    pub je_start: f64,
    #[serde(default)]
    pub je_end: f64,
}

impl RunInfo {
    pub fn display_label(&self) -> String {
        let id = &self.run_id[..8.min(self.run_id.len())];
        let film = match &self.device_params {
            Some(dp) => format!("{}x{}", dp.film_width.unwrap_or(0.0), dp.film_height.unwrap_or(0.0)),
            None => "?".into(),
        };
        let je = match &self.raw_timing_params {
            Some(p) => {
                let ini = p.get("je_initial").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let fin = p.get("je_final").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let step = p.get("je_step").and_then(|v| v.as_f64()).unwrap_or(0.0);
                format!("Je {}->{} step={}", ini, fin, step)
            }
            None => "Je ?".into(),
        };
        let frames = self.n_frames.map(|n| format!("{}fr", n)).unwrap_or("-".into());
        format!("{} | {} | {} | {} | {}", id, film, je, frames, self.status)
    }
}
```

- [ ] **Step 2: Implement MinIO HTTP client**

`tdgl-viewer-rust/src/minio.rs`:
```rust
use crate::run_info::RunInfo;

pub struct MinioClient {
    endpoint: String,
    bucket: String,
    client: reqwest::blocking::Client,
}

impl MinioClient {
    pub fn new(endpoint: &str, bucket: &str) -> Self {
        MinioClient {
            endpoint: endpoint.trim_end_matches('/').to_string(),
            bucket: bucket.to_string(),
            client: reqwest::blocking::Client::new(),
        }
    }

    pub fn list_runs(&self) -> Result<Vec<RunInfo>, String> {
        let url = format!("{}/{}?list-type=2&prefix=tdgl-runs/&delimiter=/",
            self.endpoint, self.bucket);
        let resp = self.client.get(&url).send().map_err(|e| e.to_string())?;
        let body = resp.text().map_err(|e| e.to_string())?;
        // Parse ListBucketResult XML to find run prefixes
        let prefixes = extract_prefixes(&body);
        let mut runs = Vec::new();
        for prefix in prefixes {
            if let Some(run) = self.get_manifest_by_prefix(&prefix)? {
                runs.push(run);
            }
        }
        runs.sort_by(|a, b| b.created_at.cmp(&a.created_at));
        Ok(runs)
    }

    pub fn get_manifest(&self, run_id: &str) -> Result<Option<RunInfo>, String> {
        let url = format!("{}/{}/tdgl-runs/{}/manifest.json",
            self.endpoint, self.bucket, run_id);
        let resp = self.client.get(&url).send().map_err(|e| e.to_string())?;
        if resp.status() == reqwest::StatusCode::NOT_FOUND {
            return Ok(None);
        }
        let body = resp.text().map_err(|e| e.to_string())?;
        let run: RunInfo = serde_json::from_str(&body).map_err(|e| e.to_string())?;
        Ok(Some(run))
    }

    fn get_manifest_by_prefix(&self, prefix: &str) -> Result<Option<RunInfo>, String> {
        let url = format!("{}/{}?prefix={}&suffix=manifest.json&list-type=2",
            self.endpoint, self.bucket, prefix);
        let resp = self.client.get(&url).send().map_err(|e| e.to_string())?;
        let body = resp.text().map_err(|e| e.to_string())?;
        // Extract manifest key from XML, then fetch it
        if let Some(key) = extract_manifest_key(&body) {
            let url = format!("{}/{}", self.endpoint, key);
            let resp = self.client.get(&url).send().map_err(|e| e.to_string())?;
            if resp.status() == reqwest::StatusCode::NOT_FOUND {
                return Ok(None);
            }
            let body = resp.text().map_err(|e| e.to_string())?;
            let run: RunInfo = serde_json::from_str(&body).map_err(|e| e.to_string())?;
            Ok(Some(run))
        } else {
            Ok(None)
        }
    }

    pub fn read_range(&self, key: &str, offset: u64, length: u64) -> Result<Vec<u8>, String> {
        let url = format!("{}/{}", self.endpoint, key);
        let range = format!("bytes={}-{}", offset, offset + length - 1);
        let resp = self.client.get(&url)
            .header("Range", &range)
            .send()
            .map_err(|e| e.to_string())?;
        let bytes = resp.bytes().map_err(|e| e.to_string())?;
        Ok(bytes.to_vec())
    }

    pub fn h5_key(&self, run_id: &str) -> String {
        format!("tdgl-runs/{}/output.h5", run_id)
    }
}

fn extract_prefixes(xml: &str) -> Vec<String> {
    let mut prefixes = Vec::new();
    for part in xml.split("<CommonPrefixes>") {
        if let Some(end) = part.find("</CommonPrefixes>") {
            let inner = &part[..end];
            if let (Some(s), Some(e)) = (inner.find("<Prefix>"), inner.find("</Prefix>")) {
                prefixes.push(inner[s+8..e].to_string());
            }
        }
    }
    prefixes
}

fn extract_manifest_key(xml: &str) -> Option<String> {
    for part in xml.split("<Contents>") {
        if let Some(end) = part.find("</Contents>") {
            let inner = &part[..end];
            if inner.contains("manifest.json") {
                if let (Some(s), Some(e)) = (inner.find("<Key>"), inner.find("</Key>")) {
                    return Some(inner[s+5..e].to_string());
                }
            }
        }
    }
    None
}
```

- [ ] **Step 3: Wire MinioClient into TdglViewer**

Update `lib.rs` to add `list_runs` and `open` methods that use MinioClient. Add `use mod run_info; use mod minio;` module declarations.

- [ ] **Step 4: Write test for manifest parsing**

`tdgl-viewer-rust/tests/test_minio.rs`:
```rust
use tdgl_viewer_rust::run_info::RunInfo;

#[test]
fn test_parse_manifest() {
    let json = r#"{
        "run_id": "abc-123-def",
        "status": "completed",
        "created_at": "2026-05-25T10:00:00",
        "n_sites": 1500,
        "n_frames": 12400,
        "device_params": {"film_width": 6.0, "film_height": 4.0},
        "timing_params": {"mode": "step", "n_steps": 100},
        "raw_timing_params": {"je_initial": 0.0, "je_final": 20.0, "je_step": 0.2}
    }"#;
    let run: RunInfo = serde_json::from_str(json).unwrap();
    assert_eq!(run.run_id, "abc-123-def");
    assert_eq!(run.status, "completed");
    assert_eq!(run.n_frames, Some(12400));
    let label = run.display_label();
    assert!(label.contains("abc-123"));
    assert!(label.contains("6x4"));
    assert!(label.contains("0->20"));
}

#[test]
fn test_display_label_short_id() {
    let json = r#"{"run_id":"short","status":"running","created_at":"2026-01-01"}"#;
    let run: RunInfo = serde_json::from_str(json).unwrap();
    assert!(run.display_label().contains("short"));
}
```

- [ ] **Step 5: Build and run tests**

```bash
cd tdgl-viewer-rust && cargo test
```

Expected: 2 tests pass

- [ ] **Step 6: Commit**

```bash
git add tdgl-viewer-rust/
git commit -m "feat: add MinIO HTTP client and manifest parsing"
```

---

### Task 3: HDF5 Binary Parser

**Files:**
- Create: `tdgl-viewer-rust/src/hdf5_index.rs`
- Test: `tdgl-viewer-rust/tests/test_hdf5_index.rs`

This is the core challenge: parse HDF5 binary structure via HTTP range requests to locate dataset byte offsets without using any HDF5 library.

**HDF5 structures we parse:**

| Structure | Purpose |
|-----------|---------|
| Superblock (v0/1) | Find root group object header |
| Object header (v1) | Find symbol table + dataspace/datatype/layout messages |
| B-tree v1 | Navigate group hierarchy |
| Datatype message | Determine element size (float64 = 8 bytes, complex128 = 16 bytes) |
| Dataspace message | Determine array shape (N, 2) etc |
| Data layout message (contiguous) | Get byte offset + total size of raw data |

- [ ] **Step 1: Define HDF5 index types**

`tdgl-viewer-rust/src/hdf5_index.rs` — key types:

```rust
pub struct DatasetLocation {
    pub offset: u64,       // byte offset in file
    pub size: u64,         // total bytes
    pub element_size: u64, // bytes per element (8 for f64, 16 for c128)
    pub shape: Vec<u64>,   // e.g. [1500, 2] for sites
}

pub struct H5Index {
    pub mesh_sites: DatasetLocation,
    pub mesh_edges: DatasetLocation,
    pub mesh_directions: DatasetLocation,
    pub mesh_dual_lengths: DatasetLocation,
    pub frame_psi_offsets: Vec<u64>,     // byte offset of data/{i}/psi data
    pub frame_mu_offsets: Vec<u64>,      // byte offset of data/{i}/mu data
    pub frame_rsmu_offsets: Vec<u64>,    // byte offset of data/{i}/running_state/mu
    pub frame_rsdt_offsets: Vec<u64>,    // byte offset of data/{i}/running_state/dt
    pub frame_time_offsets: Vec<u64>,    // byte offset of data/{i} attr "time"
    pub total_frames: usize,
    pub mesh_points: usize,              // N (from sites shape[0])
}
```

- [ ] **Step 2: Implement superblock parser**

Parse the HDF5 superblock (first 48 bytes for v0, up to 96 bytes for v2). Extract root group object header address. Implementation reads bytes 0-255 from the file via HTTP range.

Key offsets for v0/v1 superblock:
- Byte 0: signature `\x89HDF\r\n\x1a\n` (8 bytes)
- Byte 8: superblock version (1 byte)
- For v0: root group address at offset 24 (8 bytes for 8-byte addressing)
- For v1: root group address at offset 32

```rust
pub fn parse_superblock(first_bytes: &[u8]) -> Result<u64, String> {
    if &first_bytes[0..8] != b"\x89HDF\r\n\x1a\n" {
        return Err("Not an HDF5 file".into());
    }
    let version = first_bytes[8];
    let sizes_offset = match version {
        0 => 13,
        1 => 13,
        2 => 9,
        _ => return Err(format!("Unsupported superblock version {}", version)),
    };
    let off_size = first_bytes[sizes_offset] as usize;
    let _len_size = first_bytes[sizes_offset + 1] as usize;
    // For v0/v1: root group address follows group_leaf/node sizes
    let root_addr_offset = match version {
        0 => 8 + 4 + off_size * 2 + 16,
        1 => 8 + 4 + off_size * 2 + 16,
        2 => 8 + 1 + 1 + off_size * 2 + off_size + off_size,
        _ => return Err("bad version".into()),
    };
    // Read root address using off_size bytes (little-endian)
    let addr = read_le_uint(&first_bytes[root_addr_offset..], off_size);
    Ok(addr)
}
```

- [ ] **Step 3: Implement object header and B-tree traversal**

Parse object headers to find datasets by navigating the group hierarchy. For each group, find its symbol table B-tree, then traverse it to find child objects.

Key helper functions:
- `parse_object_header(bytes) -> Vec<Message>` — parse v1 object header, return list of messages (symbol table, dataspace, datatype, data layout, attribute)
- `traverse_btree(client, h5_key, root_addr) -> HashMap<String, u64>` — recursively traverse B-tree to build a map of "path → object_header_addr"
- `parse_dataset_info(client, h5_key, obj_addr) -> DatasetLocation` — read object header at addr, extract dataspace + datatype + layout messages

The traversal builds a flat map like:
```
"solution/device/mesh/sites" -> 0x1234
"solution/device/mesh/edge_mesh/edges" -> 0x5678
"data/0/psi" -> 0x9ABC
...
```

- [ ] **Step 4: Build H5Index from traversal**

After traversal, for each known path, read the object header and extract:
- From dataspace message: array shape (N, 2) etc
- From datatype message: element size (8 for float64, 16 for complex128)
- From data layout message: byte offset and total size

For frames, detect the pattern: scan all `data/{i}` entries, extract offsets for `psi`, `mu`, `running_state/mu`, `running_state/dt`, and attribute `time`.

- [ ] **Step 5: Write unit tests with a real HDF5 file**

Download a small HDF5 file from MinIO and test the parser:

```bash
# First generate a small test file via the existing Python viewer
python -c "
from tdgl_sdk.client import TDGLRunStore
store = TDGLRunStore()
runs = store.list_runs()
if runs:
    store.download_h5(runs[0]['run_id'], 'tdgl-viewer-rust/tests/test_data.h5')
"
```

`tdgl-viewer-rust/tests/test_hdf5_index.rs`:
```rust
use std::fs;

#[test]
fn test_parse_superblock() {
    let data = fs::read("tests/test_data.h5").expect("test HDF5 file missing");
    let result = tdgl_viewer_rust::hdf5_index::parse_superblock(&data[..256.min(data.len())]);
    assert!(result.is_ok(), "superblock parse failed: {:?}", result);
    let root_addr = result.unwrap();
    assert!(root_addr > 0, "root addr should be > 0, got {}", root_addr);
}

#[test]
fn test_find_mesh_sites() {
    let data = fs::read("tests/test_data.h5").expect("test HDF5 file missing");
    let index = tdgl_viewer_rust::hdf5_index::build_index_from_bytes(&data)
        .expect("index build failed");
    assert!(index.mesh_points > 0, "mesh_points should be > 0");
    assert!(index.total_frames > 0, "total_frames should be > 0");
}
```

- [ ] **Step 6: Commit**

```bash
git add tdgl-viewer-rust/
git commit -m "feat: add HDF5 binary parser for dataset offset lookup"
```

---

### Task 4: Frame and Mesh Reader

**Files:**
- Create: `tdgl-viewer-rust/src/frame_reader.rs`
- Modify: `tdgl-viewer-rust/src/lib.rs`

- [ ] **Step 1: Implement frame reader**

`tdgl-viewer-rust/src/frame_reader.rs` uses `MinioClient.read_range()` + `H5Index` offsets to read raw arrays:

```rust
use crate::hdf5_index::H5Index;
use crate::minio::MinioClient;

pub struct FrameReader<'a> {
    client: &'a MinioClient,
    h5_key: String,
    index: &'a H5Index,
}

impl<'a> FrameReader<'a> {
    pub fn new(client: &'a MinioClient, run_id: &str, index: &'a H5Index) -> Self {
        FrameReader {
            client,
            h5_key: client.h5_key(run_id),
            index,
        }
    }

    pub fn read_psi(&self, frame: usize) -> Result<Vec<[f64; 2]>, String> {
        let loc = &self.index.frame_psi_offsets.get(frame)
            .ok_or_else(|| format!("frame {} out of range", frame))?;
        let bytes = self.client.read_range(
            &self.h5_key, *loc, self.index.mesh_points as u64 * 16)?;
        // complex128 = two f64s (real, imag), parse as pairs
        let mut result = Vec::with_capacity(self.index.mesh_points);
        for chunk in bytes.chunks_exact(16) {
            let re = f64::from_le_bytes([chunk[0],chunk[1],chunk[2],chunk[3],chunk[4],chunk[5],chunk[6],chunk[7]]);
            let im = f64::from_le_bytes([chunk[8],chunk[9],chunk[10],chunk[11],chunk[12],chunk[13],chunk[14],chunk[15]]);
            result.push([re, im]);
        }
        Ok(result)
    }

    pub fn read_mu(&self, frame: usize) -> Result<Vec<f64>, String> {
        let offset = self.index.frame_mu_offsets.get(frame)
            .ok_or_else(|| format!("frame {} out of range", frame))?;
        let bytes = self.client.read_range(
            &self.h5_key, *offset, self.index.mesh_points as u64 * 8)?;
        parse_f64_array(&bytes)
    }

    pub fn read_mesh_sites(&self) -> Result<Vec<[f64; 2]>, String> {
        let loc = &self.index.mesh_sites;
        let bytes = self.client.read_range(
            &self.h5_key, loc.offset, loc.size)?;
        let mut sites = Vec::with_capacity(self.index.mesh_points);
        for chunk in bytes.chunks_exact(16) {
            let x = f64::from_le_bytes(chunk[0..8].try_into().unwrap());
            let y = f64::from_le_bytes(chunk[8..16].try_into().unwrap());
            sites.push([x, y]);
        }
        Ok(sites)
    }

    pub fn read_frame_time(&self, frame: usize) -> Result<f64, String> {
        // Read the "time" attribute from data/{frame} group
        // Attributes are stored in the object header after the group's messages
        let offset = self.index.frame_time_offsets.get(frame)
            .ok_or_else(|| format!("frame {} out of range", frame))?;
        let bytes = self.client.read_range(&self.h5_key, *offset, 8)?;
        Ok(f64::from_le_bytes(bytes[0..8].try_into().unwrap()))
    }
}

fn parse_f64_array(bytes: &[u8]) -> Result<Vec<f64>, String> {
    if bytes.len() % 8 != 0 {
        return Err("not aligned to f64".into());
    }
    Ok(bytes.chunks_exact(8)
        .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
        .collect())
}
```

- [ ] **Step 2: Test frame reading with real MinIO data**

Write a quick test that reads frame 0 psi/mu from a real run. This is an integration test that requires MinIO port-forward to be active.

- [ ] **Step 3: Commit**

```bash
git add tdgl-viewer-rust/
git commit -m "feat: add frame and mesh reader via HTTP range requests"
```

---

### Task 5: Renderer

**Files:**
- Create: `tdgl-viewer-rust/src/renderer.rs`
- Create: `tdgl-viewer-rust/src/colormaps.rs`
- Test: `tdgl-viewer-rust/tests/test_renderer.rs`

- [ ] **Step 1: Generate colormap lookup tables**

`tdgl-viewer-rust/src/colormaps.rs` — pre-computed 256-entry RGBA tables for inferno and RdBu_r colormaps. Generate these from matplotlib's published colormap data (hardcode the 256 RGB values).

```rust
pub const INFERNO: [[u8; 4]; 256] = include!("../data/inferno.in");
pub const RDBU_R: [[u8; 4]; 256] = include!("../data/rdbu_r.in");
```

Generate the `.in` files using a Python script that runs once:
```python
import matplotlib.cm, numpy as np
for name, path in [("inferno", "inferno.in"), ("RdBu_r", "rdbu_r.in")]:
    cmap = matplotlib.colormaps[name]
    vals = [cmap(i/255.0) for i in range(256)]
    lines = [f"[{int(r*255)},{int(g*255)},{int(b*255)},255]" for r,g,b,_ in vals]
    with open(f"src/data/{path}", "w") as f:
        f.write("[\n" + ",\n".join(lines) + "\n]")
```

- [ ] **Step 2: Implement griddata cubic interpolation**

`tdgl-viewer-rust/src/renderer.rs` — cubic interpolation from unstructured mesh points to a regular 100×50 grid. Use a simplified approach: for each grid point, find K nearest mesh sites and compute weighted average (Shepard's method with power parameter p=3), or implement Delaunay-based cubic interpolation.

For the MVP, use inverse distance weighting (faster than Delaunay cubic, good enough for visualization):

```rust
pub fn interpolate_idw(sites: &[[f64; 2]], values: &[f64],
                       grid_pts: &[[f64; 2]], power: f64) -> Vec<f64> {
    grid_pts.iter().map(|&gp| {
        let mut w_sum = 0.0;
        let mut v_sum = 0.0;
        for (i, &site) in sites.iter().enumerate() {
            let dx = gp[0] - site[0];
            let dy = gp[1] - site[1];
            let dist = (dx * dx + dy * dy).sqrt().max(1e-12);
            let w = 1.0 / dist.powf(power);
            w_sum += w;
            v_sum += w * values[i];
        }
        v_sum / w_sum
    }).collect()
}
```

- [ ] **Step 3: Implement apply_colormap**

Map normalized values [0,1] to RGBA using LUT:

```rust
pub fn apply_colormap(values: &[f64], lut: &[[u8; 4]; 256]) -> Vec<u8> {
    values.iter().flat_map(|&v| {
        let idx = ((v * 255.0).round() as usize).clamp(0, 255);
        lut[idx]
    }).collect()
}
```

- [ ] **Step 4: Implement 2x2 canvas composite**

```rust
const FRAME_W: u32 = 760;
const FRAME_H: u32 = 470;
const PANEL_W: u32 = 360;
const PANEL_H: u32 = 180;
const NX: usize = 100;
const NY: usize = 50;

pub fn render_frame_2x2(
    psi_raw: &[f64],      // |psi| values at mesh sites
    mu_raw: &[f64],       // mu values at mesh sites
    mu_vmax: f64,
    frame_idx: usize,
    total_frames: usize,
) -> Vec<u8> {
    let mut canvas = vec![30u8; (FRAME_W * FRAME_H * 4) as usize]; // dark gray BG

    // Psi panel: normalize to [0, 1] using PSI_VMAX=1.05
    let psi_norm: Vec<f64> = psi_raw.iter().map(|&v| (v / 1.05).clamp(0.0, 1.0)).collect();
    let psi_rgba = apply_colormap(&psi_norm, &colormaps::INFERNO);
    blit_panel(&mut canvas, &psi_rgba, 14, 42, NX, NY, PANEL_W, PANEL_H);

    // Mu panel: normalize to [0, 1] using ±mu_vmax
    let mu_norm: Vec<f64> = mu_raw.iter()
        .map(|&v| ((v + mu_vmax) / (2.0 * mu_vmax)).clamp(0.0, 1.0))
        .collect();
    let mu_rgba = apply_colormap(&mu_norm, &colormaps::RDBU_R);
    blit_panel(&mut canvas, &mu_rgba, 386, 42, NX, NY, PANEL_W, PANEL_H);

    // Encode to PNG
    encode_png(&canvas, FRAME_W, FRAME_H)
}

fn blit_panel(canvas: &mut [u8], rgba: &[u8], x0: u32, y0: u32,
              src_w: usize, src_h: usize, dst_w: u32, dst_h: u32) {
    // Nearest-neighbor resize + blit
    for dy in 0..dst_h {
        let sy = (dy as usize * src_h / dst_h as usize).min(src_h - 1);
        for dx in 0..dst_w {
            let sx = (dx as usize * src_w / dst_w as usize).min(src_w - 1);
            let src_idx = (sy * src_w + sx) * 4;
            let dst_idx = ((y0 + dy) * FRAME_W + x0 + dx) as usize * 4;
            canvas[dst_idx..dst_idx+4].copy_from_slice(&rgba[src_idx..src_idx+4]);
        }
    }
}

fn encode_png(rgba: &[u8], w: u32, h: u32) -> Vec<u8> {
    let img = image::RgbaImage::from_raw(w, h, rgba.to_vec()).unwrap();
    let mut buf = std::io::Cursor::new(Vec::new());
    img.write_to(&mut buf, image::ImageFormat::Png).unwrap();
    buf.into_inner()
}
```

- [ ] **Step 5: Write renderer unit test**

`tdgl-viewer-rust/tests/test_renderer.rs`:
```rust
#[test]
fn test_render_frame_produces_png() {
    let psi: Vec<f64> = vec![0.5; 1500]; // |psi| all 0.5
    let mu: Vec<f64> = vec![0.0; 1500];  // mu all 0
    let png = tdgl_viewer_rust::renderer::render_frame_2x2(
        &psi, &mu, 1.0, 0, 100);
    // PNG magic bytes
    assert_eq!(&png[0..4], &[0x89, 0x50, 0x4E, 0x47]);
    assert!(png.len() > 1000);
}
```

- [ ] **Step 6: Commit**

```bash
git add tdgl-viewer-rust/
git commit -m "feat: add renderer with colormaps, interpolation, PNG output"
```

---

### Task 6: IV Computation

**Files:**
- Create: `tdgl-viewer-rust/src/iv.rs`
- Modify: `tdgl-viewer-rust/src/frame_reader.rs`

- [ ] **Step 1: Implement voltage computation from running_state**

```rust
pub struct FrameIV {
    pub current: f64,
    pub voltage: f64,
    pub time: f64,
}

pub fn compute_frame_voltage(rsmu: &[f64], rsdt: &[f64]) -> f64 {
    // rsmu is (2, K) flattened: [mu0_0, mu0_1, ..., mu0_K, mu1_0, mu1_1, ..., mu1_K]
    // rsdt is (K,) flattened
    let k = rsdt.len();
    if k == 0 { return f64::NAN; }
    let voltage_samples: Vec<f64> = (0..k).map(|i| rsmu[i] - rsmu[k + i]).collect();
    let dt_sum: f64 = rsdt.iter().sum();
    if dt_sum > 0.0 {
        voltage_samples.iter().zip(rsdt.iter())
            .map(|(v, dt)| v * dt).sum::<f64>() / dt_sum
    } else {
        voltage_samples.iter().sum::<f64>() / k as f64
    }
}
```

- [ ] **Step 2: Add running_state reading to frame_reader**

Add `read_running_state` method to `FrameReader` that reads `data/{frame}/running_state/mu` and `data/{frame}/running_state/dt` arrays.

- [ ] **Step 3: Write test**

```rust
#[test]
fn test_voltage_computation() {
    let rsmu = vec![1.0, 2.0, 3.0, 0.5, 1.0, 1.5]; // 2x3: row0=[1,2,3], row1=[0.5,1,1.5]
    let rsdt = vec![0.1, 0.2, 0.1];
    let v = tdgl_viewer_rust::iv::compute_frame_voltage(&rsmu, &rsdt);
    // voltage_samples = [0.5, 1.0, 1.5], dt_weighted = (0.5*0.1 + 1.0*0.2 + 1.5*0.1) / 0.4
    let expected = (0.05 + 0.2 + 0.15) / 0.4;
    assert!((v - expected).abs() < 1e-10);
}
```

- [ ] **Step 4: Commit**

```bash
git add tdgl-viewer-rust/
git commit -m "feat: add IV computation from running_state data"
```

---

### Task 7: Frame Buffer with Prefetch

**Files:**
- Create: `tdgl-viewer-rust/src/buffer.rs`

- [ ] **Step 1: Implement ring buffer with background prefetch**

```rust
use std::collections::HashMap;
use std::sync::{Arc, Mutex, Condvar};
use std::thread;

pub struct FrameBuffer {
    frames: Mutex<HashMap<usize, Vec<u8>>>,  // frame_idx -> PNG bytes
    capacity: usize,
    // Prefetch thread signals
    prefetch_cmd: Mutex<Option<PrefetchCommand>>,
    prefetch_signal: Condvar,
    stop: Mutex<bool>,
}

struct PrefetchCommand {
    center: usize,
    direction: i32,  // +1 forward, -1 backward
    ahead: usize,    // how many frames to prefetch
}

impl FrameBuffer {
    pub fn new(capacity: usize) -> Self {
        FrameBuffer {
            frames: Mutex::new(HashMap::new()),
            capacity,
            prefetch_cmd: Mutex::new(None),
            prefetch_signal: Condvar::new(),
            stop: Mutex::new(false),
        }
    }

    pub fn get(&self, idx: usize) -> Option<Vec<u8>> {
        self.frames.lock().unwrap().get(&idx).cloned()
    }

    pub fn insert(&self, idx: usize, png: Vec<u8>) {
        let mut frames = self.frames.lock().unwrap();
        frames.insert(idx, png);
        // Evict oldest entries beyond capacity
        while frames.len() > self.capacity {
            if let Some(k) = frames.keys().min().copied() {
                frames.remove(&k);
            }
        }
    }

    pub fn request_prefetch(&self, center: usize, direction: i32, ahead: usize) {
        *self.prefetch_cmd.lock().unwrap() = Some(PrefetchCommand { center, direction, ahead });
        self.prefetch_signal.notify_one();
    }

    pub fn stop(&self) {
        *self.stop.lock().unwrap() = true;
        self.prefetch_signal.notify_all();
    }
}
```

- [ ] **Step 2: Commit**

```bash
git add tdgl-viewer-rust/
git commit -m "feat: add ring buffer with prefetch signaling"
```

---

### Task 8: PyO3 TdglViewer Integration

**Files:**
- Modify: `tdgl-viewer-rust/src/lib.rs`

- [ ] **Step 1: Wire all modules into TdglViewer**

Update `lib.rs` to declare all modules and implement the full `TdglViewer` class:

```rust
mod minio;
mod hdf5_index;
mod frame_reader;
mod renderer;
mod buffer;
mod iv;
mod run_info;
mod colormaps;

use pyo3::prelude::*;
use minio::MinioClient;
use hdf5_index::H5Index;
use buffer::FrameBuffer;

#[pyclass]
struct TdglViewer {
    minio_url: String,
    client: MinioClient,
    runs: Vec<run_info::RunInfo>,
    current_run_index: Option<usize>,
    index: Option<H5Index>,
    buffer: FrameBuffer,
}

#[pymethods]
impl TdglViewer {
    #[new]
    fn new(minio_url: String) -> Self {
        let client = MinioClient::new(&minio_url, "tdgl-results");
        TdglViewer {
            minio_url,
            client,
            runs: Vec::new(),
            current_run_index: None,
            index: None,
            buffer: FrameBuffer::new(11),  // ±5 + current
        }
    }

    fn list_runs(&mut self) -> PyResult<Vec<String>> {
        self.runs = self.client.list_runs().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(e)
        })?;
        Ok(self.runs.iter().map(|r| r.display_label()).collect())
    }

    fn open(&mut self, run_id: Option<&str>, run_index: Option<usize>) -> PyResult<()> {
        // Find run by id or index, then index its HDF5
        if self.runs.is_empty() {
            self.list_runs()?;
        }
        let idx = match (run_id, run_index) {
            (Some(id), _) => self.runs.iter().position(|r| r.run_id == id)
                .ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("run {} not found", id)))?,
            (None, Some(i)) => i.min(self.runs.len() - 1),
            (None, None) => 0,
        };
        self.current_run_index = Some(idx);
        let run = &self.runs[idx];
        // Build HDF5 index via HTTP range reads
        self.index = Some(hdf5_index::build_index(&self.client, &run.run_id)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?);
        Ok(())
    }

    fn render_frame(&self, frame_idx: usize) -> PyResult<Vec<u8>> {
        // Check buffer first
        if let Some(png) = self.buffer.get(frame_idx) {
            return Ok(png);
        }
        let index = self.index.as_ref()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let run_id = &self.runs[self.current_run_index.unwrap()].run_id;
        let reader = frame_reader::FrameReader::new(&self.client, run_id, index);
        let psi = reader.read_psi(frame_idx).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        let mu = reader.read_mu(frame_idx).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        let psi_abs: Vec<f64> = psi.iter().map(|[re, im]| (re*re + im*im).sqrt()).collect();
        let png = renderer::render_frame_2x2(&psi_abs, &mu, 1.0, frame_idx, index.total_frames);
        Ok(png)
    }

    fn total_frames(&self) -> PyResult<usize> {
        Ok(self.index.as_ref().map(|i| i.total_frames).unwrap_or(0))
    }

    fn display(&self) -> PyResult<()> {
        // TODO: ipywidgets UI (Task 9)
        Ok(())
    }
}
```

- [ ] **Step 2: Build and test import**

```bash
cd tdgl-viewer-rust && maturin develop --release
python -c "
from tdgl_viewer_rust import TdglViewer
v = TdglViewer('http://localhost:30900')
runs = v.list_runs()
print(f'Found {len(runs)} runs')
if runs:
    print(f'First: {runs[0]}')
"
```

- [ ] **Step 3: Commit**

```bash
git add tdgl-viewer-rust/
git commit -m "feat: wire all Rust modules into PyO3 TdglViewer class"
```

---

### Task 9: ipywidgets UI

**Files:**
- Modify: `tdgl-viewer-rust/python/tdgl_viewer_rust/__init__.py`
- Create: `tdgl-viewer-rust/python/tdgl_viewer_rust/widget.py`

This task builds the Python-side ipywidgets interface that calls into the Rust backend.

- [ ] **Step 1: Create widget module**

`tdgl-viewer-rust/python/tdgl_viewer_rust/widget.py`:
```python
import threading
import time
import ipywidgets as widgets
from IPython.display import display

from tdgl_viewer_rust.tdgl_viewer_rust import TdglViewer as _RustViewer

FRAME_W = 760

class TdglViewer:
    def __init__(self, minio_url="http://localhost:30900"):
        self._rust = _RustViewer(minio_url)
        self._playing = False
        self._stop = threading.Event()
        self._thread = None

    def list_runs(self):
        return self._rust.list_runs()

    def open(self, run_id=None, run_index=None):
        self._rust.open(run_id=run_id, run_index=run_index)

    def display(self):
        # Build widgets
        runs = self._rust.list_runs()
        run_dropdown = widgets.Dropdown(
            options=[(label, i) for i, label in enumerate(runs)],
            description="Run:",
            layout=widgets.Layout(width="clamp(400px, 60vw, 800px)"),
        )
        total = self._rust.total_frames()
        image = widgets.Image(format="png", width=FRAME_W)
        play_btn = widgets.Button(description="Play", icon="play", layout=widgets.Layout(width="92px"))
        slider = widgets.IntSlider(
            value=0, min=0, max=max(0, total - 1), step=1,
            continuous_update=False, layout=widgets.Layout(width="500px"),
        )
        time_label = widgets.Label(value="frame 0 / 0", layout=widgets.Layout(width="220px"))
        fps_slider = widgets.IntSlider(value=10, min=1, max=30, description="FPS",
            continuous_update=False, layout=widgets.Layout(width="180px"))
        speed_input = widgets.IntText(value=1, description="Speed",
            layout=widgets.Layout(width="120px"))
        status = widgets.Label(value="ready")

        # Render first frame
        if total > 0:
            self._rust.open(run_index=run_dropdown.value if run_dropdown.value is not None else 0)
            total = self._rust.total_frames()
            slider.max = max(0, total - 1)
            png = self._rust.render_frame(0)
            image.value = png

        def on_dropdown(change):
            idx = change["new"]
            self._rust.open(run_index=idx)
            total = self._rust.total_frames()
            slider.max = max(0, total - 1)
            slider.value = 0
            _render(0)

        def on_slider(change):
            _render(change["new"])

        def on_play(_):
            if self._playing:
                self._pause()
            else:
                self._play(slider, image, status, total)

        def _render(idx):
            idx = max(0, min(idx, self._rust.total_frames() - 1))
            png = self._rust.render_frame(idx)
            image.value = png
            slider.value = idx
            time_label.value = f"frame {idx} / {self._rust.total_frames() - 1}"
            status.value = f"frame {idx}/{self._rust.total_frames() - 1}"

        run_dropdown.observe(on_dropdown, names="value")
        slider.observe(on_slider, names="value")
        play_btn.on_click(on_play)

        ui = widgets.VBox([
            run_dropdown,
            widgets.HBox([play_btn, slider, time_label]),
            widgets.HBox([fps_slider, speed_input, status]),
            image,
        ])
        display(ui)

    def _play(self, slider, image, status, total):
        self._playing = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, args=(slider, image, status), daemon=True)
        self._thread.start()

    def _pause(self):
        self._playing = False
        self._stop.set()

    def _loop(self, slider, image, status):
        while not self._stop.is_set():
            current = slider.value
            next_frame = current + 1
            total = self._rust.total_frames()
            if next_frame >= total:
                self._stop.set()
                break
            t0 = time.perf_counter()
            png = self._rust.render_frame(next_frame)
            image.value = png
            slider.value = next_frame
            elapsed = time.perf_counter() - t0
            remaining = max(0.0, 0.1 - elapsed)  # 10 FPS
            self._stop.wait(remaining)

    def get_iv_data(self):
        # Returns dict with I, V arrays
        raise NotImplementedError("IV data retrieval coming in next iteration")
```

- [ ] **Step 2: Update __init__.py**

`tdgl-viewer-rust/python/tdgl_viewer_rust/__init__.py`:
```python
from tdgl_viewer_rust.widget import TdglViewer

__all__ = ["TdglViewer"]
```

- [ ] **Step 3: Test in Jupyter**

```bash
cd tdgl-viewer-rust && maturin develop --release
```

In Jupyter:
```python
from tdgl_viewer_rust import TdglViewer
v = TdglViewer()
v.open(run_index=0)
v.display()
```

- [ ] **Step 4: Commit**

```bash
git add tdgl-viewer-rust/
git commit -m "feat: add ipywidgets UI with run selector and playback controls"
```

---

### Task 10: End-to-End Integration Test

**Files:**
- Create: `tdgl-viewer-rust/tests/test_integration.py`

- [ ] **Step 1: Write integration test**

`tdgl-viewer-rust/tests/test_integration.py`:
```python
import pytest

def test_list_runs():
    from tdgl_viewer_rust import TdglViewer
    v = TdglViewer("http://localhost:30900")
    runs = v.list_runs()
    assert isinstance(runs, list)

def test_open_and_render():
    from tdgl_viewer_rust import TdglViewer
    v = TdglViewer("http://localhost:30900")
    runs = v.list_runs()
    if not runs:
        pytest.skip("No runs in MinIO")
    v.open(run_index=0)
    total = v.total_frames()
    assert total > 0
    png = v.render_frame(0)
    assert png[:4] == b"\x89PNG"
```

- [ ] **Step 2: Run integration test**

```bash
cd tdgl-viewer-rust && maturin develop --release
python -m pytest tests/test_integration.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tdgl-viewer-rust/
git commit -m "test: add end-to-end integration test"
```
