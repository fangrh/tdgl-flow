use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize, Serialize)]
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

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct DeviceParams {
    pub film_width: Option<f64>,
    pub film_height: Option<f64>,
    pub elec_width: Option<f64>,
    pub elec_height: Option<f64>,
    pub max_edge_length: Option<f64>,
    pub smooth: Option<f64>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TimingSummary {
    pub mode: Option<String>,
    pub n_steps: Option<u64>,
    pub solve_time: Option<f64>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
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