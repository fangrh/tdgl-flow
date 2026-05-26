mod buffer;
pub mod colormaps;
mod frame_reader;
pub mod hdf5_index;
mod interp;
pub mod iv;
mod minio;
pub mod renderer;
pub mod run_info;

use std::collections::HashMap;
use std::sync::Arc;

use buffer::FrameBuffer;
use hdf5_index::H5Index;
use interp::InterpolationGrid;
use iv::IVScanner;
use minio::MinioClient;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use serde::Deserialize;

const NX: usize = 100;
const NY: usize = 50;

#[derive(Debug, Clone, Deserialize)]
struct IVSidecarPoint {
    i: f64,
    v: f64,
    #[allow(dead_code)]
    step_idx: usize,
}

#[derive(Debug, Clone, Deserialize)]
struct IVSidecar {
    #[serde(default)]
    average_time: Option<f64>,
    #[serde(default)]
    points: Vec<IVSidecarPoint>,
    #[serde(default)]
    vt_by_step: HashMap<usize, Vec<(f64, f64)>>,
}

#[pyclass]
struct TdglViewer {
    minio_url: String,
    client: MinioClient,
    runs: Vec<run_info::RunInfo>,
    current_run_index: Option<usize>,
    index: Option<H5Index>,
    buffer: FrameBuffer,
    mu_vmax: f64, // 0.0 = auto-detect from data
    iv_scanner: Option<IVScanner>,
    iv_sidecar: Option<IVSidecar>,
    interp: Option<InterpolationGrid>,
    step_vt_cache: HashMap<usize, Vec<(f64, f64)>>,
    last_iv_point_count: usize, // track IV progress to invalidate cache
    show_vt_dot: bool,
    iv_average_time: Option<f64>,
    last_h5_size: Option<u64>,
    last_viewer_index_size: Option<u64>,
    last_viewer_index_etag: Option<String>,
}

impl TdglViewer {
    fn frame_time(&self, frame_idx: usize) -> Option<f64> {
        let index = self.index.as_ref()?;
        if index.frame_times.is_empty() {
            return None;
        }
        if frame_idx >= index.total_frames {
            return None;
        }
        Some(index.frame_times[frame_idx])
    }

    fn frame_to_step(&self, frame_idx: usize) -> Option<usize> {
        let idx = self.current_run_index?;
        let steps = self.runs[idx].all_timing_steps();
        if steps.is_empty() {
            return None;
        }
        let frame_time = self.frame_time(frame_idx)?;
        // Match Python: ramp_start <= t < stable_end
        for (si, step) in steps.iter().enumerate() {
            if frame_time >= step.ramp_start && frame_time < step.stable_end {
                return Some(si);
            }
        }
        // Past last step — clamp to last step (like Python)
        if !steps.is_empty() && frame_time >= steps.last().unwrap().stable_end {
            return Some(steps.len() - 1);
        }
        // Before first step
        if !steps.is_empty() && frame_time < steps[0].ramp_start {
            return Some(0);
        }
        None
    }

    fn compute_step_vt(&self, step_idx: usize) -> Option<Vec<(f64, f64)>> {
        let idx = self.current_run_index?;
        let steps = self.runs[idx].all_timing_steps();
        let step = steps.get(step_idx)?;
        if let Some(vt) = self
            .iv_sidecar
            .as_ref()
            .and_then(|sidecar| sidecar.vt_by_step.get(&step_idx))
        {
            return Some(vt.clone());
        }
        let index = self.index.as_ref()?;
        let run_id = &self.runs[idx].run_id;
        let reader = frame_reader::FrameReader::new(&self.client, run_id, index);

        let frame_times = &index.frame_times;
        if frame_times.is_empty() {
            return None;
        }

        // Find frame range for this step using precomputed frame_times.
        // Match Python: ramp_start <= t < stable_end.
        let frame_start = frame_times
            .iter()
            .position(|&t| t >= step.ramp_start)
            .filter(|&i| frame_times[i] < step.stable_end)?;
        let frame_end = frame_times[frame_start..]
            .iter()
            .position(|&t| t >= step.stable_end)
            .map(|offset| frame_start + offset.saturating_sub(1))
            .unwrap_or(index.total_frames.saturating_sub(1));
        let n_frames = frame_end.saturating_sub(frame_start) + 1;
        let sample = (n_frames / 300).max(1);

        let mut vt = Vec::new();
        for fi in (frame_start..=frame_end).step_by(sample) {
            match reader.read_running_state(fi) {
                Ok(Some((rsmu, rsdt))) => {
                    let v = iv::compute_frame_voltage(&rsmu, &rsdt);
                    let t = frame_times[fi] - step.ramp_start;
                    if !v.is_nan() {
                        vt.push((t, v));
                    }
                }
                _ => {}
            }
        }
        filter_voltage_outliers(&mut vt);
        Some(vt)
    }

