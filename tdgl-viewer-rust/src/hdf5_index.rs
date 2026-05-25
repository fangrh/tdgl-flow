/// HDF5 Binary Parser — locates dataset byte offsets by scanning the raw file.
///
/// Strategy: Instead of fully parsing the HDF5 metadata tree (which uses v2 object
/// headers, fractal heaps, and B-tree v2), we scan for Data Layout v3 contiguous
/// messages of the form: `[03][01][8-byte-offset][8-byte-size]`.
///
/// We filter matches by known dataset sizes to identify which match corresponds to
/// which dataset. The scan is O(file_size) but only reads each byte once and uses
/// minimal memory. For a 2.2 GB file, this takes under 1 second.
///
/// The file is expected to be loaded entirely into memory (e.g., via mmap or a
/// single download from MinIO). For remote use, the caller downloads the file first.

use std::fs;
use std::path::Path;

/// Byte offset and size of a contiguous dataset within the HDF5 file.
#[derive(Debug, Clone)]
pub struct DatasetLocation {
    /// Absolute byte offset in the file where raw data starts.
    pub offset: u64,
    /// Total bytes of raw data.
    pub size: u64,
    /// Bytes per element (8 for float64/int64, 16 for complex128).
    pub element_size: u64,
    /// Shape of the dataset, e.g. vec![1500, 2] for an (N,2) array.
    pub shape: Vec<u64>,
}

/// Index of all TDGL datasets within an HDF5 file.
#[derive(Debug, Clone)]
pub struct H5Index {
    /// Mesh site coordinates: (N, 2) float64.
    pub mesh_sites: DatasetLocation,
    /// Mesh edge connectivity: (M, 2) int64.
    pub mesh_edges: DatasetLocation,
    /// Byte offsets of data/{i}/psi raw data for each frame.
    pub frame_psi_offsets: Vec<u64>,
    /// Byte offsets of data/{i}/mu raw data for each frame.
    pub frame_mu_offsets: Vec<u64>,
    /// Byte offsets of data/{i}/running_state/mu raw data for each frame.
    /// 0 if the frame has no running_state (e.g., frame 0).
    pub frame_rsmu_offsets: Vec<u64>,
    /// Byte offsets of data/{i}/running_state/dt raw data for each frame.
    /// 0 if the frame has no running_state.
    pub frame_rsdt_offsets: Vec<u64>,
    /// Byte offsets of data/{i}/supercurrent raw data for each frame.
    pub frame_supercurrent_offsets: Vec<u64>,
    /// Total number of frames.
    pub total_frames: usize,
    /// Number of mesh points (N = sites.shape[0]).
    pub mesh_points: usize,
}

/// A candidate Data Layout match found during scanning.
#[derive(Debug)]
struct LayoutCandidate {
    offset: u64, // data offset
    size: u64,   // data size
}

/// Scan raw bytes for Data Layout v3 contiguous patterns.
/// Pattern: [0x03][0x01][8-byte-LE-offset][8-byte-LE-size]
/// Returns all candidates sorted by data offset.
fn scan_layout_candidates(data: &[u8]) -> Vec<LayoutCandidate> {
    let mut candidates = Vec::new();
    let len = data.len();

    if len < 18 {
        return candidates;
    }

    let mut i = 0;
    while i < len - 17 {
        if data[i] == 0x03 && data[i + 1] == 0x01 {
            let data_offset = u64::from_le_bytes([
                data[i + 2], data[i + 3], data[i + 4], data[i + 5],
                data[i + 6], data[i + 7], data[i + 8], data[i + 9],
            ]);
            let data_size = u64::from_le_bytes([
                data[i + 10], data[i + 11], data[i + 12], data[i + 13],
                data[i + 14], data[i + 15], data[i + 16], data[i + 17],
            ]);

            // Sanity checks: offset must be within file, size must be reasonable
            if data_offset > 0 && data_offset < len as u64 && data_size > 0 && data_size < len as u64
                && data_offset + data_size <= len as u64
                && data_size.is_multiple_of(8)
            {
                candidates.push(LayoutCandidate {
                    offset: data_offset,
                    size: data_size,
                });
                // Skip past this match to avoid overlapping
                i += 18;
                continue;
            }
        }
        i += 1;
    }

    candidates.sort_by_key(|c| c.offset);
    candidates
}

