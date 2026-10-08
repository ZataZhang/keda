"""Tests for shared KedaCode version resolution."""

from __future__ import annotations

import importlib.metadata as importlib_metadata

import pytest

from backend.api import version_info


def test_resolve_keda_version_reads_distribution_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有发行版元数据时返回查询到的版本。"""
    monkeypatch.setattr(version_info.importlib_metadata, "version", lambda _name: "9.9.9")
    assert version_info.resolve_keda_version() == "9.9.9"


def test_resolve_keda_version_falls_back_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """发行版元数据不可得时回退到形态合法的占位版本，而不是抛异常。"""

    def _raise(_name: str) -> str:
        raise importlib_metadata.PackageNotFoundError

    monkeypatch.setattr(version_info.importlib_metadata, "version", _raise)
    assert version_info.resolve_keda_version() == "0.0.0+unknown"
