"""ai_exercises.py 테스트 (네트워크 없음)."""
import json
import sys
from pathlib import Path
from urllib.parse import unquote

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
    for rule in ("URL", "타임스탬프", "개인 맞춤 처방", "진단", "disclaimer", "활용 가능 운동"):
        assert rule in instructions
    _, with_names = ai.build_prompt(req(), ["벤치프레스", "덤벨 프레스"])
    assert with_names.splitlines()[-1] == "활용 가능 운동: 벤치프레스, 덤벨 프레스"


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


# ---------- 한조바디 검색 링크 ----------
def test_hanjobody_search_url_is_encoded_search_page():
    url = ai.hanjobody_search_url("글루트 브리지 자세 a&b=c")
    assert url.startswith("https://www.youtube.com/@hanjobody/search?query=")
    query = url.split("query=", 1)[1]
    assert " " not in query and "&" not in query and "=" not in query
    assert unquote(query) == "글루트 브리지 자세 a&b=c"
    assert "watch?v=" not in url


# ---------- 응답 정리: 새 AI 운동 ----------
def test_parse_new_exercises():
    ids, new, stats = ai.parse_response(
        response(item("덤벨 풀오버"), item("덤벨 스퀴즈 프레스"), item("디클라인 덤벨 프레스")), req(), CATALOG)
    assert [new[i]["name"] for i in ids] == ["덤벨 풀오버", "덤벨 스퀴즈 프레스", "디클라인 덤벨 프레스"]
    assert stats["received"] == 3 and stats["accepted"] == 3 and stats["new"] == 3 and stats["base"] == 0
    for ex in new.values():
        assert ex["id"].startswith("ai-") and ex["source"] == "ai" and ex["saved"] is False
        assert "video" not in ex


def test_parse_strips_links_and_prescriptions():
    ids, new, stats = ai.parse_response(response(item(
        description="벤치에 누워 덤벨을 내렸다 올린다. 10~12회씩 3세트 반복한다. 영상: https://youtu.be/abc123",
        caution="www.example.com 참고. 20kg부터 시작한다.",
        search_keyword="덤벨 풀오버 https://www.youtube.com/watch?v=abc",
    )), req(), CATALOG)
    assert stats["link_removed"] == 1 and stats["prescription_removed"] == 1
    e = new[ids[0]]
    text = " ".join([e["description"], e["caution"], e["search_keyword"]])
    assert not ru.URL_RE.search(text) and "youtu" not in text
    assert not ai._PRESCRIPTION_RE.search(text)
    assert e["description"].startswith("벤치에 누워 덤벨을 내렸다 올린다.")
    assert e["search_keyword"] == "덤벨 풀오버"


def test_parse_fixes_weight_basis_to_equipment():
    ids, new, _ = ai.parse_response(response(item(weight_basis="봉을 포함한 전체 중량")), req(), CATALOG)
    assert new[ids[0]]["weight_basis"] == "한 손 덤벨의 중량"


def test_empty_keyword_gets_default():
    ids, new, _ = ai.parse_response(response(item(search_keyword="https://youtu.be/x")), req(), CATALOG)
    assert new[ids[0]]["search_keyword"] == "덤벨 풀오버 자세"


def test_parse_any_conditions_accept_each_value():
    ids, new, _ = ai.parse_response(response(
        item("덤벨 풀오버"),
        item("머신 체스트 프레스", equipment="머신", level="중급"),
        item("니 푸쉬업", equipment="맨몸"),
    ), req(equipment=ai.ANY, level=ai.ANY), CATALOG)
    assert [new[i]["equipment"] for i in ids] == ["덤벨", "머신", "맨몸"]
    assert new[ids[2]]["weight_basis"] == ru.WEIGHT_BASIS_BY_EQUIPMENT["맨몸"]


# ---------- 응답 정리: 기본 운동 연결·재사용 ----------
def test_parse_links_base_exercise_by_name():
    ids, new, stats = ai.parse_response(response(item("덤벨 프레스"), item("덤벨 풀오버")), req(count=3), CATALOG)
    assert ids[0] == "db_press" and CATALOG["db_press"]["video"]["url"]
    assert new and ids[1] in new
    assert stats["base"] == 1 and stats["new"] == 1


