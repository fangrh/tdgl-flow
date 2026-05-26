use tdgl_viewer_rust::run_info::RunInfo;

#[test]
fn test_parse_manifest() {
    let json = r#"{
        "run_id": "abc-123-def",
        "status": "completed",
        "created_at": "2026-05-25T10:00:00",
        "n_sites": 1500,
        "n_frames": 12400,
        "device_params": {"film_width": 6.0, "film_height": 4.0},
        "timing_params": {"mode": "step", "n_steps": 100},
        "raw_timing_params": {"je_initial": 0.0, "je_final": 20.0, "je_step": 0.2}
    }"#;
    let run: RunInfo = serde_json::from_str(json).unwrap();
    assert_eq!(run.run_id, "abc-123-def");
    assert_eq!(run.status, "completed");
    assert_eq!(run.n_frames, Some(12400));
    let label = run.display_label();
    assert!(
        label.contains("abc-123"),
        "label should contain id: {}",
        label
    );
    assert!(
        label.contains("6x4"),
        "label should contain film: {}",
        label
    );
    assert!(
        label.contains("0->20"),
        "label should contain je range: {}",
        label
    );
}

#[test]
fn test_display_label_short_id() {
    let json = r#"{"run_id":"short","status":"running","created_at":"2026-01-01"}"#;
    let run: RunInfo = serde_json::from_str(json).unwrap();
    assert!(run.display_label().contains("short"));
}
