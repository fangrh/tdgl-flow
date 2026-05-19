from typing import Annotated

from pydantic import BaseModel, Field

StrictPositiveInt = Annotated[int, Field(strict=True, gt=0)]
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class CreateRunRequest(BaseModel):
    solver_type: str = "synthetic"
    n_sites: StrictPositiveInt = Field(default=100)
    device_params: dict = Field(default_factory=dict)
    timing_params: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
    git_commit: str | None = None
    image_tag: str | None = None
    total_frames: int | None = None
    mesh_sites: list[list[float]] | None = None
    mesh_elements: list[list[int]] | None = None
    solver_options: dict | None = None


class RunResponse(BaseModel):
    run_id: str
    status: str
    solver_type: str
    mesh_metadata: dict
    device_params: dict
    timing_params: dict
    metadata: dict
    created_at: str | None = None
    total_frames: int | None = None
    n_sites: int | None = None


class UpdateRunStatusRequest(BaseModel):
    status: str


class FrameAppendRequest(BaseModel):
    frame_index: StrictNonNegativeInt
    time_value: float
    je: float
    voltage: float
    psi_real: list[float]
    psi_imag: list[float]
    mu: list[float]
    frame_stats: dict = Field(default_factory=dict)


class FrameMetadataResponse(BaseModel):
    frame_index: int
    time_value: float
    je: float
    voltage: float
    status: str


class TimelineResponse(BaseModel):
    run_id: str
    frames: list[FrameMetadataResponse]
    stats: dict[str, dict[str, float]]


class IVPointResponse(BaseModel):
    frame_index: int
    time_value: float
    je: float
    voltage: float


class IVPointAppendRequest(BaseModel):
    frame_index: StrictNonNegativeInt
    time_value: float
    je: float
    voltage: float


class FrameResponse(BaseModel):
    run_id: str
    frame_index: int
    time_value: float
    je: float
    voltage: float
    arrays: dict[str, list[float]]


class MeshResponse(BaseModel):
    sites: list[list[float]]
    elements: list[list[int]]
    probe_indices: list[int]
    n_sites: int


class DeviceBuildRequest(BaseModel):
    film_width: float = 10.0
    film_height: float = 2.0
    elec_width: float = 0.5
    elec_height: float = 1.0
    elec_y_offset: float = 0.0
    probe_points: list[list[float]] = Field(default_factory=lambda: [[-2.0, 0.0], [2.0, 0.0]])
    max_edge_length: float = 0.5
    smooth: int = 100


class TimingBuildRequest(BaseModel):
    mode: str = "simple"
    je_initial: float = 0.0
    je_final: float = 10.0
    je_step: float = 1.0
    ramp_time: float = 1.0
    stable_time: float = 5.0
    save_time: float = 3.0
    ramp_down: bool = False
    segments: list[dict] | None = None
    solver_options: dict = Field(default_factory=dict)


class WorkflowSubmitRequest(BaseModel):
    device_params: dict = Field(default_factory=dict)
    timing_params: dict = Field(default_factory=dict)
    mesh_data: dict = Field(default_factory=dict)
    schedule: dict = Field(default_factory=dict)
    solver_options: dict = Field(default_factory=dict)
    resources: dict = Field(default_factory=lambda: {"cpu_cores": 2, "memory_mib": 2048})
