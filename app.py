import time
from datetime import date
from pathlib import Path

import streamlit as st

import ai_client
import ai_exercises as ai
import record_utils as ru

BASE = Path(__file__).resolve().parent
PARTS, CATALOG = ru.load_catalog(BASE / "data" / "exercises.json")
DEMO_PATH = BASE / "data" / "demo_records.json"
INITIAL_ROWS = 3
TAB_AI, TAB_REC, TAB_VIEW, TAB_BACKUP = "AI 운동 추천", "기록하기", "기록 보기", "백업·복원"

st.set_page_config(page_title=ru.APP_NAME, page_icon="🏋️", layout="centered")


# ---------- 세션 상태 ----------
def init_state():
    ss = st.session_state
    ss.setdefault("records", [])
    ss.setdefault("uid", 0)
    ss.setdefault("rows", {})        # exercise_id -> [row uid, ...]
    ss.setdefault("nonce", {})       # exercise_id -> 저장 후 입력칸 초기화용 번호
    ss.setdefault("saved_nonces", set())
    ss.setdefault("flash", {})       # 영역 이름 -> (종류, 메시지)
    ss.setdefault("pending_delete", None)
    ss.setdefault("pending_restore", None)
    ss.setdefault("pending_demo", False)
    ss.setdefault("confirm_reset", False)
    ss.setdefault("uploader_n", 0)
    ss.setdefault("custom_ex", {})       # AI 생성 운동: exercise_id -> 운동 dict (saved: 내 운동 목록 여부)
    ss.setdefault("rec_ids", [])         # 기록하기에 등록된(AI가 추천한) 운동 ID 목록
    ss.setdefault("ai_request", None)    # 가장 최근 추천 조건
    ss.setdefault("ai_generations", 0)
    ss.setdefault("ai_trace", None)      # 최근 생성 과정: [(성공 여부, 단계, 내용), ...]
    ss.setdefault("rec_plan", {})        # exercise_id -> 권장 세트·횟수·시작 중량(신체 정보를 입력했을 때만, 세션 한정)
    ss.setdefault("goto_tab", None)      # 다음 실행에서 열 탭
    ss.setdefault("view_day", date.today())
    if "main_tab" not in ss:             # AI를 못 쓰면 처음부터 기록하기 탭
        ss["main_tab"] = TAB_REC if ai_client.unavailable_reason() else TAB_AI


def all_catalog():
    """기본 카탈로그 + 이 세션의 AI 생성 운동."""
    return {**CATALOG, **st.session_state.custom_ex}


def sync_rec_ids():
    """사라진 운동은 기록하기 목록에서 뺀다."""
    ss = st.session_state
    catalog = all_catalog()
    ss.rec_ids = [i for i in ss.rec_ids if i in catalog]


def new_uid():
    st.session_state.uid += 1
    return st.session_state.uid


def rows_for(ex_id):
    ss = st.session_state
    if ex_id not in ss.rows:
        ss.rows[ex_id] = [new_uid() for _ in range(INITIAL_ROWS)]
    return ss.rows[ex_id]


def nonce_for(ex_id):
    return st.session_state.nonce.setdefault(ex_id, 0)


def flash(area, kind, msg):
    st.session_state.flash[area] = (kind, msg)


def show_flash(area):
    """안내 문구. 문구가 없어도 자리(st.empty)를 남겨, 사라질 때 아래 위젯 위치가 밀려 expander가 닫히지 않게 한다."""
    slot = st.empty()
    item = st.session_state.flash.pop(area, None)
    if item:
        getattr(slot, item[0])(item[1])


# ---------- 콜백 ----------
def add_row(ex_id):
    rows_for(ex_id).append(new_uid())


def remove_row(ex_id, uid):
    rows = rows_for(ex_id)
    if len(rows) > 1:
        rows.remove(uid)


