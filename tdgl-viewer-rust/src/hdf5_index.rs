use std::fs;
use std::io::Write;
use std::path::Path;

use hdf5::Dataset;
use hdf5_sys::h5d::{H5Dget_create_plist, H5Dget_offset};
use hdf5_sys::h5p::{H5Pclose, H5Pget_nfilters};
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

/// Get the byte offset of a dataset's raw data in the file.
fn dataset_offset(ds: &Dataset) -> u64 {
    unsafe { H5Dget_offset(ds.id()) as u64 }
}

/// Check if a dataset uses chunked storage with filters (e.g. gzip compression).
fn is_dataset_compressed(ds: &Dataset) -> bool {
    unsafe {
        let dcpl = H5Dget_create_plist(ds.id());
        if dcpl < 0 {
            return false;
        }
        let n_filters = H5Pget_nfilters(dcpl);
        H5Pclose(dcpl);
        n_filters > 0
    }
}

/// Build H5Index by parsing the HDF5 file with the hdf5 crate.
pub fn build_index_from_file(path: &Path) -> Result<H5Index, String> {
    let file = hdf5::File::open(path).map_err(|e| format!("Failed to open HDF5 file: {}", e))?;

    let file_size = fs::metadata(path).map(|m| m.len()).unwrap_or(0);

    let data_group = file
        .group("data")
        .map_err(|e| format!("No 'data' group: {}", e))?;

    // Collect and sort frame indices
    let mut frame_indices: Vec<usize> = data_group
        .member_names()
        .map_err(|e| format!("Failed to list data members: {}", e))?
        .iter()
        .filter_map(|n| n.parse::<usize>().ok())
        .collect();
    frame_indices.sort_unstable();
    let total_frames = frame_indices.len();

    if total_frames == 0 {
        return Err("No frames found in HDF5 file".into());
    }

    // Determine n_sites from first frame's psi dataset
    let first_group = data_group
        .group(&frame_indices[0].to_string())
        .map_err(|e| format!("Failed to open first frame: {}", e))?;
    let psi_ds = first_group
        .dataset("psi")
        .map_err(|e| format!("No psi in frame 0: {}", e))?;
    let psi_shape = psi_ds.shape();
    let n_sites = psi_shape[0];
    let n_sites_u64 = n_sites as u64;

    let mut frame_psi_offsets = Vec::with_capacity(total_frames);
    let mut frame_mu_offsets = Vec::with_capacity(total_frames);
    let mut frame_rsmu_offsets = vec![0u64; total_frames];
    let mut frame_rsdt_offsets = vec![0u64; total_frames];
    let mut frame_rsdt_sizes = vec![0u64; total_frames];
    let mut frame_supercurrent_offsets = vec![0u64; total_frames];
    let mut frame_times = Vec::with_capacity(total_frames);

    let mut cumulative_time = 0.0f64;

    for (fi_idx, &fi) in frame_indices.iter().enumerate() {
        let group_name = fi.to_string();
        let group = data_group
            .group(&group_name)
            .map_err(|e| format!("Failed to open data/{}: {}", fi, e))?;

        // psi
        let psi = group
            .dataset("psi")
            .map_err(|e| format!("No psi in frame {}: {}", fi, e))?;
        frame_psi_offsets.push(dataset_offset(&psi));

        // mu
        if let Ok(mu) = group.dataset("mu") {
            frame_mu_offsets.push(dataset_offset(&mu));
        } else {
            frame_mu_offsets.push(0);
        }

        // supercurrent (optional)
        if let Ok(sc) = group.dataset("supercurrent") {
            frame_supercurrent_offsets[fi_idx] = dataset_offset(&sc);
        }

        // running_state
        if let Ok(rs) = group.group("running_state") {
            if let Ok(rsmu) = rs.dataset("mu") {
                frame_rsmu_offsets[fi_idx] = dataset_offset(&rsmu);
            }
            if let Ok(rsdt) = rs.dataset("dt") {
                frame_rsdt_offsets[fi_idx] = dataset_offset(&rsdt);
                let dt_shape = rsdt.shape();
                let k: u64 = dt_shape.iter().map(|&d| d as u64).product();
                frame_rsdt_sizes[fi_idx] = k * 8;
                // Read dt values to compute cumulative time
                if let Ok(dt_arr) = rsdt.read_1d::<f64>() {
                    cumulative_time += dt_arr.iter().sum::<f64>();
                }
            }
        }

        frame_times.push(cumulative_time);
    }

    // mesh_sites — try common locations
    let mesh_sites_loc = find_mesh_sites(&file, n_sites_u64);

    // Detect compression on psi dataset
    let psi_compressed = first_group
        .dataset("psi")
        .ok()
        .map(|ds| is_dataset_compressed(&ds));

    // mesh_edges
    let mesh_edges_loc = find_mesh_edges(&file);

    Ok(H5Index {
        mesh_sites: mesh_sites_loc,
        mesh_edges: mesh_edges_loc,
        frame_psi_offsets,
        frame_mu_offsets,
        frame_rsmu_offsets,
        frame_rsdt_offsets,
        frame_rsdt_sizes,
        frame_supercurrent_offsets,
        total_frames,
        mesh_points: n_sites,
        frame_times,
        file_size,
        psi_compressed,
    })
}

