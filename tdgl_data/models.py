from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(UTC)


json_type = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    solver_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    git_commit: Mapped[str | None] = mapped_column(String(128))
    image_tag: Mapped[str | None] = mapped_column(String(256))
    kubeflow_run_id: Mapped[str | None] = mapped_column(String(256))
    kubeflow_pipeline_id: Mapped[str | None] = mapped_column(String(256))
    kubeflow_task_id: Mapped[str | None] = mapped_column(String(256))
    device_params: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(json_type), default=dict, nullable=False
    )
    timing_params: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(json_type), default=dict, nullable=False
    )
    mesh_metadata: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(json_type), default=dict, nullable=False
    )
    zarr_root: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", MutableDict.as_mutable(json_type), default=dict, nullable=False
    )

    frames: Mapped[list["Frame"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    iv_points: Mapped[list["IVPoint"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    events: Mapped[list["RunEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Frame(Base):
    __tablename__ = "frames"
    __table_args__ = (UniqueConstraint("run_id", "frame_index", name="uq_frames_run_frame"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False)
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    time_value: Mapped[float] = mapped_column(Float, nullable=False)
    je: Mapped[float] = mapped_column(Float, nullable=False)
    voltage: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="available")
    zarr_group: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128))
    frame_stats: Mapped[dict | None] = mapped_column(
        MutableDict.as_mutable(json_type), default=None, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    run: Mapped[Run] = relationship(back_populates="frames")


class IVPoint(Base):
    __tablename__ = "iv_points"
    __table_args__ = (UniqueConstraint("run_id", "frame_index", name="uq_iv_points_run_frame"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False)
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    je: Mapped[float] = mapped_column(Float, nullable=False)
    voltage: Mapped[float] = mapped_column(Float, nullable=False)
    time_value: Mapped[float] = mapped_column(Float, nullable=False)

    run: Mapped[Run] = relationship(back_populates="iv_points")


class RunEvent(Base):
    __tablename__ = "run_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(json_type), default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    run: Mapped[Run] = relationship(back_populates="events")