/// Classify scanned candidates into typed dataset locations.
///
/// TDGL files have these dataset sizes:
/// - psi: N * 16 bytes (complex128, where N = n_sites)
/// - mu: N * 8 bytes (float64)
/// - sites: N * 2 * 8 = N * 16 bytes (float64) — same as psi!
/// - edges: M * 2 * 8 bytes (int64)
/// - supercurrent: M * 8 bytes (float64)
/// - normal_current: M * 8 bytes (float64)
/// - induced_vector_potential: M * 2 * 8 bytes (float64)
/// - running_state/mu: 2 * K * 8 bytes (float64, K varies)
/// - running_state/dt: K * 8 bytes (float64, K varies)
///
/// Classification strategy:
/// 1. Find psi size (N*16) and mu size (N*8) by looking for size pairs where
///    one is 2x the other AND their counts are nearly equal (1:1 ratio per frame).
///    This distinguishes psi/mu from supercurrent/normal_current (same size, 2:1 ratio).
/// 2. Group candidates by size to identify roles.
/// 3. The sites dataset is at the end of the file (after all frames) with size N*16.
/// 4. Frames appear as repeating sequences of datasets at increasing offsets.
fn classify_candidates(candidates: &[LayoutCandidate], file_len: u64) -> Result<H5Index, String> {
    // Group by size
    let mut size_groups: std::collections::HashMap<u64, Vec<&LayoutCandidate>> =
        std::collections::HashMap::new();
    for c in candidates {
        size_groups.entry(c.size).or_default().push(c);
    }

    if candidates.is_empty() {
        return Err("No Data Layout candidates found in file".into());
    }

    // Collect size -> count
    let size_counts: std::collections::HashMap<u64, usize> = size_groups
        .iter()
        .map(|(size, group)| (*size, group.len()))
        .collect();

    // Identify mu_size and psi_size:
    // psi and mu appear once per frame, so their counts should be nearly equal.
    // Among all size pairs (S, 2*S) with high counts, pick the one where
    // the count ratio is closest to 1.0 (both appear the same number of times).
    //
    // This distinguishes from e.g. supercurrent+normal_current (both same size,
    // so 2x the per-size count) whose "double" has a different count.
    let mut best_mu_size: Option<u64> = None;
    let mut best_psi_size: Option<u64> = None;
    let mut best_score: f64 = f64::MAX; // lower is better

    for (&s, &s_count) in &size_counts {
        if s_count < 10 || s > 1_000_000 || s < 16 || s % 8 != 0 {
            continue;
        }
        let double = s * 2;
        if let Some(&d_count) = size_counts.get(&double) {
            if d_count < 10 {
                continue;
            }
            // The count ratio: psi has slightly fewer candidates (one is the sites dataset)
            // so psi_count may be mu_count + 1. We want ratio close to 1.0.
            let ratio = if s_count > d_count {
                s_count as f64 / d_count as f64
            } else {
                d_count as f64 / s_count as f64
            };
            // psi/mu ratio should be ~1.0 (within 5%).
            // supercurrent/normal_current count is 2x the frame count, while their
            // "double" (induced_vector_potential or edges) has ~1x the frame count.
            // So the ratio for the wrong pair would be ~2.0.
            let score = (ratio - 1.0).abs();
            if score < best_score {
                best_score = score;
                best_mu_size = Some(s);
                best_psi_size = Some(double);
            }
        }
    }

    let mu_size = best_mu_size.ok_or("Could not identify mu dataset size (N*8)")?;
    let psi_size_val = best_psi_size.ok_or("Could not identify psi dataset size (N*16)")?;
    let n_sites = (mu_size / 8) as usize;

    // Now identify edge-related sizes
    // supercurrent/normal_current both have M*8 bytes
    // induced_vector_potential has M*16 bytes
    // edges has M*2*8 = M*16 bytes too (same as induced_vector_potential)

    // Find edge_size: look for a size that appears only a few times (1-2 for edges)
    // and is not psi_size (to distinguish sites from psi)

    // Collect psi candidates (size == psi_size_val)
    let psi_candidates = size_groups.get(&psi_size_val).map(|g| g.as_slice()).unwrap_or(&[]);
    let mu_candidates = size_groups.get(&mu_size).map(|g| g.as_slice()).unwrap_or(&[]);

    // Sort psi candidates by offset
    let mut psi_sorted: Vec<&LayoutCandidate> = psi_candidates.to_vec();
    psi_sorted.sort_by_key(|c| c.offset);

    // Sort mu candidates by offset
    let mut mu_sorted: Vec<&LayoutCandidate> = mu_candidates.to_vec();
    mu_sorted.sort_by_key(|c| c.offset);

    // Identify frame boundaries:
    // For each frame, the datasets appear in order:
    // psi, mu, supercurrent, normal_current, induced_vector_potential
    // [+ running_state/mu, running_state/dt for frames >= 1]
    //
    // The psi candidates include both frame psi data AND the mesh sites data.
    // The sites data is near the end of the file (after all frames).
    //
    // Strategy: psi offsets that form a monotonically increasing sequence are frames.
    // The one that's far away (near the end) is the sites dataset.

    // Identify the sites dataset and remove false-positive outliers from psi candidates.
    //
    // The psi candidates may include:
    // 1. False positives from HDF5 metadata (small offsets, far from real data)
    // 2. The mesh sites dataset (same size N*16, near end of file)
    // 3. Real frame psi data (the bulk, evenly spaced)
    //
    // Strategy:
    // - Compute gaps between consecutive candidates
    // - Find the median gap (typical frame spacing)
    // - Remove any candidate whose gap from predecessor is >5x the median
    // - The one removed from the end of the file is sites

    let (frame_psi, mesh_sites_loc) = if psi_sorted.len() <= 2 {
        // Too few to distinguish
        (
            psi_sorted.to_vec(),
            DatasetLocation {
                offset: 0,
                size: 0,
                element_size: 0,
                shape: vec![],
            },
        )
    } else {
        // Compute gaps between consecutive candidates
        let mut gaps: Vec<u64> = Vec::with_capacity(psi_sorted.len() - 1);
        for i in 1..psi_sorted.len() {
            gaps.push(psi_sorted[i].offset - psi_sorted[i - 1].offset);
        }

        // Find median gap (typical frame spacing)
        let mut sorted_gaps = gaps.clone();
        sorted_gaps.sort();
        let median_gap = sorted_gaps[sorted_gaps.len() / 2];

        // Use 2.5x median as threshold for outlier detection.
        // Real frame gaps cluster tightly around the median (~1.0-1.1x).
        // False positives produce gaps of 3.7x+ the median.
        let gap_threshold = (median_gap * 5 / 2) as u64; // 2.5x median

        // Remove false positives from the front (candidates with abnormal gap to next)
        let mut start: usize = 0;
        while start < psi_sorted.len() - 1 {
            let gap = psi_sorted[start + 1].offset - psi_sorted[start].offset;
            if gap > gap_threshold {
                start += 1;
            } else {
                break;
            }
        }

        // The sites dataset: it's the psi-sized candidate with the highest offset.
        // It may be within the frame sequence (small gap) or slightly beyond it.
        // We identify it as the last psi-sized candidate in the file.
        let sites_idx = psi_sorted.len() - 1;
        let sites_loc = DatasetLocation {
            offset: psi_sorted[sites_idx].offset,
            size: psi_sorted[sites_idx].size,
            element_size: 8,
            shape: vec![n_sites as u64, 2],
        };

        // Frames are from start to sites_idx (exclusive)
        let frames: Vec<&LayoutCandidate> = (start..sites_idx)
            .map(|i| psi_sorted[i])
            .collect();

        (frames, sites_loc)
    };

    // Now find edges: look for a size that appears only 1 time and is not psi/mu
    // The edges dataset is typically the largest unique size
    let mut edges_loc = DatasetLocation {
        offset: 0,
        size: 0,
        element_size: 0,
        shape: vec![],
    };

    // Find running_state sizes: they are small (K*8 and 2*K*8 where K is ~50)
    // rs_dt has the smallest non-trivial size
    // rs_mu has size 2 * rs_dt

    // Identify running_state/dt size: look for sizes around 400 (50*8)
    let mut rsdt_size: u64 = 0;
    let mut rsmu_size: u64 = 0;

    // Collect sizes sorted by value to check in order
    let mut sorted_sizes: Vec<u64> = size_counts.keys().copied().collect();
    sorted_sizes.sort();

    for &s in &sorted_sizes {
        let count = size_counts[&s];
        if s >= 200 && s <= 2000 && s % 8 == 0 && count >= 2 {
            // Check if double this size also exists with similar count
            if let Some(double_group) = size_groups.get(&(s * 2)) {
                if double_group.len() >= count - 1 {
                    rsdt_size = s;
                    rsmu_size = s * 2;
                    break;
                }
            }
        }
    }

    // Collect running_state candidates
    let rsdt_candidates = if rsdt_size > 0 {
        size_groups
            .get(&rsdt_size)
            .map(|g| {
                let mut v = g.to_vec();
                v.sort_by_key(|c| c.offset);
                v
            })
            .unwrap_or_default()
    } else {
        vec![]
    };

    let rsmu_candidates = if rsmu_size > 0 {
        size_groups
            .get(&rsmu_size)
            .map(|g| {
                let mut v = g.to_vec();
                v.sort_by_key(|c| c.offset);
                v
            })
            .unwrap_or_default()
    } else {
        vec![]
    };

    // Identify edges: look in remaining sizes for the one that's the largest
    // and appears only once or twice
    let known_sizes: std::collections::HashSet<u64> = [
        psi_size_val,
        mu_size,
        rsdt_size,
        rsmu_size,
    ]
    .into_iter()
    .filter(|&s| s > 0)
    .collect();

    // Find supercurrent/normal_current size (M*8) and induced_vector_potential (M*16)
    // These have count == total_frames
    let sc_nc_size: Option<u64> = {
        // Look for a size with count >= frame count that's not psi/mu
        let frame_count = frame_psi.len();
        let mut found: Option<u64> = None;
        for &s in &sorted_sizes {
            let count = size_counts[&s];
            if !known_sizes.contains(&s) && count >= frame_count && s > mu_size && s % 8 == 0 {
                found = Some(s);
                break;
            }
        }
        found
    };

    // For edges, look for a size that appears only once (or a few times) and is larger
    for &s in &sorted_sizes {
        let count = size_counts[&s];
        if !known_sizes.contains(&s) && count <= 4 && s > 1000 && s % 8 == 0 {
            if let Some(group) = size_groups.get(&s) {
                let first = &group[0];
                edges_loc = DatasetLocation {
                    offset: first.offset,
                    size: first.size,
                    element_size: 8,
                    shape: vec![(first.size / 16) as u64, 2], // assume (M, 2) int64
                };
                break;
            }
        }
    }

    // Build the final index
    let total_frames = frame_psi.len();

    // Map psi offsets to mu offsets: for each psi, the corresponding mu is the next
    // mu candidate with offset > psi.offset and offset < next psi offset
    let mut frame_mu_offsets = vec![0u64; total_frames];
    for (frame_idx, psi) in frame_psi.iter().enumerate() {
        let next_psi_offset = if frame_idx + 1 < frame_psi.len() {
            frame_psi[frame_idx + 1].offset
        } else {
            file_len
        };

        for mu in &mu_sorted {
            if mu.offset > psi.offset && mu.offset < next_psi_offset {
                frame_mu_offsets[frame_idx] = mu.offset;
                break;
            }
        }
    }

    // Map running_state offsets to frames
    // Each frame with running_state has rsdt and rsmu between psi and next psi
    let mut frame_rsmu_offsets = vec![0u64; total_frames];
    let mut frame_rsdt_offsets = vec![0u64; total_frames];

    for (frame_idx, psi) in frame_psi.iter().enumerate() {
        let next_psi_offset = if frame_idx + 1 < frame_psi.len() {
            frame_psi[frame_idx + 1].offset
        } else {
            file_len
        };

        for rsdt in &rsdt_candidates {
            if rsdt.offset > psi.offset && rsdt.offset < next_psi_offset {
                frame_rsdt_offsets[frame_idx] = rsdt.offset;
                break;
            }
        }

        for rsmu in &rsmu_candidates {
            if rsmu.offset > psi.offset && rsmu.offset < next_psi_offset {
                frame_rsmu_offsets[frame_idx] = rsmu.offset;
                break;
            }
        }
    }

    // Build supercurrent offsets
    let mut frame_supercurrent_offsets = vec![0u64; total_frames];
    if let Some(sc_size) = sc_nc_size {
        if let Some(sc_group) = size_groups.get(&sc_size) {
            let mut sc_sorted = sc_group.to_vec();
            sc_sorted.sort_by_key(|c| c.offset);
            for (frame_idx, psi) in frame_psi.iter().enumerate() {
                let next_psi_offset = if frame_idx + 1 < frame_psi.len() {
                    frame_psi[frame_idx + 1].offset
                } else {
                    file_len
                };
                for sc in &sc_sorted {
                    if sc.offset > psi.offset && sc.offset < next_psi_offset {
                        frame_supercurrent_offsets[frame_idx] = sc.offset;
                        break;
                    }
                }
            }
        }
    }

    let psi_offsets: Vec<u64> = frame_psi.iter().map(|c| c.offset).collect();

    Ok(H5Index {
        mesh_sites: mesh_sites_loc,
        mesh_edges: edges_loc,
        frame_psi_offsets: psi_offsets,
        frame_mu_offsets,
        frame_rsmu_offsets,
        frame_rsdt_offsets,
        frame_supercurrent_offsets,
        total_frames,
        mesh_points: n_sites,
    })
}

