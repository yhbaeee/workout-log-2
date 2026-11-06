"""운동 기록 처리 로직 (Streamlit 비의존).

기록은 dict 리스트이며 필드는 record_id, date, exercise_id, weight_kg, reps, memo.
AI 생성 운동(사용자 운동)은 {exercise_id: 운동 dict} 사전이며, 기본 카탈로그와 합쳐 검증에 쓴다.
모든 함수는 입력 리스트·사전을 변경하지 않고 새 객체를 반환한다.
"""
import json
import math
import re
import uuid
from datetime import datetime
from pathlib import Path

APP_NAME = "오늘의 운동기록"
BACKUP_VERSION = 2               # 2: AI 생성 운동 정의(custom_exercises) 포함
SUPPORTED_BACKUP_VERSIONS = (1, 2)
MAX_WEIGHT_KG = 1000
MAX_REPS = 1000
MAX_MEMO_LEN = 200
MAX_RECORDS = 20000

# AI 생성 운동
EQUIPMENTS = ("맨몸", "덤벨", "바벨", "머신", "케이블")
LEVELS = ("초보", "중급")
WEIGHT_BASIS_BY_EQUIPMENT = {
    "덤벨": "한 손 덤벨의 중량",
    "바벨": "봉을 포함한 전체 중량",
    "머신": "해당 기구에 표시된 중량",
    "케이블": "해당 기구에 표시된 중량",
    "맨몸": "맨몸 운동은 0kg 입력 가능(추가 중량이 있으면 그 중량)",
}
WEIGHT_BASES = tuple(dict.fromkeys(WEIGHT_BASIS_BY_EQUIPMENT.values()))
MAX_NAME_LEN = 40
MAX_DESC_LEN = 200
MAX_KEYWORD_LEN = 60
MAX_CAUTION_LEN = 200
MAX_SAVED_CUSTOM = 20            # '내 운동 목록' 최대 개수
MAX_CUSTOM = 100                 # 세션에 둘 수 있는 AI 운동 최대 개수

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CUSTOM_ID_RE = re.compile(r"^ai-[0-9a-f]{8,32}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
URL_RE = re.compile(
    r"https?://\S*|www\.\S*|\S*youtu\.be/\S*|\S*youtube\.com\S*"
    r"|\b[\w.-]+\.(?:com|net|org|kr|io|be|ly|co)\b(?:/\S*)?",
    re.IGNORECASE,
)


class ValidationError(ValueError):
    """사용자에게 그대로 보여줘도 되는 검증 오류."""


def load_catalog(path):
    """exercises.json을 읽어 (부위 목록, {exercise_id: 운동 dict})를 반환."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["parts"], {e["id"]: e for e in data["exercises"]}


def exercises_by_part(catalog, part):
    return [e for e in catalog.values() if e["part"] == part]


def validate_date(value):
    if hasattr(value, "isoformat") and not isinstance(value, str):
        value = value.isoformat()
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ValidationError("날짜는 YYYY-MM-DD 형식이어야 합니다.")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValidationError("존재하지 않는 날짜입니다.") from None
    return value


def validate_weight(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("중량은 숫자로 입력해 주세요.")
    value = float(value)
    if not math.isfinite(value):
        raise ValidationError("중량 값이 올바르지 않습니다.")
    if value < 0:
        raise ValidationError("중량은 0 이상이어야 합니다.")
    if value > MAX_WEIGHT_KG:
        raise ValidationError(f"중량은 {MAX_WEIGHT_KG}kg 이하로 입력해 주세요.")
    return value


def validate_reps(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("반복 횟수는 숫자로 입력해 주세요.")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValidationError("반복 횟수는 정수여야 합니다.")
        value = int(value)
    if value < 1:
        raise ValidationError("반복 횟수는 1 이상이어야 합니다.")
    if value > MAX_REPS:
        raise ValidationError(f"반복 횟수는 {MAX_REPS}회 이하로 입력해 주세요.")
    return value


def validate_memo(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError("메모는 문자열이어야 합니다.")
    value = value.strip()
    if len(value) > MAX_MEMO_LEN:
        raise ValidationError(f"메모는 {MAX_MEMO_LEN}자 이하로 입력해 주세요.")
    return value


def make_record(day, exercise_id, weight_kg, reps, memo, catalog, record_id=None):
    """검증을 거친 기록 dict를 만든다. 실패하면 ValidationError."""
    if exercise_id not in catalog:
        raise ValidationError("존재하지 않는 운동입니다.")
    if record_id is not None and (not isinstance(record_id, str) or not record_id):
        raise ValidationError("기록 ID가 올바르지 않습니다.")
    return {
        "record_id": record_id or uuid.uuid4().hex,
        "date": validate_date(day),
        "exercise_id": exercise_id,
        "weight_kg": validate_weight(weight_kg),
        "reps": validate_reps(reps),
        "memo": validate_memo(memo),
    }


def add_records(records, new_records):
    """여러 세트를 한 번에 추가. 하나라도 ID가 겹치면 거부."""
    ids = {r["record_id"] for r in records}
    for r in new_records:
        if r["record_id"] in ids:
            raise ValidationError("이미 저장된 기록입니다.")
        ids.add(r["record_id"])
    if len(records) + len(new_records) > MAX_RECORDS:
        raise ValidationError("저장할 수 있는 기록 수를 넘었습니다.")
    return records + list(new_records)


def update_record(records, record_id, weight_kg, reps, memo, catalog):
    """중량·횟수·메모만 수정한다 (날짜·운동은 유지)."""
    for i, r in enumerate(records):
        if r["record_id"] == record_id:
            updated = make_record(
                r["date"], r["exercise_id"], weight_kg, reps, memo, catalog, record_id
            )
            return records[:i] + [updated] + records[i + 1:]
    raise ValidationError("수정할 기록을 찾을 수 없습니다.")


def delete_record(records, record_id):
    if not any(r["record_id"] == record_id for r in records):
        raise ValidationError("삭제할 기록을 찾을 수 없습니다.")
    return [r for r in records if r["record_id"] != record_id]


def records_on(records, day):
    return [r for r in records if r["date"] == day]


def group_by_exercise(day_records):
    """같은 날짜 기록을 운동별로 묶는다. 세트 번호는 저장 순서(1부터)."""
    groups = {}
    for r in day_records:
        groups.setdefault(r["exercise_id"], []).append(r)
    return groups


def export_json(records, now=None, custom=None):
    """백업 JSON 문자열. AI 운동은 '내 운동 목록'에 있거나 기록에 쓰인 것만 포함한다."""
    now = now or datetime.now()
    payload = {
        "app": APP_NAME,
        "version": BACKUP_VERSION,
        "exported_at": now.isoformat(timespec="seconds"),
        "records": records,
        "custom_exercises": list(prune_custom(custom or {}, records).values()),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _reject_constant(name):
    raise ValidationError("올바르지 않은 숫자 값이 포함되어 있습니다.")


def parse_backup_data(text, catalog):
    """백업 JSON을 검증해 (기록 리스트, AI 운동 사전)을 반환. 실패하면 ValidationError.

    catalog는 기본 운동 카탈로그. 버전 1(AI 운동 없음)과 2를 모두 읽는다.
    """
    try:
        data = json.loads(text, parse_constant=_reject_constant)
    except ValidationError:
        raise
    except (ValueError, RecursionError):
        raise ValidationError("JSON 파일을 읽을 수 없습니다.") from None
    if not isinstance(data, dict) or data.get("app") != APP_NAME:
        raise ValidationError("이 앱의 백업 파일이 아닙니다.")
    version = data.get("version")
    if isinstance(version, bool) or version not in SUPPORTED_BACKUP_VERSIONS:
        raise ValidationError("지원하지 않는 백업 버전입니다.")
    raw = data.get("records")
    if not isinstance(raw, list):
        raise ValidationError("기록 목록이 없습니다.")
    if len(raw) > MAX_RECORDS:
        raise ValidationError("기록 수가 너무 많습니다.")

    custom = {}
    if version >= 2:
        raw_custom = data.get("custom_exercises", [])
        if not isinstance(raw_custom, list):
            raise ValidationError("AI 운동 목록 형식이 올바르지 않습니다.")
        if len(raw_custom) > MAX_CUSTOM:
            raise ValidationError("AI 운동 수가 너무 많습니다.")
        parts = parts_of(catalog)
        for item in raw_custom:
            ex = validate_custom_exercise(item, parts)
            if ex["id"] in custom or ex["id"] in catalog:
                raise ValidationError("중복된 AI 운동 ID가 있습니다.")
            custom[ex["id"]] = ex
        if sum(1 for e in custom.values() if e["saved"]) > MAX_SAVED_CUSTOM:
            raise ValidationError("내 운동 목록의 운동 수가 너무 많습니다.")
    merged = {**catalog, **custom}

    records, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValidationError("기록 형식이 올바르지 않습니다.")
        rec = make_record(
            item.get("date"),
            item.get("exercise_id"),
            item.get("weight_kg"),
            item.get("reps"),
            item.get("memo", ""),
            merged,
            item.get("record_id"),
        )
        if rec["record_id"] in seen:
            raise ValidationError("중복된 기록 ID가 있습니다.")
        seen.add(rec["record_id"])
        records.append(rec)
    return records, custom


def parse_backup(text, catalog):
    """백업 JSON 문자열을 검증해 기록 리스트만 반환. 실패하면 ValidationError."""
    return parse_backup_data(text, catalog)[0]


def try_restore(current, text, catalog):
    """복원 시도. 성공하면 (새 기록, None), 실패하면 (기존 기록, 오류 메시지)."""
    try:
        return parse_backup(text, catalog), None
    except ValidationError as e:
        return current, str(e)


def is_demo(records):
    return any(r["record_id"].startswith("demo-") for r in records)


# ---------- AI 생성 운동 (사용자 운동) ----------
def parts_of(catalog):
    return list(dict.fromkeys(e["part"] for e in catalog.values()))


def clean_text(value, label, max_len, required=True):
    """제어 문자·연속 공백을 정리한 문자열. 링크가 있거나 길이를 넘으면 ValidationError."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{label} 형식이 올바르지 않습니다.")
    value = " ".join(_CTRL_RE.sub(" ", value).split())
    if URL_RE.search(value):
        raise ValidationError(f"{label}에는 링크를 넣을 수 없습니다.")
    if required and not value:
        raise ValidationError(f"{label}이(가) 비어 있습니다.")
    if len(value) > max_len:
        raise ValidationError(f"{label}은(는) {max_len}자 이하여야 합니다.")
    return value


