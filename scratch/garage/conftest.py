"""Shared fixtures for the garage tests."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hw20k")
)


@pytest.fixture(scope="session")
def codec():
    """The C bulk codec, or a skip if the dylib is not built.

    ``importorskip`` alone is not enough: ``clib`` imports fine and loads the
    library lazily, so an absent dylib surfaces as FileNotFoundError from the
    first call rather than as an ImportError. Only that error skips; a codec that
    is present but wrong must fail.

    Lives here rather than in one test module so every test that needs the codec
    skips the same way. README.md documents running without the library.
    """
    clib = pytest.importorskip("clib", reason="clib not importable")
    try:
        clib.geometry(clib.make_cfg())
    except FileNotFoundError as exc:
        pytest.skip(f"C bulk codec not built: {exc}")
    return clib
