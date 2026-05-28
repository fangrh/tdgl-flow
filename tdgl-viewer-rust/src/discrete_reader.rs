use crate::discrete_index::DiscreteIndex;
use crate::minio::MinioClient;

pub struct DiscreteReader<'a> {
    client: &'a MinioClient,
    run_id: String,
    index: &'a DiscreteIndex,
}

impl<'a> DiscreteReader<'a> {
    pub fn new(client: &'a MinioClient, run_id: &str, index: &'a DiscreteIndex) -> Self {
        DiscreteReader {
            client,
            run_id: run_id.to_string(),
            index,
        }
    }

    fn step_h5_key(&self, step_list_idx: usize) -> String {
        let step = &self.index.steps[step_list_idx];
        format!("tdgl-runs/{}/{}", self.run_id, step.h5_file)
    }

    pub fn read_mesh_sites(&self) -> Result<Vec<[f64; 2]>, String> {
        if self.index.steps.is_empty() || self.index.mesh_sites_size == 0 {
            return Err("no mesh site data in discrete index".into());
        }
        let h5_key = self.step_h5_key(0);
        let bytes = self.client.read_range(
            &h5_key,
            self.index.mesh_sites_offset,
            self.index.mesh_sites_size,
        )?;
        let n = self.index.mesh_points;
        let mut sites = Vec::with_capacity(n);
        for chunk in bytes.chunks_exact(16) {
            let x = f64::from_le_bytes(chunk[0..8].try_into().unwrap());
            let y = f64::from_le_bytes(chunk[8..16].try_into().unwrap());
            sites.push([x, y]);
        }
        Ok(sites)
    }

    pub fn read_psi(&self, global_frame: usize) -> Result<Vec<[f64; 2]>, String> {
        let step_idx = *self
            .index
            .frame_step_map
            .get(global_frame)
            .ok_or_else(|| format!("frame {} out of range", global_frame))?;
        let local = self.index.frame_local_idx[global_frame];
        let step = self.index.steps.get(step_idx)
            .ok_or_else(|| format!("step {} out of range ({} steps)", step_idx, self.index.steps.len()))?;
        let offset = step
            .psi_offsets
            .get(local)
            .ok_or_else(|| format!("local frame {} out of range for step {}", local, step_idx))?;
        let h5_key = self.step_h5_key(step_idx);
        let nbytes = self.index.mesh_points as u64 * 16; // complex128 = 16 bytes
        let bytes = self.client.read_range(&h5_key, *offset, nbytes)?;
        let mut result = Vec::with_capacity(self.index.mesh_points);
        for chunk in bytes.chunks_exact(16) {
            let re = f64::from_le_bytes(chunk[0..8].try_into().unwrap());
            let im = f64::from_le_bytes(chunk[8..16].try_into().unwrap());
            result.push([re, im]);
        }
        Ok(result)
    }

    pub fn read_mu(&self, global_frame: usize) -> Result<Vec<f64>, String> {
        let step_idx = *self.index.frame_step_map.get(global_frame)
            .ok_or_else(|| format!("frame {} out of range", global_frame))?;
        let local = self.index.frame_local_idx[global_frame];
        let step = self.index.steps.get(step_idx)
            .ok_or_else(|| format!("step {} out of range ({} steps)", step_idx, self.index.steps.len()))?;
        let offset = step
            .mu_offsets
            .get(local)
            .ok_or_else(|| format!("local mu {} out of range for step {}", local, step_idx))?;
        let h5_key = self.step_h5_key(step_idx);
        let nbytes = self.index.mesh_points as u64 * 8;
        let bytes = self.client.read_range(&h5_key, *offset, nbytes)?;
        parse_f64_array(&bytes)
    }

    pub fn read_running_state(
        &self,
        global_frame: usize,
    ) -> Result<Option<(Vec<f64>, Vec<f64>)>, String> {
        let step_idx = *self.index.frame_step_map.get(global_frame)
            .ok_or_else(|| format!("frame {} out of range", global_frame))?;
        let local = self.index.frame_local_idx[global_frame];
        let step = self.index.steps.get(step_idx)
            .ok_or_else(|| format!("step {} out of range ({} steps)", step_idx, self.index.steps.len()))?;

        let rsmu_offset = step.rsmu_offsets.get(local).copied().unwrap_or(0);
        let rsdt_offset = step.rsdt_offsets.get(local).copied().unwrap_or(0);
        let rsdt_size = step.rsdt_sizes.get(local).copied().unwrap_or(0);

        if rsmu_offset == 0 || rsdt_offset == 0 || rsdt_size == 0 {
            return Ok(None);
        }

        let h5_key = self.step_h5_key(step_idx);

        let dt_bytes = self.client.read_range(&h5_key, rsdt_offset, rsdt_size)?;
        let dt = parse_f64_array(&dt_bytes)?;
        if dt.iter().any(|v| !v.is_finite() || *v <= 0.0) {
            return Ok(None);
        }

        let k = dt.len();
        let rsmu_nbytes = (2 * k) as u64 * 8;
        let rsmu_bytes = self.client.read_range(&h5_key, rsmu_offset, rsmu_nbytes)?;
        let rsmu = parse_f64_array(&rsmu_bytes)?;

        if rsmu.iter().any(|v| !v.is_finite() || v.abs() > 1e10) {
            return Ok(None);
        }

        Ok(Some((rsmu, dt)))
    }
}

fn parse_f64_array(bytes: &[u8]) -> Result<Vec<f64>, String> {
    if bytes.len() % 8 != 0 {
        return Err(format!("not aligned to f64: {} bytes", bytes.len()));
    }
    Ok(bytes
        .chunks_exact(8)
        .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
        .collect())
}
