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
        }));
        let stop = Arc::new(Mutex::new(false));
        let p = progress.clone();
        let s = stop.clone();

        let handle = std::thread::Builder::new()
            .name("iv-scanner".into())
            .spawn(move || {
                scan_iv(&client, &run_id, &index, &timing_steps, average_time, &p, &s);
            })
            .ok();

        IVScanner { progress, stop, thread: handle }
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
    let n_steps = timing_steps.len();

    if n_steps == 0 || total_frames == 0 {
        let mut p = progress.lock().unwrap();
        p.done = true;
        return;
    }

    // Estimate frame-to-time mapping.
    // total_time = last step's stable_end. frame_density ≈ total_frames / total_time.
    // This gives a fast frame index estimate for each step boundary without
    // reading any frame data from MinIO.
    let total_time = timing_steps.last().unwrap().stable_end;
    let frame_rate = total_frames as f64 / total_time; // frames per unit time

    let reader = FrameReader::new(client, run_id, index);
    let mut points = Vec::new();
    let mut total_scanned = 0usize;

    for (si, step) in timing_steps.iter().enumerate() {
        if *stop.lock().unwrap() {
            return;
        }

        let avg_start = compute_avg_start(step, average_time);
        let je = step.je_end;

        // Estimate frame range for this step
        let frame_start = ((step.ramp_start * frame_rate) as usize).max(0);
        let frame_end = ((step.stable_end * frame_rate) as usize).min(total_frames - 1);
        let avg_frame_start = ((avg_start * frame_rate) as usize).max(frame_start);

        // Read running_state only for frames in the averaging window
        let mut v_sum = 0.0f64;
        let mut v_count = 0usize;
        let mut frames_in_step = 0usize;

        // Scan a few frames before avg_frame_start to handle estimation error
        let scan_start = if avg_frame_start > 5 { avg_frame_start - 5 } else { 0 };

        for fi in scan_start..=frame_end {
            if fi >= total_frames {
                break;
            }

            // Read running_state to get voltage and time
            match reader.read_running_state(fi) {
                Ok(Some((rsmu, rsdt))) => {
                    // Reconstruct time from dt cumulative sum
                    let dt_sum: f64 = rsdt.iter().sum();

                    // Check if in averaging window using timing boundaries
                    // Since we don't have exact frame time, use frame index estimate
                    // with a generous window
                    frames_in_step += 1;
                    if fi >= avg_frame_start {
                        let v = compute_frame_voltage(&rsmu, &rsdt);
                        if !v.is_nan() {
                            v_sum += v;
                            v_count += 1;
                        }
                    }
                }
                _ => {}
            }

            total_scanned += 1;
        }

        if v_count > 0 {
            points.push(IVPoint {
                i: je,
                v: v_sum / v_count as f64,
                step_idx: si,
            });
        }

        // Publish progress
        {
            let mut p = progress.lock().unwrap();
            p.points = points.clone();
            p.steps_completed = si + 1;
            p.frames_scanned = total_scanned;
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
        voltage_samples.iter()
            .zip(rsdt.iter())
            .map(|(v, dt)| v * dt)
            .sum::<f64>() / dt_sum
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
