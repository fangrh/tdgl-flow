use serde::Deserialize;
use std::collections::HashMap;

#[derive(Debug, Clone, Deserialize)]
pub struct Hdf5Index {
    #[serde(default)]
    pub steps: Vec<StepInfo>,
    #[serde(default)]
    pub n_sites: usize,
}

#[derive(Debug, Clone, Deserialize)]
pub struct StepInfo {
    #[allow(dead_code)]
    pub step_idx: u32,
    #[serde(rename = "file")]
    pub h5_file: String,
    #[serde(default)]
    pub offsets: HashMap<String, u64>,
    pub total_frames: usize,
    #[serde(default)]
    pub je: f64,
    #[serde(default)]
    pub ramp_start: f64,
    #[serde(default)]
    pub stable_end: f64,
}

impl Hdf5Index {
    pub fn from_json(json: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(json)
    }

    pub fn n_steps(&self) -> usize {
        self.steps.len()
    }
}

#[derive(Debug, Clone, Deserialize)]
#[allow(dead_code)]
pub struct Manifest {
    pub run_id: String,
    #[serde(default)]
    pub solve_time: f64,
    #[serde(default)]
    pub num_steps: usize,
    #[serde(default)]
    pub mesh_file: String,
    #[serde(default)]
    pub discrete_index_file: String,
}

impl Manifest {
    #[allow(dead_code)]
    pub fn from_json(json: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(json)
    }
}
