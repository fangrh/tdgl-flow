mod buffer;
pub mod colormaps;
mod discrete_index;
mod discrete_reader;
mod frame_reader;
pub mod hdf5_index;
mod interp;
pub mod iv;
mod minio;
pub mod renderer;
pub mod run_info;

use std::collections::HashMap;
use std::io::Write;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use buffer::FrameBuffer;
use hdf5_index::H5Index;
use interp::InterpolationGrid;
use iv::{IVPoint, IVScanner};
use minio::MinioClient;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

const NX: usize = 100;
const NY: usize = 50;

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
    interp: Option<InterpolationGrid>,
    step_vt_cache: HashMap<usize, Vec<(f64, f64)>>,
    last_iv_point_count: usize, // track IV progress to invalidate cache
    show_vt_dot: bool,
    iv_average_time: Option<f64>,
    last_h5_size: Option<u64>,
    last_viewer_index_size: Option<u64>,
    last_viewer_index_etag: Option<String>,
    iv_legacy_points: Vec<IVPoint>,
    debug_log: Mutex<Option<std::fs::File>>,
}

impl TdglViewer {
    fn log(&self, msg: &str) {
        if let Ok(mut guard) = self.debug_log.lock() {
            if let Some(file) = guard.as_mut() {
                let ts = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_secs_f64())
                    .unwrap_or(0.0);
                let _ = writeln!(file, "[{:.3}] {}", ts, msg);
                let _ = file.flush();
            }
        }
    }

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
        Some(vt)
    }

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
            interp: None,
            step_vt_cache: HashMap::new(),
            last_iv_point_count: 0,
            show_vt_dot: true,
            iv_average_time: Some(0.5),
            last_h5_size: None,
            last_viewer_index_size: None,
            last_viewer_index_etag: None,
            iv_legacy_points: Vec::new(),
            debug_log: Mutex::new(None),
        }
    }

    #[pyo3(signature = (path=None))]
    fn enable_debug(&self, path: Option<String>) -> PyResult<()> {
        let log_path = path.unwrap_or_else(|| {
            std::env::temp_dir().join("tdgl-viewer-debug.log").to_string_lossy().to_string()
        });
        let file = std::fs::File::create(&log_path)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Failed to create debug log: {}", e
            )))?;
        if let Ok(mut guard) = self.debug_log.lock() {
            *guard = Some(file);
        }
        self.log(&format!("Debug logging enabled -> {}", log_path));
        Ok(())
    }

    fn disable_debug(&self) {
        self.log("Debug logging disabled");
        if let Ok(mut guard) = self.debug_log.lock() {
            *guard = None;
        }
    }

    fn is_debug_enabled(&self) -> bool {
        self.debug_log.lock().map(|g| g.is_some()).unwrap_or(false)
    }

    #[pyo3(signature = (refresh=false))]
    fn list_runs(&mut self, refresh: bool) -> PyResult<Vec<String>> {
        if refresh || self.runs.is_empty() {
            self.runs = self
                .client
                .list_runs()
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        }
        Ok(self.runs.iter().map(|r| r.display_label()).collect())
    }

    #[pyo3(signature = (run_id=None, run_index=None))]
    fn open(&mut self, run_id: Option<&str>, run_index: Option<usize>) -> PyResult<()> {
        if self.runs.is_empty() {
            self.list_runs(true)?;
        }
        // If looking up by run_id and not found, refresh the list once
        if let Some(id) = run_id {
            if !self.runs.iter().any(|r| r.run_id == id) {
                self.list_runs(true)?;
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
        // Stop any existing IV scanner
        self.iv_scanner = None;
        self.iv_legacy_points.clear();
        let run = &self.runs[idx];

        let t0 = Instant::now();
        self.log(&format!("open() run_id={}", run.run_id));

        let index = hdf5_index::build_index(&self.client, &run.run_id, Some(&|msg| self.log(msg)))
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;

        self.log(&format!(
            "open() build_index OK: {} frames, file_size={}, {:.1}s",
            index.total_frames,
            index.file_size,
            t0.elapsed().as_secs_f64()
        ));

        // Track sizes for refresh checks (use index metadata, no extra HEAD requests)
        self.last_h5_size = if index.file_size > 0 { Some(index.file_size) } else { None };
        self.last_viewer_index_size = None;
        self.last_viewer_index_etag = None;

        // Read mesh sites and build interpolation grid
        let reader = frame_reader::FrameReader::new(&self.client, &run.run_id, &index);
        let sites = reader
            .read_mesh_sites()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        self.log(&format!(
            "open() mesh loaded: {} sites, total {:.1}s",
            sites.len(),
            t0.elapsed().as_secs_f64()
        ));
        self.interp = Some(InterpolationGrid::new(&sites, NX, NY));

        self.index = Some(index.clone());

        // Auto-start IV scanner
        let timing_steps = run.all_timing_steps();
        if !timing_steps.is_empty() {
            let client = Arc::new(MinioClient::new(&self.minio_url, "tdgl-results"));
            let scan_index = Arc::new(index);
            let scan_run_id = run.run_id.clone();
            self.iv_scanner = Some(IVScanner::start(
                client,
                scan_run_id,
                scan_index,
                timing_steps,
                self.iv_average_time,
            ));
        }
        Ok(())
    }

    fn render_frame<'py>(
        &mut self,
        py: Python<'py>,
        frame_idx: usize,
    ) -> PyResult<Bound<'py, PyBytes>> {
        // Merge legacy + scanner IV points for change tracking.
        // Legacy points come from previous scanner instances (before index rebuild).
        // Scanner points are newer and preferred when step_idx overlaps.
        let scanner_points: Vec<IVPoint> = match &self.iv_scanner {
            Some(scanner) => scanner.get_progress().points,
            None => Vec::new(),
        };
        let merged_count = {
            let mut steps: HashMap<usize, ()> = HashMap::new();
            for pt in &self.iv_legacy_points {
                steps.insert(pt.step_idx, ());
            }
            for pt in &scanner_points {
                steps.insert(pt.step_idx, ());
            }
            steps.len()
        };
        let iv_changed = merged_count != self.last_iv_point_count;
        if iv_changed {
            self.log(&format!(
                "render_frame({}) iv_changed: {} -> {} merged (legacy={}, scanner={})",
                frame_idx, self.last_iv_point_count, merged_count,
                self.iv_legacy_points.len(), scanner_points.len()
            ));
            self.last_iv_point_count = merged_count;
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
                        current_v = Some(v);
                        // Filter highlight against step V(t) data to catch outliers
                        let is_outlier = vt_data
                            .as_ref()
                            .map_or(false, |vt| is_voltage_outlier(v, vt));
                        if !is_outlier {
                            highlight_vt = Some((t_rel, v));
                        }
                    }
                }
                _ => {}
            }
        }

        // I-V curve: merge legacy + scanner points (prefer scanner's newer values)
        let iv_data: Option<Vec<(f64, f64)>> = {
            let mut merged: HashMap<usize, (f64, f64)> = HashMap::new();
            for pt in &self.iv_legacy_points {
                merged.insert(pt.step_idx, (pt.i, pt.v));
            }
            for pt in &scanner_points {
                merged.insert(pt.step_idx, (pt.i, pt.v));
            }
            if merged.is_empty() {
                None
            } else {
                let mut points: Vec<(usize, (f64, f64))> = merged.into_iter().collect();
                points.sort_by_key(|(step_idx, _)| *step_idx);
                Some(points.into_iter().map(|(_, point)| point).collect())
            }
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
                        color: [30, 40, 75, 255],
                    },
                    renderer::PlotRegion {
                        x0: ramp_end,
                        x1: stable_end,
                        color: [18, 50, 38, 255],
                    },
                    renderer::PlotRegion {
                        x0: avg_start,
                        x1: stable_end,
                        color: [80, 60, 18, 255],
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

        let t0 = Instant::now();
        self.log(&format!("refresh_index() run_id={}", run_id));

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
            // Known size matches — unchanged
            (Some(info), Some(last)) if last > 0 && info.content_length == Some(last) => true,
            // HEAD returns 0 or None (multipart upload in progress) — can't tell, treat as unchanged
            (Some(info), Some(_)) if info.content_length.unwrap_or(0) == 0 => true,
            (None, Some(_)) => true,
            (None, None) => true,
            _ => false,
        };
        let index_unchanged = match (current_index.as_ref(), self.last_viewer_index_etag.as_ref()) {
            (Some(info), Some(last_etag)) if info.etag.as_deref() == Some(last_etag.as_str()) => true,
            (None, None) => true,
            _ => false,
        };

        self.log(&format!(
            "refresh_index() h5_unchanged={}, idx_unchanged={}, last_h5_size={:?}, last_idx_size={:?}",
            h5_unchanged, index_unchanged, self.last_h5_size, self.last_viewer_index_size
        ));

        if h5_unchanged && index_unchanged {
            return Ok(self.index.as_ref().map(|i| i.total_frames).unwrap_or(0));
        }

        // Something changed — try to rebuild index
        hdf5_index::clear_index_cache(Some(run_id));

        match hdf5_index::build_index(&self.client, run_id, Some(&|msg| self.log(msg))) {
            Ok(index) => {
                let h5_size = current_h5.as_ref().and_then(|info| info.content_length);
                self.last_h5_size = if h5_size.unwrap_or(0) > 0 { h5_size } else { self.last_h5_size };
                self.last_viewer_index_size = current_index.as_ref().and_then(|info| info.content_length);
                self.last_viewer_index_etag = current_index.as_ref().and_then(|info| info.etag.clone());
                let total = index.total_frames;
                self.log(&format!(
                    "refresh_index() rebuilt: {} frames, {:.3}s",
                    total,
                    t0.elapsed().as_secs_f64()
                ));
                self.buffer.clear();
                self.step_vt_cache.clear();
                self.index = Some(index);

                // Restart IV scanner with updated index to discover new timing steps.
                // Preserve old scanner's points as legacy data to avoid visual I-V reset.
                let idx = self.current_run_index.unwrap();
                let run = &self.runs[idx];
                let timing_steps = run.all_timing_steps();
                if !timing_steps.is_empty() {
                    // Merge old scanner points into legacy (dedup by step_idx)
                    if let Some(scanner) = &self.iv_scanner {
                        let prog = scanner.get_progress();
                        let mut merged: HashMap<usize, IVPoint> = HashMap::new();
                        for pt in &self.iv_legacy_points {
                            merged.insert(pt.step_idx, pt.clone());
                        }
                        for pt in &prog.points {
                            merged.insert(pt.step_idx, pt.clone());
                        }
                        self.iv_legacy_points = merged.into_values().collect();
                    }
                    let index = self.index.as_ref().unwrap();
                    let client = Arc::new(MinioClient::new(&self.minio_url, "tdgl-results"));
                    let scan_index = Arc::new(index.clone());
                    let scan_run_id = run.run_id.clone();
                    self.iv_scanner = Some(IVScanner::start(
                        client,
                        scan_run_id,
                        scan_index,
                        timing_steps,
                        self.iv_average_time,
                    ));
                    self.log(&format!(
                        "refresh_index() IV scanner restarted with {} frames, {} legacy points preserved",
                        total, self.iv_legacy_points.len()
                    ));
                }

                Ok(total)
            }
            Err(_) => {
                // Rebuild failed — keep existing index, will retry next refresh
                self.log(&format!(
                    "refresh_index() rebuild FAILED, keeping {} frames, {:.3}s",
                    self.index.as_ref().map(|i| i.total_frames).unwrap_or(0),
                    t0.elapsed().as_secs_f64()
                ));
                Ok(self.index.as_ref().map(|i| i.total_frames).unwrap_or(0))
            }
        }
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

        let old_count = match &self.iv_scanner {
            Some(scanner) => scanner.get_progress().points.len(),
            None => 0,
        };
        self.log(&format!(
            "start_iv_scan() RESTARTING scanner (had {} points), average_time={:?}",
            old_count, average_time
        ));

        self.iv_scanner = None;
        self.iv_average_time = average_time;
        self.iv_legacy_points.clear();
        self.buffer.clear();

        let client = Arc::new(MinioClient::new(&self.minio_url, "tdgl-results"));
        let scan_index = Arc::new(index.clone());
        let run_id = run.run_id.clone();

        self.iv_scanner = Some(IVScanner::start(
            client,
            run_id,
            scan_index,
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
                Ok(r#"{"points":[],"steps_completed":0,"steps_total":0,"frames_scanned":0,"done":false}"#.into())
            }
        }
    }

    /// Stop I-V scanner if running.
    fn stop_iv_scan(&mut self) {
        let old_count = match &self.iv_scanner {
            Some(scanner) => scanner.get_progress().points.len(),
            None => 0,
        };
        self.log(&format!(
            "stop_iv_scan() STOPPING scanner (had {} points)",
            old_count
        ));
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

#[pyclass]
struct TdglDiscreteViewer {
    minio_url: String,
    client: MinioClient,
    run_id: Option<String>,
    index: Option<discrete_index::DiscreteIndex>,
    buffer: FrameBuffer,
    mu_vmax: f64,
    show_vt_dot: bool,
    iv_average_time: Option<f64>,
    interp: Option<InterpolationGrid>,
    // IV data loaded from iv.json
    iv_points: Vec<(f64, f64)>,
    vt_by_step: HashMap<usize, Vec<(f64, f64)>>,
    debug_log: Mutex<Option<std::fs::File>>,
}

impl TdglDiscreteViewer {
    fn log(&self, msg: &str) {
        if let Ok(mut guard) = self.debug_log.lock() {
            if let Some(file) = guard.as_mut() {
                let ts = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_secs_f64())
                    .unwrap_or(0.0);
                let _ = writeln!(file, "[{:.3}] {}", ts, msg);
                let _ = file.flush();
            }
        }
    }

    fn frame_time(&self, frame_idx: usize) -> Option<f64> {
        let index = self.index.as_ref()?;
        index.frame_times.get(frame_idx).copied()
    }

    fn frame_to_step(&self, frame_idx: usize) -> Option<usize> {
        let index = self.index.as_ref()?;
        let step_list_idx = *index.frame_step_map.get(frame_idx)?;
        let step = index.steps.get(step_list_idx)?;
        Some(step_list_idx)
    }

    fn load_iv_from_minio(&mut self) {
        let run_id = match &self.run_id {
            Some(id) => id.clone(),
            None => return,
        };
        match self.client.read_text_optional(&self.client.iv_key(&run_id)) {
            Ok(Some(json_str)) => {
                if let Ok(data) = serde_json::from_str::<serde_json::Value>(&json_str) {
                    if let Some(points) = data.get("points").and_then(|p| p.as_array()) {
                        self.iv_points = points
                            .iter()
                            .filter_map(|p| {
                                let i = p.get("i")?.as_f64()?;
                                let v = p.get("v")?.as_f64()?;
                                Some((i, v))
                            })
                            .collect();
                    }
                    if let Some(vt) = data.get("vt_by_step") {
                        if let Some(obj) = vt.as_object() {
                            self.vt_by_step.clear();
                            for (key, arr) in obj {
                                if let Ok(step_idx) = key.parse::<usize>() {
                                    if let Some(items) = arr.as_array() {
                                        let entries: Vec<(f64, f64)> = items
                                            .iter()
                                            .filter_map(|item| {
                                                let a = item.as_array()?;
                                                Some((a.first()?.as_f64()?, a.get(1)?.as_f64()?))
                                            })
                                            .collect();
                                        self.vt_by_step.insert(step_idx, entries);
                                    }
                                }
                            }
                        }
                    }
                    self.log(&format!(
                        "load_iv_from_minio() loaded {} IV points, {} vt steps",
                        self.iv_points.len(),
                        self.vt_by_step.len()
                    ));
                }
            }
            Ok(None) => {
                self.log("load_iv_from_minio() iv.json not found");
            }
            Err(e) => {
                self.log(&format!("load_iv_from_minio() error: {}", e));
            }
        }
    }
}

