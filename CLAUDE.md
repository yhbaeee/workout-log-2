# 오늘의 운동기록 (Streamlit)

날짜별로 부위(어깨·가슴·등·하체)를 고르고 기본 운동 후보와 한조바디 자세 영상을 보며 세트별 중량·횟수를 기록하는 한국어 웹앱. 교육용 개인 미니 프로젝트. GitHub: yhbaeee/workout-log (public), Streamlit Community Cloud 배포: https://workout-log-sxfzv8bszvclawnrozr2nb.streamlit.app/

## 명령
- 실행: `streamlit run app.py` (로컬은 `.venv` 사용: `.venv\Scripts\python -m streamlit run app.py`)
- 테스트: `.venv\Scripts\python -m pytest -q tests` (118개: record_utils 67·ai_exercises 28·ai_client 23). 폴더명에 `[ ]`가 있으면 `tests` 경로를 빼면 0개 수집된다.
- 브라우저 점검: Playwright(chromium)는 개발용으로만 `.venv`에 설치. `requirements.txt`에는 `streamlit`, `openai`, `python-dotenv`만 둔다.

## 구조
- `app.py`: UI와 세션 상태·콜백만. 검증·기록 변환 로직을 넣지 않는다.
- `record_utils.py`: Streamlit 비의존 순수 로직(검증, 추가/수정/삭제, JSON 내보내기·복원, AI 운동 정의 검증·추가·삭제·정리). 입력 리스트·사전을 변경하지 않고 새 객체를 반환. 오류는 `ValidationError`(사용자에게 보여도 되는 메시지).
- `ai_exercises.py`: AI 후보 생성의 순수 로직(요청 검증, 지시문·JSON 스키마, 응답 정리: 링크·수치 처방 제거, 조건 불일치·이름 중복 제외).
- `ai_client.py`: OpenAI 호출만. `.env` 로드, Responses API(JSON 스키마, `store=False`), 예외를 `AIError`(고정 한국어 문구)로 변환. `DEFAULT_MODEL`·타임아웃·재시도 상수.
- `data/exercises.json`: 운동 카탈로그 26개(어깨 7·가슴 7·등 6·하체 6; 부위/장비/중량 기준/영상). `data/demo_records.json`: 가상 데모(record_id는 `demo-` 접두사).
- `docs/video_research.md`: 영상 조사 근거. `docs/screenshots/`: Playwright 캡처.
- `.env`(Git 제외): `OPENAI_API_KEY`, 선택 `OPENAI_MODEL`. `.env.example`에는 키 이름만.
- `_prev/`: 초기 버전 보관(수정 금지, Git 제외). 현재 작업 폴더에는 없다.
- 제출용 작업 폴더는 Git 저장소가 아니며 오프라인 제출용이다(압축 시 `.env`·`.venv`·`~$*.docx` 제외). Git 작업은 별도 복제본(`workout-log` 폴더)에서 하고, 작업 폴더의 변경 파일을 복사해 커밋한다.

## 규칙
- 모든 UI 문구는 한국어. 오류 메시지에 내부 예외를 그대로 노출하지 않는다.
- 기록은 `st.session_state`에만 둔다. 전역 변수·`st.cache_*`에 사용자 기록을 저장하지 않는다. 장기 보관은 JSON 백업.
- 기록 필드: record_id, date(YYYY-MM-DD), exercise_id, weight_kg, reps, memo. 부위·운동명·중량 기준은 카탈로그에서 조회. 세트 번호는 같은 날짜·운동 내 저장 순서.
- 세트 입력 위젯 키는 `행 uid`와 저장마다 증가하는 `nonce`를 포함한다(행 삭제 시 값이 섞이지 않고 중복 저장이 막힌다). 인덱스 기반 키를 쓰지 않는다.
- 저장·수정·삭제·복원은 `on_click` 콜백에서 처리하고 결과는 `flash`로 표시한다.
- 입력칸 기본값은 비워 둔다(운동 처방처럼 보이는 기본값 금지). 상한: 중량 1000kg, 횟수 1000회, 메모 200자.
- 영상 정보(제목·채널·URL·ID·길이·시작 시각)는 실제로 확인한 것만 넣는다. 추측 금지, 미확인은 `null`. 한조바디가 아닌 채널로 임의 대체 금지. 영상이 없으면 '참고 영상 준비 중'.
- 앱 실행 중 유튜브 검색·크롤링 없음. 외부 API 호출은 AI 운동 후보 생성(OpenAI)뿐이며 로컬 `.env`에 키가 있을 때만 켜진다(배포본은 키 미등록 → 비활성). 경로는 `Path(__file__)` 기준.
- AI 기능: 키는 `.env`에서만 읽고(환경 변수·Secrets 미사용) 화면·로그·오류 메시지·커밋에 넣지 않는다. 모델에는 요청 조건과 해당 부위의 기존 운동명만 보내고 기록·중량·메모·개인정보는 보내지 않는다. 모델이 만든 URL·영상 ID·길이·타임스탬프는 표시하지 않으며 검색 키워드는 텍스트로만 보인다. AI 운동은 `exercises.json`에 쓰지 않고 세션(`custom_ex`)과 백업 JSON(v2 `custom_exercises`)에만 둔다. 기본 운동과 구분해 'AI 생성'으로 표시하고, 기록이 남은 AI 운동은 삭제를 막는다.
- 운동 효과·부상 판단, 개인별 중량 처방, 1RM·볼륨·칼로리 계산은 넣지 않는다.
- 새 의존성은 꼭 필요할 때만 추가.

