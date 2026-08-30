"""환경변수 로딩. 전체 목록: docs/reference/infra-ops.md#환경변수"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- core ---
    database_url: str = "postgresql+asyncpg://lastletter:lastletter@postgres:5432/lastletter"
    redis_url: str = "redis://redis:6379/0"

    # --- auth ---
    jwt_secret: str = "dev-secret-change-me"
    jwt_access_ttl: int = 900
    jwt_refresh_ttl: int = 2592000

    # --- object storage (OCI, S3 호환) ---
    oci_namespace: str = ""
    oci_bucket: str = ""
    oci_region: str = ""
    oci_s3_endpoint: str = ""
    oci_access_key: str = ""
    oci_secret_key: str = ""
    oci_vault_key_ocid: str = ""

    # --- messaging ---
    alimtalk_api_key: str = ""
    alimtalk_sender_key: str = ""
    sms_api_key: str = ""

    # --- kakao oauth ---
    kakao_client_id: str = ""
    kakao_client_secret: str = ""
    kakao_redirect_uri: str = ""

    # --- auth cookie ---
    # 로컬(http)에서 리프레시 쿠키 테스트하려면 .env에서 false로. 프로덕션은 Caddy가 TLS 종단하므로 true 유지.
    cookie_secure: bool = True

    # --- AI (GPU 서버, OpenAI 호환) ---
    ai_base_url: str = "http://localhost:8001/v1"
    ai_api_key: str = ""
    ai_model_chat: str = "qwen2.5-32b-instruct"
    ai_model_asr: str = "whisper-large-v3"
    ai_timeout_sec: int = 60

    # --- ops ---
    admin_alert_webhook: str = ""


settings = Settings()
