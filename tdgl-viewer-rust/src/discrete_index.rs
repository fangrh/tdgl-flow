use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize)]
pub struct DiscreteStepOffsets {
    pub h5_file: String,
    pub je_start: f64,
    pub je_end: f64,
    pub ramp_start: f64,
    pub ramp_end: f64,
    pub stable_end: f64,
    pub n_frames: usize,
    #[serde(default)]
    pub psi_offsets: Vec<u64>,
    #[serde(default)]
    pub mu_offsets: Vec<u64>,
    #[serde(default)]
    pub rsmu_offsets: Vec<u64>,
    #[serde(default)]
    pub rsdt_offsets: Vec<u64>,
    #[serde(default)]
    pub rsdt_sizes: Vec<u64>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct DiscreteIndex {
    pub total_frames: usize,
    pub mesh_points: usize,
    #[serde(default)]
    pub mesh_sites_offset: u64,
    #[serde(default)]
    pub mesh_sites_size: u64,
    pub frame_times: Vec<f64>,
    pub frame_step_map: Vec<usize>,
    pub frame_local_idx: Vec<usize>,
    pub completed_steps: usize,
    pub total_steps: usize,
    pub status: String,
    pub steps: Vec<DiscreteStepOffsets>,
    #[serde(default)]
    pub run_id: Option<String>,
}

impl DiscreteIndex {
    pub fn is_valid(&self) -> bool {
        self.total_frames > 0
            && self.frame_times.len() == self.total_frames
            && self.frame_step_map.len() == self.total_frames
            && self.frame_local_idx.len() == self.total_frames
            && !self.steps.is_empty()
    }
}
