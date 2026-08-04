from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from omnimodal.errors import ReadImageError
from omnimodal.paths import ensure_allowed_output_dir


def test_allows_temp_and_workspace_dirs() -> None:
    target = Path(tempfile.gettempdir()) / "read-image-output-test"
    result = ensure_allowed_output_dir(str(target))
    assert result == target.resolve()


def test_rejects_output_outside_allowed_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("READ_IMAGE_ALLOWED_OUTPUT_DIRS", raising=False)
    forbidden = Path.home() / "read-image-forbidden-output"
    with pytest.raises(ReadImageError):
        ensure_allowed_output_dir(str(forbidden))


def test_allows_explicit_configured_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed = tmp_path / "allowed"
    monkeypatch.setenv("READ_IMAGE_ALLOWED_OUTPUT_DIRS", str(allowed))
    result = ensure_allowed_output_dir(str(allowed / "nested"))
    assert result == (allowed / "nested").resolve()


def test_allows_extra_root(tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    result = ensure_allowed_output_dir(
        str(extra / "nested"),
        extra_allowed_roots=[str(extra)],
    )
    assert result == (extra / "nested").resolve()
