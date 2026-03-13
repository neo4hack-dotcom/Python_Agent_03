from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class LLMProvider(str, Enum):
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"
    LMSTUDIO = "lmstudio"
    CUSTOM = "custom"


class LLMConfig(BaseModel):
    provider: LLMProvider = LLMProvider.OLLAMA
    base_url: str = Field(default="http://localhost:11434/v1", description="Base URL of the LLM HTTP endpoint")
    model: str = Field(default="llama3.2", description="Model name to use")
    api_key: str = Field(default="ollama", description="API key (can be dummy for local)")
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=64, le=32768)
    timeout: int = Field(default=120, ge=10, le=600, description="Request timeout in seconds")
    streaming: bool = Field(default=True)

    class Config:
        json_schema_extra = {
            "example": {
                "provider": "ollama",
                "base_url": "http://localhost:11434/v1",
                "model": "llama3.2",
                "api_key": "ollama",
                "temperature": 0.1,
                "max_tokens": 4096,
                "timeout": 120,
                "streaming": True,
            }
        }
