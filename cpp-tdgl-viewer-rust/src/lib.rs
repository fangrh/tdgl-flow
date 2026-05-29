mod discrete_reader;
mod hdf5_index;
mod minio_client;

use discrete_reader::DiscreteReader;
use hdf5_index::Hdf5Index;
use image::ImageEncoder;
use minio_client::MinioClient;
use pyo3::prelude::*;

#[pyclass]
struct CppTdglViewer {
    #[allow(dead_code)]
    minio_url: String,
    #[allow(dead_code)]
    bucket: String,
    run_id: String,
    client: MinioClient,
    index: Option<Hdf5Index>,
    reader: Option<DiscreteReader>,
}

#[pymethods]
impl CppTdglViewer {
    #[new]
    fn new(minio_url: String, bucket: String) -> Self {
        CppTdglViewer {
            minio_url: minio_url.clone(),
            bucket,
            run_id: String::new(),
            client: MinioClient::new(&minio_url, "tdgl-results"),
            index: None,
            reader: None,
        }
    }

    fn open(&mut self, run_id: &str) -> PyResult<()> {
        self.run_id = run_id.to_string();

        let index_key = format!("tdgl-runs/{}/discrete_index.json", self.run_id);
        let json = self.client
            .read_text_optional(&index_key)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e))?
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("discrete_index.json not found for run {}", run_id)
            ))?;

        let index = Hdf5Index::from_json(&json)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("JSON parse error: {}", e)))?;

        let mut reader = DiscreteReader::new(
            self.client.clone(),
            &self.run_id,
            index.clone(),
        );

        reader.load_mesh()
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e))?;

        self.index = Some(index);
        self.reader = Some(reader);

        Ok(())
    }

    fn get_step_count(&self) -> usize {
        self.index.as_ref().map(|i| i.n_steps()).unwrap_or(0)
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

    fn get_je(&self, step_idx: usize) -> f64 {
        self.index.as_ref()
            .and_then(|i| i.steps.get(step_idx))
            .map(|s| s.je)
            .unwrap_or(0.0)
    }

    fn get_mesh_points(&self) -> usize {
        self.reader.as_ref().map(|r| r.n_sites()).unwrap_or(0)
    }

    fn render_frame(&self, step_idx: usize, frame_idx: usize) -> PyResult<Vec<u8>> {
        let reader = self.reader.as_ref()
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("no run opened"))?;

        let psi = reader.read_psi(step_idx, frame_idx)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e))?;

        let n_sites = reader.n_sites();
        if psi.len() != n_sites * 2 {
            return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("psi size mismatch: expected {}, got {}", n_sites * 2, psi.len())
            ));
        }

        let img = render_2x2_panel(&psi, n_sites);
        Ok(img)
    }

    fn close(&mut self) {
        self.index = None;
        self.reader = None;
        self.run_id.clear();
    }
}

fn render_2x2_panel(psi: &[f64], n_sites: usize) -> Vec<u8> {
    use image::{ImageBuffer, Rgb, RgbImage};

    let (w, h) = if n_sites > 1000 {
        (256u32, 128u32)
    } else if n_sites > 100 {
        (128u32, 64u32)
    } else {
        (64u32, 32u32)
    };

    let mut img: RgbImage = ImageBuffer::new(w * 2, h * 2);

    let max_val = psi.iter()
        .step_by(2)
        .map(|&re| re * re)
        .fold(0.0f64, |a, b| a.max(b))
        .sqrt()
        .max(1e-10);

    let step = ((n_sites as f64 / (w as f64 * h as f64)).sqrt() as usize).max(1);

    for y in 0..h {
        for x in 0..w {
            let idx = ((y as usize) * step * n_sites + (x as usize) * step) * 2;
            if idx + 1 < psi.len() {
                let re = psi[idx];
                let im = psi[idx + 1];
                let amp = (re * re + im * im).sqrt() / max_val;
                let v = (255.0 * amp.min(1.0)) as u8;
                let pixel = Rgb([v, v, v.min(200)]);

                img.put_pixel(x, y, pixel);
                img.put_pixel(x + w, y, Rgb([0, v / 2, v]));
                img.put_pixel(x, y + h, Rgb([v, 0, 0]));
                img.put_pixel(x + w, y + h, Rgb([0, v, 0]));
            }
        }
    }

    let mut png_bytes: Vec<u8> = Vec::new();
    let encoder = image::codecs::png::PngEncoder::new(&mut png_bytes);
    encoder.write_image(&img, w * 2, h * 2, image::ExtendedColorType::Rgb8)
        .expect("PNG encode failed");

    png_bytes
}

#[pymodule]
fn cpp_tdgl_viewer_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<CppTdglViewer>()?;
    Ok(())
}
