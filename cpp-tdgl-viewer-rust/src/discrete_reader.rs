use crate::hdf5_index::Hdf5Index;
use crate::minio_client::MinioClient;

pub struct DiscreteReader {
    client: MinioClient,
    run_id: String,
    index: Hdf5Index,
    mesh_sites: Vec<f64>,
    mesh_edges: Vec<i64>,
    mesh_areas: Vec<f64>,
    edge_centers: Vec<f64>,
    edge_lengths: Vec<f64>,
    n_sites: usize,
    n_edges: usize,
}

impl DiscreteReader {
    pub fn new(client: MinioClient, run_id: &str, index: Hdf5Index) -> Self {
        DiscreteReader {
            client,
            run_id: run_id.to_string(),
            index,
            mesh_sites: Vec::new(),
            mesh_edges: Vec::new(),
            mesh_areas: Vec::new(),
            edge_centers: Vec::new(),
            edge_lengths: Vec::new(),
            n_sites: 0,
            n_edges: 0,
        }
    }

    #[allow(dead_code)]
    pub fn index(&self) -> &Hdf5Index {
        &self.index
    }

    pub fn n_sites(&self) -> usize {
        self.n_sites
    }

    #[allow(dead_code)]
    pub fn n_edges(&self) -> usize {
        self.n_edges
    }

    #[allow(dead_code)]
    pub fn mesh_sites(&self) -> &[f64] {
        &self.mesh_sites
    }

    #[allow(dead_code)]
    pub fn mesh_edges(&self) -> &[i64] {
        &self.mesh_edges
    }

    pub fn load_mesh(&mut self) -> Result<(), String> {
        let mesh_key = format!("tdgl-runs/{}/mesh.h5", self.run_id);

        let size = self.client.object_size(&mesh_key)?.unwrap_or(0);
        if size == 0 {
            return Err("mesh.h5 is empty or not found".into());
        }

        let bytes = self.client.read_range(&mesh_key, 0, size.min(100 * 1024 * 1024))?;

        self.parse_mesh_hdf5(&bytes)?;

        Ok(())
    }

    fn parse_mesh_hdf5(&mut self, bytes: &[u8]) -> Result<(), String> {
        if bytes.len() < 512 {
            return Err("mesh HDF5 too small".into());
        }

        if &bytes[0..8] != b"\x89HDF\r\n\x1a\n" {
            return Err("Not an HDF5 file".into());
        }

        let mut dataset_offsets: Vec<(String, u64, usize)> = Vec::new();
        let mut pos: usize = 8;

        while pos + 168 < bytes.len() {
            let _v1_start = pos;
            let reserved1 = bytes[pos + 4];
            let version = bytes[pos + 6];
            if version != 1 || reserved1 != 0 {
                pos += 12;
                continue;
            }

            let num_messages = u16::from_le_bytes([bytes[pos + 8], bytes[pos + 9]]) as usize;
            let _group_flags = bytes[pos + 10];
            let _compile_flags = bytes[pos + 11];
            let _base_addr = u64::from_le_bytes([
                bytes[pos + 16], bytes[pos + 17], bytes[pos + 18], bytes[pos + 19],
                bytes[pos + 20], bytes[pos + 21], bytes[pos + 22], bytes[pos + 23],
            ]);
            pos += 24;

            for _ in 0..num_messages {
                if pos + 12 > bytes.len() {
                    break;
                }
                let msg_type = u16::from_le_bytes([bytes[pos], bytes[pos + 1]]);
                let msg_size = u16::from_le_bytes([bytes[pos + 2], bytes[pos + 3]]) as usize;
                pos += 4;

                if msg_type == 5 && msg_size > 0 {
                    let name_len = bytes[pos] as usize;
                    let datatype_size =
                        u16::from_le_bytes([bytes[pos + 1], bytes[pos + 2]]) as usize;
                    let version = bytes[pos + 4];

                    let name_start = pos + 8;
                    let name_end = name_start + name_len;
                    if name_end > bytes.len() {
                        pos += msg_size;
                        continue;
                    }
                    let name = String::from_utf8_lossy(&bytes[name_start..name_end]).to_string();
                    pos = (name_end + 7) & !7usize;

                    if version >= 2 && pos + 8 <= bytes.len() {
                        let data_offset =
                            u64::from_le_bytes([bytes[pos], bytes[pos + 1], bytes[pos + 2],
                                bytes[pos + 3], bytes[pos + 4], bytes[pos + 5],
                                bytes[pos + 6], bytes[pos + 7]]);
                        pos += 8;
                        if data_offset > 0 && data_offset != 0xFFFFFFFFFFFFFFFF {
                            dataset_offsets.push((name, data_offset, datatype_size));
                        }
                    }
                } else if msg_type == 1 || msg_type == 3 {
                    pos += msg_size;
                } else {
                    pos += msg_size;
                }

                if pos & 7 != 0 {
                    pos = (pos + 7) & !7usize;
                }
            }
            break;
        }

        for (name, offset, _elem_size) in dataset_offsets {
            if offset as usize >= bytes.len() || offset < 512 {
                continue;
            }
            match name.as_str() {
                "mesh/sites" | "sites" => {
                    if let Some(sites) = self.read_f64_dataset(bytes, offset, 2) {
                        self.n_sites = sites.len() / 2;
                        self.mesh_sites = sites;
                    }
                }
                "mesh/edges" | "edges" => {
                    if let Some(edges) = self.read_i64_dataset(bytes, offset, 2) {
                        self.n_edges = edges.len() / 2;
                        self.mesh_edges = edges;
                    }
                }
                "mesh/areas" | "areas" => {
                    if let Some(areas) = self.read_f64_dataset(bytes, offset, 1) {
                        self.mesh_areas = areas;
                    }
                }
                "mesh/edge_mesh/centers" | "edge_mesh/centers" | "centers" => {
                    if let Some(centers) = self.read_f64_dataset(bytes, offset, 2) {
                        self.edge_centers = centers;
                    }
                }
                "mesh/edge_mesh/edge_lengths" | "edge_mesh/edge_lengths" | "edge_lengths" => {
                    if let Some(lengths) = self.read_f64_dataset(bytes, offset, 1) {
                        self.edge_lengths = lengths;
                    }
                }
                _ => {}
            }
        }

        Ok(())
    }

