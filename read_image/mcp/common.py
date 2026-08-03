from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from read_image.errors import PluginError


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass


def run_cli(
    parser: argparse.ArgumentParser,
    handler: Callable[[argparse.Namespace], int],
    argv: list[str] | None = None,
) -> int:
    configure_stdio()
    args = parser.parse_args(argv)
    try:
        return handler(args)
    except PluginError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
