# CareerMate

Python · FastAPI · LangGraph · GMS · SQLite로 구현한 취업 준비 도우미입니다. 현재 보유 역량과 성장 계획을 구분해 공고를 추천하고, 현재·미래 적합도, 지원 일정, Agent 실행 기록을 함께 관리합니다.

## 실행

프로젝트 루트의 Git Bash에서 실행합니다.

```bash
cd /c/Users/SSAFY/Desktop/CareerMate
source .venv/Scripts/activate
# 새 환경이라면: python -m venv .venv 후 위 activate 실행
python -m pip install -r requirement.txt
python -m backend.app.db.database
python -m uvicorn backend.app.main:app --reload
```

- 사용자 화면: <http://127.0.0.1:8000/>
- API 문서: <http://127.0.0.1:8000/docs>
- 서버 확인: <http://127.0.0.1:8000/health>

이미 의존성과 DB가 준비되어 있다면 마지막 uvicorn 명령만 실행하면 됩니다. HTML/CSS/JavaScript 화면은 FastAPI가 같은 주소에서 제공합니다. Node.js 빌드나 별도 프론트엔드 서버가 필요하지 않습니다.

## 환경변수

루트 `.env`에 다음 이름으로 설정합니다. `.env.example`은 키 이름만 제공합니다. 기존 `.env`를 덮어쓰지 마세요.

```dotenv
GMS_API_KEY=
GMS_MODEL=gpt-4.1
ALIO_API_KEY=
DATA_GO_KR_KEY=
```

GMS는 대화·공고 조건 분석에, 잡알리오 키는 외부 공고 목록·상세 조회에, DATA_GO_KR_KEY는 NCS·Q-Net 조회에 사용합니다. 키가 없어도 프로필·계획·로그 화면은 사용할 수 있고, 대화에서 키 설정 안내를 표시합니다. 키 변경 후 서버를 다시 시작합니다.

## 사용 순서

1. **내 프로필**: 희망 직무·지역·고용형태, 현재 보유 기술·자격을 입력하고 저장합니다. 여러 항목은 쉼표로 구분합니다. 현재 역량에는 이미 보유한 것만 입력합니다.
2. **성장 계획**: 앞으로 학습하거나 취득할 역량과 목표일을 추가합니다. 공고 마감 전 완료하는 활성 계획을 미래 점수에 반영합니다.
3. **AI 취업 상담**: 조건을 입력해 공고를 찾습니다. 프로필을 선택하고 적합도 계산을 켜면 현재·미래 점수와 확인 배점, 필수조건 판정, 확인할 역량을 보여 줍니다. 같은 대화에서 지역 등 조건만 바꿀 수 있습니다.
4. **준비 계획 만들기**: 추천 공고의 버튼으로 제목·공고·마감일을 가져와 일정을 저장합니다. 임의의 지원 일정도 직접 추가할 수 있습니다.
5. **지원 계획**: 날짜만 지정한 마감 또는 시간 지정 일정을 등록·수정·삭제합니다. 겹치는 일정도 저장하며 충돌 목록과 경고를 보여 줍니다.
6. **실행 기록**: 단계·도구 호출·판단 이유·재계획·오류를 발생 순서대로 확인합니다. 이전 대화를 선택하거나 세션 로그를 삭제할 수 있습니다. 로그 수정 기능은 제공하지 않습니다.

시간 입력·표시는 KST이며, 서버 저장은 UTC입니다. 날짜만 있는 마감에는 시각을 만들어 넣지 않습니다. 다른 프로필의 일정과 취소된 일정은 충돌 대상으로 보지 않습니다. 인접한 일정(앞 일정 종료=뒤 일정 시작)은 충돌하지 않습니다.

점수는 확인된 배점의 규칙 기반 참고 값이며 합격 확률이 아닙니다. 필수조건 미확인과 실제 불충족을 구분합니다. 프로필에 없는 역량이나 공고만으로 확정할 수 없는 조건은 추가 확인 대상으로 표시할 수 있습니다. 기타 우대조건은 기술 점수와 별도로 표시합니다. 완료한 성장 계획의 역량은 현재 프로필을 수정해 반영합니다.

## 주요 API

