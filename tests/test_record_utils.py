import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import record_utils as ru  # noqa: E402

PARTS, CATALOG = ru.load_catalog(ROOT / "data" / "exercises.json")
EX = "bench_press"


def rec(day="2026-10-27", ex=EX, w=40, reps=10, memo="", rid=None):
    return ru.make_record(day, ex, w, reps, memo, CATALOG, rid)


def test_catalog_has_four_parts_with_at_least_3_exercises():
    assert PARTS == ["어깨", "가슴", "등", "하체"]
    for p in PARTS:
        assert len(ru.exercises_by_part(CATALOG, p)) >= 3


def test_add_valid_record_and_zero_weight():
    records = ru.add_records([], [rec(), rec(w=0)])
    assert len(records) == 2
    assert records[1]["weight_kg"] == 0.0
    assert len({r["record_id"] for r in records}) == 2


def test_add_does_not_mutate_input():
    base = [rec()]
    ru.add_records(base, [rec()])
    assert len(base) == 1


@pytest.mark.parametrize("w", [-1, -0.5, float("nan"), float("inf"), "40", None, True])
def test_bad_weight_rejected(w):
    with pytest.raises(ru.ValidationError):
        rec(w=w)


@pytest.mark.parametrize("reps", [0, -3, 2.5, "10", None, True, float("nan")])
def test_bad_reps_rejected(reps):
    with pytest.raises(ru.ValidationError):
        rec(reps=reps)


@pytest.mark.parametrize("day", ["2026-13-01", "2026-02-30", "20261027", "2026/10/27", "", None, "2026-1-5"])
def test_bad_date_rejected(day):
    with pytest.raises(ru.ValidationError):
        rec(day=day)


def test_unknown_exercise_rejected():
    with pytest.raises(ru.ValidationError):
        rec(ex="no_such_exercise")


def test_update_and_delete_target_only_that_record():
    a, b, c = rec(rid="a"), rec(rid="b"), rec(rid="c")
    records = [a, b, c]
    updated = ru.update_record(records, "b", 50, 8, "메모", CATALOG)
    assert [r["record_id"] for r in updated] == ["a", "b", "c"]
    assert updated[1]["weight_kg"] == 50 and updated[1]["reps"] == 8
    assert updated[0] == a and updated[2] == c
    assert records[1] == b  # 원본 불변

    remaining = ru.delete_record(updated, "b")
    assert [r["record_id"] for r in remaining] == ["a", "c"]


def test_update_rejects_invalid_and_keeps_data():
    records = [rec(rid="a")]
    with pytest.raises(ru.ValidationError):
        ru.update_record(records, "a", -5, 8, "", CATALOG)
    assert records[0]["weight_kg"] == 40.0
    with pytest.raises(ru.ValidationError):
        ru.update_record(records, "zzz", 10, 8, "", CATALOG)


def test_delete_missing_record_raises():
    with pytest.raises(ru.ValidationError):
        ru.delete_record([rec(rid="a")], "nope")


def test_duplicate_record_id_rejected_on_add():
    with pytest.raises(ru.ValidationError):
        ru.add_records([rec(rid="a")], [rec(rid="a")])


def test_group_by_exercise_keeps_order():
    day = [rec(ex="bench_press", rid="1"), rec(ex="db_press", rid="2"), rec(ex="bench_press", rid="3")]
    groups = ru.group_by_exercise(day)
    assert [r["record_id"] for r in groups["bench_press"]] == ["1", "3"]


def test_export_then_restore_roundtrip():
    records = ru.add_records([], [rec(memo="한글 메모"), rec(day="2026-10-28", ex="squat", w=60.5, reps=5)])
    restored, err = ru.try_restore([], ru.export_json(records), CATALOG)
    assert err is None
    assert restored == records


def test_demo_file_is_valid_and_flagged():
    text = (ROOT / "data" / "demo_records.json").read_text(encoding="utf-8")
    records = ru.parse_backup(text, CATALOG)
    assert records and ru.is_demo(records)
    assert not ru.is_demo([rec()])