    fn load_iv_sidecar(&self, run_id: &str) -> Result<Option<IVSidecar>, String> {
        let key = self.client.iv_key(run_id);
        let Some(json) = self.client.read_text_optional(&key)? else {
            return Ok(None);
        };
        serde_json::from_str::<IVSidecar>(&json)
            .map(Some)
            .map_err(|e| format!("Failed to parse {}: {}", key, e))
    }

    fn iv_sidecar_matches_average(&self, average_time: Option<f64>) -> bool {
        let Some(sidecar) = &self.iv_sidecar else {
            return false;
        };
        match (sidecar.average_time, average_time) {
            (Some(a), Some(b)) => (a - b).abs() < 1e-9,
            (None, None) => true,
            _ => false,
        }
    }
}

/// Filter voltage outliers using Median Absolute Deviation.
/// Removes points where |v - median| > threshold * MAD * 1.4826.
fn filter_voltage_outliers(vt: &mut Vec<(f64, f64)>) {
    if vt.len() < 3 {
        return;
    }
    let mut volts: Vec<f64> = vt.iter().map(|&(_, v)| v).collect();
    volts.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let median = volts[volts.len() / 2];
    let mut devs: Vec<f64> = volts.iter().map(|&v| (v - median).abs()).collect();
    devs.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let mad = devs[devs.len() / 2];
    if mad <= 0.0 {
        return;
    }
    let threshold = mad * 1.4826 * 5.0;
    vt.retain(|&(_, v)| (v - median).abs() <= threshold);
}

/// Check if a single voltage value is an outlier relative to a V(t) series.
fn is_voltage_outlier(v: f64, vt: &[(f64, f64)]) -> bool {
    if vt.len() < 3 {
        return false;
    }
    let mut volts: Vec<f64> = vt.iter().map(|&(_, v)| v).collect();
    volts.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let median = volts[volts.len() / 2];
    let mut devs: Vec<f64> = volts.iter().map(|&v| (v - median).abs()).collect();
    devs.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let mad = devs[devs.len() / 2];
    if mad <= 0.0 {
        return false;
    }
    let threshold = mad * 1.4826 * 5.0;
    (v - median).abs() > threshold
}

#[pymethods]
impl TdglViewer {
    #[new]
    fn new(minio_url: String) -> Self {
        let client = MinioClient::new(&minio_url, "tdgl-results");
        TdglViewer {
            minio_url,
            client,
            runs: Vec::new(),
            current_run_index: None,
            index: None,
            buffer: FrameBuffer::new(21),
            mu_vmax: 0.0, // auto-detect
            iv_scanner: None,
            iv_sidecar: None,
            interp: None,
            step_vt_cache: HashMap::new(),
            last_iv_point_count: 0,
            show_vt_dot: true,
            iv_average_time: None,
            last_h5_size: None,
            last_viewer_index_size: None,
            last_viewer_index_etag: None,
        }
    }

    fn list_runs(&mut self) -> PyResult<Vec<String>> {
        self.runs = self
            .client
            .list_runs()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        Ok(self.runs.iter().map(|r| r.display_label()).collect())
    }

