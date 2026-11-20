from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.log_schemas import ErrorResponse
from backend.app.workspace_schemas import ExperienceFields, ExperienceRead, ExperienceList, Deleted, GrowthInput, GrowthList, GrowthRead, PlanInput, PlanList, PlanPatch, PlanRead, PlanSaved, ProfileInput, ProfileList, ProfileRead
from backend.app.workspace_service import WorkspaceService

router = APIRouter(prefix="/api", tags=["프로필·지원 계획"], responses={code: {"model": ErrorResponse} for code in (404, 409, 422, 500, 503)})


def get_workspace_service():
    return WorkspaceService()


Service = Annotated[WorkspaceService, Depends(get_workspace_service)]
PositiveId = Annotated[int, Path(ge=1)]
ProfileQuery = Annotated[int, Query(ge=1)]


@router.get("/profiles", response_model=ProfileList)
def profiles(service: Service):
    return service.profiles()


@router.post("/profiles", response_model=ProfileRead, status_code=201)
def create_profile(data: ProfileInput, service: Service):
    return service.save_profile(data)


@router.get("/profiles/{profile_id}", response_model=ProfileRead)
def profile(profile_id: PositiveId, service: Service):
    return service.profile(profile_id)


@router.put("/profiles/{profile_id}", response_model=ProfileRead)
def update_profile(profile_id: PositiveId, data: ProfileInput, service: Service):
    return service.save_profile(data, profile_id)


@router.get("/growth-plans", response_model=GrowthList)
def growth_plans(profile_id: ProfileQuery, service: Service):
    return service.growth_plans(profile_id)


@router.post("/growth-plans", response_model=GrowthRead, status_code=201)
def create_growth(data: GrowthInput, service: Service):
    return service.save_growth(data)


@router.delete("/growth-plans/{growth_id}", response_model=Deleted)
def delete_growth(growth_id: PositiveId, profile_id: ProfileQuery, service: Service):
    return service.delete_growth(growth_id, profile_id)


@router.get("/plans", response_model=PlanList)
def plans(profile_id: ProfileQuery, service: Service):
    return service.plans(profile_id)


@router.post("/plans", response_model=PlanSaved, status_code=201)
def create_plan(data: PlanInput, service: Service):
    return service.save_plan(data)


@router.get("/plans/{plan_id}", response_model=PlanRead)
def plan(plan_id: PositiveId, profile_id: ProfileQuery, service: Service):
    return service.plan(plan_id, profile_id)


@router.patch("/plans/{plan_id}", response_model=PlanSaved)
def update_plan(plan_id: PositiveId, profile_id: ProfileQuery, data: PlanPatch, service: Service):
    return service.patch_plan(plan_id, profile_id, data)


@router.delete("/plans/{plan_id}", response_model=Deleted)
def delete_plan(plan_id: PositiveId, profile_id: ProfileQuery, service: Service):
    return service.delete_plan(plan_id, profile_id)


@router.get("/profiles/{profile_id}/experiences", response_model=ExperienceList)
def experiences(profile_id: PositiveId, service: Service):
    return service.experiences(profile_id)


@router.post("/profiles/{profile_id}/experiences", response_model=ExperienceRead, status_code=201)
def create_experience(profile_id: PositiveId, data: ExperienceFields, service: Service):
    return service.save_experience(profile_id, data)


@router.put("/profiles/{profile_id}/experiences/{experience_id}", response_model=ExperienceRead)
def update_experience(profile_id: PositiveId, experience_id: PositiveId, data: ExperienceFields, service: Service):
    return service.save_experience(profile_id, data, experience_id)


@router.delete("/profiles/{profile_id}/experiences/{experience_id}", response_model=Deleted)
def delete_experience(profile_id: PositiveId, experience_id: PositiveId, service: Service):
    return service.delete_experience(profile_id, experience_id)