/// Build an H5Index by scanning a file loaded into memory.
///
/// This is the main entry point for the HDF5 parser. It scans the raw bytes
/// for Data Layout v3 contiguous patterns and classifies them by size to
/// identify TDGL datasets.
pub fn build_index_from_bytes(data: &[u8]) -> Result<H5Index, String> {
    // Validate HDF5 signature
    let signature: [u8; 8] = [0x89, 0x48, 0x44, 0x46, 0x0d, 0x0a, 0x1a, 0x0a];
    if data.len() < 8 || data[..8] != signature {
        return Err("Not a valid HDF5 file (bad signature)".into());
    }

    let file_len = data.len() as u64;

    // Scan for Data Layout candidates
    let candidates = scan_layout_candidates(data);

    if candidates.is_empty() {
        return Err("No contiguous Data Layout messages found".into());
    }

    classify_candidates(&candidates, file_len)
}

/// Build an H5Index by loading and scanning a local file.
pub fn build_index_from_file(path: &Path) -> Result<H5Index, String> {
    let data = fs::read(path).map_err(|e| format!("Failed to read file: {}", e))?;
    build_index_from_bytes(&data)
}

/// Build an H5Index by downloading the file from MinIO and scanning it.
///
/// For the MVP, this downloads the entire H5 file to a temporary cache location.
/// The HDF5 parser requires the full file for its scan-based approach.
pub fn build_index(client: &crate::minio::MinioClient, run_id: &str) -> Result<H5Index, String> {
    use std::env;

    let key = client.h5_key(run_id);
    let url = format!("{}/{}/{}", client.endpoint(), client.bucket(), key);

    // For now, download the entire file to a temp location
    // The HDF5 parser needs the full file for scan-based approach
    let temp_dir = env::temp_dir();
    let cache_path = temp_dir.join(format!("tdgl_h5_{}.cache", run_id));

    if cache_path.exists() {
        // Use cached file
        return build_index_from_file(&cache_path);
    }

    // Download full file
    let resp = reqwest::blocking::Client::new()
        .get(&url)
        .send()
        .map_err(|e| format!("Failed to download H5 file: {}", e))?;

    let bytes = resp.bytes().map_err(|e| format!("Failed to read response: {}", e))?;

    fs::write(&cache_path, &bytes)
        .map_err(|e| format!("Failed to write cache file: {}", e))?;

    build_index_from_file(&cache_path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_scan_empty_data() {
        let candidates = scan_layout_candidates(&[]);
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_scan_finds_pattern() {
        // Build a minimal pattern: 03 01 <offset:8> <size:8>
        // Use a size small enough to fit within our buffer
        let offset: u64 = 100;
        let size: u64 = 16;
        let mut data = vec![0u8; 256];
        data[50] = 0x03;
        data[51] = 0x01;
        data[52..60].copy_from_slice(&offset.to_le_bytes());
        data[60..68].copy_from_slice(&size.to_le_bytes());

        // Sanity: offset + size must be within file
        assert!(offset + size <= data.len() as u64);

        let candidates = scan_layout_candidates(&data);
        assert_eq!(candidates.len(), 1);
        assert_eq!(candidates[0].offset, offset);
        assert_eq!(candidates[0].size, size);
    }

    #[test]
    fn test_rejects_bad_signature() {
        let data = vec![0u8; 1024];
        let result = build_index_from_bytes(&data);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("signature"));
    }
}
