from __future__ import annotations

import os
from typing import Any

from pydantic import AnyUrl, BaseModel, Field


class RemnawaveConfig(BaseModel):
    base_url: AnyUrl
    token: str = Field(min_length=1)

    model_config = {
        "extra": "forbid",
        "validate_default": True,
    }

    @classmethod
    def from_env(cls) -> "RemnawaveConfig":
        # Let Pydantic raise a ValidationError if required env vars are missing/invalid.
        data: dict[str, Any] = {
            "base_url": os.getenv("REMNAWAVE_URL"),
            "token": os.getenv("REMNAWAVE_TOKEN"),
        }
        return cls.model_validate(data)
