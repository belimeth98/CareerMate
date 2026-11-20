from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

PlanStatus = Literal["planned", "in_progress", "completed", "cancelled"]
PlanType = Literal["cert_registration", "portfolio", "coding_test", "application", "other"]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExperienceFields(InputModel):
    experience_type: Literal["work", "project", "other"] = "project"
    title: str = Field(min_length=1, max_length=200)
    organization: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    description: str | None = Field(default=None, max_length=4000)
    situation: str | None = Field(default=None, max_length=2000)
    task: str | None = Field(default=None, max_length=2000)
    action: str | None = Field(default=None, max_length=3000)
    result: str | None = Field(default=None, max_length=2000)
    skills: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def check_dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("Experience end must not precede start")
        return self


class ExperienceRead(ExperienceFields):
    id: int
    user_profile_id: int


class ExperienceList(BaseModel):
    experiences: list[ExperienceRead]


class ProfileFields(InputModel):
    profile_name: str | None = Field(default=None, max_length=100)
    desired_role: str = Field(min_length=1, max_length=100)
    preferred_locations: list[str] = Field(default_factory=list, max_length=30)
    preferred_employment_types: list[str] = Field(default_factory=list, max_length=20)
    education_level: str | None = None
    education_status: str | None = None
    major: str | None = None
    career_months: int | None = Field(default=None, ge=0, le=1200)
    skills: list[str] = Field(default_factory=list, max_length=100)
    certifications: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("preferred_locations", "preferred_employment_types", "skills", "certifications")
    @classmethod
    def clean_list(cls, values):
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class ProfileInput(ProfileFields):
    initial_experiences: list[ExperienceFields] = Field(default_factory=list, max_length=20)


class ProfileRead(ProfileFields):
    id: int


class ProfileList(BaseModel):
    profiles: list[ProfileRead]


class GrowthInput(InputModel):
    user_profile_id: int = Field(ge=1)
    plan_type: Literal["skill", "certification", "project"]
    target_name: str = Field(min_length=1, max_length=200)
    expected_skills: list[str] = Field(default_factory=list, max_length=100)
    target_date: date
    status: PlanStatus = "planned"


class GrowthRead(GrowthInput):
    id: int


class GrowthList(BaseModel):
    growth_plans: list[GrowthRead]


class PlanInput(InputModel):
    user_profile_id: int = Field(ge=1)
    job_posting_id: int | None = Field(default=None, ge=1)
    growth_plan_id: int | None = Field(default=None, ge=1)
    plan_type: PlanType = "other"
    title: str = Field(min_length=1, max_length=200)
    due_date: date | None = None
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    status: PlanStatus = "planned"
    notes: str | None = Field(default=None, max_length=3000)

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.due_date is None and self.start_at is None:
            raise ValueError("A due date or a time range is required")
        if (self.start_at is None) != (self.end_at is None):
            raise ValueError("Both start and end are required")
        if self.start_at is not None and self.end_at <= self.start_at:
            raise ValueError("End must be later than start")
        return self


class PlanDraft(PlanInput):
    user_profile_id: int | None = Field(default=None, ge=1)


class PlanPatch(InputModel):
    job_posting_id: int | None = Field(default=None, ge=1)
    growth_plan_id: int | None = Field(default=None, ge=1)
    plan_type: PlanType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    due_date: date | None = None
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    status: PlanStatus | None = None
    notes: str | None = Field(default=None, max_length=3000)


class PlanRead(PlanInput):
    id: int
    created_at: datetime


class PlanList(BaseModel):
    plans: list[PlanRead]


class PlanSaved(BaseModel):
    plan: PlanRead
    conflicts: list[PlanRead]
    warning: str | None


class Deleted(BaseModel):
    id: int
    deleted: bool = True
