from __future__ import annotations

from omnimodal.config import (
    base_url,
    image_model,
)
from omnimodal.providers.base import VisionProvider
from omnimodal.providers.openai_compatible import OpenAICompatibleProvider


def create_provider() -> VisionProvider:
    return OpenAICompatibleProvider(base_url(), image_model())
