from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./reroute.db"
    razorpay_key_id: str = Field(
        default="",
        validation_alias=AliasChoices("REROUTE_RAZORPAY_KEY_ID", "RAZORPAY_KEY_ID"),
    )
    razorpay_key_secret: str = Field(
        default="",
        validation_alias=AliasChoices("REROUTE_RAZORPAY_KEY_SECRET", "RAZORPAY_KEY_SECRET"),
    )
    razorpay_webhook_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "REROUTE_RAZORPAY_WEBHOOK_SECRET", "RAZORPAY_WEBHOOK_SECRET"
        ),
    )
    max_request_body_bytes: int = 1_000_000
    quiet_hours_start: int = 21
    quiet_hours_end: int = 8
    kill_switch: bool = False
    openrouter_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("REROUTE_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
    )
    openrouter_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    openrouter_http_referer: str = ""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="REROUTE_", populate_by_name=True, extra="ignore"
    )
