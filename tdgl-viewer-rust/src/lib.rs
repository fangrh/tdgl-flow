pub mod hdf5_index;
pub mod minio;
pub mod run_info;
pub mod frame_reader;

use pyo3::prelude::*;
use minio::MinioClient;

#[pyclass]
struct TdglViewer {
    minio_url: String,
    client: MinioClient,
}

#[pymethods]
impl TdglViewer {
    #[new]
    fn new(minio_url: String) -> Self {
        let client = MinioClient::new(&minio_url, "tdgl-results");
        TdglViewer { minio_url, client }
    }

    fn list_runs(&mut self) -> PyResult<Vec<String>> {
        self.client.list_runs().map(|runs| {
            runs.iter().map(|r| r.display_label()).collect()
        }).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
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