"""ai_client.py 테스트 (실제 API 호출 없음, 가짜 클라이언트와 임시 .env 사용)."""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ai_client  # noqa: E402

openai = pytest.importorskip("openai")

FAKE_KEY = "sk-test-SECRET123"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)


@pytest.fixture
def env_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text(f"OPENAI_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    return p


class FakeClient:
    def __init__(self, result):
        self.result = result
        self.init_kwargs = None
        self.create_kwargs = None
        self.responses = self

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def fake_factory(result):
    client = FakeClient(result)

    def factory(**kwargs):
        client.init_kwargs = kwargs
        return client
    return client, factory


def fake_response(text='{"exercises": [], "disclaimer": ""}', status="completed", output=()):
    return types.SimpleNamespace(output_text=text, status=status, output=list(output))


def make_error(cls, status=None):
    """키 일부가 담긴 메시지를 가진 예외(원래 메시지가 화면으로 새지 않는지 확인용)."""
    e = cls.__new__(cls)
    Exception.__init__(e, f"Incorrect API key provided: {FAKE_KEY}")
    if status is not None:
        e.status_code = status
    return e


# ---------- .env 로드 ----------
def test_load_settings_reads_key_and_default_model(env_file):
    assert ai_client.load_settings(env_file) == (FAKE_KEY, ai_client.DEFAULT_MODEL)


def test_load_settings_model_override(tmp_path):
    p = tmp_path / ".env"
    p.write_text(f'OPENAI_API_KEY="{FAKE_KEY}"\nOPENAI_MODEL=gpt-6-sol\n', encoding="utf-8")
    assert ai_client.load_settings(p) == (FAKE_KEY, "gpt-6-sol")


@pytest.mark.parametrize("content", ["", "OPENAI_API_KEY=\nOPENAI_MODEL=\n", "OPENAI_API_KEY=   \n", "OTHER=1\n"])
def test_load_settings_empty_key(tmp_path, content):
    p = tmp_path / ".env"
    p.write_text(content, encoding="utf-8")
    assert ai_client.load_settings(p) == (None, ai_client.DEFAULT_MODEL)


def test_env_file_wins_over_process_env(env_file, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-process-env")
    key, _ = ai_client.load_settings(env_file)
    assert key == FAKE_KEY


def test_process_env_is_used_when_no_env_file(tmp_path, monkeypatch):  # Streamlit Secrets가 전달하는 경우
    monkeypatch.setenv("OPENAI_API_KEY", "  sk-from-secrets  ")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-sol")
    assert ai_client.load_settings(tmp_path / "no.env") == ("sk-from-secrets", "gpt-6-sol")
    assert ai_client.unavailable_reason(tmp_path / "no.env") is None


def test_no_key_anywhere_is_unavailable(tmp_path):
    assert ai_client.load_settings(tmp_path / "no.env") == (None, ai_client.DEFAULT_MODEL)
    assert "Secrets" in ai_client.unavailable_reason(tmp_path / "no.env")


def test_available_with_key(env_file):
    assert ai_client.unavailable_reason(env_file) is None


# ---------- 호출 ----------
def test_generate_sends_expected_request(env_file):
    client, factory = fake_factory(fake_response('{"ok": 1}'))
    schema = {"type": "object"}
    text = ai_client.generate_json("지시문", "부위: 가슴", schema, env_path=env_file, client_factory=factory)
    assert text == '{"ok": 1}'
    assert client.init_kwargs == {"api_key": FAKE_KEY, "timeout": ai_client.TIMEOUT_SEC,
                                  "max_retries": ai_client.MAX_RETRIES}
    kw = client.create_kwargs
    assert kw["model"] == ai_client.DEFAULT_MODEL
    assert kw["instructions"] == "지시문" and kw["input"] == "부위: 가슴"
    assert kw["store"] is False
    fmt = kw["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["schema"] is schema


def test_generate_without_key_does_not_call(tmp_path):
    client, factory = fake_factory(fake_response())
    with pytest.raises(ai_client.AIError):
        ai_client.generate_json("i", "u", {}, env_path=tmp_path / "no.env", client_factory=factory)
    assert client.init_kwargs is None


@pytest.mark.parametrize("cls, status, expected", [
    ("AuthenticationError", None, "인증"),
    ("PermissionDeniedError", None, "권한"),
    ("NotFoundError", None, "OPENAI_MODEL"),
    ("RateLimitError", None, "한도"),
    ("APITimeoutError", None, "시간이 초과"),
    ("APIConnectionError", None, "네트워크"),
    ("BadRequestError", None, "거부"),
    ("InternalServerError", 500, "서버"),
    ("APIStatusError", 409, "HTTP 409"),
])
def test_api_errors_mapped_without_leaking(env_file, cls, status, expected):
    _, factory = fake_factory(make_error(getattr(openai, cls), status))
    with pytest.raises(ai_client.AIError) as info:
        ai_client.generate_json("i", "u", {}, env_path=env_file, client_factory=factory)
    msg = str(info.value)
    assert expected in msg
    assert FAKE_KEY not in msg and "SECRET" not in msg
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_unknown_error_mapped(env_file):
    _, factory = fake_factory(ValueError(FAKE_KEY))
    with pytest.raises(ai_client.AIError) as info:
        ai_client.generate_json("i", "u", {}, env_path=env_file, client_factory=factory)
    assert "알 수 없는" in str(info.value) and FAKE_KEY not in str(info.value)


@pytest.mark.parametrize("resp", [
    fake_response(output=[types.SimpleNamespace(type="message",
                                                content=[types.SimpleNamespace(type="refusal")])]),
    fake_response(status="incomplete"),
    fake_response(text="   "),
])
def test_unusable_responses_rejected(env_file, resp):
    _, factory = fake_factory(resp)
    with pytest.raises(ai_client.AIError):
        ai_client.generate_json("i", "u", {}, env_path=env_file, client_factory=factory)