def save_sets(ex_id, day_key):
    ss = st.session_state
    nonce = nonce_for(ex_id)
    token = (ex_id, nonce)
    if token in ss.saved_nonces:  # 같은 입력창의 중복 저장 방지
        return
    new = []
    catalog = all_catalog()
    try:
        day = ru.validate_date(ss[day_key])
        for n, uid in enumerate(rows_for(ex_id), start=1):
            w = ss.get(f"w_{ex_id}_{nonce}_{uid}")
            r = ss.get(f"r_{ex_id}_{nonce}_{uid}")
            if w is None and r is None:
                continue
            if w is None or r is None:
                raise ru.ValidationError(f"{n}번째 입력 행의 중량과 횟수를 모두 입력해 주세요.")
            memo = ss.get(f"m_{ex_id}_{nonce}_{uid}", "")
            try:
                new.append(ru.make_record(day, ex_id, w, r, memo, catalog))
            except ru.ValidationError as e:
                raise ru.ValidationError(f"{n}번째 입력 행: {e}") from None
        if not new:
            raise ru.ValidationError("저장할 세트가 없습니다. 중량과 횟수를 입력해 주세요.")
        ss.records = ru.add_records(ss.records, new)
    except ru.ValidationError as e:
        flash(f"rec_{ex_id}", "error", str(e))
        return
    ss.saved_nonces.add(token)
    ss.nonce[ex_id] = nonce + 1
    ss.rows[ex_id] = [new_uid() for _ in range(INITIAL_ROWS)]
    ss["view_day"] = ss[day_key]  # 기록 보기가 방금 저장한 날짜를 열도록
    flash(f"rec_{ex_id}", "success",
          f"{day} {catalog[ex_id]['name']} {len(new)}세트를 저장했습니다. '기록 보기' 탭에서 확인할 수 있습니다.")


def save_edit(record_id):
    ss = st.session_state
    try:
        ss.records = ru.update_record(
            ss.records,
            record_id,
            ss.get(f"ew_{record_id}"),
            ss.get(f"er_{record_id}"),
            ss.get(f"em_{record_id}", ""),
            all_catalog(),
        )
        flash(f"edit_{record_id}", "success", "수정했습니다.")
    except ru.ValidationError as e:
        flash(f"edit_{record_id}", "error", str(e))


def ask_delete(record_id):
    st.session_state.pending_delete = record_id


def cancel_delete():
    st.session_state.pending_delete = None


def confirm_delete(record_id):
    ss = st.session_state
    try:
        ss.records = ru.delete_record(ss.records, record_id)
        flash("view", "success", "기록을 삭제했습니다.")
    except ru.ValidationError as e:
        flash("view", "error", str(e))
    ss.pending_delete = None


def apply_records(new_records, message, custom=None):
    """기록을 교체한다. custom을 주면(복원) AI 운동도 교체, 아니면 저장·사용 중인 것만 남긴다."""
    ss = st.session_state
    ss.records = new_records
    ss.custom_ex = custom if custom is not None else ru.prune_custom(ss.custom_ex, new_records, ss.rec_ids)
    sync_rec_ids()
    ss.pending_restore = None
    ss.pending_demo = False
    ss.pending_delete = None
    flash("backup", "success", message)


def load_demo():
    text = DEMO_PATH.read_text(encoding="utf-8")
    apply_records(ru.parse_backup(text, CATALOG), "가상 데모 기록을 불러왔습니다.")


def ask_demo():
    if st.session_state.records:
        st.session_state.pending_demo = True
    else:
        load_demo()


def cancel_demo():
    st.session_state.pending_demo = False


def do_restore():
    ss = st.session_state
    if ss.pending_restore is not None:
        records, custom = ss.pending_restore
        apply_records(records, "백업 파일로 기록을 복원했습니다.", custom)
        ss.uploader_n += 1


def cancel_restore():
    st.session_state.pending_restore = None
    st.session_state.uploader_n += 1


def do_reset():
    apply_records([], "모든 기록을 삭제했습니다.")
    st.session_state.confirm_reset = False


