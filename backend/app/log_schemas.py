"""Public response contracts for immutable execution logs."""

from pydantic import BaseModel, Field, JsonValue


class APIError(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: APIError


class LogSessionSummary(BaseModel):
    session_id: str
    event_count: int = Field(ge=1)
    run_count: int = Field(ge=1)
    first_event_at: str
    last_event_at: str
    error_count: int = Field(ge=0)


class LogSessionList(BaseModel):
    total: int = Field(ge=0)
    limit: int
    offset: int
    sessions: list[LogSessionSummary]


class ExecutionEventRead(BaseModel):
    id: int
    session_id: str
    run_id: str
    event_type: str
    created_at: str
    duration_ms: int | None
    payload: dict[str, JsonValue] | None


class LogSessionDetail(BaseModel):
    session_id: str
    total: int = Field(ge=1)
    limit: int
    offset: int
    events: list[ExecutionEventRead]


class LogSessionDeleted(BaseModel):
    session_id: str
    deleted_count: int = Field(ge=1)
