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
    pub ramp_down_steps: Option<Vec<TimingStep>>,
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
    pub fn all_timing_steps(&self) -> Vec<TimingStep> {
        if let Some(steps) = self.steps_from_raw_timing_params() {
            return steps;
        }

        let mut steps = self.timing_steps.clone().unwrap_or_default();
        if let Some(ramp_down_steps) = &self.ramp_down_steps {
            steps.extend(ramp_down_steps.clone());
        }
        self.infer_missing_return_sweep(&mut steps);
        steps
    }

    fn steps_from_raw_timing_params(&self) -> Option<Vec<TimingStep>> {
        let raw = self.raw_timing_params.as_ref()?;
        let je_initial = raw.get("je_initial")?.as_f64()?;
        let je_final = raw.get("je_final")?.as_f64()?;
        let je_step = raw.get("je_step")?.as_f64()?;
        let ramp_time = raw.get("ramp_time")?.as_f64()?;
        let stable_time = raw.get("stable_time")?.as_f64()?;
        if je_step == 0.0 || ramp_time < 0.0 || stable_time <= 0.0 {
            return None;
        }

        let (mut steps, total_up_time) =
            build_steps(je_initial, je_final, je_step, ramp_time, stable_time, 0.0);
        if raw
            .get("ramp_down")
            .and_then(|value| value.as_bool())
            .unwrap_or(false)
        {
            let (down_steps, _) = build_steps(
                je_final,
                je_initial,
                je_step,
                ramp_time,
                stable_time,
                total_up_time,
            );
            steps.extend(down_steps);
        }
        Some(steps)
    }

    fn infer_missing_return_sweep(&self, steps: &mut Vec<TimingStep>) {
        if steps.is_empty() || self.ramp_down_steps.as_ref().is_some_and(|s| !s.is_empty()) {
            return;
        }

        let summary = match &self.timing_params {
            Some(summary) => summary,
            None => return,
        };
        let total_steps = match summary.n_steps {
            Some(n) if n as usize > steps.len() => n as usize,
            _ => return,
        };
        let solve_time = match summary.solve_time {
            Some(t) if t > steps.last().unwrap().stable_end => t,
            _ => return,
        };

        let missing = total_steps - steps.len();
        let target_je = steps.first().unwrap().je_start;
        let mut current_je = steps.last().unwrap().je_end;
        if (current_je - target_je).abs() < f64::EPSILON {
            return;
        }

        let mut t = steps.last().unwrap().stable_end;
        let direction = if target_je >= current_je { 1.0 } else { -1.0 };
        let templates: Vec<TimingStep> = steps.iter().rev().cloned().collect();
        for i in 0..missing {
            if t >= solve_time {
                break;
            }
            let template = &templates[i.min(templates.len() - 1)];
            let ramp_time = template.ramp_end - template.ramp_start;
            let stable_time = template.stable_end - template.ramp_end;
            if ramp_time < 0.0 || stable_time <= 0.0 {
                break;
            }

            let delta = (template.je_end - template.je_start).abs();
            let mut next_je = current_je + direction * delta;
            if direction > 0.0 {
                next_je = next_je.min(target_je);
            } else {
                next_je = next_je.max(target_je);
            }

            steps.push(TimingStep {
                je_start: current_je,
                je_end: next_je,
                ramp_start: t,
                ramp_end: t + ramp_time,
                stable_end: t + ramp_time + stable_time,
            });
            current_je = next_je;
            t += ramp_time + stable_time;

            if (current_je - target_je).abs() < f64::EPSILON {
                break;
            }
        }
    }

    pub fn display_label(&self) -> String {
        let id = &self.run_id[..8.min(self.run_id.len())];
        let film = match &self.device_params {
            Some(dp) => format!(
                "{}x{}",
                dp.film_width.unwrap_or(0.0),
                dp.film_height.unwrap_or(0.0)
            ),
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
        let frames = self
            .n_frames
            .map(|n| format!("{}fr", n))
            .unwrap_or("-".into());
        format!("{} | {} | {} | {} | {}", id, film, je, frames, self.status)
    }
}