def add_rec(ex_id):
    """내 운동 목록의 운동을 기록하기 목록에 올리고 기록하기 탭으로 이동."""
    ss = st.session_state
    if ex_id not in all_catalog():
        flash("ai", "error", "운동을 찾을 수 없습니다.")
        return
    if ex_id not in ss.rec_ids:
        ss.rec_ids = ss.rec_ids + [ex_id]
    ss.goto_tab = TAB_REC
    flash("rec_top", "success", f"'{all_catalog()[ex_id]['name']}'을(를) 기록하기 목록에 올렸습니다.")


def set_extra(text):
    st.session_state["ai_extra"] = text


def remove_rec(ex_id):
    """기록하기 목록에서 운동을 뺀다(저장·기록이 없는 AI 운동은 함께 정리)."""
    ss = st.session_state
    ss.rec_ids = [i for i in ss.rec_ids if i != ex_id]
    ss.custom_ex = ru.prune_custom(ss.custom_ex, ss.records, ss.rec_ids)


def save_my_ai(ex_id, area):
    """AI 운동을 내 운동 목록(최대 20개)에 저장."""
    ss = st.session_state
    ex = ss.custom_ex.get(ex_id)
    if ex is None:
        flash(area, "error", "운동을 찾을 수 없습니다. 다시 추천받아 주세요.")
        return
    try:
        ss.custom_ex = ru.add_custom(ss.custom_ex, ex, CATALOG, True)
        flash(area, "success", f"'{ex['name']}'을(를) 내 운동 목록에 저장했습니다.")
    except ru.ValidationError as e:
        flash(area, "error", str(e))


def remove_ai(ex_id):
    ss = st.session_state
    try:
        name = ss.custom_ex[ex_id]["name"] if ex_id in ss.custom_ex else ""
        ss.custom_ex = ru.remove_custom(ss.custom_ex, ex_id, ss.records)
        sync_rec_ids()
        flash("ai", "success", f"'{name}'을(를) 목록에서 삭제했습니다.")
    except ru.ValidationError as e:
        flash("ai", "error", str(e))


TRACE_STEPS = ("요청 조건 확인", "보낼 정보 구성", "AI 호출", "응답 검증")


def run_generation(part, equipment, level, count, extra, height=None, weight=None, goal=None,
                   progress=lambda msg: None):
    """AI 운동 추천 → 기록하기 목록 등록 → 기록하기 탭으로 이동.

    단계별 진행 내역을 ai_trace에 남긴다(조건 요약·모델명·응답 시간·검증 통계만, 키·전체 프롬프트 제외).
    저장하지 않았고 기록도 없는 이전 AI 운동은 정리한다. progress는 생성 중 화면에 단계를 바로 보여 주는 함수.
    """
    ss = st.session_state
    steps = []

    def step(title, detail, ok=True):
        steps.append((ok, title, detail))
        progress(f"{'✅' if ok else '❌'} **{title}** — {detail}")

    try:
        body = ai.validate_body(height, weight, goal)
        req = ai.validate_request(part, equipment, level, count, extra, PARTS, body)
        step(TRACE_STEPS[0], f"{req['part']} · 장비 {req['equipment']} · 난이도 {req['level']} · "
                             f"{req['count']}개 · 추가 요청 {'있음' if req['extra'] else '없음'} · "
                             f"신체 정보 {ai.describe_body(body) if body else '없음'}")
        custom = ru.prune_custom(ss.custom_ex, ss.records)
        known = {**CATALOG, **custom}
        names = [e["name"] for e in ru.exercises_by_part(known, req["part"])]
        instructions, user_input = ai.build_prompt(req, names)
        step(TRACE_STEPS[1], f"요청 조건{' + 신체 정보(키·몸무게·목표)' if body else ''} + {req['part']} 부위 "
                             f"활용 가능 운동명 {len(names)}개(중복 방지·영상 연결). 저장된 기록·중량·메모·이름은 보내지 않음")
        _, model = ai_client.load_settings()
        progress(f"⏳ **{TRACE_STEPS[2]}** — {model} 응답 대기 중")
        ss.ai_generations += 1
        started = time.perf_counter()
        text = ai_client.generate_json(instructions, user_input, ai.response_schema(req))
        step(TRACE_STEPS[2], f"{model} · JSON 스키마 형식 응답 · {time.perf_counter() - started:.1f}초")
        plans = {}
        ids, new, stats = ai.parse_response(text, req, CATALOG, custom, plans_out=plans if body else None)
        step(TRACE_STEPS[3], ai.describe_stats(stats))
    except (ru.ValidationError, ai_client.AIError) as e:
        step(TRACE_STEPS[min(len(steps), len(TRACE_STEPS) - 1)], str(e), ok=False)
        ss.ai_trace = steps
        flash("ai", "error", str(e))
        return False
    merged = {**CATALOG, **custom, **new}
    listing = ", ".join(f"{merged[i]['name']}({'AI' if ru.is_custom(merged[i]) else '기본'})" for i in ids)
    step("결과", f"기록하기에 {len(ids)}개 등록: {listing}"
                 + (f" · 권장 운동량 {len(plans)}개" if body else ""))
    ss.ai_trace = steps
    ss.custom_ex = {**custom, **new}
    ss.rec_ids = ids
    ss.rec_plan = plans
    ss.ai_request = req
    ss.goto_tab = TAB_REC
    flash("rec_top", "success", f"AI가 추천한 운동 {len(ids)}개를 등록했습니다. 운동을 열어 세트를 기록하세요.")
    return True


