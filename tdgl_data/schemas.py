from typing import Annotated

from pydantic import BaseModel, Field

StrictPositiveInt = Annotated[int, Field(strict=True, gt=0)]
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class CreateRunRequest(BaseModel):
    solver_type: str = "synthetic"
    grid_shape: tuple[StrictPositiveInt, StrictPositiveInt] = Field(default=(64, 64))
    device_params: dict = Field(default_factory=dict)
    timing_params: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
    git_commit: str | None = None
    image_tag: str | None = None
    total_frames: int | None = None




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


class FrameAppendRequest(BaseModel):
    frame_index: StrictNonNegativeInt
    time_value: float
    je: float
    voltage: float
    psi_real: list[list[float]]
    psi_imag: list[list[float]]
    mu: list[list[float]]


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


class FrameResponse(BaseModel):
    run_id: str
    frame_index: int
    time_value: float
    je: float
    voltage: float
    arrays: dict[str, list[list[float]]]
