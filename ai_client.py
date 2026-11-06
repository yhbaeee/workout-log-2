"""OpenAI 호출 계층: .env 키 로드, 운동 후보 생성 요청, 오류 분류.

- 키는 프로젝트 루트의 .env를 먼저 보고, 없으면 환경 변수 OPENAI_API_KEY를 쓴다.
  Streamlit Community Cloud는 Secrets의 최상위 값을 환경 변수로 전달하므로 배포 앱은 Secrets에 키를 등록한다.
  키가 없으면 AI 기능만 비활성화한다.
- 키·.env 내용·프롬프트를 화면·로그·예외 메시지에 넣지 않는다. 오류는 AIError(고정 한국어 문구)로 바꾸고
  원래 예외는 연결하지 않는다(OpenAI 인증 오류 메시지에는 키 일부가 들어 있다).
- 응답은 OpenAI 쪽에 저장하지 않도록 store=False로 요청한다. 타임아웃·재시도는 최소로 둔다.
"""
import base64
import importlib.util
import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / ".env"
DEFAULT_MODEL = "gpt-6-astra"   # .env의 OPENAI_MODEL이 비어 있으면 사용
TIMEOUT_SEC = 45
DEFAULT_IMAGE_MODEL = "gpt-image-2"   # .env의 OPENAI_IMAGE_MODEL이 비어 있으면 사용
IMAGE_TIMEOUT_SEC = 120
IMAGE_SIZE = "1024x1024"
IMAGE_QUALITY = "low"          # 비용·시간 절약(참고용 그림)
MAX_IMAGE_BYTES = 8_000_000
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
    key = (values.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip() or None
    model = (values.get("OPENAI_MODEL") or os.environ.get("OPENAI_MODEL") or "").strip() or DEFAULT_MODEL
    return key, model


def unavailable_reason(env_path=None):
    """AI 기능을 쓸 수 없으면 그 이유(한국어), 쓸 수 있으면 None."""
    key, _ = load_settings(env_path)
    if not key:
        return "OPENAI_API_KEY가 설정되지 않았습니다(로컬은 .env, 배포는 Streamlit Secrets에 설정)."
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
        return "AI 요청이 거부되었습니다. 설정된 모델이 이 기능(JSON 형식 응답·이미지 생성)을 지원하는지, 요청이 정책에 맞는지 확인해 주세요."
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


def load_image_model(env_path=None):
    """이미지 모델명: .env → 환경 변수 OPENAI_IMAGE_MODEL → DEFAULT_IMAGE_MODEL."""
    env_path = Path(env_path or ENV_PATH)
    value = ""
    try:
        from dotenv import dotenv_values

        if env_path.is_file():
            value = (dotenv_values(env_path).get("OPENAI_IMAGE_MODEL") or "").strip()
    except Exception:
        value = ""
    return value or (os.environ.get("OPENAI_IMAGE_MODEL") or "").strip() or DEFAULT_IMAGE_MODEL


def _is_image(data):
    return data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")


def _decodes(data):
    """실제로 열리는 이미지인지 확인(깨진 이미지가 화면을 멈추지 않게). Pillow가 없으면 형식 확인만 한다."""
    try:
        import io

        from PIL import Image
    except ImportError:
        return True
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
        return True
    except Exception:
        return False


def generate_image(prompt, env_path=None, client_factory=None):
    """프롬프트로 이미지 1장을 만들어 바이트(PNG/JPEG/WebP)로 반환한다. 실패하면 AIError.

    client_factory는 테스트에서 가짜 클라이언트를 넣을 때만 쓴다. 키·프롬프트는 로그·오류에 넣지 않는다.
    """
    key, _ = load_settings(env_path)
    if not key:
        raise AIError("이미지 생성 기능을 사용할 수 없습니다. OPENAI_API_KEY가 설정되지 않았습니다.")
    try:
        import openai
    except ImportError:
        raise AIError("openai 패키지가 설치되지 않아 이미지 생성 기능을 사용할 수 없습니다.") from None
    factory = client_factory or openai.OpenAI
    try:
        client = factory(api_key=key, timeout=IMAGE_TIMEOUT_SEC, max_retries=MAX_RETRIES)
        response = client.images.generate(model=load_image_model(env_path), prompt=prompt, n=1,
                                          size=IMAGE_SIZE, quality=IMAGE_QUALITY)
    except Exception as e:  # 원래 예외(키 일부 포함 가능)는 노출하지 않는다
        raise AIError(_message_for(e)) from None
    try:
        data = base64.b64decode(response.data[0].b64_json, validate=True)
    except Exception:
        raise AIError("이미지 응답을 해석하지 못했습니다. 다시 시도해 주세요.") from None
    if not data or len(data) > MAX_IMAGE_BYTES or not _is_image(data) or not _decodes(data):
        raise AIError("올바른 이미지를 받지 못했습니다. 다시 시도해 주세요.")
    return data