| Method | 경로 | 기능 |
|---|---|---|
| POST | /api/chat | session_id 기반 대화, 추천·적합도·현재 실행 이벤트 |
| GET, POST | /api/profiles | 프로필 목록·등록 |
| GET, PUT | /api/profiles/{id} | 프로필 상세·저장 |
| GET, POST | /api/growth-plans | 성장 계획 목록(profile_id)·등록 |
| DELETE | /api/growth-plans/{id} | 성장 계획 삭제(profile_id) |
| GET, POST | /api/plans | 지원 계획 목록(profile_id)·등록 |
| GET, PATCH, DELETE | /api/plans/{id} | 상세·수정·삭제(profile_id) |
| GET | /api/logs | 세션 로그 목록 |
| GET, DELETE | /api/logs/{session_id} | 발생 순서 상세·세션 삭제 |

계획 저장·수정 응답은 `{plan, conflicts, warning}`입니다. 충돌이 있어도 정상 저장합니다. 요청/응답 스키마 및 오류 코드는 `/docs`에서 확인할 수 있습니다.

## Agent 및 데이터

- 실행 흐름: 분석 → Tool 순서 계획 → 실행 → 제약 판단 → 필요 시 재계획 → 적합도 → 응답.
- 새 공고 검색은 잡알리오 진행 공고를 전체 수집·갱신합니다. 재계획은 같은 조건으로 후보 limit만 넓히며 확보한 캐시를 재사용합니다. 최대 2회입니다.
- 정상 0건은 Tool 오류나 마감 조건 불충족과 구분합니다.
- 이벤트 발행기와 Logging Agent는 분리되어 있습니다. 저장 오류·구독자 부재에도 대화를 계속합니다. 대화 응답의 실행 이벤트는 저장 실패 시에도 전달합니다.
- 인증키·요청/프로필 원문·Tool 응답 본문은 실행 이벤트에 넣지 않습니다. 구독자는 필터링이 끝난 이벤트를 그대로 기록합니다.
- 세션 맥락은 서버 프로세스 메모리입니다. 서버 재시작·reload 시 이전 맥락은 초기화됩니다. 단일 worker의 로컬 교육용 실행을 기준으로 합니다.

DB는 `careermate.sqlite3`이며 스키마는 `backend/app/db/schema.sql`에 있습니다. 프로필·계획은 실제 저장되고, `--demo-fit` 예시 평가는 롤백됩니다. 화면 테스트는 별도 DB 복사본과 임시 DB를 사용합니다.

Tool 명세는 `docs/tools.md`, 입력 JSON Schema는 `docs/tool_input_schemas.json`에 있습니다. 외부 출처는 [잡알리오 API](https://opendata.alio.go.kr), [한국산업인력공단 공공데이터 API](https://apis.data.go.kr/B490007)이며, GMS는 SSAFY 제공 엔드포인트를 사용합니다. 공고 원문 링크를 함께 제공하며 최종 지원 요건·마감일은 원문을 기준으로 확인합니다. 데이터 재배포나 별도 서비스 공개 시 해당 API의 이용 조건을 별도로 확인해야 하며, 이 저장소에서 이용허락 범위를 판정하지 않습니다.

## 검증

네트워크·원본 DB 변경 없이 실행하는 회귀 테스트:

```bash
python -B -m unittest discover -s test -p "test_execution_logging.py" -v
python -B -m unittest discover -s test -p "test_job_refresh.py" -v
python -B -m unittest discover -s test -p "test_log_api.py" -v
python -B -m unittest discover -s test -p "test_workspace_ui.py" -v
```

기존 test 폴더에는 import 시 실제 API를 호출하는 수동 검증 스크립트도 있어 위 파일을 지정해 실행합니다. 자동 회귀 테스트의 Mock·장애 주입과 실제 API 통합 검증을 구분합니다.

실제 CLI 대화:

```bash
python -m backend.app.career_agent \
  "강원 지역 정규직 약사 공고를 1건 찾아줘." \
  --follow-up "지역만 서울로 바꿔줘."
```

실제 외부 API를 호출하고 공고 캐시·실행 로그를 저장합니다. 화면에서 일정 저장·변경·삭제도 SQLite에 반영됩니다.
