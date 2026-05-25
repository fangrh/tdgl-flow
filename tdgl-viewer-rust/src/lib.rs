mod minio;
mod run_info;
mod hdf5_index;
mod frame_reader;
mod renderer;
mod buffer;
mod iv;
mod colormaps;

use pyo3::prelude::*;
use pyo3::types::PyBytes;
use minio::MinioClient;
use hdf5_index::H5Index;
use buffer::FrameBuffer;

#[pyclass]
struct TdglViewer {
    minio_url: String,
    client: MinioClient,
    runs: Vec<run_info::RunInfo>,
    current_run_index: Option<usize>,
    index: Option<H5Index>,
    buffer: FrameBuffer,
    mu_vmax: f64,
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
            buffer: FrameBuffer::new(21),  // ±10 + current
            mu_vmax: 1.0,
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
        let run = &self.runs[idx];

        // Try to get a local file path for faster indexing if the H5 is available locally
        // Otherwise use HTTP range requests
        self.index = Some(hdf5_index::build_index(&self.client, &run.run_id)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?);
        Ok(())
    }

    fn render_frame<'py>(&mut self, py: Python<'py>, frame_idx: usize) -> PyResult<Bound<'py, PyBytes>> {
        // Check buffer first
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

    fn display(&self) -> PyResult<()> {
        // UI handled by Python side (Task 9)
        Ok(())
    }

    fn set_mu_vmax(&mut self, vmax: f64) {
        self.mu_vmax = vmax;
        self.buffer.clear();
    }
}

#[pymodule]
fn tdgl_viewer_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TdglViewer>()?;
    Ok(())
}