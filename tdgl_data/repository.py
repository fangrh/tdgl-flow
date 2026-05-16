from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tdgl_data.models import Frame, IVPoint, Run, RunEvent, utcnow


def create_run(
    session: Session,
    *,
    solver_type: str,
    grid_shape: tuple[int, int],
    device_params: dict | None = None,
    timing_params: dict | None = None,
    metadata: dict | None = None,
    git_commit: str | None = None,
    image_tag: str | None = None,
    total_frames: int | None = None,
) -> Run:
    run = Run(
        run_id=str(uuid4()),
        solver_type=solver_type,
        status="created",
        mesh_metadata={"grid_shape": list(grid_shape)},
        device_params=device_params or {},
        timing_params=timing_params or {},
        metadata_=metadata or {},
        git_commit=git_commit,
        image_tag=image_tag,
        total_frames=total_frames,
    )
    session.add(run)
    session.flush()
    return run


def get_run(session: Session, run_id: str) -> Run | None:
    return session.get(Run, run_id)


def list_runs(session: Session) -> list[Run]:
    return list(session.scalars(select(Run).order_by(Run.created_at.desc())))


def delete_run(session: Session, run: Run) -> None:
    run_id = run.run_id
    session.execute(delete(RunEvent).where(RunEvent.run_id == run_id))
    session.execute(delete(IVPoint).where(IVPoint.run_id == run_id))
    session.execute(delete(Frame).where(Frame.run_id == run_id))
    session.delete(run)
    session.flush()


def append_frame_record(
    session: Session,
    *,
    run_id: str,
    frame_index: int,
    time_value: float,
    je: float,
    voltage: float,
    psi_real: list[list[float]],
    psi_imag: list[list[float]],
    mu: list[list[float]],
    frame_stats: dict | None = None,
    status: str = "available",
) -> Frame:
    now = utcnow()
    frame = Frame(
        run_id=run_id,
        frame_index=frame_index,
        time_value=time_value,
        je=je,
        voltage=voltage,
        status=status,
        psi_real=psi_real,
        psi_imag=psi_imag,
        mu=mu,
        frame_stats=frame_stats,
        created_at=now,
        committed_at=now,
    )
    iv_point = IVPoint(
        run_id=run_id,
        frame_index=frame_index,
        je=je,
        voltage=voltage,
        time_value=time_value,
    )
    session.add_all([frame, iv_point])
    session.flush()
    return frame



def delete_frame_record(session: Session, run_id: str, frame_index: int) -> None:
    session.execute(
        delete(IVPoint).where(IVPoint.run_id == run_id, IVPoint.frame_index == frame_index)
    )
    session.execute(delete(Frame).where(Frame.run_id == run_id, Frame.frame_index == frame_index))
    session.flush()


def get_frame(session: Session, run_id: str, frame_index: int) -> Frame | None:
    stmt = select(Frame).where(Frame.run_id == run_id, Frame.frame_index == frame_index)
    return session.scalar(stmt)


def get_timeline(session: Session, run_id: str) -> list[Frame]:
    stmt = select(Frame).where(Frame.run_id == run_id).order_by(Frame.frame_index)
    return list(session.scalars(stmt))


def get_iv_points(session: Session, run_id: str) -> list[IVPoint]:
    stmt = select(IVPoint).where(IVPoint.run_id == run_id).order_by(IVPoint.frame_index)
    return list(session.scalars(stmt))


def update_run_status(session: Session, run_id: str, status: str) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError(f"Run {run_id} not found")
    run.status = status
    if status in ("completed", "failed"):
        run.completed_at = utcnow()
    elif status == "running":
        run.started_at = utcnow()
    session.flush()
    return run


def complete_run(session: Session, run_id: str) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError(f"Run {run_id} not found")
    run.status = "completed"
    run.completed_at = utcnow()
    session.flush()
    return run


def fail_run(session: Session, run_id: str, message: str) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError(f"Run {run_id} not found")
    run.status = "failed"
    run.completed_at = utcnow()
    run.metadata_ = {**run.metadata_, "failure_message": message}
    session.flush()
    return run


def create_event(session: Session, run_id: str, event_type: str, payload: dict) -> RunEvent:
    event = RunEvent(run_id=run_id, event_type=event_type, payload=payload)
    session.add(event)
    session.flush()
    return event


def get_events_after(
    session: Session,
    run_id: str,
    last_event_id: int | None = None,
) -> list[RunEvent]:
    stmt = select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.event_id)
    if last_event_id is not None:
        stmt = stmt.where(RunEvent.event_id > last_event_id)
    return list(session.scalars(stmt))