## 점검 시 알아둘 점
- Streamlit은 접힌 expander 안의 위젯도 DOM에 렌더한다. Playwright에서는 `details[open]`로 범위를 좁혀 찾는다.
- 번들 chromium 헤드리스는 유튜브 영상이 검은 화면으로 나온다. 영상 캡처는 `p.chromium.launch(channel="chrome")`(설치된 Chrome)을 쓰면 썸네일·플레이어가 로드된다.
- 날짜 입력은 year/month/day spinbutton으로 나뉘어 있다. 각각 click 후 숫자를 입력하고 Escape 후 다른 곳을 클릭해 확정한다.
- 화면 캡처는 `docs/screenshots/`에 `2-1a`~`2-3c` 이름으로 두며(결과보고서 2.1~2.3 대응), 재생성 시 Pillow로 상단 헤더와 하단 여백을 자른다(개발용, requirements 제외).
- 모바일(390px)에서는 columns가 세로로 쌓인다. 가로 스크롤은 없음을 확인함.
- 새로고침하면 세션이 초기화되어 기록이 사라지는 것이 설계상 동작이다.

## 점검 이력 (2026-10-27)
- pytest 41개 통과(운동 26개 확장 후에도 통과). Playwright 12개 시나리오 통과(부위 전환, 세트 추가·삭제 값 유지, 더블클릭 중복 저장 방지, 삭제 대상, 백업 다운로드/복원, 잘못된 파일, 초기화, 모바일 폭).
- 발견·수정: 전체 초기화 후 동의 체크박스가 체크된 채 남던 문제(키 기반 위젯으로 변경), 업로더에 5MB 제한 안내가 없던 문제(라벨에 표기).
- 최종 점검(26개 운동, push 후 배포본 확인은 사용자가 수행): 최종 버전 화면 캡처 8장 생성, 영상 임베드 재생 화면 확인(Chrome).

## 점검 이력 (2026-11-06, AI 운동 후보 추가)
- pytest 118개 통과(실제 API 미호출). Streamlit AppTest 33개 항목 통과(키 없음 상태, 가짜 응답으로 생성·생성 과정 단계·추가·저장·기록·삭제 차단·재생성 정리·데모 교체·오류 안내·실패 단계 표시·횟수 제한·키 미노출).
- 실제 API 1회 호출(gpt-6-astra, 가슴/덤벨/초보/3개): 11.5초, 입력 832·출력 634토큰, JSON 스키마 응답 정상, 링크·수치 처방 없음. 기본 목록과 겹친 1개가 제외되어 기존 운동명을 제외 목록으로 보내도록 수정(수정 후 실제 호출은 하지 않음).
- `gpt-6-astra`, `gpt-6-sol` 모두 이 키로 조회 가능(models.retrieve). openai 3.46.0 기준.
- 'AI 운동 후보' 탭에 단계별 '생성 과정'(조건 확인 → 보낼 정보 → AI 호출(응답 시간) → 응답 검증(제외·정리 사유별 개수) → 결과) 표시 추가. 생성 중에는 st.status로 실시간 표시.
- Playwright(Chrome) 브라우저 점검: 리허설은 가짜 응답 서버(스크래치패드 launcher로 app.py 실행, API 미호출)로 먼저 돌린 뒤 실제 서버에서 1회 호출. 하체/상관없음/초보/4개/추가 요청: 16.6초, 받은 4개→채택 4개. 후보 추가·저장·세트 기록·기록 보기 AI 표시 정상, 모바일 390px 가로 스크롤 없음.
- 점검 시: Streamlit은 내부 `[data-testid=stMain]`이 스크롤되므로 `full_page` 캡처가 화면 높이만 찍힌다. 창 높이를 scrollHeight로 늘린 뒤 요소 좌표로 clip한다. 1.67에는 `stVerticalBlockBorderWrapper` testid가 없으므로 버튼 역할·순서로 찾는다.

## 배포
- Community Cloud: 저장소 `yhbaeee/workout-log`, 브랜치 `main`, 파일 `app.py`, Python 3.12 권장(로컬 `.venv`는 3.12). AI 기능은 로컬 전용이라 Secrets에 키를 등록하지 않는다.
- push하면 배포 앱이 재배포된다. 외부 공개·push는 사용자 확인 후 진행.