    fn read_f64_dataset(&self, bytes: &[u8], offset: u64, dims: usize) -> Option<Vec<f64>> {
        let offset = offset as usize;
        if offset >= bytes.len() || offset == 0 {
            return None;
        }

        let mut pos = offset;
        if &bytes[pos..pos + 8] != b"DATASPACE" {
            return None;
        }
        pos += 8;

        let version = bytes[pos];
        pos += 8;

        let shape = if version == 1 {
            let _rank = bytes[pos] as usize;
            pos += 4;
            let mut shape = Vec::with_capacity(_rank);
            for _ in 0.._rank {
                let dim = u32::from_le_bytes([bytes[pos], bytes[pos + 1], bytes[pos + 2], bytes[pos + 3]]) as usize;
                shape.push(dim);
                pos += 4;
            }
            shape
        } else if version == 2 {
            let flags = bytes[pos];
            pos += 1;
            let _rank = (flags & 0x0F) as usize;
            let mut shape = Vec::with_capacity(_rank);
            if flags & 0x10 != 0 {
                pos += 4;
            }
            if flags & 0x20 != 0 {
                pos += 4;
            }
            for _ in 0.._rank {
                let dim = u32::from_le_bytes([bytes[pos], bytes[pos + 1], bytes[pos + 2], bytes[pos + 3]]) as usize;
                shape.push(dim);
                pos += 4;
            }
            shape
        } else {
            return None;
        };

        let total: usize = shape.iter().product();
        if total == 0 || dims == 0 {
            return None;
        }

        if &bytes[pos..pos + 6] != b"DATATYPE" {
            return None;
        }
        pos += 8;

        if &bytes[pos..pos + 6] != b"FLOAT64" {
            return None;
        }
        pos += 8;

        if &bytes[pos..pos + 6] != b"DATA" {
            return None;
        }
        pos += 8;

        if bytes[pos - 1] == 1 {
            pos += 4;
        }

        let n_bytes = total * 8;
        if pos + n_bytes > bytes.len() {
            return None;
        }

        let mut result = Vec::with_capacity(total);
        for i in 0..total {
            let val = f64::from_le_bytes([
                bytes[pos + i * 8],
                bytes[pos + i * 8 + 1],
                bytes[pos + i * 8 + 2],
                bytes[pos + i * 8 + 3],
                bytes[pos + i * 8 + 4],
                bytes[pos + i * 8 + 5],
                bytes[pos + i * 8 + 6],
                bytes[pos + i * 8 + 7],
            ]);
            result.push(val);
        }

        Some(result)
    }

