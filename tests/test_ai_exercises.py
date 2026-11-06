"""ai_exercises.py 테스트 (네트워크 없음)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ai_exercises as ai  # noqa: E402
import record_utils as ru  # noqa: E402

PARTS, CATALOG = ru.load_catalog(ROOT / "data" / "exercises.json")


def req(**kw):
    base = dict(part="가슴", equipment="덤벨", level="초보", count=3, extra="")
    base.update(kw)
    return ai.validate_request(base["part"], base["equipment"], base["level"], base["count"], base["extra"], PARTS)


def item(name="덤벨 풀오버", part="가슴", equipment="덤벨", level="초보", **kw):
    d = {
        "name": name, "part": part, "equipment": equipment, "level": level,
        "description": "벤치에 누워 덤벨을 머리 뒤로 내렸다가 가슴 위로 올린다.",
        "weight_basis": ru.WEIGHT_BASIS_BY_EQUIPMENT[equipment],
        "search_keyword": f"{name} 자세", "caution": "어깨에 무리가 가지 않는 범위에서 움직인다.",
    }
    d.update(kw)
    return d


def response(*items):
    return json.dumps({"exercises": list(items), "disclaimer": "일반적인 운동 정보이며 개인 처방이 아닙니다."},
                      ensure_ascii=False)


# ---------- 요청 검증 ----------
@pytest.mark.parametrize("kw", [
    dict(part="팔"), dict(equipment="밴드"), dict(level="고급"), dict(count=2), dict(count=7),
    dict(count=True), dict(count="4"), dict(extra="가" * 301), dict(extra=123),
])
def test_bad_request_rejected(kw):
    with pytest.raises(ru.ValidationError):
        req(**kw)


def test_request_normalizes_extra():
    assert req(extra="  집에서\n할 수 있는\t운동  ")["extra"] == "집에서 할 수 있는 운동"
    assert req(extra=None)["extra"] == ""
    assert req(extra="가" * 300)["extra"] == "가" * 300
    assert req(equipment=ai.ANY, level=ai.ANY, count=6)["count"] == 6


def test_mentions_medical():
    assert ai.mentions_medical("허리 통증이 있어요")
    assert ai.mentions_medical("어깨 재활용 운동")
    assert not ai.mentions_medical("집에서 할 수 있는 운동")
    assert not ai.mentions_medical(None)


# ---------- 프롬프트·스키마 ----------
def test_prompt_contains_only_request_conditions():
    instructions, user_input = ai.build_prompt(req(extra="집에서"))
    assert user_input.splitlines() == [
        "부위: 가슴", "장비: 덤벨", "난이도: 초보", "운동 개수: 3", "추가 요청(참고용): <<<집에서>>>",
    ]
    for rule in ("URL", "타임스탬프", "개인 맞춤 처방", "진단", "disclaimer"):
        assert rule in instructions
    _, with_exclude = ai.build_prompt(req(), ["벤치프레스", "덤벨 프레스"])
    assert with_exclude.splitlines()[-1] == "이미 목록에 있는 운동(제외): 벤치프레스, 덤벨 프레스"


def test_schema_is_strict_and_has_no_link_fields():
    schema = ai.response_schema(req(equipment=ai.ANY, level=ai.ANY))
    item_schema = schema["properties"]["exercises"]["items"]
    assert schema["additionalProperties"] is False and item_schema["additionalProperties"] is False
    assert set(item_schema["required"]) == set(item_schema["properties"])
    assert set(schema["required"]) == set(schema["properties"])
    for banned in ("url", "video_id", "video", "duration_sec", "start_sec", "link", "timestamp"):
        assert banned not in item_schema["properties"]
    assert item_schema["properties"]["part"]["enum"] == ["가슴"]
    assert item_schema["properties"]["equipment"]["enum"] == list(ru.EQUIPMENTS)
    assert ai.response_schema(req())["properties"]["exercises"]["items"]["properties"]["equipment"]["enum"] == ["덤벨"]


# ---------- 응답 정리 ----------
def test_parse_valid_response():
    exs, stats = ai.parse_response(
        response(item("덤벨 풀오버"), item("덤벨 스퀴즈 프레스"), item("디클라인 덤벨 프레스")), req(), CATALOG)
    assert [e["name"] for e in exs] == ["덤벨 풀오버", "덤벨 스퀴즈 프레스", "디클라인 덤벨 프레스"]
    assert stats["received"] == 3 and stats["accepted"] == 3 and stats["skipped"] == 0
    assert len({e["id"] for e in exs}) == 3
    for e in exs:
        assert e["id"].startswith("ai-") and e["source"] == "ai" and e["saved"] is False
        assert "video" not in e


def test_parse_strips_links_and_prescriptions():
    exs, stats = ai.parse_response(response(item(
        description="벤치에 누워 덤벨을 내렸다 올린다. 10~12회씩 3세트 반복한다. 영상: https://youtu.be/abc123",
        caution="www.example.com 참고. 20kg부터 시작한다.",
        search_keyword="덤벨 풀오버 https://www.youtube.com/watch?v=abc",
    ), item("덤벨 스퀴즈 프레스")), req(), CATALOG)
    assert stats["link_removed"] == 1 and stats["prescription_removed"] == 1
    e = exs[0]
    text = " ".join([e["description"], e["caution"], e["search_keyword"]])
    assert not ru.URL_RE.search(text) and "youtu" not in text
    assert not ai._PRESCRIPTION_RE.search(text)
    assert e["description"].startswith("벤치에 누워 덤벨을 내렸다 올린다.")
    assert e["search_keyword"] == "덤벨 풀오버"


def test_parse_skips_mismatches_duplicates_and_trims_to_count():
    exs, stats = ai.parse_response(response(
        item("벤치 프레스"),                        # 기본 목록 '벤치프레스'와 이름 중복
        item("덤벨 풀오버"),
        item("덤벨 풀 오버"),                       # 응답 안에서 중복
        item("바벨 풀오버", equipment="바벨"),       # 장비 조건 불일치
        item("케이블 풀다운", part="등"),            # 부위 불일치
        item("덤벨 프레스 응용", level="중급"),       # 난이도 불일치
        item("가" * 41),                            # 운동명 길이 초과
        item("덤벨 스퀴즈 프레스"),
        item("디클라인 덤벨 프레스"),
        item("남는 운동"),                          # 요청 개수 초과
    ), req(count=3), CATALOG)
    assert [e["name"] for e in exs] == ["덤벨 풀오버", "덤벨 스퀴즈 프레스", "디클라인 덤벨 프레스"]
    assert {k: stats[k] for k in ("received", "accepted", "duplicate", "mismatch", "invalid", "over", "skipped")} == {
        "received": 10, "accepted": 3, "duplicate": 2, "mismatch": 3, "invalid": 1, "over": 1, "skipped": 6}
    summary = ai.describe_stats(stats)
    assert summary.startswith("받은 10개 → 채택 3개")
    assert "기존 목록과 이름 중복 2" in summary and "요청 개수 초과 1" in summary and "정리: 없음" in summary


def test_parse_fixes_weight_basis_to_equipment():
    exs, _ = ai.parse_response(response(item(weight_basis="봉을 포함한 전체 중량")), req(), CATALOG)
    assert exs[0]["weight_basis"] == "한 손 덤벨의 중량"


def test_parse_any_conditions_accept_each_value():
    exs, _ = ai.parse_response(response(
        item("덤벨 풀오버"),
        item("머신 체스트 프레스", equipment="머신", level="중급"),
        item("니 푸쉬업", equipment="맨몸"),
    ), req(equipment=ai.ANY, level=ai.ANY), CATALOG)
    assert [e["equipment"] for e in exs] == ["덤벨", "머신", "맨몸"]
    assert exs[2]["weight_basis"] == ru.WEIGHT_BASIS_BY_EQUIPMENT["맨몸"]


def test_parse_avoids_names_already_added_in_session():
    first, _ = ai.parse_response(response(item("덤벨 풀오버")), req(), CATALOG)
    custom = ru.add_custom({}, first[0], CATALOG)
    exs, stats = ai.parse_response(response(item("덤벨 풀오버"), item("디클라인 덤벨 프레스")),
                                   req(), {**CATALOG, **custom})
    assert [e["name"] for e in exs] == ["디클라인 덤벨 프레스"] and stats["duplicate"] == 1


def test_empty_keyword_gets_default():
    exs, _ = ai.parse_response(response(item(search_keyword="https://youtu.be/x")), req(), CATALOG)
    assert exs[0]["search_keyword"] == "덤벨 풀오버 자세"


@pytest.mark.parametrize("text", [
    "not json", "", None, "[]", json.dumps({"exercises": "x"}), json.dumps({"items": []}),
])
def test_parse_bad_format_rejected(text):
    with pytest.raises(ru.ValidationError):
        ai.parse_response(text, req(), CATALOG)


def test_parse_with_no_valid_items_rejected():
    with pytest.raises(ru.ValidationError):
        ai.parse_response(response(item("벤치프레스"), item("x", part="등")), req(), CATALOG)
    with pytest.raises(ru.ValidationError):  # 설명이 전부 수치 처방이면 제외
        ai.parse_response(response(item(description="12회씩 4세트 한다.")), req(), CATALOG)


def test_describe_stats_when_nothing_excluded():
    _, stats = ai.parse_response(response(item("덤벨 풀오버")), req(), CATALOG)
    assert ai.describe_stats(stats) == "받은 1개 → 채택 1개 · 제외: 없음 · 정리: 없음"