def validate_custom_exercise(item, parts):
    """AI 운동 dict를 검증해 정해진 필드만 담은 새 dict를 반환. 영상 정보는 두지 않는다."""
    if not isinstance(item, dict):
        raise ValidationError("AI 운동 형식이 올바르지 않습니다.")
    ex_id = item.get("id")
    if not isinstance(ex_id, str) or not _CUSTOM_ID_RE.match(ex_id):
        raise ValidationError("AI 운동 ID가 올바르지 않습니다.")
    checks = (("part", parts, "부위"), ("equipment", EQUIPMENTS, "장비"),
              ("level", LEVELS, "난이도"), ("weight_basis", WEIGHT_BASES, "중량 입력 기준"))
    for field, allowed, label in checks:
        if item.get(field) not in allowed:
            raise ValidationError(f"AI 운동의 {label} 값이 올바르지 않습니다.")
    saved = item.get("saved", False)
    if not isinstance(saved, bool):
        raise ValidationError("AI 운동의 저장 상태 값이 올바르지 않습니다.")
    return {
        "id": ex_id,
        "source": "ai",
        "part": item["part"],
        "name": clean_text(item.get("name"), "운동명", MAX_NAME_LEN),
        "equipment": item["equipment"],
        "level": item["level"],
        "description": clean_text(item.get("description"), "운동 설명", MAX_DESC_LEN),
        "weight_basis": item["weight_basis"],
        "search_keyword": clean_text(item.get("search_keyword"), "검색 키워드", MAX_KEYWORD_LEN),
        "caution": clean_text(item.get("caution"), "주의사항", MAX_CAUTION_LEN, required=False),
        "saved": saved,
    }


