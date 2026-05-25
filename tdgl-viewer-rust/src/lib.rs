mod minio;
mod run_info;
mod hdf5_index;
mod frame_reader;
mod renderer;
mod buffer;
mod iv;
mod colormaps;

use std::sync::Arc;

use pyo3::prelude::*;
use pyo3::types::PyBytes;
use minio::MinioClient;
use hdf5_index::H5Index;
use buffer::FrameBuffer;
use iv::IVScanner;

#[pyclass]
struct TdglViewer {
    minio_url: String,
    client: MinioClient,
    runs: Vec<run_info::RunInfo>,
    current_run_index: Option<usize>,
    index: Option<H5Index>,
    buffer: FrameBuffer,
    mu_vmax: f64,
    iv_scanner: Option<IVScanner>,
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
            mu_vmax: 1.0,
            iv_scanner: None,
        }
    }

    fn list_runs(&mut self) -> PyResult<Vec<String>> {
        self.runs = self.client.list_runs().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(e)
        })?;
        Ok(self.runs.iter().map(|r| r.display_label()).collect())
    }

    #[pyo3(signature = (run_id=None, run_index=None))]
    fn open(&mut self, run_id: Option<&str>, run_index: Option<usize>) -> PyResult<()> {
        if self.runs.is_empty() {
            self.list_runs()?;
        }
        let idx = match (run_id, run_index) {
            (Some(id), _) => self.runs.iter().position(|r| r.run_id == id)
                .ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("run {} not found", id)))?,
            (None, Some(i)) => i.min(self.runs.len().saturating_sub(1)),
            (None, None) => 0,
        };
        self.current_run_index = Some(idx);
        self.buffer.clear();
        // Stop any existing IV scanner
        self.iv_scanner = None;
        let run = &self.runs[idx];

        self.index = Some(hdf5_index::build_index(&self.client, &run.run_id)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?);
        Ok(())
    }

    fn render_frame<'py>(&mut self, py: Python<'py>, frame_idx: usize) -> PyResult<Bound<'py, PyBytes>> {
        if let Some(png) = self.buffer.get(frame_idx) {
            return Ok(PyBytes::new_bound(py, &png));
        }
        let index = self.index.as_ref()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let run_id = &self.runs[self.current_run_index.unwrap()].run_id;
        let reader = frame_reader::FrameReader::new(&self.client, run_id, index);
        let psi = reader.read_psi(frame_idx)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        let mu = reader.read_mu(frame_idx)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;
        let psi_abs: Vec<f64> = psi.iter().map(|[re, im]| (re*re + im*im).sqrt()).collect();
        let png = renderer::render_frame_2x2(&psi_abs, &mu, self.mu_vmax, frame_idx, index.total_frames);
        self.buffer.insert(frame_idx, png.clone());
        Ok(PyBytes::new_bound(py, &png))
    }

    fn total_frames(&self) -> PyResult<usize> {
        Ok(self.index.as_ref().map(|i| i.total_frames).unwrap_or(0))
    }

    fn get_run_info(&self) -> PyResult<Option<String>> {
        Ok(self.current_run_index.map(|idx| {
            serde_json::to_string(&self.runs[idx]).unwrap_or_default()
        }))
    }

    /// Get timing steps for the current run as JSON.
    fn get_timing_steps(&self) -> PyResult<String> {
        let idx = self.current_run_index
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let steps = self.runs[idx].timing_steps.clone().unwrap_or_default();
        serde_json::to_string(&steps)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    /// Start background I-V scan with given average_time fraction.
    /// average_time: fraction of stable period to average (e.g. 0.5).
    ///   If None, averages over full stable period.
    #[pyo3(signature = (average_time=None))]
    fn start_iv_scan(&mut self, average_time: Option<f64>) -> PyResult<()> {
        let idx = self.current_run_index
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let index = self.index.as_ref()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("no run opened"))?;
        let run = &self.runs[idx];
        let timing_steps = run.timing_steps.clone().unwrap_or_default();

        if timing_steps.is_empty() {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "no timing steps for this run"
            ));
        }

        // Stop existing scanner
        self.iv_scanner = None;

        let client = Arc::new(MinioClient::new(&self.minio_url, "tdgl-results"));
        let index = Arc::new(index.clone());
        let run_id = run.run_id.clone();

        self.iv_scanner = Some(IVScanner::start(
            client, run_id, index, timing_steps, average_time,
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
            None => Ok(r#"{"points":[],"steps_completed":0,"steps_total":0,"frames_scanned":0,"done":false}"#.into()),
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
