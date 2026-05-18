from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    run_id: str
    viewer_type: str = "data-viewer"


class SessionResponse(BaseModel):
    session_id: str
    run_id: str
    viewer_type: str
    status: str
    session_url: str | None = None
    active_clients: int = 0
    error_message: str | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]