from typing import Annotated
from uuid import uuid4
from typing import Literal
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, JsonValue, model_validator

from backend.app.chat_service import ChatService
from backend.app.log_schemas import ErrorResponse
from backend.app.workspace_schemas import InputModel
from backend.app.workspace_schemas import PlanDraft

router = APIRouter(prefix="/api", tags=["AI 취업 상담"])
_chat = ChatService()


def get_chat_service():
    return _chat


class ChatInput(InputModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    profile_id: int | None = Field(default=None, ge=1)
    assess_fit: bool = True
    job_source: Literal["all", "alio", "incruit", "greenhouse", "lever"] = "all"
    requirements_job_id: int | None = Field(default=None, ge=1)
    original_confirmed: bool = False
    requirements_complete: bool = False
    recruitment_period_status: Literal["provided", "unknown", "until_filled"] | None = None
    recruitment_start: date | None = None
    recruitment_end: date | None = None
    fit_follow_up_action: Literal["none", "recheck"] | None = None

    @model_validator(mode="after")
    def validate_period(self):
        if self.requirements_job_id is not None and self.recruitment_period_status is None:
            raise ValueError("Recruitment period or an explicit unknown status is required")
        if self.recruitment_period_status == "provided":
            if not self.recruitment_start or not self.recruitment_end or self.recruitment_end < self.recruitment_start:
                raise ValueError("Recruitment start and end dates are required in order")
        elif self.recruitment_start or self.recruitment_end:
            raise ValueError("Dates require provided period status")
        if self.fit_follow_up_action and self.requirements_job_id is not None:
            raise ValueError("Follow-up actions cannot replace a new requirements submission")
        return self


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
    reference_status: str = "current"
    plan_draft: PlanDraft | None = None
    fit_follow_up: dict[str, JsonValue] | None = None
    search_suggestions: list[str] = Field(default_factory=list)
    source_results: list[dict[str, JsonValue]] = Field(default_factory=list)
    experience_draft: dict[str, JsonValue] | None = None


class ChatError(ErrorResponse):
    session_id: str | None = None
    run_id: str | None = None
    events: list[dict[str, JsonValue]] = Field(default_factory=list)


@router.post("/chat", response_model=ChatOutput, responses={code: {"model": ChatError} for code in (404, 409, 422, 500, 502, 503, 504)})
def chat(data: ChatInput, service: Annotated[ChatService, Depends(get_chat_service)]):
    return service.invoke(data.session_id, data.message, data.profile_id, data.assess_fit,
                          job_source=data.job_source, requirements_job_id=data.requirements_job_id,
                          original_confirmed=data.original_confirmed, requirements_complete=data.requirements_complete,
                          recruitment_period_status=data.recruitment_period_status,
                          recruitment_start=data.recruitment_start, recruitment_end=data.recruitment_end,
                          fit_follow_up_action=data.fit_follow_up_action)
