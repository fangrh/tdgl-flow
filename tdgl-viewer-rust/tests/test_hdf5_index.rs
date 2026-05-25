use std::path::Path;
use tdgl_viewer_rust::hdf5_index::{build_index_from_file, build_index_from_bytes};

const TEST_DATA_PATH: &str = "tests/test_data.h5";

/// Returns true if the test data file exists and is accessible.
fn test_data_available() -> bool {
    Path::new(TEST_DATA_PATH).exists()
}

#[test]
fn test_build_index_from_file() {
    if !test_data_available() {
        eprintln!("Skipping test: test data file not found at {}", TEST_DATA_PATH);
        return;
    }

    let index = build_index_from_file(Path::new(TEST_DATA_PATH))
        .expect("Failed to build index from test file");

    // Basic sanity checks
    assert!(index.total_frames > 0, "Should have at least one frame");
    assert!(index.mesh_points > 0, "Should have mesh points");
    assert_eq!(
        index.frame_psi_offsets.len(),
        index.total_frames,
        "psi offsets should match frame count"
    );
    assert_eq!(
        index.frame_mu_offsets.len(),
        index.total_frames,
        "mu offsets should match frame count"
    );

    // Mesh should have valid offsets
    assert!(
        index.mesh_sites.offset > 0,
        "Mesh sites offset should be positive"
    );
    assert!(
        index.mesh_sites.size > 0,
        "Mesh sites size should be positive"
    );
    assert_eq!(
        index.mesh_sites.shape.len(),
        2,
        "Sites should be 2D"
    );
    assert_eq!(
        index.mesh_sites.shape[1], 2,
        "Sites should have 2 columns (x, y)"
    );

    println!("Index built successfully:");
    println!("  Total frames: {}", index.total_frames);
    println!("  Mesh points: {}", index.mesh_points);
    println!("  Sites offset: {:#x}, size: {}", index.mesh_sites.offset, index.mesh_sites.size);
    println!("  Edges offset: {:#x}, size: {}", index.mesh_edges.offset, index.mesh_edges.size);
    println!("  Frame 0 psi offset: {:#x}", index.frame_psi_offsets[0]);
    println!("  Frame 0 mu offset: {:#x}", index.frame_mu_offsets[0]);
}

#[test]
fn test_known_offsets() {
    // Validates parser output against ground truth from h5py.
    // If the test file is not present, skip.
    if !test_data_available() {
        eprintln!("Skipping test: test data file not found");
        return;
    }

    let index = build_index_from_file(Path::new(TEST_DATA_PATH)).unwrap();

    // Ground truth from h5py:
    // psi0=0x8c0eb, mu0=0x90f2b
    // psi1=0xb05ab, mu1=0xb53eb
    // sites=0x86d96656, edges=0x86dbafa6
    // rs_mu1=0xd4bfb, rs_dt1=0xd4a6b
    // total_frames=14740, n_sites=1252

    assert_eq!(index.total_frames, 14740, "Total frames should be 14740");
    assert_eq!(index.mesh_points, 1252, "Mesh points should be 1252");

    assert_eq!(index.frame_psi_offsets[0], 0x8c0eb, "Frame 0 psi offset");
    assert_eq!(index.frame_mu_offsets[0], 0x90f2b, "Frame 0 mu offset");
    assert_eq!(index.frame_psi_offsets[1], 0xb05ab, "Frame 1 psi offset");
    assert_eq!(index.frame_mu_offsets[1], 0xb53eb, "Frame 1 mu offset");

    assert_eq!(index.mesh_sites.offset, 0x86d96656, "Sites offset");
    assert_eq!(index.mesh_sites.size, 20032, "Sites size (1252 * 2 * 8)");

    // Frame 0 has no running state
    assert_eq!(index.frame_rsdt_offsets[0], 0, "Frame 0 should have no rs_dt");

    // Frame 1 has running state
    assert_eq!(index.frame_rsdt_offsets[1], 0xd4a6b, "Frame 1 rs_dt offset");
    assert_eq!(index.frame_rsmu_offsets[1], 0xd4bfb, "Frame 1 rs_mu offset");
}

#[test]
fn test_psi_offsets_are_monotonic() {
    if !test_data_available() {
        eprintln!("Skipping test: test data file not found");
        return;
    }

    let index = build_index_from_file(Path::new(TEST_DATA_PATH)).unwrap();

    for i in 1..index.frame_psi_offsets.len() {
        assert!(
            index.frame_psi_offsets[i] > index.frame_psi_offsets[i - 1],
            "Psi offsets should be monotonically increasing: frame {} offset {:#x} <= frame {} offset {:#x}",
            i, index.frame_psi_offsets[i],
            i - 1, index.frame_psi_offsets[i - 1]
        );
    }
}

#[test]
fn test_mu_offsets_follow_psi_offsets() {
    if !test_data_available() {
        eprintln!("Skipping test: test data file not found");
        return;
    }

    let index = build_index_from_file(Path::new(TEST_DATA_PATH)).unwrap();

    for i in 0..index.total_frames {
        let psi_off = index.frame_psi_offsets[i];
        let mu_off = index.frame_mu_offsets[i];
        assert!(
            mu_off > psi_off,
            "Frame {}: mu offset {:#x} should be after psi offset {:#x}",
            i, mu_off, psi_off
        );

        // mu should be within the same frame (before next psi)
        let next_psi = if i + 1 < index.total_frames {
            index.frame_psi_offsets[i + 1]
        } else {
            index.mesh_sites.offset // after last frame, before mesh
        };
        assert!(
            mu_off < next_psi,
            "Frame {}: mu offset {:#x} should be before next psi offset {:#x}",
            i, mu_off, next_psi
        );
    }
}

#[test]
fn test_running_state_offsets() {
    if !test_data_available() {
        eprintln!("Skipping test: test data file not found");
        return;
    }

    let index = build_index_from_file(Path::new(TEST_DATA_PATH)).unwrap();

    // Frame 0 typically has no running state
    // Frame 1+ should have running state
    let frames_with_rs: Vec<usize> = (0..index.total_frames)
        .filter(|&i| index.frame_rsdt_offsets[i] > 0)
        .collect();

    assert!(
        frames_with_rs.len() > 0,
        "At least some frames should have running_state"
    );

    // For frames with running_state, rsdt should be before rsmu
    for &i in &frames_with_rs {
        let rsdt = index.frame_rsdt_offsets[i];
        let rsmu = index.frame_rsmu_offsets[i];
        assert!(
            rsmu > rsdt,
            "Frame {}: rsmu offset {:#x} should be after rsdt offset {:#x}",
            i, rsmu, rsdt
        );
    }
}

#[test]
fn test_bad_signature_rejected() {
    let data = vec![0u8; 1024];
    let result = build_index_from_bytes(&data);
    assert!(result.is_err());
    assert!(
        result.unwrap_err().contains("signature"),
        "Error should mention signature"
    );
}

#[test]
fn test_valid_hdf5_signature() {
    if !test_data_available() {
        eprintln!("Skipping test: test data file not found");
        return;
    }

    let data = std::fs::read(TEST_DATA_PATH).unwrap();

    // Check HDF5 signature
    let expected_sig: [u8; 8] = [0x89, 0x48, 0x44, 0x46, 0x0d, 0x0a, 0x1a, 0x0a];
    assert_eq!(
        &data[..8], &expected_sig,
        "File should have valid HDF5 signature"
    );

    // Should be able to build index
    let result = build_index_from_bytes(&data);
    assert!(result.is_ok(), "Should successfully build index from valid HDF5 file");
}