def test_parse_base_name_ignores_spacing():
    ids, new, _ = ai.parse_response(response(item("벤치 프레스", equipment="바벨")),
                                    req(equipment=ai.ANY), CATALOG)
    assert ids == ["bench_press"] and new == {}


def test_parse_base_exercise_of_other_part_is_mismatch():
    with pytest.raises(ru.ValidationError):
        ai.parse_response(response(item("스쿼트", part="가슴", equipment="바벨")), req(equipment=ai.ANY), CATALOG)


def test_parse_reuses_existing_ai_exercise():
    ids, new, _ = ai.parse_response(response(item("덤벨 풀오버")), req(), CATALOG)
    custom = ru.add_custom({}, new[ids[0]], CATALOG)
    ids2, new2, stats = ai.parse_response(response(item("덤벨 풀오버"), item("디클라인 덤벨 프레스")),
                                          req(), CATALOG, custom)
    assert ids2[0] == ids[0] and len(new2) == 1 and stats["reuse"] == 1 and stats["new"] == 1


def test_parse_mixed_response_statistics_and_count_limit():
    ids, new, stats = ai.parse_response(response(
        item("벤치 프레스", equipment="바벨"),         # 기본 운동 연결
        item("덤벨 풀오버"),                          # 새 AI
        item("덤벨 풀 오버"),                         # 응답 안 중복
        item("케이블 풀다운", part="등"),              # 부위 불일치
        item("덤벨 프레스 응용", level="중급"),         # 난이도 불일치
        item("가" * 41),                              # 운동명 길이 초과
        item("덤벨 스퀴즈 프레스"),                    # 새 AI
        item("덤벨 프레스"),                          # 기본 운동 연결
        item("남는 운동"),                            # 요청 개수 초과
    ), req(equipment=ai.ANY, count=4), CATALOG)
    assert ids[0] == "bench_press" and ids[-1] == "db_press" and len(ids) == 4
    assert {k: stats[k] for k in ("received", "accepted", "base", "new", "reuse", "duplicate",
                                  "mismatch", "invalid", "over", "skipped")} == {
        "received": 9, "accepted": 4, "base": 2, "new": 2, "reuse": 0, "duplicate": 1,
        "mismatch": 2, "invalid": 1, "over": 1, "skipped": 4}
    summary = ai.describe_stats(stats)
    assert summary.startswith("받은 9개 → 채택 4개(기본 운동 2 · 새 AI 운동 2 · 재사용 0)")
    assert "이름 중복 1" in summary and "요청 개수 초과 1" in summary and "정리: 없음" in summary


def test_describe_stats_when_nothing_excluded():
    _, _, stats = ai.parse_response(response(item("덤벨 풀오버")), req(), CATALOG)
    assert ai.describe_stats(stats) == (
        "받은 1개 → 채택 1개(기본 운동 0 · 새 AI 운동 1 · 재사용 0) · 제외: 없음 · 정리: 없음")


@pytest.mark.parametrize("text", [
    "not json", "", None, "[]", json.dumps({"exercises": "x"}), json.dumps({"items": []}),
])
def test_parse_bad_format_rejected(text):
    with pytest.raises(ru.ValidationError):
        ai.parse_response(text, req(), CATALOG)


def test_parse_with_no_valid_items_rejected():
    with pytest.raises(ru.ValidationError):
        ai.parse_response(response(item("x", part="등")), req(), CATALOG)
    with pytest.raises(ru.ValidationError):  # 설명이 전부 수치 처방이면 제외
        ai.parse_response(response(item(description="12회씩 4세트 한다.")), req(), CATALOG)


def test_extra_examples_are_valid_requests():
    assert len(ai.EXTRA_EXAMPLES) == 4 and len(set(ai.EXTRA_EXAMPLES)) == 4
    for text in ai.EXTRA_EXAMPLES:
        assert not ai.mentions_medical(text) and not ru.URL_RE.search(text)
        assert req(extra=text)["extra"] == text
