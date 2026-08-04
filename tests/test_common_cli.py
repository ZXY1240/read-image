from __future__ import annotations

import argparse

from omnimodal.errors import ReadImageError
from omnimodal.mcp.common import run_cli


def test_run_cli_returns_handler_result() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--value", default="ok")

    def handler(args: argparse.Namespace) -> int:
        print(args.value)
        return 0

    assert run_cli(parser, handler, argv=[]) == 0


def test_run_cli_catches_plugin_error() -> None:
    parser = argparse.ArgumentParser()

    def handler(args: argparse.Namespace) -> int:
        raise ReadImageError("boom")

    assert run_cli(parser, handler, argv=[]) == 1
