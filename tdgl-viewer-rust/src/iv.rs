use std::panic::AssertUnwindSafe;
use std::sync::{Arc, Mutex};

use crate::frame_reader::FrameReader;
use crate::hdf5_index::H5Index;
use crate::minio::MinioClient;
use crate::run_info::TimingStep;

#[derive(Debug, Clone, serde::Serialize)]
pub struct IVPoint {
    pub i: f64,
    pub v: f64,
    pub step_idx: usize,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct IVProgress {
    pub points: Vec<IVPoint>,
    pub steps_completed: usize,
    pub steps_total: usize,
    pub frames_scanned: usize,
    pub done: bool,
    pub last_error: Option<String>,
}

pub struct IVScanner {
    progress: Arc<Mutex<IVProgress>>,
    stop: Arc<Mutex<bool>>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl IVScanner {
    pub fn start(
        client: Arc<MinioClient>,
        run_id: String,
        index: Arc<H5Index>,
        timing_steps: Vec<TimingStep>,
        average_time: Option<f64>,
    ) -> Self {
        let progress = Arc::new(Mutex::new(IVProgress {
            points: Vec::new(),
            steps_completed: 0,
            steps_total: timing_steps.len(),
            frames_scanned: 0,
            done: false,
            last_error: None,
        }));
        let stop = Arc::new(Mutex::new(false));
        let p = progress.clone();
        let s = stop.clone();

        let handle = std::thread::Builder::new()
            .name("iv-scanner".into())
            .spawn(move || {
                let result = std::panic::catch_unwind(AssertUnwindSafe(|| {
                    scan_iv(
                        &client,
                        &run_id,
                        &index,
                        &timing_steps,
                        average_time,
                        &p,
                        &s,
                    );
                }));
                if let Err(e) = result {
                    let msg = if let Some(s) = e.downcast_ref::<String>() {
                        s.clone()
                    } else if let Some(s) = e.downcast_ref::<&str>() {
                        s.to_string()
                    } else {
                        "scanner panicked".to_string()
                    };
                    let mut p2 = p.lock().unwrap();
                    p2.last_error = Some(msg);
                    p2.done = true;
                }
            })
            .ok();

        IVScanner {
            progress,
            stop,
            thread: handle,
        }
    }

    pub fn get_progress(&self) -> IVProgress {
        self.progress.lock().unwrap().clone()
    }

    pub fn is_done(&self) -> bool {
        self.progress.lock().unwrap().done
    }

    pub fn stop(&self) {
        *self.stop.lock().unwrap() = true;
    }
}

impl Drop for IVScanner {
    fn drop(&mut self) {
        *self.stop.lock().unwrap() = true;
        if let Some(h) = self.thread.take() {
            let _ = h.join();
        }
    }
}

/// Find frame index range for a timing step using actual frame_times.
/// Returns None when no loaded frame intersects [t_start, t_end).
fn find_frame_range(frame_times: &[f64], t_start: f64, t_end: f64) -> Option<(usize, usize)> {
    if frame_times.is_empty() || t_start >= t_end {
        return None;
    }

    let first = frame_times.iter().position(|&t| t >= t_start)?;
    if frame_times[first] >= t_end {
        return None;
    }

    let last = frame_times[first..]
        .iter()
        .position(|&t| t >= t_end)
        .map(|offset| first + offset.saturating_sub(1))
        .unwrap_or_else(|| frame_times.len().saturating_sub(1));
    Some((first, last))
}

fn scan_iv(
    client: &MinioClient,
    run_id: &str,
    index: &H5Index,
    timing_steps: &[TimingStep],
    average_time: Option<f64>,
    progress: &Mutex<IVProgress>,
    stop: &Mutex<bool>,
) {
    let total_frames = index.total_frames;
    let frame_times = &index.frame_times;
    let n_steps = timing_steps.len();

    if n_steps == 0 || total_frames == 0 || frame_times.is_empty() {
        let mut p = progress.lock().unwrap();
        p.done = true;
        return;
    }

    let reader = FrameReader::new(client, run_id, index);
    let mut points = Vec::new();
    let mut total_scanned = 0usize;
    let mut last_err: Option<String> = None;

    for (si, step) in timing_steps.iter().enumerate() {
        if *stop.lock().unwrap() {
            return;
        }

        let avg_start = compute_avg_start(step, average_time);

        // Use actual frame_times to find frame range for this step.
        // If the file/index only contains earlier frames, stop here instead
        // of scanning the whole loaded prefix again for every future step.
        let Some((frame_start, frame_end)) =
            find_frame_range(frame_times, step.ramp_start, step.stable_end)
        else {
            if frame_times.last().copied().unwrap_or(0.0) < step.ramp_start {
                break;
            }
            let mut p = progress.lock().unwrap();
            p.points = points.clone();
            p.steps_completed = si + 1;
            p.frames_scanned = total_scanned;
            p.last_error = last_err.clone();
            continue;
        };

        let mut v_sum = 0.0f64;
        let mut v_count = 0usize;
        let mut read_ok = 0usize;
        let mut read_err = 0usize;

        for fi in frame_start..=frame_end {
            if fi >= total_frames {
                break;
            }
            let t = frame_times[fi];

            // Skip frames outside step range [ramp_start, stable_end)
            if t < step.ramp_start || t >= step.stable_end {
                continue;
            }

            match reader.read_running_state(fi) {
                Ok(Some((rsmu, rsdt))) => {
                    read_ok += 1;
                    let v = compute_frame_voltage(&rsmu, &rsdt);
                    if t >= avg_start && !v.is_nan() {
                        v_sum += v;
                        v_count += 1;
                    }
                }
                Ok(None) => {
                    // Frame has no running_state data (e.g., frame 0)
                }
                Err(e) => {
                    read_err += 1;
                    if last_err.is_none() {
                        last_err = Some(format!("step {} frame {} read error: {}", si, fi, e));
                    }
                }
            }

            total_scanned += 1;
        }

        if v_count > 0 {
            points.push(IVPoint {
                i: step.je_end,
                v: v_sum / v_count as f64,
                step_idx: si,
            });
        } else if read_err > 0 && last_err.is_none() {
            last_err = Some(format!(
                "step {}: 0/{}/{} frames OK/err for avg window",
                si, read_ok, read_err
            ));
        }

        // Publish progress
        {
            let mut p = progress.lock().unwrap();
            p.points = points.clone();
            p.steps_completed = si + 1;
            p.frames_scanned = total_scanned;
            p.last_error = last_err.clone();
        }
    }

    let mut p = progress.lock().unwrap();
    p.done = true;
}

pub fn compute_frame_voltage(rsmu: &[f64], rsdt: &[f64]) -> f64 {
    let k = rsdt.len();
    if k == 0 || rsmu.len() < 2 * k {
        return f64::NAN;
    }
    let voltage_samples: Vec<f64> = (0..k).map(|i| rsmu[i] - rsmu[k + i]).collect();
    let dt_sum: f64 = rsdt.iter().sum();
    if dt_sum > 0.0 {
        voltage_samples
            .iter()
            .zip(rsdt.iter())
            .map(|(v, dt)| v * dt)
            .sum::<f64>()
            / dt_sum
    } else {
        voltage_samples.iter().sum::<f64>() / k as f64
    }
}

fn compute_avg_start(step: &TimingStep, average_time: Option<f64>) -> f64 {
    match average_time {
        Some(frac) => {
            let stable_duration = step.stable_end - step.ramp_end;
            step.stable_end - frac * stable_duration
        }
        None => step.ramp_end,
    }
}

#[cfg(test)]
mod tests {
    use super::find_frame_range;

    #[test]
    fn frame_range_uses_actual_intersection() {
        let times = [0.0, 1.0, 2.0, 3.0, 4.0];
        assert_eq!(find_frame_range(&times, 1.5, 3.5), Some((2, 3)));
    }

    #[test]
    fn frame_range_is_empty_for_future_step() {
        let times = [0.0, 1.0, 2.0];
        assert_eq!(find_frame_range(&times, 3.0, 4.0), None);
    }

    #[test]
    fn frame_range_is_empty_for_gap_before_next_frame() {
        let times = [0.0, 10.0];
        assert_eq!(find_frame_range(&times, 2.0, 5.0), None);
    }
}
