"""Editable model configuration. Responses contain no API secrets."""
from typing import Literal
from urllib.parse import urlsplit
from pydantic import Field, field_validator, model_validator
from app.calculations import Inputs


class ModelNames(Inputs):
    fast: str = Field(default="", max_length=200)
    balanced: str = Field(default="", max_length=200)
    deep: str = Field(default="", max_length=200)

    @field_validator("fast", "balanced", "deep")
    @classmethod
    def clean(cls, value):
        return value.strip()


class ModelSettings(Inputs):
    provider: Literal["openai_compatible", "ollama"] = "openai_compatible"
    base_url: str = Field(min_length=1, max_length=1000)
    models: ModelNames = Field(default_factory=ModelNames)
    vision_model: str = Field(default="", max_length=200)
    api_key: str = Field(default="", max_length=4000, repr=False)
    clear_api_key: bool = False
    reasoning_supported: bool = False
    timeout: int = Field(default=120, ge=10, le=300)
    iterations: int = Field(default=8, ge=1, le=20)

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        value = value.strip().rstrip("/")
        try:
            parsed = urlsplit(value)
            valid = parsed.scheme in {"http", "https"} and parsed.hostname and parsed.port != 0
        except ValueError:
            valid = False
        if not valid or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("请输入有效的 HTTP/HTTPS API 地址，地址中不要包含密钥、查询参数或用户凭据")
        return value

    @field_validator("api_key", "vision_model")
    @classmethod
    def clean(cls, value):
        return value.strip()

    @model_validator(mode="after")
    def key_choice(self):
        if self.clear_api_key and self.api_key:
            raise ValueError("更新密钥和清除密钥不能同时选择")
        return self