# ---------- 화면 조각 ----------
def ai_info(ex):
    """AI 생성 운동 설명. 확인된 영상이 없으므로 키워드와 한조바디 채널 검색 링크를 제공한다."""
    st.markdown(f"`AI 생성` {ex['part']} · {ex['equipment']} · {ex['level']}")
    st.write(ex["description"])
    st.caption(f"중량 입력 기준: {ex['weight_basis']}")
    if ex["caution"]:
        st.caption(f"일반 주의사항: {ex['caution']}")
    st.caption("참고 영상: AI가 추천한 운동이라 확인된 영상이 없습니다. 한조바디 채널에서 검색해 보세요.")
    st.code(ex["search_keyword"], language=None)
    st.link_button("한조바디 채널에서 검색", ai.hanjobody_search_url(ex["search_keyword"]))


def video_block(ex):
    v = ex.get("video")
    if not v or not v.get("url"):
        st.caption("참고 영상 준비 중")
        return
    start = v.get("start_sec") or 0
    if st.toggle("자세 영상 보기", key=f"vt_{ex['id']}"):
        try:
            st.video(v["url"], start_time=start)
        except Exception:
            st.warning("영상을 표시하지 못했습니다. 아래 버튼으로 유튜브에서 열어 주세요.")
    link = v["url"] + (f"&t={start}s" if start else "")
    st.link_button("유튜브에서 열기", link)
    dur = v.get("duration_sec")
    length = f"{dur // 60}:{dur % 60:02d}" if dur else "길이 미확인"
    st.caption(f"{v['channel']} · {v['title']} · {length}")


def input_rows(ex):
    ex_id = ex["id"]
    nonce = nonce_for(ex_id)
    rows = rows_for(ex_id)
    for n, uid in enumerate(rows, start=1):
        st.markdown(f"**{n}세트**")
        c1, c2 = st.columns(2)
        c1.number_input("중량(kg)", min_value=0.0, step=0.5, value=None,
                        key=f"w_{ex_id}_{nonce}_{uid}", format="%g")
        c2.number_input("반복 횟수", min_value=1, step=1, value=None,
                        key=f"r_{ex_id}_{nonce}_{uid}")
        st.text_input("메모 (선택)", key=f"m_{ex_id}_{nonce}_{uid}", max_chars=ru.MAX_MEMO_LEN)
        if len(rows) > 1:
            st.button("이 세트 삭제", key=f"rm_{ex_id}_{nonce}_{uid}",
                      on_click=remove_row, args=(ex_id, uid))
    st.button("＋ 세트 추가", key=f"add_{ex_id}_{nonce}", on_click=add_row, args=(ex_id,))


