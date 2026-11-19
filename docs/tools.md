# CareerMate Tool 명세

현재 등록 Tool은 `backend/app/career_agent.py`의 `TOOLS` 레지스트리와 `ToolName` 의도 스키마에서 확인할 수 있다. 네 함수 모두 LangChain `@tool`을 사용해 이름·설명·입력 JSON Schema를 제공한다. 다음 입력 스키마는 `docs/tool_input_schemas.json`에 그대로 내보낸다.

외부 API Tool과 로컬 기능을 구분한다. 아래 네 개는 외부 API에 접근하는 Tool이다. 공고의 DB 캐시 조회·갱신은 그 내부 구현이며, `/health` 및 `/api/logs`는 로컬 FastAPI 엔드포인트로서 Agent Tool에는 등록하지 않는다.

## 공통 응답

성공: `{"ok": true, "data": {...}}`.

실패 및 정상 0건: `{"ok": false, "error": {"code": "코드", "message": "안내"}}`.

`NO_RESULT`는 정상 조회 결과가 없다는 뜻으로 실행 오류와 구분한다. Agent는 이를 `status=no_result`로 기록한다. 실제 실패는 `INVALID_INPUT`, `DATABASE_ERROR`, `MISSING_API_KEY`, `UPSTREAM_TIMEOUT`, `UPSTREAM_ERROR`, `UPSTREAM_INVALID_RESPONSE` 등으로 반환한다. Tool 프레임워크의 인자 검증 오류와 예기치 않은 예외는 Agent의 `_invoke_tool`에서 `INVALID_INPUT`·`TOOL_ERROR`로 변환한다. 응답 구조가 잘못되면 `INVALID_TOOL_RESPONSE`로 처리한다.

## search_jobs — 외부 API 후보 목록 조회

잡알리오 목록 API `/new/v1/recruit/list.do`를 호출해 진행 공고 전체를 페이지 단위로 확보하고, 정규화·DB 저장 후 사용자 조건으로 검색한다.

입력:

- `keyword`, `region`, `employment_type`, `ncs_category`, `career_type`: 문자열, 기본값 빈 문자열.
- `ongoing_only`: bool, 기본값 `true`. 진행 중이면서 마감일이 오늘(KST) 이후이거나 미확인인 공고를 검색한다.
- `limit`: int, 기본값 20, 1 이상.
- `refresh`: bool, 기본값 `true`. 외부 목록을 새로 확보한다. 같은 요청의 재계획에서는 `false`로 앞서 확보한 후보를 재사용한다.

성공 `data`:

```text
jobs: array<object>
  id: integer — 상세 Tool 입력에 사용하는 SQLite ID
  source, board_id, source_job_id, company, title: string
  location, employment_type, career_type, ncs_categories: array<string> | null
  deadline: YYYY-MM-DD | null
  is_open: 0 | 1 | null
  job_url: string | null
count: integer — 반환한 jobs 길이
data_source: "external_api" | "cache"
```

결과가 없으면 `NO_RESULT`. 외부 수집이 실패하면 기존 캐시의 정상 검색으로 위장하지 않고 해당 오류를 반환한다. 전체 수집에 성공한 후 하나의 DB 트랜잭션으로 반영하며, 목록에서 사라진 잡알리오 공고는 진행 종료로 바꾼다. 다른 출처와 이미 확보한 자격 정보는 유지한다. `ongoing_only=false`이면 과거에 저장한 종료 공고도 조회할 수 있다.

## get_job_detail — 외부 API 상세 조회

입력: `job_id: integer`, 필수, 1 이상. `search_jobs.data.jobs[].id`를 사용한다.

DB에서 공고 식별자를 확인한다. 잡알리오 공고의 필수·우대 자격이 모두 미확인이면 외부 상세 API `/new/v1/recruit/detail.do`를 호출하고 확보한 자격을 캐시한다. 이미 확보한 상세 정보는 재사용한다.

성공 `data`:

```text
id: integer
source, board_id, source_job_id, company, title: string
deadline: YYYY-MM-DD | null
job_url, description_html: string | null
required_qualifications, preferred_qualifications: array<string> | null
```

공고가 없으면 `NO_RESULT`. 확인되지 않은 자격은 `null`, 확인했지만 별도 조건이 없으면 빈 배열이다. 값은 추측해서 채우지 않는다.

## search_ncs_skills — 다른 출처의 직무 능력단위 조회

외부 API: 공공데이터포털 한국산업인력공단 NCS API, `/B490007/hrdkapi/NCS005`.

입력: `ncs_code: string` 필수(확인된 8자리 코드), `limit: integer` 기본값 20(1 이상).

성공 `data`:

```text
ncs_code: string
count: integer
skills: array<object>
  code: string | null
  name: string
  definition, level, version: API 값 | null
```

사용 중인 최신 차수의 능력단위를 반환한다. 결과가 없으면 `NO_RESULT`. 코드가 없을 때 Agent는 임의로 생성하지 않고 추가 입력을 요청한다.

## get_cert_schedule — 다른 출처의 자격시험 일정 조회

외부 API: 공공데이터포털 한국산업인력공단 Q-Net, `/B490007/qualExamSchd/getQualExamSchdList`.

입력: `year: integer` 필수(1000~9999), `qualification_type: string` 기본값 `T`(`T/C/W/S`), `qualification_code: string | null` 기본값 `null`(확인된 4자리 종목코드), `page: integer` 기본값 1, `page_size: integer` 기본값 10.

성공 `data`:

```text
count: integer — 현재 페이지 반환 건수
total: integer — 외부 API 전체 건수
page: integer
schedules: array<object>
  description, round: API 값 | null
  written_registration_start, written_registration_end: YYYY-MM-DD | null
  written_exam_start, written_exam_end, written_result_date: YYYY-MM-DD | null
  practical_registration_start, practical_registration_end: YYYY-MM-DD | null
  practical_exam_start, practical_exam_end, practical_result_date: YYYY-MM-DD | null
```

결과가 없으면 `NO_RESULT`. 확인되지 않은 종목코드나 연도를 임의로 정하지 않는다. 외부 API 키는 `DATA_GO_KR_KEY`를 사용한다.

## 실행 이벤트

요청·응답은 동일한 `call_id`로 연결한다. 응답 상태·건수·식별자·소요 시간과 허용된 오류 코드만 발행한다. `search_jobs`의 성공 이벤트에는 `data_source`를 포함해 외부 갱신과 캐시 재사용을 구분한다. 인증키·사용자 요청·자유 텍스트 인자·원문 응답은 발행하지 않는다.
