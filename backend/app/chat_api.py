from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, JsonValue

from backend.app.chat_service import ChatService
from backend.app.log_schemas import ErrorResponse
from backend.app.workspace_schemas import InputModel

router = APIRouter(prefix="/api", tags=["AI 취업 상담"])
_chat = ChatService()


def get_chat_service():
    return _chat


class ChatInput(InputModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    profile_id: int | None = Field(default=None, ge=1)
    assess_fit: bool = True


class ChatOutput(BaseModel):
    session_id: str
    run_id: str
    answer: str
    analysis: dict[str, JsonValue]
    used_previous_context: bool
    jobs: list[dict[str, JsonValue]]
    matches: list[dict[str, JsonValue]]
    excluded_jobs: list[dict[str, JsonValue]]
    tool_calls: list[dict[str, JsonValue]]
    search_summary: dict[str, JsonValue] | None
    replan_count: int
    events: list[dict[str, JsonValue]]


class ChatError(ErrorResponse):
    session_id: str | None = None
    run_id: str | None = None
    events: list[dict[str, JsonValue]] = Field(default_factory=list)


@router.post("/chat", response_model=ChatOutput, responses={code: {"model": ChatError} for code in (404, 409, 422, 500, 502, 503, 504)})
def chat(data: ChatInput, service: Annotated[ChatService, Depends(get_chat_service)]):
    return service.invoke(data.session_id, data.message, data.profile_id, data.assess_fit)
