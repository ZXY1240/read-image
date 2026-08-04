"""Compatibility facade for image and video processing modules."""

from omnimodal.config import video_base64_max_bytes, video_download_max_bytes
from omnimodal.image_processing import prepare_image, prepare_image_variants
from omnimodal.video_processing import (
    MAX_VIDEO_CONVERSION_DEPTH,
    analyze_video,
    video_max_bytes,
)

__all__ = [
    "MAX_VIDEO_CONVERSION_DEPTH",
    "analyze_video",
    "prepare_image",
    "prepare_image_variants",
    "video_base64_max_bytes",
    "video_download_max_bytes",
    "video_max_bytes",
]