fn find_mesh_sites(file: &hdf5::File, n_sites: u64) -> DatasetLocation {
    // Try common paths: tdgl canonical, cpp-tdgl, and short aliases
    for path in &[
        "solution/device/mesh/sites",
        "mesh/sites",
        "sites",
        "mesh_sites",
    ] {
        if let Ok(ds) = file.dataset(path) {
            let shape: Vec<u64> = ds.shape().into_iter().map(|d| d as u64).collect();
            let nbytes: u64 = shape.iter().product::<u64>() * 8;
            return DatasetLocation {
                offset: dataset_offset(&ds),
                size: nbytes,
                element_size: 8,
                shape,
            };
        }
    }
    // Fallback: construct from n_sites
    DatasetLocation {
        offset: 0,
        size: n_sites * 2 * 8,
        element_size: 8,
        shape: vec![n_sites, 2],
    }
}

fn find_mesh_edges(file: &hdf5::File) -> DatasetLocation {
    for path in &["mesh/edges", "edges", "mesh_edges"] {
        if let Ok(ds) = file.dataset(path) {
            let shape: Vec<u64> = ds.shape().into_iter().map(|d| d as u64).collect();
            let nbytes: u64 = shape.iter().product::<u64>() * 8;
            return DatasetLocation {
                offset: dataset_offset(&ds),
                size: nbytes,
                element_size: 8,
                shape,
            };
        }
    }
    DatasetLocation::default()
}

/// Build an H5Index by downloading the file from MinIO and parsing with hdf5.
///
/// Caches the serialized index as JSON in the system temp directory.
/// On subsequent calls, loads the cached index directly (sub-millisecond).
pub fn build_index(client: &crate::minio::MinioClient, run_id: &str) -> Result<H5Index, String> {
    let index_cache_path = index_cache_path(run_id);

    if let Some(index) = build_index_from_sidecar(client, run_id)? {
        let json = serde_json::to_string(&index)
            .map_err(|e| format!("Failed to serialize sidecar index: {}", e))?;
        let _ = fs::write(&index_cache_path, json);
        return Ok(index);
    }

    // Try loading cached index, validate against current file size
    if index_cache_path.exists() {
        let json = fs::read_to_string(&index_cache_path)
            .map_err(|e| format!("Failed to read index cache: {}", e))?;
        if let Ok(index) = serde_json::from_str::<H5Index>(&json) {
            let h5_key = client.h5_key(run_id);
            let current_size = client.object_size(&h5_key).ok().flatten();
            let cache_valid = match (current_size, index.file_size) {
                (Some(cur), cached) if cached > 0 => cur == cached,
                _ => true,
            };
            if cache_valid {
                return Ok(index);
            }
            let _ = fs::remove_file(&index_cache_path);
        }
    }

    let key = client.h5_key(run_id);

    // Download to temp file (streaming, not into memory)
    let url = format!("{}/{}/{}", client.endpoint(), client.bucket(), key);
    let temp_path = std::env::temp_dir().join(format!("tdgl_download_{}.h5", run_id));

    let mut resp = reqwest::blocking::Client::new()
        .get(&url)
        .send()
        .map_err(|e| format!("Failed to download H5 file: {}", e))?;

    {
        let mut file = fs::File::create(&temp_path)
            .map_err(|e| format!("Failed to create temp file: {}", e))?;
        resp.copy_to(&mut file)
            .map_err(|e| format!("Failed to write H5 file: {}", e))?;
        file.flush()
            .map_err(|e| format!("Failed to flush: {}", e))?;
    }

    // Parse with HDF5 library
    let index = build_index_from_file(&temp_path)?;

    // Clean up temp file
    let _ = fs::remove_file(&temp_path);

    // Cache the index as JSON
    let json =
        serde_json::to_string(&index).map_err(|e| format!("Failed to serialize index: {}", e))?;
    fs::write(&index_cache_path, json)
        .map_err(|e| format!("Failed to write index cache: {}", e))?;

    Ok(index)
}

fn build_index_from_sidecar(
    client: &crate::minio::MinioClient,
    run_id: &str,
) -> Result<Option<H5Index>, String> {
    let key = client.viewer_index_key(run_id);
    let Some(json) = client.read_text_optional(&key)? else {
        return Ok(None);
    };
    let index: H5Index =
        serde_json::from_str(&json).map_err(|e| format!("Failed to parse {}: {}", key, e))?;

    let h5_key = client.h5_key(run_id);
    let current_size = client.object_size(&h5_key).ok().flatten();
    if let (Some(cur), cached) = (current_size, index.file_size) {
        if cached > 0 && cur < cached {
            return Ok(None);
        }
    }
    if index.total_frames == 0
        || index.frame_times.len() != index.total_frames
        || index.frame_psi_offsets.len() != index.total_frames
        || index.frame_mu_offsets.len() != index.total_frames
    {
        return Ok(None);
    }
    Ok(Some(index))
}

/// Clear cached index for a specific run (or all runs).
pub fn clear_index_cache(run_id: Option<&str>) {
    if let Some(id) = run_id {
        let path = index_cache_path(id);
        let _ = fs::remove_file(path);
        // Also clean up temp download file
        let temp_h5 = std::env::temp_dir().join(format!("tdgl_download_{}.h5", id));
        let _ = fs::remove_file(temp_h5);
    } else {
        let temp_dir = std::env::temp_dir();
        if let Ok(entries) = fs::read_dir(&temp_dir) {
            for entry in entries.flatten() {
                let name = entry.file_name();
                let name = name.to_string_lossy();
                if (name.starts_with("tdgl_idx_") && name.ends_with(".json"))
                    || (name.starts_with("tdgl_download_") && name.ends_with(".h5"))
                {
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
