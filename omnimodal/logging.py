from __future__ import annotations

import logging
import sys

from omnimodal.config import log_level


def configure_logging(name: str = "omnimodal") -> logging.Logger:
    level_name = log_level().upper()
    level = getattr(logging, level_name, logging.INFO)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    return logger