def normalize_name(name):
    return re.sub(r"[\s\-_·()（）]", "", name).lower()


def find_same_name(catalog, name, exclude_id=None):
    """공백·기호를 무시하고 이름이 같은 운동의 ID. 없으면 None."""
    key = normalize_name(name)
    for ex_id, ex in catalog.items():
        if ex_id != exclude_id and normalize_name(ex["name"]) == key:
            return ex_id
    return None


def is_custom(exercise):
    return exercise.get("source") == "ai"


def _check_saved_limit(custom):
    if sum(1 for e in custom.values() if e["saved"]) >= MAX_SAVED_CUSTOM:
        raise ValidationError(f"내 운동 목록은 최대 {MAX_SAVED_CUSTOM}개까지 저장할 수 있습니다.")


def add_custom(custom, exercise, base_catalog, saved=False):
    """AI 운동을 세션 운동 목록에 추가한다. saved=True면 '내 운동 목록'에 저장.

    이미 추가된 운동에 saved=True를 주면 저장 상태로 바꾼다.
    """
    ex_id = exercise.get("id")
    if ex_id in custom:
        if not saved or custom[ex_id]["saved"]:
            raise ValidationError("이미 추가된 운동입니다.")
        _check_saved_limit(custom)
        return {**custom, ex_id: {**custom[ex_id], "saved": True}}
    item = validate_custom_exercise({**exercise, "saved": saved}, parts_of(base_catalog))
    if ex_id in base_catalog:
        raise ValidationError("AI 운동 ID가 올바르지 않습니다.")
    if find_same_name({**base_catalog, **custom}, item["name"]):
        raise ValidationError(f"'{item['name']}'과(와) 같은 이름의 운동이 이미 목록에 있습니다.")
    if len(custom) >= MAX_CUSTOM:
        raise ValidationError("추가할 수 있는 AI 운동 수를 넘었습니다. 쓰지 않는 운동을 삭제해 주세요.")
    if saved:
        _check_saved_limit(custom)
    return {**custom, ex_id: item}


def count_records_for(records, exercise_id):
    return sum(1 for r in records if r["exercise_id"] == exercise_id)


def remove_custom(custom, exercise_id, records):
    """AI 운동을 목록에서 삭제. 그 운동의 기록이 남아 있으면 거부한다."""
    if exercise_id not in custom:
        raise ValidationError("삭제할 운동을 찾을 수 없습니다.")
    n = count_records_for(records, exercise_id)
    if n:
        raise ValidationError(
            f"이 운동의 기록 {n}세트가 있어 삭제할 수 없습니다. '기록 보기'에서 기록을 먼저 삭제해 주세요."
        )
    return {k: v for k, v in custom.items() if k != exercise_id}


def prune_custom(custom, records, keep=()):
    """'내 운동 목록'에 저장했거나 기록에 쓰였거나 keep(현재 기록하기 목록)에 있는 AI 운동만 남긴다."""
    used = {r["exercise_id"] for r in records} | set(keep)
    return {k: v for k, v in custom.items() if v["saved"] or k in used}
