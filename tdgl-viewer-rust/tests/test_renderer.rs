#[test]
fn test_render_frame_produces_png() {
    let psi: Vec<f64> = vec![0.5; 5000]; // |psi| all 0.5 (NX*NY = 100*50)
    let mu: Vec<f64> = vec![0.0; 5000]; // mu all 0 (NX*NY = 100*50)
    let png = tdgl_viewer_rust::renderer::render_frame_2x2(
        &psi, &mu, 1.0, 0, 100, None, None, None, None, None, None,
    );
    // PNG magic bytes
    assert_eq!(&png[0..4], &[0x89, 0x50, 0x4E, 0x47]);
    assert!(
        png.len() > 1000,
        "PNG should be at least 1KB, got {} bytes",
        png.len()
    );
}

#[test]
fn test_apply_colormap() {
    let values = vec![0.0, 0.5, 1.0];
    let rgba =
        tdgl_viewer_rust::renderer::apply_colormap(&values, &tdgl_viewer_rust::colormaps::INFERNO);
    assert_eq!(rgba.len(), 12); // 3 values * 4 bytes
}