    #[pyo3(signature = (run_id=None, run_index=None))]
    fn open(&mut self, run_id: Option<&str>, run_index: Option<usize>) -> PyResult<()> {
        if self.runs.is_empty() {
            self.list_runs()?;
        }
        // If looking up by run_id and not found, refresh the list once
        if let Some(id) = run_id {
            if !self.runs.iter().any(|r| r.run_id == id) {
                self.list_runs()?;
            }
        }
        let idx = match (run_id, run_index) {
            (Some(id), _) => self
                .runs
                .iter()
                .position(|r| r.run_id == id)
                .ok_or_else(|| {
                    pyo3::exceptions::PyValueError::new_err(format!("run {} not found", id))
                })?,
            (None, Some(i)) => i.min(self.runs.len().saturating_sub(1)),
            (None, None) => 0,
        };
        self.current_run_index = Some(idx);
        self.buffer.clear();
        self.step_vt_cache.clear();
        self.iv_sidecar = None;
        // Stop any existing IV scanner
        self.iv_scanner = None;
        let run = &self.runs[idx];

        let index = hdf5_index::build_index(&self.client, &run.run_id)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;

        // Track H5 file size for cheap refresh checks
        let h5_key = self.client.h5_key(&run.run_id);
        self.last_h5_size = self.client.object_size(&h5_key).ok().flatten();
        if let Ok(Some(info)) = self.client.object_info(&self.client.viewer_index_key(&run.run_id))
        {
            self.last_viewer_index_size = info.content_length;
            self.last_viewer_index_etag = info.etag;
        } else {
            self.last_viewer_index_size = None;
            self.last_viewer_index_etag = None;
        }

        // Read mesh sites and build interpolation grid
        let reader = frame_reader::FrameReader::new(&self.client, &run.run_id, &index);
        let sites = reader
            .read_mesh_sites()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        self.interp = Some(InterpolationGrid::new(&sites, NX, NY));
        self.iv_sidecar = self.load_iv_sidecar(&run.run_id).ok().flatten();

        self.index = Some(index);
        Ok(())
    }

