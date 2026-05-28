use pyo3::prelude::*;

#[pymodule]
fn cpp_tdgl_viewer_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<CppTdglViewer>()?;
    Ok(())
}

#[pyclass]
struct CppTdglViewer {
    minio_url: String,
    bucket: String,
    run_id: String,
    index: Option<CppHdf5Index>,
}

#[derive(Debug, Clone)]
pub struct CppHdf5Index {
    pub steps: Vec<CppStepInfo>,
}

#[derive(Debug, Clone)]
pub struct CppStepInfo {
    pub step_idx: u32,
    pub h5_file: String,
    pub total_frames: usize,
    pub je: f64,
    pub ramp_start: f64,
    pub stable_end: f64,
}

#[pymethods]
impl CppTdglViewer {
    #[new]
    fn new(minio_url: String, bucket: String) -> Self {
        CppTdglViewer {
            minio_url,
            bucket,
            run_id: String::new(),
            index: None,
        }
    }

    fn open(&mut self, run_id: &str) -> PyResult<()> {
        self.run_id = run_id.to_string();
        self.index = None;
        Ok(())
    }

    fn get_step_count(&self) -> usize {
        self.index.as_ref().map(|i| i.steps.len()).unwrap_or(0)
    }

    fn get_frame_count(&self, step_idx: usize) -> usize {
        self.index.as_ref()
            .and_then(|i| i.steps.get(step_idx))
            .map(|s| s.total_frames)
            .unwrap_or(0)
    }

    fn get_min_time(&self, step_idx: usize) -> f64 {
        self.index.as_ref()
            .and_then(|i| i.steps.get(step_idx))
            .map(|s| s.ramp_start)
            .unwrap_or(0.0)
    }

    fn get_max_time(&self, step_idx: usize) -> f64 {
        self.index.as_ref()
            .and_then(|i| i.steps.get(step_idx))
            .map(|s| s.stable_end)
            .unwrap_or(0.0)
    }

    fn render_frame(&self, step_idx: usize, frame_idx: usize) -> PyResult<Vec<u8>> {
        Ok(vec![0u8; 100])
    }
}
