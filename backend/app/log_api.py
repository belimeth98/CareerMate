"""F3014 HTTP routes: list, detail and delete only."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.log_schemas import ErrorResponse, LogSessionDeleted, LogSessionDetail, LogSessionList
from backend.app.log_service import LogService

router = APIRouter(prefix="/api/logs", tags=["실행 로그"])


def get_log_service() -> LogService:
    return LogService()


Service = Annotated[LogService, Depends(get_log_service)]
SessionId = Annotated[str, Path(min_length=1, max_length=128, description="대화 세션 식별자")]
ERRORS = {
    422: {"model": ErrorResponse, "description": "잘못된 요청"},
    503: {"model": ErrorResponse, "description": "SQLite 접근 실패"},
}


@router.get("", response_model=LogSessionList, responses=ERRORS, summary="실행 로그 세션 목록")
def list_sessions(
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return service.list_sessions(limit=limit, offset=offset)


@router.get(
    "/{session_id}", response_model=LogSessionDetail,
    responses={**ERRORS, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="세션의 실행 로그를 발생 순서대로 조회",
)
def get_session(
    session_id: SessionId, service: Service,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return service.get_session(session_id, limit=limit, offset=offset)


@router.delete(
    "/{session_id}", response_model=LogSessionDeleted,
    responses={**ERRORS, 404: {"model": ErrorResponse}}, summary="세션의 실행 로그 전체 삭제",
)
def delete_session(session_id: SessionId, service: Service):
    return service.delete_session(session_id)