BAD_FILES = {
    "not json": "{broken",
    "wrong app": json.dumps({"app": "other", "version": 1, "records": []}),
    "wrong version": json.dumps({"app": ru.APP_NAME, "version": 99, "records": []}),
    "no records": json.dumps({"app": ru.APP_NAME, "version": 1}),
    "list root": "[]",
    "NaN weight": '{"app":"%s","version":1,"records":[{"record_id":"x","date":"2026-10-27","exercise_id":"bench_press","weight_kg":NaN,"reps":5}]}' % ru.APP_NAME,
    "bad exercise": json.dumps({"app": ru.APP_NAME, "version": 1, "records": [
        {"record_id": "x", "date": "2026-10-27", "exercise_id": "zzz", "weight_kg": 1, "reps": 1}]}),
    "dup ids": json.dumps({"app": ru.APP_NAME, "version": 1, "records": [
        {"record_id": "x", "date": "2026-10-27", "exercise_id": EX, "weight_kg": 1, "reps": 1},
        {"record_id": "x", "date": "2026-10-27", "exercise_id": EX, "weight_kg": 1, "reps": 1}]}),
    "non-dict item": json.dumps({"app": ru.APP_NAME, "version": 1, "records": [1]}),
    "bool version": json.dumps({"app": ru.APP_NAME, "version": True, "records": []}),
    "custom not list": json.dumps({"app": ru.APP_NAME, "version": 2, "records": [], "custom_exercises": {}}),
}


@pytest.mark.parametrize("name", list(BAD_FILES))
def test_invalid_restore_keeps_existing_records(name):
    current = [rec(rid="keep")]
    result, err = ru.try_restore(current, BAD_FILES[name], CATALOG)
    assert err
    assert result is current
    assert result == [rec(rid="keep")] or result[0]["record_id"] == "keep"


# ---------- 입력 상한 ----------
def test_memo_length_limit():
    assert len(rec(memo="가" * ru.MAX_MEMO_LEN)["memo"]) == ru.MAX_MEMO_LEN
    with pytest.raises(ru.ValidationError):
        rec(memo="가" * (ru.MAX_MEMO_LEN + 1))


def test_add_records_over_limit_rejected():
    full = [{"record_id": str(i)} for i in range(ru.MAX_RECORDS)]
    with pytest.raises(ru.ValidationError):
        ru.add_records(full, [rec()])


# ---------- AI 생성 운동 ----------
AI_ID = "ai-0123456789ab"


def ai_ex(ex_id=AI_ID, name="케틀벨 스윙", part="하체", equipment="덤벨", saved=False, **kw):
    item = {
        "id": ex_id, "part": part, "name": name, "equipment": equipment, "level": "초보",
        "description": "엉덩이를 뒤로 빼며 덤벨을 앞으로 흔들어 올린다.",
        "weight_basis": ru.WEIGHT_BASIS_BY_EQUIPMENT.get(equipment, ru.WEIGHT_BASES[0]),
        "search_keyword": f"{name} 자세", "caution": "허리를 둥글게 말지 않는다.", "saved": saved,
    }
    item.update(kw)
    return item


def test_ai_exercise_is_recordable_only_with_merged_catalog():
    custom = ru.add_custom({}, ai_ex(), CATALOG)
    assert custom[AI_ID]["source"] == "ai" and "video" not in custom[AI_ID]
    r = ru.make_record("2026-11-06", AI_ID, 10, 12, "", {**CATALOG, **custom})
    assert r["exercise_id"] == AI_ID
    with pytest.raises(ru.ValidationError):
        ru.make_record("2026-11-06", AI_ID, 10, 12, "", CATALOG)


def test_add_custom_rejects_same_name_as_base_catalog():
    with pytest.raises(ru.ValidationError):
        ru.add_custom({}, ai_ex(name="벤치 프레스", part="가슴", equipment="바벨"), CATALOG)


def test_add_custom_save_upgrade_and_saved_limit():
    custom = ru.add_custom({}, ai_ex(), CATALOG)
    with pytest.raises(ru.ValidationError):  # 이미 추가됨
        ru.add_custom(custom, ai_ex(), CATALOG)
    saved = ru.add_custom(custom, ai_ex(), CATALOG, saved=True)
    assert saved[AI_ID]["saved"] is True and custom[AI_ID]["saved"] is False  # 원본 불변

    full = {}
    for i in range(ru.MAX_SAVED_CUSTOM):
        full = ru.add_custom(full, ai_ex(f"ai-{i:012x}", f"운동{i}"), CATALOG, saved=True)
    with pytest.raises(ru.ValidationError):
        ru.add_custom(full, ai_ex("ai-ffffffffffff", "하나 더"), CATALOG, saved=True)