fn build_steps(
    je_initial: f64,
    je_final: f64,
    je_step: f64,
    ramp_time: f64,
    stable_time: f64,
    t_offset: f64,
) -> (Vec<TimingStep>, f64) {
    let n_steps = ((je_final - je_initial).abs() / je_step.abs())
        .round()
        .max(1.0) as usize;
    let period = ramp_time + stable_time;
    let sign = if je_final >= je_initial { 1.0 } else { -1.0 };

    let mut steps = Vec::with_capacity(n_steps);
    for i in 0..n_steps {
        let t = t_offset + i as f64 * period;
        let je_start = je_initial + sign * i as f64 * je_step.abs();
        let mut je_end = je_start + sign * je_step.abs();
        if sign > 0.0 {
            je_end = je_end.min(je_final);
        } else {
            je_end = je_end.max(je_final);
        }
        steps.push(TimingStep {
            je_start,
            je_end,
            ramp_start: t,
            ramp_end: t + ramp_time,
            stable_end: t + period,
        });
    }

    (steps, n_steps as f64 * period)
}

#[cfg(test)]
mod tests {
    use super::{RunInfo, TimingStep, TimingSummary};
    use serde_json::json;

    #[test]
    fn raw_timing_builds_return_sweep() {
        let run = RunInfo {
            run_id: "r".into(),
            status: "completed".into(),
            created_at: "".into(),
            n_sites: None,
            n_frames: None,
            device_params: None,
            timing_params: None,
            raw_timing_params: Some(json!({
                "je_initial": 0.0,
                "je_final": 1.0,
                "je_step": 0.5,
                "ramp_time": 2.0,
                "stable_time": 3.0,
                "ramp_down": true
            })),
            timing_steps: None,
            ramp_down_steps: None,
            solver_options: None,
        };

        let steps = run.all_timing_steps();
        assert_eq!(steps.len(), 4);
        assert_eq!(steps[0].je_start, 0.0);
        assert_eq!(steps[0].je_end, 0.5);
        assert_eq!(steps[1].je_end, 1.0);
        assert_eq!(steps[2].je_start, 1.0);
        assert_eq!(steps[2].je_end, 0.5);
        assert_eq!(steps[3].je_end, 0.0);
        assert_eq!(steps[2].ramp_start, 10.0);
    }

    #[test]
    fn manifest_summary_infers_missing_return_sweep() {
        let run = RunInfo {
            run_id: "r".into(),
            status: "completed".into(),
            created_at: "".into(),
            n_sites: None,
            n_frames: None,
            device_params: None,
            timing_params: Some(TimingSummary {
                mode: Some("simple".into()),
                n_steps: Some(4),
                solve_time: Some(20.0),
            }),
            raw_timing_params: Some(json!({})),
            timing_steps: Some(vec![
                TimingStep {
                    je_start: 0.0,
                    je_end: 0.5,
                    ramp_start: 0.0,
                    ramp_end: 2.0,
                    stable_end: 5.0,
                },
                TimingStep {
                    je_start: 0.5,
                    je_end: 1.0,
                    ramp_start: 5.0,
                    ramp_end: 7.0,
                    stable_end: 10.0,
                },
            ]),
            ramp_down_steps: None,
            solver_options: None,
        };

        let steps = run.all_timing_steps();
        assert_eq!(steps.len(), 4);
        assert_eq!(steps[2].je_start, 1.0);
        assert_eq!(steps[2].je_end, 0.5);
        assert_eq!(steps[2].ramp_start, 10.0);
        assert_eq!(steps[3].je_start, 0.5);
        assert_eq!(steps[3].je_end, 0.0);
        assert_eq!(steps[3].stable_end, 20.0);
    }
}
