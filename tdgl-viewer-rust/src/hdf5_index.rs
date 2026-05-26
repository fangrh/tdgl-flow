use std::fs;
use serde::{Deserialize, Serialize};

/// Byte offset and size of a contiguous dataset within the HDF5 file.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DatasetLocation {
    pub offset: u64,
    pub size: u64,
    pub element_size: u64,
    pub shape: Vec<u64>,
}

impl Default for DatasetLocation {
    fn default() -> Self {
        DatasetLocation {
            offset: 0,
            size: 0,
            element_size: 0,
            shape: vec![],
        }
    }
}

/// Index of all TDGL datasets within an HDF5 file.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct H5Index {
    pub mesh_sites: DatasetLocation,
    pub mesh_edges: DatasetLocation,
    pub frame_psi_offsets: Vec<u64>,
    pub frame_mu_offsets: Vec<u64>,
    pub frame_rsmu_offsets: Vec<u64>,
    pub frame_rsdt_offsets: Vec<u64>,
    /// Byte sizes of running_state/dt arrays (K*8 per frame).
    #[serde(default)]
    pub frame_rsdt_sizes: Vec<u64>,
    pub frame_supercurrent_offsets: Vec<u64>,
    pub total_frames: usize,
    pub mesh_points: usize,
    pub frame_times: Vec<f64>,
    #[serde(default)]
    pub file_size: u64,
    /// Whether psi datasets use chunked+compressed storage.
    #[serde(default)]
    pub psi_compressed: Option<bool>,
}

/// Build an H5Index by loading the viewer-index.json sidecar from MinIO.
///
/// Caches the serialized index as JSON in the system temp directory.
/// On subsequent calls, loads the cached index directly (sub-millisecond).
pub fn build_index(client: &crate::minio::MinioClient, run_id: &str, log_fn: Option<&dyn Fn(&str)>) -> Result<H5Index, String> {
    macro_rules! log {
        ($($arg:tt)*) => {
            if let Some(f) = &log_fn { f(&format!($($arg)*)); }
        };
    }

    log!("build_index() run_id={}", run_id);
    let t0 = std::time::Instant::now();
    let index_cache_path = index_cache_path(run_id);

    match build_index_from_sidecar(client, run_id, log_fn)? {
        Some(index) => {
            log!("build_index() sidecar loaded: {} frames, file_size={}, psi_compressed={:?}, {:.3}s",
                index.total_frames, index.file_size, index.psi_compressed, t0.elapsed().as_secs_f64());
            let json = serde_json::to_string(&index)
                .map_err(|e| format!("Failed to serialize sidecar index: {}", e))?;
            let _ = fs::write(&index_cache_path, json);
            Ok(index)
        }
        None => {
            log!("build_index() sidecar NOT FOUND or invalid, {:.3}s", t0.elapsed().as_secs_f64());
            Err(format!(
                "viewer-index.json not found for run '{}'. \
                 The runner must generate it during simulation. \
                 Re-run the simulation or upload the sidecar index manually.",
                run_id
            ))
        }
    }
}

fn build_index_from_sidecar(
    client: &crate::minio::MinioClient,
    run_id: &str,
    log_fn: Option<&dyn Fn(&str)>,
) -> Result<Option<H5Index>, String> {
    macro_rules! log {
        ($($arg:tt)*) => {
            if let Some(f) = &log_fn { f(&format!($($arg)*)); }
        };
    }

    let key = client.viewer_index_key(run_id);
    let Some(json) = client.read_text_optional(&key)? else {
        log!("build_index_from_sidecar() key={} not found in MinIO", key);
        return Ok(None);
    };
    let index: H5Index =
        serde_json::from_str(&json).map_err(|e| format!("Failed to parse {}: {}", key, e))?;

    log!("build_index_from_sidecar() parsed OK: {} frames, file_size={}", index.total_frames, index.file_size);

    let h5_key = client.h5_key(run_id);
    let current_size = client.object_size(&h5_key).ok().flatten();
    if let (Some(cur), cached) = (current_size, index.file_size) {
        if cached > 0 && cur < cached {
            log!("build_index_from_sidecar() H5 size check failed: current={} < cached={}", cur, cached);
            return Ok(None);
        }
    }
    if index.total_frames == 0
        || index.frame_times.len() != index.total_frames
        || index.frame_psi_offsets.len() != index.total_frames
        || index.frame_mu_offsets.len() != index.total_frames
    {
        log!("build_index_from_sidecar() frame count invalid: total={}, psi={}, mu={}",
            index.total_frames, index.frame_psi_offsets.len(), index.frame_mu_offsets.len());
        return Ok(None);
    }
    Ok(Some(index))
}

/// Clear cached index for a specific run (or all runs).
pub fn clear_index_cache(run_id: Option<&str>) {
    if let Some(id) = run_id {
        let path = index_cache_path(id);
        let _ = fs::remove_file(path);
    } else {
        let temp_dir = std::env::temp_dir();
        if let Ok(entries) = fs::read_dir(&temp_dir) {
            for entry in entries.flatten() {
                let name = entry.file_name();
                let name = name.to_string_lossy();
                if name.starts_with("tdgl_idx_") && name.ends_with(".json") {
                    let _ = fs::remove_file(entry.path());
                }
            }
        }
    }
}

fn index_cache_path(run_id: &str) -> std::path::PathBuf {
    std::env::temp_dir().join(format!("tdgl_idx_{}.json", run_id))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_index_cache_path_format() {
        let path = index_cache_path("test-run-id");
        assert!(path.to_string_lossy().contains("tdgl_idx_test-run-id.json"));
    }
}
