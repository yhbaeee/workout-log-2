"""OpenAI 호출 계층: .env 키 로드, 운동 후보 생성 요청, 오류 분류.

- AI 운동 생성은 로컬 전용 기능이다. 키는 프로젝트 루트의 .env에서만 읽는다(환경 변수·Secrets 미사용).
  배포본처럼 .env가 없으면 키가 없는 것으로 보고 AI 기능만 비활성화한다.
- 키·.env 내용·프롬프트를 화면·로그·예외 메시지에 넣지 않는다. 오류는 AIError(고정 한국어 문구)로 바꾸고
  원래 예외는 연결하지 않는다(OpenAI 인증 오류 메시지에는 키 일부가 들어 있다).
- 응답은 OpenAI 쪽에 저장하지 않도록 store=False로 요청한다. 타임아웃·재시도는 최소로 둔다.
"""
import importlib.util
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / ".env"
DEFAULT_MODEL = "gpt-6-astra"   # .env의 OPENAI_MODEL이 비어 있으면 사용
TIMEOUT_SEC = 45
MAX_RETRIES = 1
MAX_OUTPUT_TOKENS = 4000   # 실측(3개): 출력 약 630토큰


class AIError(Exception):
    """사용자에게 그대로 보여줘도 되는 AI 기능 오류."""


def load_settings(env_path=None):
    """(.env의 OPENAI_API_KEY 또는 None, 모델명). 파일이 없거나 읽을 수 없으면 키는 None."""
    env_path = Path(env_path or ENV_PATH)
    values = {}
    try:
        from dotenv import dotenv_values

        if env_path.is_file():
            values = dotenv_values(env_path)
    except Exception:  # dotenv 미설치, 인코딩 오류 등: 키 없음으로 처리
        values = {}
    key = (values.get("OPENAI_API_KEY") or "").strip() or None
    model = (values.get("OPENAI_MODEL") or "").strip() or DEFAULT_MODEL
    return key, model


def unavailable_reason(env_path=None):
    """AI 기능을 쓸 수 없으면 그 이유(한국어), 쓸 수 있으면 None."""
    key, _ = load_settings(env_path)
    if not key:
        return "OPENAI_API_KEY가 설정되지 않았습니다(로컬 .env 파일 필요)."
    if importlib.util.find_spec("openai") is None:
        return "openai 패키지가 설치되지 않았습니다."
    return None


def _message_for(error):
    import openai

    if isinstance(error, openai.AuthenticationError):
        return "API 키 인증에 실패했습니다. .env의 OPENAI_API_KEY 값을 확인해 주세요."
    if isinstance(error, openai.PermissionDeniedError):
        return "이 API 키로는 설정된 모델이나 기능을 사용할 권한이 없습니다."
    if isinstance(error, openai.NotFoundError):
        return "설정된 AI 모델을 찾을 수 없습니다. .env의 OPENAI_MODEL 값을 확인해 주세요."
    if isinstance(error, openai.RateLimitError):
        return "요청 한도 또는 사용량 한도를 넘었습니다. 잠시 후 다시 시도하거나 OpenAI 사용량 설정을 확인해 주세요."
    if isinstance(error, openai.APITimeoutError):
        return "AI 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."
    if isinstance(error, openai.APIConnectionError):
        return "네트워크 연결에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 시도해 주세요."
    if isinstance(error, openai.BadRequestError):
        return "AI 요청이 거부되었습니다. 설정된 모델이 이 기능(JSON 형식 응답)을 지원하는지 확인해 주세요."
    if isinstance(error, openai.APIStatusError):
        if error.status_code >= 500:
            return "AI 서버에 일시적인 문제가 있습니다. 잠시 후 다시 시도해 주세요."
        return f"AI 요청 처리 중 오류가 발생했습니다(HTTP {error.status_code})."
    return "AI 운동 후보 생성 중 알 수 없는 오류가 발생했습니다."


def _has_refusal(response):
    for output in getattr(response, "output", None) or []:
        for content in getattr(output, "content", None) or []:
            if getattr(content, "type", None) == "refusal":
                return True
    return False


def generate_json(instructions, user_input, schema, env_path=None, client_factory=None):
    """JSON 스키마 형식으로 응답을 받아 그 텍스트를 반환한다. 실패하면 AIError.

    client_factory는 테스트에서 가짜 클라이언트를 넣을 때만 쓴다.
    """
    key, model = load_settings(env_path)
    if not key:
        raise AIError("AI 운동 생성 기능을 사용할 수 없습니다. OPENAI_API_KEY가 설정되지 않았습니다.")
    try:
        import openai
    except ImportError:
        raise AIError("openai 패키지가 설치되지 않아 AI 운동 생성 기능을 사용할 수 없습니다.") from None
    factory = client_factory or openai.OpenAI
    try:
        client = factory(api_key=key, timeout=TIMEOUT_SEC, max_retries=MAX_RETRIES)
        response = client.responses.create(
            model=model,
            instructions=instructions,
            input=user_input,
            text={"format": {"type": "json_schema", "name": "exercise_candidates",
                             "schema": schema, "strict": True}},
            max_output_tokens=MAX_OUTPUT_TOKENS,
            store=False,
        )
    except Exception as e:  # 원래 예외(키 일부 포함 가능)는 노출하지 않는다
        raise AIError(_message_for(e)) from None
    if _has_refusal(response):
        raise AIError("AI가 이 요청에 대한 운동 후보를 만들지 않았습니다. 추가 요청을 바꿔 다시 시도해 주세요.")
    if getattr(response, "status", "completed") not in (None, "completed"):
        raise AIError("AI 응답이 끝까지 완성되지 않았습니다. 운동 개수를 줄여 다시 시도해 주세요.")
    text = getattr(response, "output_text", "") or ""
    if not text.strip():
        raise AIError("AI 응답이 비어 있습니다. 다시 시도해 주세요.")
    return text