#[pymethods]
impl TdglDiscreteViewer {
    #[new]
    fn new(minio_url: String) -> Self {
        let client = MinioClient::new(&minio_url, "tdgl-results");
        TdglDiscreteViewer {
            minio_url,
            client,
            run_id: None,
            index: None,
            buffer: FrameBuffer::new(21),
            mu_vmax: 0.0,
            show_vt_dot: true,
            iv_average_time: Some(0.5),
            interp: None,
            iv_points: Vec::new(),
            vt_by_step: HashMap::new(),
            debug_log: Mutex::new(None),
        }
    }

    #[pyo3(signature = (path=None))]
    fn enable_debug(&self, path: Option<String>) -> PyResult<()> {
        let log_path = path.unwrap_or_else(|| {
            std::env::temp_dir()
                .join("tdgl-discrete-debug.log")
                .to_string_lossy()
                .to_string()
        });
        let file = std::fs::File::create(&log_path).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Failed to create debug log: {}",
                e
            ))
        })?;
        if let Ok(mut guard) = self.debug_log.lock() {
            *guard = Some(file);
        }
        self.log(&format!("Debug logging enabled -> {}", log_path));
        Ok(())
    }

    fn disable_debug(&self) {
        if let Ok(mut guard) = self.debug_log.lock() {
            *guard = None;
        }
    }

    fn is_debug_enabled(&self) -> bool {
        self.debug_log
            .lock()
            .map(|g| g.is_some())
            .unwrap_or(false)
    }

    fn open(&mut self, run_id: &str) -> PyResult<()> {
        let t0 = Instant::now();
        self.log(&format!("open() run_id={}", run_id));
        self.run_id = Some(run_id.to_string());
        self.buffer.clear();

        // Download viewer-index.json
        let index_key = self.client.viewer_index_key(run_id);
        let json = self
            .client
            .read_text_optional(&index_key)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?
            .ok_or_else(|| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "viewer-index.json not found for run {}",
                    run_id
                ))
            })?;

        let index: discrete_index::DiscreteIndex = serde_json::from_str(&json)
            .map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "Failed to parse viewer-index.json: {}",
                    e
                ))
            })?;

        if !index.is_valid() {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "viewer-index.json is invalid (no frames or mismatched arrays)",
            ));
        }

        self.log(&format!(
            "open() parsed index: {} frames, {} steps, {:.1}s",
            index.total_frames,
            index.steps.len(),
            t0.elapsed().as_secs_f64()
        ));

        // Read mesh sites from first step's H5
        let reader = discrete_reader::DiscreteReader::new(&self.client, run_id, &index);
        let sites = reader
            .read_mesh_sites()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        self.log(&format!(
            "open() mesh loaded: {} sites, {:.1}s",
            sites.len(),
            t0.elapsed().as_secs_f64()
        ));
        self.interp = Some(InterpolationGrid::new(&sites, NX, NY));
        self.index = Some(index);

        // Load IV data from iv.json
        self.load_iv_from_minio();

        Ok(())
    }

    fn render_frame<'py>(
        &mut self,
        py: Python<'py>,
        frame_idx: usize,
    ) -> PyResult<Bound<'py, PyBytes>> {
        if let Some(png) = self.buffer.get(frame_idx) {
            return Ok(PyBytes::new_bound(py, &png));
        }

        let index = self
            .index
            .as_ref()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let run_id = self.run_id.as_ref().unwrap();

        let reader = discrete_reader::DiscreteReader::new(&self.client, run_id, index);
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

        let interp = self
            .interp
            .as_ref()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no interpolation grid"))?;
        let psi_grid = interp.interpolate(&psi_abs);
        let mu_grid = interp.interpolate(&mu);

        // Timing step info
        let step_list_idx = self.frame_to_step(frame_idx);
        let step_info = step_list_idx.and_then(|si| index.steps.get(si));
        let current_step = step_list_idx;

        let frame_time = self.frame_time(frame_idx);

        // V-t data for this step (from pre-computed iv.json), converted to relative time
        let vt_data: Option<Vec<(f64, f64)>> = current_step.and_then(|si| {
            if let Some(vt) = self.vt_by_step.get(&si) {
                let ramp_start = index.steps.get(si)?.ramp_start;
                Some(vt.iter().map(|(t, v)| (t - ramp_start, *v)).collect())
            } else {
                None
            }
        });

        // Compute instantaneous Je
        let mut current_je: Option<f64> = None;
        let mut current_v: Option<f64> = None;
        let mut highlight_vt: Option<(f64, f64)> = None;

        if let (Some(step), Some(ft)) = (step_info, frame_time) {
            let t_rel = ft - step.ramp_start;
            let ramp_duration = step.ramp_end - step.ramp_start;
            if ramp_duration > 0.0 && t_rel < ramp_duration {
                current_je = Some(step.je_start + (step.je_end - step.je_start) * t_rel / ramp_duration);
            } else {
                current_je = Some(step.je_end);
            }

            match reader.read_running_state(frame_idx) {
                Ok(Some((rsmu, rsdt))) => {
                    let v = iv::compute_frame_voltage(&rsmu, &rsdt);
                    if !v.is_nan() {
                        current_v = Some(v);
                        if self.show_vt_dot {
                            highlight_vt = Some((t_rel, v));
                        }
                    }
                }
                _ => {}
            }
        }

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

        let step_info_label = match (current_step, current_je) {
            (Some(si), Some(je)) => {
                let total = index.steps.len();
                Some(format!("step {}/{} Je={:.3}", si + 1, total, je))
            }
            _ => None,
        };

        let vt_regions: Option<Vec<renderer::PlotRegion>> = step_info.map(|step| {
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
                    color: [30, 40, 75, 255],
                },
                renderer::PlotRegion {
                    x0: ramp_end,
                    x1: stable_end,
                    color: [18, 50, 38, 255],
                },
                renderer::PlotRegion {
                    x0: avg_start,
                    x1: stable_end,
                    color: [80, 60, 18, 255],
                },
            ]
        });

        let iv_data_ref: Vec<(f64, f64)> = self.iv_points.clone();
        let png = renderer::render_frame_2x2(
            &psi_grid,
            &mu_grid,
            effective_mu_vmax,
            frame_idx,
            index.total_frames,
            vt_data.as_deref(),
            vt_regions.as_deref(),
            highlight_vt,
            if !iv_data_ref.is_empty() {
                Some(iv_data_ref.as_slice())
            } else {
                None
            },
            highlight_iv,
            step_info_label.as_deref(),
        );
        self.buffer.insert(frame_idx, png.clone());
        Ok(PyBytes::new_bound(py, &png))
    }

    fn total_frames(&self) -> PyResult<usize> {
        Ok(self.index.as_ref().map(|i| i.total_frames).unwrap_or(0))
    }

    fn frame_time_at(&self, frame_idx: usize) -> PyResult<Option<f64>> {
        Ok(self.frame_time(frame_idx))
    }

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

    fn refresh_index(&mut self) -> PyResult<usize> {
        let run_id = match &self.run_id {
            Some(id) => id.clone(),
            None => return Err(pyo3::exceptions::PyRuntimeError::new_err("no run opened")),
        };
        let t0 = Instant::now();
        self.log(&format!("refresh_index() run_id={}", run_id));

        // Re-download viewer-index.json
        let index_key = self.client.viewer_index_key(&run_id);
        let json = match self.client.read_text_optional(&index_key) {
            Ok(Some(j)) => j,
            Ok(None) => {
                self.log("refresh_index() viewer-index.json not found");
                return Ok(self.index.as_ref().map(|i| i.total_frames).unwrap_or(0));
            }
            Err(e) => {
                self.log(&format!("refresh_index() error: {}", e));
                return Ok(self.index.as_ref().map(|i| i.total_frames).unwrap_or(0));
            }
        };

        let index: discrete_index::DiscreteIndex = match serde_json::from_str(&json) {
            Ok(i) => i,
            Err(e) => {
                self.log(&format!("refresh_index() parse error: {}", e));
                return Ok(self.index.as_ref().map(|i| i.total_frames).unwrap_or(0));
            }
        };

        if !index.is_valid() {
            self.log("refresh_index() index invalid, keeping old");
            return Ok(self.index.as_ref().map(|i| i.total_frames).unwrap_or(0));
        }

        let old_frames = self.index.as_ref().map(|i| i.total_frames).unwrap_or(0);
        if index.total_frames == old_frames && index.status == self.index.as_ref().map(|i| i.status.clone()).unwrap_or_default() {
            return Ok(old_frames);
        }

        self.log(&format!(
            "refresh_index() {} -> {} frames, {:.3}s",
            old_frames,
            index.total_frames,
            t0.elapsed().as_secs_f64()
        ));

        self.buffer.clear();
        self.index = Some(index);

        // Reload IV data
        self.load_iv_from_minio();

        Ok(self.index.as_ref().map(|i| i.total_frames).unwrap_or(0))
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

    fn get_iv_progress(&self) -> PyResult<String> {
        let n = self.iv_points.len();
        let total = self.index.as_ref().map(|i| i.total_steps).unwrap_or(0);
        let completed = self.index.as_ref().map(|i| i.completed_steps).unwrap_or(0);
        let payload = serde_json::json!({
            "points_count": n,
            "steps_completed": completed,
            "steps_total": total,
            "done": completed >= total && total > 0,
        });
        serde_json::to_string(&payload)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }
}

#[pymodule]
fn tdgl_viewer_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TdglViewer>()?;
    m.add_class::<TdglDiscreteViewer>()?;
    Ok(())
}