def exercise_card(ex, day_key, recommended=False):
    """recommended=True: AI 추천 목록의 카드([기본 운동]/[AI 생성] 라벨, 빼기·저장 버튼)."""
    ex_id = ex["id"]
    custom = ru.is_custom(ex)
    if custom:
        label = f"[AI 생성] {ex['name']} · {ex['equipment']}"
    elif recommended:
        label = f"[기본 운동] {ex['name']} · {ex['equipment']}"
    else:
        label = f"{ex['name']} · {ex['equipment']}"
    with st.expander(label):
        if custom:
            ai_info(ex)
        else:
            st.caption(f"중량 입력 기준: {ex['weight_basis']}")
            video_block(ex)
        plan = st.session_state.rec_plan.get(ex_id) if recommended else None
        if plan:
            st.markdown(f"**AI 권장(참고용)** {ai.describe_plan(plan)}")
        st.divider()
        show_flash(f"rec_{ex_id}")
        input_rows(ex)
        st.button("저장", key=f"save_{ex_id}_{nonce_for(ex_id)}", type="primary",
                  on_click=save_sets, args=(ex_id, day_key))
        if recommended:
            c1, c2 = st.columns(2)
            c1.button("목록에서 빼기", key=f"rmrec_{ex_id}", on_click=remove_rec, args=(ex_id,))
            if custom and not ex["saved"]:
                c2.button("내 운동 목록에 저장", key=f"my_{ex_id}", on_click=save_my_ai, args=(ex_id, "rec_top"))
            elif custom:
                c2.caption("✔ 내 운동 목록에 저장됨")


# ---------- 탭 ----------
def tab_record():
    ss = st.session_state
    show_flash("rec_top")
    st.date_input("날짜", value=date.today(), key="record_day")
    if ai_client.unavailable_reason():  # 키가 없으면 기본 목록으로 기록(대체 경로)
        part = st.radio("부위", PARTS, horizontal=True)
        st.subheader(f"{part} 기본 운동 후보")
        st.caption("AI 운동 추천을 쓸 수 없는 환경이라 기본 운동 목록에서 고릅니다. "
                   "운동을 열어 세트별 중량과 반복 횟수를 입력하세요. 칸은 비어 있는 상태로 시작합니다.")
        for ex in ru.exercises_by_part(CATALOG, part):
            exercise_card(ex, "record_day")
        customs = ru.exercises_by_part(ss.custom_ex, part)
        if customs:
            st.subheader(f"{part} AI 생성 운동")
            for ex in customs:
                exercise_card(ex, "record_day")
        return
    if not ss.rec_ids:
        st.info("'AI 운동 추천' 탭에서 먼저 운동을 추천받으세요. 추천받은 운동만 이곳에서 기록합니다.")
        return
    req = ss.ai_request
    st.subheader(f"이번 추천 운동 {len(ss.rec_ids)}개")
    if req:
        st.caption(f"{req['part']} · 장비 {req['equipment']} · 난이도 {req['level']} — "
                   "운동을 열어 세트별 중량과 반복 횟수를 입력하세요. 칸은 비어 있는 상태로 시작합니다.")
    plans = [ss.rec_plan[i] for i in ss.rec_ids if i in ss.rec_plan]
    if plans and req and req.get("body"):
        sm = ai.summarize_plans(plans)
        st.info(f"오늘의 권장 운동량(참고용): 운동 {sm['exercises']}개 · 총 {sm['sets']}세트 · 약 {sm['minutes']}분 "
                f"({ai.describe_body(req['body'])}). 개인 처방이 아닌 초기 참고값이니 가볍게 시작해 "
                "컨디션에 맞게 조절하세요. 의학적 조언이 아닙니다.")
    catalog = all_catalog()
    for ex_id in ss.rec_ids:
        exercise_card(catalog[ex_id], "record_day", recommended=True)


