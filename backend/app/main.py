from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

# main.py → app → backend → CareerMate
project_root = Path(__file__).resolve().parents[2]
load_dotenv(project_root / ".env")

app = FastAPI(title="CareerMate API")

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