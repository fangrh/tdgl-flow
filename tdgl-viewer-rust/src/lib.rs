use pyo3::prelude::*;

#[pyclass]
struct TdglViewer {
    minio_url: String,
}

#[pymethods]
impl TdglViewer {
    #[new]
    fn new(minio_url: String) -> Self {
        TdglViewer { minio_url }
    }

    fn open(&mut self, run_id: Option<&str>, run_index: Option<usize>) -> PyResult<()> {
        Ok(())
    }

    fn display(&self) -> PyResult<()> {
        Ok(())
    }
}

#[pymodule]
fn tdgl_viewer_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TdglViewer>()?;
    Ok(())
}