def tab_view():
    day = st.date_input("조회할 날짜", key="view_day").isoformat()
    show_flash("view")
    day_records = ru.records_on(st.session_state.records, day)
    if not day_records:
        st.info("이 날짜에 저장된 기록이 없습니다.")
        return
    catalog = all_catalog()
    for ex_id, sets in ru.group_by_exercise(day_records).items():
        ex = catalog[ex_id]
        tag = " `AI 생성`" if ru.is_custom(ex) else ""
        st.markdown(f"#### {ex['name']}{tag}  \n{ex['part']} · {ex['equipment']} · 총 {len(sets)}세트")
        for n, r in enumerate(sets, start=1):
            rid = r["record_id"]
            memo = f" · {r['memo']}" if r["memo"] else ""
            with st.expander(f"{n}세트 · {r['weight_kg']:g}kg × {r['reps']}회{memo}"):
                show_flash(f"edit_{rid}")
                c1, c2 = st.columns(2)
                c1.number_input("중량(kg)", min_value=0.0, step=0.5, value=r["weight_kg"],
                                key=f"ew_{rid}", format="%g")
                c2.number_input("반복 횟수", min_value=1, step=1, value=r["reps"], key=f"er_{rid}")
                st.text_input("메모", value=r["memo"], key=f"em_{rid}", max_chars=ru.MAX_MEMO_LEN)
                b1, b2 = st.columns(2)
                b1.button("수정 저장", key=f"es_{rid}", on_click=save_edit, args=(rid,))
                b2.button("삭제", key=f"ed_{rid}", on_click=ask_delete, args=(rid,))
                if st.session_state.pending_delete == rid:
                    st.warning(f"{n}세트 기록을 삭제할까요? 되돌릴 수 없습니다.")
                    d1, d2 = st.columns(2)
                    d1.button("삭제 확인", key=f"edc_{rid}", type="primary",
                              on_click=confirm_delete, args=(rid,))
                    d2.button("취소", key=f"edn_{rid}", on_click=cancel_delete)


def tab_backup():
    ss = st.session_state
    show_flash("backup")
    if ru.is_demo(ss.records):
        st.warning("현재 기록에 가상 데모 데이터가 포함되어 있습니다. 실제 기록이 아닙니다.")

    st.markdown("#### 백업 내려받기")
    n_custom = len(ru.prune_custom(ss.custom_ex, ss.records))
    st.caption(f"현재 기록 {len(ss.records)}세트"
               + (f" · AI 생성 운동 {n_custom}개 정의 포함(내 운동 목록·기록에 쓰인 것)" if n_custom else ""))
    st.download_button("JSON 백업 다운로드", data=ru.export_json(ss.records, custom=ss.custom_ex),
                       file_name=f"workout_backup_{date.today().isoformat()}.json",
                       mime="application/json")

    st.markdown("#### 백업에서 복원")
    st.caption("복원하면 현재 기록과 추가한 AI 운동이 모두 백업 파일의 내용으로 교체됩니다.")
    up = st.file_uploader("백업 JSON 파일 (5MB 이하)", type=["json"], key=f"uploader_{ss.uploader_n}")
    if up is not None and ss.pending_restore is None:
        if up.size > 5_000_000:
            st.error("파일이 너무 큽니다.")
        else:
            try:
                text = up.getvalue().decode("utf-8")
            except UnicodeDecodeError:
                text = ""
            try:
                records, custom = ru.parse_backup_data(text, CATALOG)
            except ru.ValidationError as e:
                st.error(f"복원할 수 없습니다. 기존 기록은 그대로 유지됩니다. ({e})")
            else:
                if ss.records or ss.custom_ex:
                    ss.pending_restore = (records, custom)
                    st.rerun()
                else:
                    apply_records(records, "백업 파일로 기록을 복원했습니다.", custom)
                    ss.uploader_n += 1
                    st.rerun()
    if ss.pending_restore is not None:
        new_records, new_custom = ss.pending_restore
        extra = f"(AI 생성 운동 {len(new_custom)}개 포함)" if new_custom else ""
        st.warning(f"기존 {len(ss.records)}세트와 추가한 AI 운동 {len(ss.custom_ex)}개를 "
                   f"백업의 {len(new_records)}세트{extra}로 교체할까요?")
        c1, c2 = st.columns(2)
        c1.button("교체 확인", type="primary", on_click=do_restore)
        c2.button("취소", key="cancel_restore", on_click=cancel_restore)

    st.markdown("#### 데모 기록")
    st.caption("실제 사용자 정보가 없는 가상의 운동 기록입니다.")
    st.button("데모 기록 불러오기", on_click=ask_demo)
    if ss.pending_demo:
        st.warning("기존 기록이 모두 가상 데모 기록으로 교체됩니다. 계속할까요?")
        c1, c2 = st.columns(2)
        c1.button("데모로 교체", type="primary", on_click=load_demo)
        c2.button("취소", key="cancel_demo", on_click=cancel_demo)

    st.markdown("#### 전체 기록 초기화")
    st.checkbox("모든 기록을 삭제하는 것에 동의합니다", key="confirm_reset")
    st.button("전체 초기화", disabled=not (ss.confirm_reset and ss.records), on_click=do_reset)


