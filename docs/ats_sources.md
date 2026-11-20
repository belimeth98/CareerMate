# Greenhouse·Lever 국내 기업 통합 검색

`search_jobs(source="all")`은 잡알리오·인크루트 RSS·Greenhouse·Lever를 통합합니다. 추가 출처는 `config/job_boards.json`에 등록한 국내 기업만 조회합니다. API는 전 세계 공고를 한 번에 검색하는 서비스가 아니라 기업별 게시판입니다. 공개 GET 목록·상세 API만 사용하며 지원서를 제출하지 않습니다. 별도 인증키는 필요하지 않습니다.

## 기본 기업과 설정

- Greenhouse: 크래프톤 `krafton` 게시판, `Corp./Studios=KRAFTON` 메타데이터 필터 적용.
- Lever: `matchgroup` 게시판의 `department=Hyperconnect`만 조회하고 응답에서도 같은 사업부인지 다시 확인. 다른 Match Group 계열사 공고는 수집하지 않습니다.
- 해외 기업인 GitLab·Spotify는 기본 수집 대상에 포함하지 않습니다.

기업 추가는 설정의 해당 출처 배열에 board_id, company, country="KR", aliases를 추가합니다. 공유 게시판이면 department 또는 metadata_filters로 허용 기업을 좁힙니다. `country=KR`은 운영자가 확인한 국내 기업 허용 목록 표시이며, API가 기업의 국적을 자동 판정한다는 뜻이 아닙니다. 등록할 때 공식 채용 페이지에서 기업과 게시판의 관계를 확인합니다. 국내 기업의 해외 근무 공고도 있을 수 있으며 근무지는 사용자의 지역 조건으로 별도 필터링합니다.

기본 설정 경로는 `config/job_boards.json`, 선택 환경변수는 `CAREERMATE_JOB_BOARDS_FILE`입니다. 빈 배열은 해당 출처에 등록된 기업이 없는 상태로 처리합니다. 기업별 15분 캐시를 사용하며 설정 범위가 바뀌면 이전 범위의 캐시를 섞지 않습니다. 같은 요청의 재계획은 refresh=false로 이미 확보한 목록을 재사용합니다.

## 수집·저장

Greenhouse는 `content=true` 목록의 전체 공개 공고를 받습니다. Lever는 skip/limit 페이지를 모두 수집하며 반복 페이지·수집 상한은 전체 성공으로 처리하지 않습니다. 완전한 목록을 받은 기업만 트랜잭션으로 갱신합니다. 성공 목록에서 사라진 해당 기업 공고는 진행 종료로 표시하고, 실패한 기업은 이전 공고 행을 보존하되 정상 최신 결과로 노출하지 않습니다. 프로필·경험·계획·평가 이력은 변경하지 않습니다.

DB는 기존 job_posting과 job_source_snapshot을 사용합니다. source는 greenhouse/lever, board_id는 실제 게시판 식별자, source_job_id는 제공기관 공고 id입니다. 게시판·기업 범위별 snapshot 키로 최신 후보 id를 관리합니다. `get_job_detail`은 저장한 공식 본문을 반환하고 본문이 없을 때 제공기관 상세를 조회합니다.

API가 제공하지 않은 마감일·경력·NCS·필수 자격을 임의로 채우지 않습니다. `Full-time`은 정규직으로 번역하지 않습니다. 확인할 수 없는 고용형태·경력·NCS 조건은 unverified_filters에 표시한 참고 후보로 안내합니다. Greenhouse의 명시적인 Regular/Permanent만 정규직으로 정규화합니다. 지역은 서울/Seoul 등 확인된 표기 대응을 적용합니다. 검색 직무는 기존 공통 직무 분류를 사용합니다.

## 인크루트 우선 중복 제거

검색 결과를 반환하기 전에 **현재 수집한 인크루트 RSS snapshot 전체**와 비교합니다. 현재 검색어에 맞는 일부 공고나 첫 페이지에만 비교하지 않습니다. 인크루트 항목을 남기고 중복된 Greenhouse·Lever 항목만 숨깁니다. DB 행을 삭제하지 않아 기존 계획·평가 연결을 보존합니다.

중복 근거는 다음 중 하나입니다.

1. 같은 원문 공고 URL/공고 식별자. 추적 파라미터와 Greenhouse 호스트 표기를 정규화합니다. 일반 회사 홈페이지는 공고 식별자로 사용하지 않습니다.
2. 법인 표기·설정된 한영 별칭을 정규화한 **동일 회사 + 동일 공고명**. 두 출처에서 지역을 확인할 수 있으면 지역도 충돌하지 않아야 합니다. 공고명에 붙은 회사명·단순 채용 접미사는 정리하지만 팀·연도·직급은 유지합니다.

다른 회사의 같은 직무, 다른 도시·직급·팀의 공고를 유사하다는 이유만으로 지우지 않습니다. 번역된 제목·크게 바뀐 제목을 AI가 추측해 합치는 방식은 사용하지 않습니다. 따라서 동일 원문이나 위 기준으로 식별할 수 없는 중복은 남을 수 있습니다.

중복 제거 후 요청 건수만큼 채우므로 앞의 중복 항목 때문에 뒤의 고유 공고를 놓치지 않습니다. 정렬은 기존처럼 제목 직무 일치를 우선하고 같은 등급에서 출처를 번갈아 배치합니다. source_results의 출처별 duplicate_count와 전체 duplicate_count에 제외 수를 기록하고 화면·실행 이력에 표시합니다.

**비교 범위는 수집한 RSS 일부 공고입니다. 인크루트 사이트 전체와의 중복 여부를 보장하지 않습니다.** RSS 조회가 실패하거나 비교 자료가 없으면 추가 출처는 DEDUP_REFERENCE_UNAVAILABLE로 노출을 보류하고, 처리 가능한 기존 출처 결과는 계속 제공합니다. 성공한 빈 RSS 목록은 정상 비교 결과이므로 고유 ATS 공고를 표시합니다.

## API·로그·화면

채팅 API의 job_source와 Tool source는 all/alio/incruit/greenhouse/lever를 지원합니다. 화면은 출처 선택 없이 통합 검색을 유지하고 카드에 실제 출처를 표시합니다. 원문 링크, 추가 출처 범위·미확인 조건, 인크루트 중복 제외 수를 안내합니다. 이벤트 발행 허용 목록은 네 출처와 정제된 오류 코드·제외 수만 기록하며 전체 본문·키·개인 프로필은 기록하지 않습니다. Logging Agent의 LLM 호출은 없습니다.

## 검증·공식 근거

```bash
python -B -m unittest discover -s test -p "test_ats_extension.py" -v
```

정상 API 계약·Lever 페이지 수집·국내 사업부 제한·캐시·실패 보존·다양한 중복/비중복 사례·요청 건수 보충·빈 RSS·RSS 장애·4개 출처·로그 허용 목록을 임시 DB에서 검증합니다. 기존 test_greenhouse.py/test_lever.py는 import 시 실제 네트워크를 호출하는 이전 수동 스크립트이므로 전체 discover 대신 파일을 지정합니다.

- [Greenhouse Job Board API](https://developers.greenhouse.io/job-board.html)
- [Lever 공식 Postings API](https://github.com/lever/postings-api)
- [크래프톤 공식 채용](https://www.krafton.com/careers/jobs/)
- [하이퍼커넥트 공식 채용](https://career.hyperconnect.com/jobs/)
- [하이퍼커넥트 국내 법인 주소](https://hyperconnect.com/ko/contact/)
