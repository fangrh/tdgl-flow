use crate::hdf5_index::{H5Index, DatasetLocation};
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

    /// Read complex128 psi for a frame. Returns Vec of [real, imag] pairs.
    pub fn read_psi(&self, frame: usize) -> Result<Vec<[f64; 2]>, String> {
        let offset = self.index.frame_psi_offsets.get(frame)
            .ok_or_else(|| format!("frame {} out of range", frame))?;
        let nbytes = self.index.mesh_points as u64 * 16; // complex128 = 16 bytes
        let bytes = self.client.read_range(&self.h5_key, *offset, nbytes)?;
        let mut result = Vec::with_capacity(self.index.mesh_points);
        for chunk in bytes.chunks_exact(16) {
            let re = f64::from_le_bytes(chunk[0..8].try_into().unwrap());
            let im = f64::from_le_bytes(chunk[8..16].try_into().unwrap());
            result.push([re, im]);
        }
        Ok(result)
    }

    /// Read float64 mu for a frame.
    pub fn read_mu(&self, frame: usize) -> Result<Vec<f64>, String> {
        let offset = self.index.frame_mu_offsets.get(frame)
            .ok_or_else(|| format!("frame {} out of range", frame))?;
        let nbytes = self.index.mesh_points as u64 * 8;
        let bytes = self.client.read_range(&self.h5_key, *offset, nbytes)?;
        parse_f64_array(&bytes)
    }

    /// Read mesh sites (N, 2) float64 coordinates.
    pub fn read_mesh_sites(&self) -> Result<Vec<[f64; 2]>, String> {
        let loc = &self.index.mesh_sites;
        let bytes = self.client.read_range(&self.h5_key, loc.offset, loc.size)?;
        let n = self.index.mesh_points;
        let mut sites = Vec::with_capacity(n);
        for chunk in bytes.chunks_exact(16) {
            let x = f64::from_le_bytes(chunk[0..8].try_into().unwrap());
            let y = f64::from_le_bytes(chunk[8..16].try_into().unwrap());
            sites.push([x, y]);
        }
        Ok(sites)
    }

    /// Read running_state/mu (2, K) flattened and running_state/dt (K,) for voltage.
    /// Returns (rsmu_flat, rsdt_flat) or None if frame has no running state.
    pub fn read_running_state(&self, frame: usize) -> Result<Option<(Vec<f64>, Vec<f64>)>, String> {
        let rsmu_offset = self.index.frame_rsmu_offsets.get(frame).copied().unwrap_or(0);
        let rsdt_offset = self.index.frame_rsdt_offsets.get(frame).copied().unwrap_or(0);
        if rsmu_offset == 0 || rsdt_offset == 0 {
            return Ok(None);
        }
        // Read a generous chunk of dt values and detect actual array length.
        // dt values are adaptive solver timesteps — always positive, typically 0.01-10.0.
        // When we read past the array, we hit data from other datasets with
        // completely different magnitude/pattern. Detect by looking for:
        // - negative or zero values (invalid for dt)
        // - extremely large jumps in magnitude
        let max_entries = 512;
        let max_rs_bytes = max_entries * 8;
        let rsdt_bytes = self.client.read_range(&self.h5_key, rsdt_offset, max_rs_bytes)?;
        let raw: Vec<f64> = rsdt_bytes.chunks_exact(8)
            .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
            .collect();

        // Find actual K: first invalid dt value
        let k = detect_dt_length(&raw);
        if k == 0 {
            return Ok(None);
        }

        let rsdt: Vec<f64> = raw[..k].to_vec();

        // rsmu is (2, K) = 2*K float64 values
        let rsmu_nbytes = (2 * k) as u64 * 8;
        let rsmu_bytes = self.client.read_range(&self.h5_key, rsmu_offset, rsmu_nbytes)?;
        let rsmu = parse_f64_array(&rsmu_bytes)?;
        Ok(Some((rsmu, rsdt)))
    }
}

fn parse_f64_array(bytes: &[u8]) -> Result<Vec<f64>, String> {
    if bytes.len() % 8 != 0 {
        return Err(format!("not aligned to f64: {} bytes", bytes.len()));
    }
    Ok(bytes.chunks_exact(8)
        .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
        .collect())
}

/// Detect actual length of dt array by finding where valid adaptive
/// timesteps end. dt values are always positive and relatively stable.
/// When we read past the array boundary, values become garbage.
fn detect_dt_length(raw: &[f64]) -> usize {
    if raw.is_empty() { return 0; }
    if raw[0] <= 0.0 { return 0; }

    // Use median of first few values as reference magnitude
    let ref_len = raw.len().min(10);
    let mut sorted: Vec<f64> = raw[..ref_len].to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let ref_median = sorted[ref_len / 2];

    for (i, &v) in raw.iter().enumerate() {
        // dt must be positive
        if v <= 0.0 { return i; }
        // If magnitude changes by >100x from reference, we've left the array
        if i >= ref_len && (v / ref_median > 100.0 || ref_median / v > 100.0) {
            return i;
        }
    }
    raw.len()
}