def ai_form():
    ss = st.session_state
    part = st.radio("부위", PARTS, horizontal=True, key="ai_part")
    equipment = st.selectbox("장비", ai.EQUIPMENT_OPTIONS, key="ai_equipment")
    level = st.radio("난이도", ai.LEVEL_OPTIONS, horizontal=True, key="ai_level")
    count = st.slider("운동 개수", ai.MIN_COUNT, ai.MAX_COUNT, 4, key="ai_count")
    with st.expander("내 신체 정보로 권장 운동량 받기 (선택)"):
        st.caption("키·현재 몸무게·목표 몸무게를 모두 입력하면, 위 조건과 결합해 운동별 권장 세트·횟수·시작 중량과 "
                   "하루 운동량을 참고용으로 제안합니다. 모두 비우면 운동 목록만 추천합니다.")
        c1, c2, c3 = st.columns(3)
        height = c1.number_input("키(cm)", min_value=0.0, max_value=500.0, step=1.0, value=None,
                                 key="ai_height", format="%g")
        weight = c2.number_input("현재 몸무게(kg)", min_value=0.0, max_value=500.0, step=0.5, value=None,
                                 key="ai_weight", format="%g")
        goal = c3.number_input("목표 몸무게(kg)", min_value=0.0, max_value=500.0, step=0.5, value=None,
                               key="ai_goal", format="%g")
        st.caption("입력한 신체 정보는 이번 추천 요청에만 AI로 전송되며 저장·백업되지 않습니다(새로고침하면 사라집니다). "
                   "결과는 개인 처방이 아닌 참고용 초기값이며 의학적 조언이 아닙니다.")
    st.caption("추가 요청 예시 (누르면 아래 입력칸에 채워집니다)")
    for row in (ai.EXTRA_EXAMPLES[:2], ai.EXTRA_EXAMPLES[2:]):
        for col, text in zip(st.columns(2), row):
            col.button(text, key=f"ex_{ai.EXTRA_EXAMPLES.index(text)}", on_click=set_extra, args=(text,),
                       use_container_width=True)
    extra = st.text_area(f"추가 요청 (선택, {ai.MAX_EXTRA_LEN}자 이내)", max_chars=ai.MAX_EXTRA_LEN,
                         key="ai_extra", placeholder="예: 집에서 할 수 있는 운동 위주로")
    st.caption("이름·회사명 같은 개인정보나 사내 정보는 입력하지 마세요. "
               "AI에는 위 조건과 해당 부위의 기본 운동명(신체 정보를 입력하면 그 값도)만 보내며, "
               "저장한 기록·중량·메모는 보내지 않습니다.")
    if ai.mentions_medical(extra):
        st.info("의료·부상·통증 관련 내용이 있어 일반적인 운동 정보로만 답하도록 요청합니다. "
                "통증이나 부상이 있다면 전문가와 상담하세요.")
    left = max(ai.MAX_GENERATIONS_PER_SESSION - ss.ai_generations, 0)
    if st.button("AI 운동 추천 받기", type="primary", disabled=left == 0):
        with st.status("AI가 운동을 추천하는 중입니다…", expanded=True) as box:
            run_generation(part, equipment, level, count, extra, height, weight, goal, progress=box.markdown)
        st.rerun()
    _, model = ai_client.load_settings()
    st.caption(f"이번 세션에서 남은 추천 횟수 {left}회 · 모델 {model}")