    fn read_i64_dataset(&self, bytes: &[u8], offset: u64, dims: usize) -> Option<Vec<i64>> {
        let offset = offset as usize;
        if offset >= bytes.len() || offset == 0 {
            return None;
        }

        let mut pos = offset;
        if &bytes[pos..pos + 8] != b"DATASPACE" {
            return None;
        }
        pos += 8;

        let version = bytes[pos];
        pos += 8;

        let shape = if version == 1 {
            let rank = bytes[pos] as usize;
            pos += 4;
            let mut shape = Vec::with_capacity(rank);
            for _ in 0..rank {
                let dim = u32::from_le_bytes([bytes[pos], bytes[pos + 1], bytes[pos + 2], bytes[pos + 3]]) as usize;
                shape.push(dim);
                pos += 4;
            }
            shape
        } else {
            return None;
        };

        let total: usize = shape.iter().product();
        if total == 0 || dims == 0 {
            return None;
        }

        if &bytes[pos..pos + 6] != b"DATATYPE" {
            return None;
        }
        pos += 8;

        if &bytes[pos..pos + 7] != b"STDIO_I64" {
            return None;
        }
        pos += 8;

        if &bytes[pos..pos + 6] != b"DATA" {
            return None;
        }
        pos += 8;

        let n_bytes = total * 8;
        if pos + n_bytes > bytes.len() {
            return None;
        }

        let mut result = Vec::with_capacity(total);
        for i in 0..total {
            let val = i64::from_le_bytes([
                bytes[pos + i * 8],
                bytes[pos + i * 8 + 1],
                bytes[pos + i * 8 + 2],
                bytes[pos + i * 8 + 3],
                bytes[pos + i * 8 + 4],
                bytes[pos + i * 8 + 5],
                bytes[pos + i * 8 + 6],
                bytes[pos + i * 8 + 7],
            ]);
            result.push(val);
        }

        Some(result)
    }

    pub fn read_psi(&self, step_idx: usize, frame_idx: usize) -> Result<Vec<f64>, String> {
        let step = self.index.steps.get(step_idx)
            .ok_or_else(|| format!("step {} not found", step_idx))?;

        let h5_key = format!("tdgl-runs/{}/{}", self.run_id, step.h5_file);

        let psi_offset = *step.offsets.get("psi")
            .ok_or_else(|| format!("no psi offset in step {}", step_idx))?;

        let frame_size = self.n_sites * 16;
        let offset = psi_offset + (frame_idx as u64) * (frame_size as u64);
        let bytes = self.client.read_range(&h5_key, offset, frame_size as u64)?;

        let mut result = Vec::with_capacity(self.n_sites * 2);
        for chunk in bytes.chunks_exact(16) {
            let re = f64::from_le_bytes(chunk[0..8].try_into().unwrap());
            let im = f64::from_le_bytes(chunk[8..16].try_into().unwrap());
            result.push(re);
            result.push(im);
        }
        Ok(result)
    }

    #[allow(dead_code)]
    pub fn read_mu(&self, step_idx: usize, frame_idx: usize) -> Result<Vec<f64>, String> {
        let step = self.index.steps.get(step_idx)
            .ok_or_else(|| format!("step {} not found", step_idx))?;

        let h5_key = format!("tdgl-runs/{}/{}", self.run_id, step.h5_file);

        let mu_offset = *step.offsets.get("mu")
            .ok_or_else(|| format!("no mu offset in step {}", step_idx))?;

        let frame_size = self.n_sites * 8;
        let offset = mu_offset + (frame_idx as u64) * (frame_size as u64);
        let bytes = self.client.read_range(&h5_key, offset, frame_size as u64)?;

        Ok(bytes.chunks_exact(8)
            .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
            .collect())
    }
}
