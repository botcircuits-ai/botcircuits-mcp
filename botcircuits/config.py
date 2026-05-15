from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BOTCIRCUITS_", extra="ignore")

    api_base_url: str = Field(
        default="https://api.botcircuits.com",
        description="BotCircuits REST API base URL",
    )
    access_token: str = Field(
        default="",
        description="Bearer access token (Cognito JWT or API key)",
    )
    default_app_id: Optional[str] = Field(
        default=None,
        description="Default appId used when none is supplied to a tool",
    )


settings = Settings()
