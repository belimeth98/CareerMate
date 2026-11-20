# GMS 모델 설정과 GPT-6 Luna 검증

현재 기본 모델은 `gpt-6-luna`이며, `.env`의 `GMS_API_KEY`로 GMS OpenAI 경로를 사용한다.

```dotenv
GMS_MODEL=gpt-6-luna
GMS_BASE_URL=https://gms.ssafy.io/gmsapi/api.openai.com/v1
```

기존 프로젝트는 Chat Completions의 함수 호출로 요청 분석·요건 추출·경험 초안·이미지 초안을 구조화한다. Luna는 이 조합에서 `reasoning_effort=none`이 필요하므로 `build_gms_model()`이 자동 적용한다. `use_responses_api=False`, 30초 제한, 자동 재시도 0회는 유지한다.

공식 모델 문서: https://developers.openai.com/api/docs/models/gpt-6-luna

실제 GMS 검증 결과:

- Anthropic 경로 + Luna: HTTP 404.
- OpenAI 경로 + Luna: 응답 모델 `gpt-6-luna`, 일반 한국어 응답 성공.
- 실제 RequestAnalysis 구조화, Tool 호출 및 ToolMessage 응답 연결 성공.
- Python·SQL 필수요건과 FastAPI 우대요건 추출 성공.
- 경험 초안: 제공하지 않은 기관·날짜를 채우지 않고 추가 질문 제공.
- 검증용 캡처: 필수·우대요건과 `2026-11-01 ~ 2026-12-31` 모집 날짜 추출 성공.
- LangGraph 후속 요청: 같은 세션·다른 실행 ID, 지역만 바뀌어도 직무·고용형태·건수 유지.
- 정상 검색 0건과 Tool 오류·마감 조건 없음 구분 확인.

검증에는 가상 요청·이미지·Tool 결과를 사용했다. 운영 DB와 실제 공고 수집은 변경하지 않았다. 기존 서비스 기능의 호환성 확인이며 전체 모델 품질 비교나 정확도 보증은 아니다.

## Claude로 되돌리는 경우

`.env`의 모델과 URL을 함께 바꾼다.

```dotenv
GMS_MODEL=claude-haiku-5-5
GMS_BASE_URL=https://gms.ssafy.io/gmsapi/api.anthropic.com/v1
```

Claude에는 OpenAI 전용 reasoning 옵션을 보내지 않는다. `GMS_BASE_URL`을 아예 생략하면 `claude-` 모델명은 Anthropic 경로, 그 외 모델명은 OpenAI 경로를 기본으로 선택한다. 명시한 URL은 항상 우선한다.

모델 설정을 변경한 뒤 실행 중인 서버를 재시작한다. 모델 연결을 직접 시험하는 CLI는 실제 공고 Tool을 호출하므로 간단한 설정 회귀 검사는 아래 명령을 사용한다.

```bash
cd /c/Users/SSAFY/Desktop/CareerMate
source .venv/Scripts/activate
python -B -m unittest discover -s test -p test_gms_model.py -v
python -m uvicorn backend.app.main:app --reload
```