def ai_trace_section():
    trace = st.session_state.ai_trace
    if not trace:
        return
    ok = all(done for done, _, _ in trace)
    label = "최근 생성 과정 · 완료" if ok else "최근 생성 과정 · 중단"
    with st.status(label, state="complete" if ok else "error", expanded=True):
        for n, (done, title, detail) in enumerate(trace, start=1):
            st.markdown(f"{'✅' if done else '❌'} **{n}. {title}**  \n{detail}")


def my_ai_section():
    ss = st.session_state
    st.markdown("#### 내 운동 목록 (AI 생성 운동 즐겨찾기)")
    mine = [e for e in ss.custom_ex.values() if e["saved"]]
    if not mine:
        st.caption("기록하기에서 '내 운동 목록에 저장'한 AI 운동이 여기에 모입니다.")
        return
    st.caption(f"{len(mine)}/{ru.MAX_SAVED_CUSTOM}개 · 기록이 남아 있는 운동은 삭제할 수 없고, "
               "기본 운동은 여기에 나오지 않습니다.")
    for ex in mine:
        n = ru.count_records_for(ss.records, ex["id"])
        with st.container(border=True):
            st.markdown(f"**{ex['name']}** · {ex['part']} · {ex['equipment']}  \n`AI 생성` 기록 {n}세트")
            c1, c2 = st.columns(2)
            if ex["id"] in ss.rec_ids:
                c1.caption("✔ 기록하기 목록에 있음")
            else:
                c1.button("기록하기에 추가", key=f"myadd_{ex['id']}", on_click=add_rec, args=(ex["id"],))
            c2.button("목록에서 삭제", key=f"mydel_{ex['id']}", on_click=remove_ai, args=(ex["id"],))


def tab_ai():
    st.subheader("AI 운동 추천")
    st.warning(ai.NOTICE)
    show_flash("ai")
    reason = ai_client.unavailable_reason()
    if reason:
        st.info(f"AI 운동 추천 기능 사용 불가: {reason} '기록하기' 탭에서 기본 운동 목록으로 기록할 수 있습니다.")
    else:
        st.caption("부위·장비·난이도·개수를 고르면 AI가 운동을 추천하고, 추천 운동이 '기록하기' 탭에 등록됩니다.")
        ai_form()
        ai_trace_section()
    my_ai_section()


def main():
    init_state()
    ss = st.session_state
    if ss.goto_tab:  # 탭을 그리기 전에 이동할 탭을 정한다
        ss["main_tab"] = ss.goto_tab
        ss.goto_tab = None
    st.title("🏋️ 오늘의 운동기록")
    st.caption("AI가 추천한 운동을 기록하고, 저장한 세트를 날짜별로 확인하세요.")
    st.info("기록은 현재 세션에서 관리됩니다. 세션이 종료되거나 초기화되면 사라질 수 있으므로 "
            "사용 후 JSON 백업 파일을 저장해 주세요.")
    t1, t2, t3, t4 = st.tabs([TAB_AI, TAB_REC, TAB_VIEW, TAB_BACKUP], key="main_tab", on_change="rerun")
    with t1:
        tab_ai()
    with t2:
        tab_record()
    with t3:
        tab_view()
    with t4:
        tab_backup()
    st.divider()
    st.caption("참고 영상과 운동 목록은 일반적인 정보입니다. 권장 세트·횟수·시작 중량은 개인 처방이 아닌 참고용 초기값이며 의학적 조언이 아닙니다.")


main()
