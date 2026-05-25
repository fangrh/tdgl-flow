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
        // We need to know K. Read a chunk and detect size from dt.
        // dt is (K,) float64. Read up to 1024 entries (8KB) — K is typically <200.
        let max_rs_bytes = 1024 * 8;
        let rsdt_bytes = self.client.read_range(&self.h5_key, rsdt_offset, max_rs_bytes)?;
        let rsdt = parse_f64_array(&rsdt_bytes)?;
        let k = rsdt.len();
        if k == 0 {
            return Ok(None);
        }
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