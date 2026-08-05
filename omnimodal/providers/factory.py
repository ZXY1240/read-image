from __future__ import annotations

import os

from omnimodal.config import (
    DOUBAO_DEFAULT_BASE_URL,
    DOUBAO_DEFAULT_MODEL,
    base_url,
    model_name,
    provider_name,
)
from omnimodal.providers.base import VisionProvider
from omnimodal.providers.doubao import DoubaoProvider
from omnimodal.providers.openai_compatible import OpenAICompatibleProvider


def create_provider() -> VisionProvider:
    name = provider_name()
    if name == "openai_compatible":
        # 默认 qwen3-vl-flash（DashScope）；可用 READ_IMAGE_BASE_URL/READ_IMAGE_MODEL 覆盖
        return OpenAICompatibleProvider(base_url(), model_name())
    # doubao：显式配置了 URL/MODEL 则用显式的，否则用豆包默认
    base = os.environ.get("READ_IMAGE_BASE_URL", "").strip() or DOUBAO_DEFAULT_BASE_URL
    model = os.environ.get("READ_IMAGE_MODEL", "").strip() or DOUBAO_DEFAULT_MODEL
    return DoubaoProvider(base, model)
