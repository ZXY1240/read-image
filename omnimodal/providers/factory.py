from __future__ import annotations

import os

from omnimodal.config import base_url, model_name, provider_name
from omnimodal.errors import ReadImageError, tr
from omnimodal.providers.base import VisionProvider
from omnimodal.providers.doubao import DoubaoProvider
from omnimodal.providers.openai_compatible import OpenAICompatibleProvider


def create_provider() -> VisionProvider:
    name = provider_name()
    if name == "openai_compatible":
        configured_base = os.environ.get("READ_IMAGE_BASE_URL", "").strip()
        configured_model = os.environ.get("READ_IMAGE_MODEL", "").strip()
        if not configured_base or not configured_model:
            raise ReadImageError(
                tr(
                    "openai_compatible 模式必须同时设置 READ_IMAGE_BASE_URL 和 READ_IMAGE_MODEL。",
                    "openai_compatible mode requires READ_IMAGE_BASE_URL and READ_IMAGE_MODEL.",
                )
            )
        return OpenAICompatibleProvider(configured_base, configured_model)
    return DoubaoProvider(base_url(), model_name())
