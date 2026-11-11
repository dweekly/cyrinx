"""Captures must land somewhere a merged pull request cannot delete."""

import os

import numpy as np
import pytest

import retain


def test_default_is_absolute_and_outside_this_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv(retain.ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    path = retain.session_dir()
    assert os.path.isabs(path) and os.path.isdir(path)
    assert path == os.path.join(str(tmp_path), "dev", "cyrinx-artifacts", "garage")


def test_environment_variable_overrides_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv(retain.ENV_VAR, str(tmp_path / "elsewhere"))
    assert retain.session_dir() == str(tmp_path / "elsewhere")


def test_a_directory_inside_a_linked_worktree_is_refused(tmp_path):
    worktree = tmp_path / "cyrinx-WORKTREE" / "crosscal"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: /somewhere/.git/worktrees/crosscal\n")
    target = worktree / "artifacts" / "garage"
    with pytest.raises(SystemExit, match="linked git worktree"):
        retain.session_dir(str(target))
    assert not target.exists(), "a refused directory must not be created"


def test_the_main_checkout_is_allowed(tmp_path):
    checkout = tmp_path / "cyrinx"
    (checkout / ".git").mkdir(parents=True)
    target = checkout / "artifacts" / "garage"
    assert retain.session_dir(str(target)) == str(target)


def test_relative_default_of_the_first_sessions_is_refused_from_a_worktree(tmp_path, monkeypatch):
    """The exact invocation that lost the September captures."""
    worktree = tmp_path / "garage-g0"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /somewhere/.git/worktrees/garage-g0\n")
    monkeypatch.chdir(worktree)
    with pytest.raises(SystemExit):
        retain.session_dir("artifacts/garage")


def test_crosscal_retains_every_phone_microphone(monkeypatch):
    crosscal = pytest.importorskip("crosscal", reason="crosscal needs the audio harness")
    stereo = np.stack([np.arange(1, 9), -np.arange(1, 9)], axis=1).astype(np.int16)
    monkeypatch.setattr(crosscal.H, "load_pcm16", lambda path, channels: stereo)

    assert crosscal._pull_capture("x.pcm").tolist() == stereo[:, 0].tolist()
    every = crosscal._pull_capture("x.pcm", all_channels=True)
    assert every.shape == (8, 2) and every[:, 1].tolist() == stereo[:, 1].tolist()

    stored = crosscal.retained_arrays({
        "mac_to_phone": {"capture": every[:, 0], "tone": every[:, 0],
                         "capture_all_mics": every, "tone_all_mics": every},
        "phone_to_mac": {"capture": every[:, 0], "tone": every[:, 0]},
    })
    assert stored["mac_to_phone_capture_all_mics"].shape == (8, 2)
    assert stored["mac_to_phone_capture_all_mics"].dtype == np.float32
    assert set(stored) == {
        "mac_to_phone_capture", "mac_to_phone_tone",
        "mac_to_phone_capture_all_mics", "mac_to_phone_tone_all_mics",
        "phone_to_mac_capture", "phone_to_mac_tone",
    }
