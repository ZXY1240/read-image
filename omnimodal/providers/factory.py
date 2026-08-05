from __future__ import annotations

from omnimodal.config import (
    base_url,
    image_model,
    provider,
)
from omnimodal.providers.base import VisionProvider
from omnimodal.providers.openai_compatible import (
    OpenAICompatibleProvider,
    ZaiProvider,
)


def create_provider() -> VisionProvider:
    active = provider()
    if active == "zai":
        return ZaiProvider(base_url(), image_model())
    if active == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url(),
            image_model(),
            provider_name="openai_compatible",
        )
    return OpenAICompatibleProvider(base_url(), image_model())
