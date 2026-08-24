from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./reroute.db"
    razorpay_webhook_secret: str = ""
    max_request_body_bytes: int = 1_000_000
    quiet_hours_start: int = 21
    quiet_hours_end: int = 8

    model_config = SettingsConfigDict(env_file=".env", env_prefix="REROUTE_")