@pytest.mark.parametrize("bad", [
    {"id": "bench_press"}, {"id": "ai-XYZ"}, {"part": "팔"}, {"equipment": "밴드"},
    {"level": "고급"}, {"weight_basis": "1RM의 70%"}, {"name": ""}, {"name": "가" * 41},
    {"description": "자세는 https://youtu.be/abc 참고"}, {"search_keyword": "www.youtube.com/watch?v=x"},
    {"caution": "example.com 참고"}, {"saved": "yes"},
])
def test_invalid_ai_exercise_rejected(bad):
    with pytest.raises(ru.ValidationError):
        ru.validate_custom_exercise(ai_ex(**bad), PARTS)


def test_remove_custom_blocked_while_records_exist():
    custom = ru.add_custom({}, ai_ex(), CATALOG)
    records = [ru.make_record("2026-11-06", AI_ID, 10, 12, "", {**CATALOG, **custom})]
    with pytest.raises(ru.ValidationError):
        ru.remove_custom(custom, AI_ID, records)
    assert ru.remove_custom(custom, AI_ID, []) == {}
    assert AI_ID in custom


def test_prune_keeps_saved_or_used_only():
    custom = ru.add_custom({}, ai_ex("ai-00000000000a", "운동A"), CATALOG)
    custom = ru.add_custom(custom, ai_ex("ai-00000000000b", "운동B"), CATALOG, saved=True)
    custom = ru.add_custom(custom, ai_ex("ai-00000000000c", "운동C"), CATALOG)
    records = [ru.make_record("2026-11-06", "ai-00000000000c", 0, 10, "", {**CATALOG, **custom})]
    assert set(ru.prune_custom(custom, records)) == {"ai-00000000000b", "ai-00000000000c"}


def test_backup_v2_roundtrip_with_ai_exercises():
    custom = ru.add_custom({}, ai_ex("ai-00000000000a", "운동A"), CATALOG)
    custom = ru.add_custom(custom, ai_ex("ai-00000000000b", "운동B"), CATALOG, saved=True)
    custom = ru.add_custom(custom, ai_ex("ai-00000000000c", "운동C"), CATALOG)  # 미저장·미사용 → 제외
    used = ru.make_record("2026-11-06", "ai-00000000000a", 8, 10, "", {**CATALOG, **custom})
    records = ru.add_records([], [rec(), used])
    text = ru.export_json(records, custom=custom)
    assert json.loads(text)["version"] == 2
    restored, restored_custom = ru.parse_backup_data(text, CATALOG)
    assert restored == records
    assert set(restored_custom) == {"ai-00000000000a", "ai-00000000000b"}
    assert restored_custom["ai-00000000000b"]["saved"] is True


def test_backup_v1_still_restores():
    text = json.dumps({"app": ru.APP_NAME, "version": 1, "records": [
        {"record_id": "x", "date": "2026-10-27", "exercise_id": EX, "weight_kg": 1, "reps": 1}]})
    records, custom = ru.parse_backup_data(text, CATALOG)
    assert len(records) == 1 and custom == {}


@pytest.mark.parametrize("custom_list, ex_id", [
    ([], AI_ID),                                          # 정의 없는 AI 운동 기록
    ([ai_ex(description="https://example.com 참고")], AI_ID),  # 링크 포함
    ([ai_ex(), ai_ex()], AI_ID),                          # 중복 ID
])
def test_backup_with_bad_ai_exercises_rejected(custom_list, ex_id):
    text = json.dumps({"app": ru.APP_NAME, "version": 2, "custom_exercises": custom_list, "records": [
        {"record_id": "x", "date": "2026-10-27", "exercise_id": ex_id, "weight_kg": 1, "reps": 1}]},
        ensure_ascii=False)
    current = [rec(rid="keep")]
    result, err = ru.try_restore(current, text, CATALOG)
    assert err and result is current


def test_prune_keeps_items_in_current_recommendation():
    custom = ru.add_custom({}, ai_ex("ai-00000000000a", "운동A"), CATALOG)
    custom = ru.add_custom(custom, ai_ex("ai-00000000000b", "운동B"), CATALOG)
    assert ru.prune_custom(custom, []) == {}
    assert set(ru.prune_custom(custom, [], keep=["ai-00000000000b", "squat"])) == {"ai-00000000000b"}
