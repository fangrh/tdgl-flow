#[test]
fn test_voltage_computation() {
    // rsmu: row0 = [1.0, 2.0, 3.0], row1 = [0.5, 1.0, 1.5]
    let rsmu = vec![1.0, 2.0, 3.0, 0.5, 1.0, 1.5];
    let rsdt = vec![0.1, 0.2, 0.1];
    let v = tdgl_viewer_rust::iv::compute_frame_voltage(&rsmu, &rsdt);
    // voltage_samples = [0.5, 1.0, 1.5]
    // dt_weighted = (0.5*0.1 + 1.0*0.2 + 1.5*0.1) / 0.4 = (0.05 + 0.2 + 0.15) / 0.4 = 1.0
    assert!((v - 1.0).abs() < 1e-10, "expected 1.0, got {}", v);
}

#[test]
fn test_voltage_zero_dt() {
    let rsmu = vec![2.0, 4.0, 0.0, 0.0]; // voltage_samples = [2, 4]
    let rsdt = vec![0.0, 0.0];
    let v = tdgl_viewer_rust::iv::compute_frame_voltage(&rsmu, &rsdt);
    // mean of [2, 4] = 3.0
    assert!((v - 3.0).abs() < 1e-10, "expected 3.0, got {}", v);
}

#[test]
fn test_voltage_empty() {
    let v = tdgl_viewer_rust::iv::compute_frame_voltage(&[], &[]);
    assert!(v.is_nan(), "empty should be NaN");
}