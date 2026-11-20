import json
import os

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError

from backend.app.db.database import PROJECT_ROOT
from backend.app.tools.job_tools import search_jobs

GMS_BASE_URL = "https://gms.ssafy.io/gmsapi/api.openai.com/v1"
GMS_ANTHROPIC_BASE_URL = "https://gms.ssafy.io/gmsapi/api.anthropic.com/v1"
DEFAULT_MODEL = "gpt-6-luna"


def build_gms_model() -> ChatOpenAI:
    api_key = os.getenv("GMS_API_KEY")
    if not api_key:
        raise RuntimeError(".env에 GMS_API_KEY를 설정해 주세요.")

    model = (os.getenv("GMS_MODEL") or DEFAULT_MODEL).strip()
    default_url = GMS_ANTHROPIC_BASE_URL if model.startswith("claude-") else GMS_BASE_URL
    options = {}
    if model == "gpt-6-luna" or model.startswith("gpt-6-luna-"):
        # CareerMate uses function_calling on Chat Completions. Luna requires
        # reasoning_effort=none for this combination; do not send it to Claude.
        options["reasoning_effort"] = "none"

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=(os.getenv("GMS_BASE_URL") or default_url).strip(),
        default_headers={"x-api-key": api_key},
        use_responses_api=False,
        timeout=30,
        max_retries=0,
        **options,
    )


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    model = build_gms_model()
    model_with_tools = model.bind_tools([search_jobs])

    messages = [
        SystemMessage(
            content=(
                "채용공고 검색 요청에는 search_jobs를 호출해. "
                "사용자가 제시한 직무, 지역, 고용형태를 검색 조건으로 전달해. "
                "Tool 결과를 받으면 그 결과에 있는 공고 정보로만 한국어 답변을 작성해."
            )
        ),
        HumanMessage(content="강원 지역 정규직 약사 채용공고를 3건까지 찾아줘."),
    ]

    request = model_with_tools.invoke(messages)
    if not request.tool_calls:
        raise RuntimeError("GMS 응답에 Tool 호출이 없습니다.")

    messages.append(request)

    for call in request.tool_calls:
        if call["name"] != search_jobs.name or not call["id"]:
            raise RuntimeError("GMS 응답의 Tool 이름 또는 호출 ID가 올바르지 않습니다.")

        print(f"GMS Tool 요청 | {call['name']} | {call['args']}")
        result = search_jobs.invoke(call["args"])

        if not result["ok"]:
            error = result["error"]
            raise RuntimeError(f"{error['code']} - {error['message']}")

        print(f"SQLite 검색 성공 | {result['data']['count']}건")
        messages.append(
            ToolMessage(
                content=json.dumps(result, ensure_ascii=False),
                tool_call_id=call["id"],
            )
        )

    answer = model.invoke(messages)
    if not answer.text.strip():
        raise RuntimeError("GMS 최종 답변이 비어 있습니다.")

    print(f"GMS 최종 답변\n{answer.text}")


if __name__ == "__main__":
    try:
        main()
    except APITimeoutError:
        raise SystemExit("GMS 응답 시간이 초과됐습니다.") from None
    except APIConnectionError:
        raise SystemExit("GMS에 연결하지 못했습니다.") from None
    except APIStatusError as error:
        raise SystemExit(f"GMS HTTP 오류 {error.status_code}") from None
    except ValidationError:
        raise SystemExit("GMS가 반환한 Tool 인자 형식이 올바르지 않습니다.") from None
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