    fn render_frame<'py>(
        &mut self,
        py: Python<'py>,
        frame_idx: usize,
    ) -> PyResult<Bound<'py, PyBytes>> {
        // Track IV progress but don't clear the entire buffer on every new point.
        // Clearing the buffer forces re-renders of all cached frames (each requiring
        // multiple MinIO range requests), which causes visible flicker during playback.
        let current_iv_count = match &self.iv_scanner {
            Some(scanner) => scanner.get_progress().points.len(),
            None => 0,
        };
        let iv_changed = current_iv_count != self.last_iv_point_count;
        if iv_changed {
            self.last_iv_point_count = current_iv_count;
        }

        // Use cached frame only if IV data hasn't changed since it was cached.
        // This avoids stale I-V curves without mass-invalidation.
        if !iv_changed {
            if let Some(png) = self.buffer.get(frame_idx) {
                return Ok(PyBytes::new_bound(py, &png));
            }
        }
        let index = self
            .index
            .as_ref()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let run_id = &self.runs[self.current_run_index.unwrap()].run_id;
        let reader = frame_reader::FrameReader::new(&self.client, run_id, index);
        let psi = reader
            .read_psi(frame_idx)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        let mu = reader
            .read_mu(frame_idx)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        let psi_abs: Vec<f64> = psi
            .iter()
            .map(|[re, im]| (re * re + im * im).sqrt())
            .collect();

        // Interpolate unstructured mesh to regular grid
        let interp = self
            .interp
            .as_ref()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no interpolation grid"))?;
        let psi_grid = interp.interpolate(&psi_abs);
        let mu_grid = interp.interpolate(&mu);

        // Determine current timing step from frame index
        let current_step = self.frame_to_step(frame_idx);

        // Current frame's time
        let frame_time = self.frame_time(frame_idx);

        // V vs t: raw voltage trace for the current step (cached per step)
        let vt_data: Option<Vec<(f64, f64)>> = match current_step {
            Some(si) => {
                if !self.step_vt_cache.contains_key(&si) {
                    if let Some(vt) = self.compute_step_vt(si) {
                        self.step_vt_cache.insert(si, vt);
                    }
                }
                self.step_vt_cache.get(&si).cloned()
            }
            None => None,
        };

        // Compute instantaneous Je at a given time within a step
        let je_at_time = |step: &run_info::TimingStep, t_rel: f64| -> f64 {
            let ramp_duration = step.ramp_end - step.ramp_start;
            if ramp_duration > 0.0 && t_rel < ramp_duration {
                step.je_start + (step.je_end - step.je_start) * t_rel / ramp_duration
            } else {
                step.je_end
            }
        };

        // Get timing step info for current frame
        let current_timing_step = current_step.and_then(|si| {
            let idx = self.current_run_index.unwrap();
            self.runs[idx].all_timing_steps().get(si).cloned()
        });

        // V-t highlight: current frame's (t, v) position within the step trace
        // Also compute instantaneous Je for I-V highlight
        let mut highlight_vt: Option<(f64, f64)> = None;
        let mut current_je: Option<f64> = None;
        let mut current_v: Option<f64> = None;

        if let (Some(step), Some(ft)) = (&current_timing_step, frame_time) {
            let t_rel = ft - step.ramp_start;
            current_je = Some(je_at_time(step, t_rel));
            match reader.read_running_state(frame_idx) {
                Ok(Some((rsmu, rsdt))) => {
                    let v = iv::compute_frame_voltage(&rsmu, &rsdt);
                    if !v.is_nan() {
                        // Filter highlight against step V(t) data to catch outliers
                        let is_outlier = vt_data
                            .as_ref()
                            .map_or(false, |vt| is_voltage_outlier(v, vt));
                        if !is_outlier {
                            highlight_vt = Some((t_rel, v));
                            current_v = Some(v);
                        }
                    }
                }
                _ => {}
            }
        }

        // I-V curve: accumulated averaged points from background scanner (one per step)
        let iv_data: Option<Vec<(f64, f64)>> = match &self.iv_scanner {
            Some(scanner) => {
                let prog = scanner.get_progress();
                Some(prog.points.iter().map(|pt| (pt.i, pt.v)).collect())
            }
            None => self
                .iv_sidecar
                .as_ref()
                .map(|sidecar| sidecar.points.iter().map(|pt| (pt.i, pt.v)).collect()),
        };

        // I-V highlight: current frame's instantaneous (Je, V) — like Python's cur_I, cur_V
        let highlight_iv = match (current_je, current_v) {
            (Some(je), Some(v)) => Some((je, v)),
            _ => None,
        };

        let effective_mu_vmax = if self.mu_vmax > 0.0 {
            self.mu_vmax
        } else {
            let mu_max = mu.iter().map(|v| v.abs()).fold(0.0f64, f64::max);
            (mu_max * 1.1).max(1e-10)
        };

        // Build step info label with instantaneous Je
        let step_info = match (current_step, current_je) {
            (Some(si), Some(je)) => {
                let idx = self.current_run_index.unwrap();
                let total = self.runs[idx].all_timing_steps().len();
                if total > 0 {
                    Some(format!("step {}/{} Je={:.3}", si + 1, total, je))
                } else {
                    None
                }
            }
            _ => None,
        };

        let vt_regions: Option<Vec<renderer::PlotRegion>> =
            current_timing_step.as_ref().map(|step| {
                let ramp_end = step.ramp_end - step.ramp_start;
                let stable_end = step.stable_end - step.ramp_start;
                let stable_duration = (step.stable_end - step.ramp_end).max(0.0);
                let avg_fraction = self.iv_average_time.unwrap_or(1.0).clamp(0.0, 1.0);
                let avg_start =
                    (step.stable_end - stable_duration * avg_fraction) - step.ramp_start;
                vec![
                    renderer::PlotRegion {
                        x0: 0.0,
                        x1: ramp_end,
                        color: [16, 26, 45, 255],
                    },
                    renderer::PlotRegion {
                        x0: ramp_end,
                        x1: stable_end,
                        color: [12, 34, 27, 255],
                    },
                    renderer::PlotRegion {
                        x0: avg_start,
                        x1: stable_end,
                        color: [58, 46, 15, 255],
                    },
                ]
            });

        let png = renderer::render_frame_2x2(
            &psi_grid,
            &mu_grid,
            effective_mu_vmax,
            frame_idx,
            index.total_frames,
            vt_data.as_deref(),
            vt_regions.as_deref(),
            if self.show_vt_dot { highlight_vt } else { None },
            iv_data.as_deref(),
            highlight_iv,
            step_info.as_deref(),
        );
        self.buffer.insert(frame_idx, png.clone());
        Ok(PyBytes::new_bound(py, &png))
    }

    fn total_frames(&self) -> PyResult<usize> {
        Ok(self.index.as_ref().map(|i| i.total_frames).unwrap_or(0))
    }

    /// Total solve time for the current run (from timing steps).
    fn solve_time(&self) -> PyResult<f64> {
        let idx = self
            .current_run_index
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let steps = self.runs[idx].all_timing_steps();
        if steps.is_empty() {
            return Ok(0.0);
        }
        Ok(steps.last().unwrap().stable_end)
    }

    /// Simulation time at a given frame index.
    fn frame_time_at(&self, frame_idx: usize) -> PyResult<Option<f64>> {
        Ok(self.frame_time(frame_idx))
    }

    /// Find the frame index closest to a given simulation time.
    /// Returns the frame whose frame_time is <= t, or the last frame if t exceeds all.
    fn time_to_frame(&self, t: f64) -> PyResult<usize> {
        let index = self
            .index
            .as_ref()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        if index.frame_times.is_empty() {
            return Ok(0);
        }
        let pos = index.frame_times.partition_point(|&ft| ft <= t);
        if pos == 0 {
            return Ok(0);
        }
        Ok(pos
            .saturating_sub(1)
            .min(index.total_frames.saturating_sub(1)))
    }

    /// Simulation time of the latest available frame.
    fn latest_frame_time(&self) -> PyResult<f64> {
        let index = self
            .index
            .as_ref()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        if index.frame_times.is_empty() {
            return Ok(0.0);
        }
        Ok(index.frame_times[index.total_frames.saturating_sub(1)])
    }

    /// Refresh the HDF5 index — only re-downloads if the H5 file has grown.
    /// Uses a HEAD request to check file size (cheap, no data transfer).
    /// Returns the new total_frames count.
    fn refresh_index(&mut self) -> PyResult<usize> {
        let idx = self
            .current_run_index
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let run_id = &self.runs[idx].run_id;

        // Cheap HEAD request to check if file has grown
        let h5_key = self.client.h5_key(run_id);
        let current_h5 = self
            .client
            .object_info(&h5_key)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        let current_index = self
            .client
            .object_info(&self.client.viewer_index_key(run_id))
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;

        let h5_unchanged = match (current_h5.as_ref(), self.last_h5_size) {
            (Some(info), Some(last)) => info.content_length == Some(last),
            (None, None) => true,
            _ => false,
        };
        let index_unchanged = match (current_index.as_ref(), self.last_viewer_index_size.as_ref()) {
            (Some(info), Some(last_size)) => {
                info.content_length == Some(*last_size) && info.etag == self.last_viewer_index_etag
            }
            (None, None) => true,
            _ => false,
        };

        if h5_unchanged && index_unchanged {
            return Ok(self.index.as_ref().map(|i| i.total_frames).unwrap_or(0));
        }

        // File has grown (or first check) — clear cache and re-scan
        self.last_h5_size = current_h5.as_ref().and_then(|info| info.content_length);
        self.last_viewer_index_size = current_index.as_ref().and_then(|info| info.content_length);
        self.last_viewer_index_etag = current_index.and_then(|info| info.etag);
        hdf5_index::clear_index_cache(Some(run_id));

        let index = hdf5_index::build_index(&self.client, run_id)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;

        let total = index.total_frames;
        self.buffer.clear();
        self.step_vt_cache.clear();
        self.index = Some(index);
        Ok(total)
    }

    fn get_run_info(&self) -> PyResult<Option<String>> {
        Ok(self
            .current_run_index
            .map(|idx| serde_json::to_string(&self.runs[idx]).unwrap_or_default()))
    }

    /// Get timing steps for the current run as JSON.
    fn get_timing_steps(&self) -> PyResult<String> {
        let idx = self
            .current_run_index
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let steps = self.runs[idx].all_timing_steps();
        serde_json::to_string(&steps)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    fn frame_diagnostics(&self, frame_idx: usize) -> PyResult<String> {
        let idx = self
            .current_run_index
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let index = self
            .index
            .as_ref()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let run = &self.runs[idx];
        let steps = run.all_timing_steps();
        let frame_time = self.frame_time(frame_idx);
        let step_idx = self.frame_to_step(frame_idx);
        let step = step_idx.and_then(|si| steps.get(si));
        let run_id = &run.run_id;
        let reader = frame_reader::FrameReader::new(&self.client, run_id, index);
        let voltage = match reader.read_running_state(frame_idx) {
            Ok(Some((rsmu, rsdt))) => {
                let v = iv::compute_frame_voltage(&rsmu, &rsdt);
                if v.is_nan() {
                    None
                } else {
                    Some(v)
                }
            }
            _ => None,
        };

        let vt = step_idx
            .and_then(|si| self.compute_step_vt(si))
            .unwrap_or_default();
        let payload = serde_json::json!({
            "frame": frame_idx,
            "frame_time": frame_time,
            "step_idx": step_idx,
            "steps_total": steps.len(),
            "step": step,
            "voltage": voltage,
            "vt_len": vt.len(),
            "vt_first": vt.first(),
            "vt_last": vt.last(),
        });
        serde_json::to_string(&payload)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    /// Diagnostic: check psi data quality for a frame.
    /// Reports whether psi is compressed and compares MinIO range-read vs hdf5-crate values.
    fn psi_diagnostics(&self, frame_idx: usize) -> PyResult<String> {
        let idx = self
            .current_run_index
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let index = self
            .index
            .as_ref()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let run_id = &self.runs[idx].run_id;
        let reader = frame_reader::FrameReader::new(&self.client, run_id, index);

        // Read psi via MinIO range request
        let psi = reader
            .read_psi(frame_idx)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        let psi_abs: Vec<f64> = psi
            .iter()
            .map(|[re, im]| (re * re + im * im).sqrt())
            .collect();

        let psi_offset = index.frame_psi_offsets.get(frame_idx).copied();
        let offset_valid = psi_offset.map_or(false, |o| o != 0xFFFFFFFFFFFFFFFF && o != 0);

        // Compute statistics on |ψ|²
        let psi_max = psi_abs.iter().cloned().fold(0.0f64, f64::max);
        let psi_min = psi_abs.iter().cloned().fold(f64::MAX, f64::min);
        let psi_mean = psi_abs.iter().sum::<f64>() / psi_abs.len().max(1) as f64;
        let near_zero_count = psi_abs.iter().filter(|&&v| v < 1e-6).count();

        // First 5 raw psi values for inspection
        let first_raw: Vec<[f64; 2]> = psi.iter().take(5).cloned().collect();

        // Mesh sites diagnostics
        let mesh_loc = &index.mesh_sites;
        let mesh_sites = reader.read_mesh_sites().ok();
        let mesh_diag = match &mesh_sites {
            Some(sites) => {
                let x_min = sites.iter().map(|p| p[0]).fold(f64::MAX, f64::min);
                let x_max = sites.iter().map(|p| p[0]).fold(f64::MIN, f64::max);
                let y_min = sites.iter().map(|p| p[1]).fold(f64::MAX, f64::min);
                let y_max = sites.iter().map(|p| p[1]).fold(f64::MIN, f64::max);
                let first_3: Vec<[f64; 2]> = sites.iter().take(3).cloned().collect();
                serde_json::json!({
                    "n_sites": sites.len(),
                    "x_range": [x_min, x_max],
                    "y_range": [y_min, y_max],
                    "first_3": first_3,
                    "mesh_offset": mesh_loc.offset,
                    "mesh_size": mesh_loc.size,
                })
            }
            None => {
                serde_json::json!({"error": "failed to read mesh_sites", "mesh_offset": mesh_loc.offset})
            }
        };

        // Interpolated grid diagnostics
        let grid_diag = match &self.interp {
            Some(interp) => {
                let psi_grid = interp.interpolate(&psi_abs);
                let g_max = psi_grid.iter().cloned().fold(0.0f64, f64::max);
                let g_min = psi_grid.iter().cloned().fold(f64::MAX, f64::min);
                let g_mean = psi_grid.iter().sum::<f64>() / psi_grid.len().max(1) as f64;
                let g_zero = psi_grid.iter().filter(|&&v| v < 1e-6).count();
                serde_json::json!({
                    "grid_size": psi_grid.len(),
                    "grid_min": g_min,
                    "grid_max": g_max,
                    "grid_mean": g_mean,
                    "grid_near_zero": g_zero,
                    "grid_zero_fraction": g_zero as f64 / psi_grid.len().max(1) as f64,
                })
            }
            None => serde_json::json!({"error": "no interpolation grid"}),
        };

        let payload = serde_json::json!({
            "frame": frame_idx,
            "psi_offset": psi_offset,
            "offset_valid": offset_valid,
            "psi_compressed": index.psi_compressed,
            "n_sites": index.mesh_points,
            "psi_abs_min": psi_min,
            "psi_abs_max": psi_max,
            "psi_abs_mean": psi_mean,
            "near_zero_count": near_zero_count,
            "first_5_raw_psi": first_raw,
            "mesh": mesh_diag,
            "grid": grid_diag,
        });
        serde_json::to_string(&payload)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    /// Start background I-V scan with given average_time fraction.
    /// average_time: fraction of stable period to average (e.g. 0.5).
    ///   If None, averages over full stable period.
    #[pyo3(signature = (average_time=None))]
    fn start_iv_scan(&mut self, average_time: Option<f64>) -> PyResult<()> {
        let idx = self
            .current_run_index
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let index = self
            .index
            .as_ref()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let run = &self.runs[idx];
        let timing_steps = run.all_timing_steps();

        if timing_steps.is_empty() {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "no timing steps for this run",
            ));
        }

        // Stop existing scanner
        self.iv_scanner = None;
        self.iv_average_time = average_time;
        self.buffer.clear();

        if self.iv_sidecar_matches_average(average_time)
            && self
                .iv_sidecar
                .as_ref()
                .is_some_and(|sidecar| !sidecar.points.is_empty())
        {
            return Ok(());
        }

        let client = Arc::new(MinioClient::new(&self.minio_url, "tdgl-results"));
        let index = Arc::new(index.clone());
        let run_id = run.run_id.clone();

        self.iv_scanner = Some(IVScanner::start(
            client,
            run_id,
            index,
            timing_steps,
            average_time,
        ));
        Ok(())
    }

    /// Get I-V scan progress as JSON.
    fn get_iv_progress(&self) -> PyResult<String> {
        match &self.iv_scanner {
            Some(scanner) => {
                let prog = scanner.get_progress();
                serde_json::to_string(&prog)
                    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
            }
            None => {
                if let Some(sidecar) = &self.iv_sidecar {
                    let points: Vec<iv::IVPoint> = sidecar
                        .points
                        .iter()
                        .map(|pt| iv::IVPoint {
                            i: pt.i,
                            v: pt.v,
                            step_idx: pt.step_idx,
                        })
                        .collect();
                    let prog = iv::IVProgress {
                        steps_completed: points.len(),
                        steps_total: points.len(),
                        frames_scanned: 0,
                        done: true,
                        last_error: None,
                        points,
                    };
                    serde_json::to_string(&prog)
                        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
                } else {
                    Ok(r#"{"points":[],"steps_completed":0,"steps_total":0,"frames_scanned":0,"done":false}"#.into())
                }
            }
        }
    }

    /// Stop I-V scanner if running.
    fn stop_iv_scan(&mut self) {
        self.iv_scanner = None;
    }

    fn display(&self) -> PyResult<()> {
        Ok(())
    }

    fn set_mu_vmax(&mut self, vmax: f64) {
        self.mu_vmax = vmax;
        self.buffer.clear();
    }

    fn set_show_vt_dot(&mut self, show: bool) {
        self.show_vt_dot = show;
        self.buffer.clear();
    }

    #[pyo3(signature = (run_id=None))]
    fn clear_cache(&self, run_id: Option<&str>) {
        hdf5_index::clear_index_cache(run_id);
    }
}

#[pymodule]
fn tdgl_viewer_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TdglViewer>()?;
    Ok(())
}
