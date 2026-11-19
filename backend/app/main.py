from pathlib import Path
import sqlite3

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from starlette.exceptions import HTTPException

from backend.app.log_api import router as log_router
from backend.app.log_service import LogPayloadError, LogSessionNotFound
from backend.app.workspace_api import router as workspace_router
from backend.app.workspace_service import AppProblem
from backend.app.chat_api import router as chat_router

# main.py → app → backend → CareerMate
project_root = Path(__file__).resolve().parents[2]
load_dotenv(project_root / ".env")

app = FastAPI(title="CareerMate API")
app.include_router(log_router)
app.include_router(workspace_router)
app.include_router(chat_router)
frontend_path = project_root / "frontend"
app.mount("/assets", StaticFiles(directory=frontend_path, check_dir=False), name="assets")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(frontend_path / "index.html")


@app.exception_handler(AppProblem)
async def handle_app_problem(request: Request, exc: AppProblem):
    return JSONResponse(status_code=exc.status, content={
        "error": {"code": exc.code, "message": exc.message}, **exc.details,
    })


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


@app.exception_handler(LogSessionNotFound)
async def handle_missing_log(request: Request, exc: LogSessionNotFound):
    return error_response(404, "LOG_SESSION_NOT_FOUND", "해당 세션의 실행 로그를 찾을 수 없습니다.")


@app.exception_handler(LogPayloadError)
async def handle_invalid_log(request: Request, exc: LogPayloadError):
    return error_response(500, "LOG_PAYLOAD_ERROR", "저장된 실행 로그 형식을 확인하지 못했습니다.")


@app.exception_handler(sqlite3.Error)
async def handle_database_error(request: Request, exc: sqlite3.Error):
    return error_response(503, "DATABASE_ERROR", "데이터베이스를 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")


@app.exception_handler(RequestValidationError)
async def handle_invalid_request(request: Request, exc: RequestValidationError):
    return error_response(422, "INVALID_INPUT", "입력 값과 날짜·시간 형식을 확인해 주세요.")


@app.exception_handler(ResponseValidationError)
async def handle_invalid_response(request: Request, exc: ResponseValidationError):
    return error_response(500, "INVALID_RESPONSE", "결과를 표시하지 못했습니다. 잠시 후 다시 시도해 주세요.")

@app.exception_handler(HTTPException)
async def handle_http_error(request: Request, exc: HTTPException):
    if exc.status_code == 404:
        code = "NOT_FOUND"
        message = "요청한 경로를 찾을 수 없습니다."
    else:
        code = "HTTP_ERROR"
        message = "요청을 처리할 수 없습니다."

    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": message}},
        headers=exc.headers,
    )

@app.get("/health")
def health():
    return {"status": "ok"}
