import pytest
from sqlalchemy.exc import IntegrityError

from tdgl_data.repository import (
    append_frame_record,
    complete_run,
    create_event,
    create_run,
    fail_run,
    get_frame,
    get_iv_points,
    get_run,
    get_timeline,
)


def test_create_run_defaults(session):
    run = create_run(
        session,
        solver_type="synthetic",
        grid_shape=(8, 6),
        zarr_root="runs/example/frames.zarr",
    )
    session.commit()

    loaded = get_run(session, run.run_id)
    assert loaded is not None
    assert loaded.status == "created"
    assert loaded.solver_type == "synthetic"
    assert loaded.mesh_metadata["grid_shape"] == [8, 6]


def test_append_frame_record_creates_timeline_and_iv_point(session):
    run = create_run(session, solver_type="synthetic", grid_shape=(4, 3), zarr_root="runs/r/frames.zarr")
    append_frame_record(
        session,
        run_id=run.run_id,
        frame_index=0,
        time_value=0.25,
        je=1.5,
        voltage=0.01,
        zarr_group="runs/r/frames.zarr",
        checksum="abc",
    )
    session.commit()

    frame = get_frame(session, run.run_id, 0)
    timeline = get_timeline(session, run.run_id)
    iv_points = get_iv_points(session, run.run_id)

    assert frame is not None
    assert frame.status == "available"
    assert len(timeline) == 1
    assert timeline[0].frame_index == 0
    assert iv_points[0].voltage == pytest.approx(0.01)


def test_duplicate_frame_record_raises_integrity_error(session):
    run = create_run(session, solver_type="synthetic", grid_shape=(4, 3), zarr_root="runs/r/frames.zarr")
    append_frame_record(session, run_id=run.run_id, frame_index=0, time_value=0.0, je=0.0, voltage=0.0, zarr_group="g")
    session.commit()

    append_frame_record(session, run_id=run.run_id, frame_index=0, time_value=0.1, je=0.1, voltage=0.1, zarr_group="g")
    with pytest.raises(IntegrityError):
        session.commit()


def test_complete_and_fail_run_status(session):
    completed = create_run(session, solver_type="synthetic", grid_shape=(2, 2), zarr_root="runs/c/frames.zarr")
    failed = create_run(session, solver_type="synthetic", grid_shape=(2, 2), zarr_root="runs/f/frames.zarr")

    complete_run(session, completed.run_id)
    fail_run(session, failed.run_id, "solver crashed")
    session.commit()

    assert get_run(session, completed.run_id).status == "completed"
    failed_run = get_run(session, failed.run_id)
    assert failed_run.status == "failed"
    assert failed_run.metadata_["failure_message"] == "solver crashed"


def test_create_event_records_ordered_payload(session):
    run = create_run(session, solver_type="synthetic", grid_shape=(2, 2), zarr_root="runs/r/frames.zarr")
    event = create_event(session, run.run_id, "frame_available", {"frame_index": 7})
    session.commit()

    assert event.event_id == 1
    assert event.event_type == "frame_available"
    assert event.payload["frame_index"] == 7
