"""The scrambler air screen, scored on digital stand-ins for a phone's recording."""

import numpy as np
import pytest

import scrambler_air_screen as screen


@pytest.fixture(scope="module")
def codec(tmp_path_factory):
    return screen.Codec(tmp_path_factory.mktemp("codec"))


def recording(tmp_path, wave, gain):
    """A phone's file: silence, the transmission on both channels, silence."""
    scaled = wave * (gain / float(np.abs(wave).max()))
    pcm = np.concatenate([np.zeros(20000), scaled, np.zeros(60000)])
    path = tmp_path / "phone.pcm"
    np.repeat((pcm * 32767).astype("<i2")[:, None], 2, axis=1).tofile(path)
    return path


@pytest.mark.parametrize("candidate", [6, 8])
def test_clean_recording_passes_and_shows_the_unscrambled_loss(codec, tmp_path, candidate):
    profiles, data, wave, facts = screen.transmission(codec, candidate)
    rows = screen.score(codec, profiles, data, wave, facts, recording(tmp_path, wave, 0.5))
    blocks = facts["blocks"]
    assert [row["profile"] for row in rows] == [candidate, candidate - 5, candidate, candidate, candidate - 5, candidate]
    # Dense controls and scrambled records come back whole; the same records unscrambled are lost.
    assert [row["blocks_verified"] for row in rows] == [blocks, 0, blocks, blocks, 0, blocks]
    assert all(row["wrong_valid_blocks"] == 0 and row["status"] == 0 for row in rows)
    scores = {"phone": rows}
    assert screen.verdict(scores, candidate) and screen.controls_decoded(scores)


def test_a_recording_that_carries_nothing_fails_and_says_the_controls_failed(codec, tmp_path):
    profiles, data, wave, facts = screen.transmission(codec, 6)
    rows = screen.score(codec, profiles, data, wave, facts, recording(tmp_path, wave, 1e-5))
    scores = {"phone": rows}
    assert not screen.verdict(scores, 6)
    assert not screen.controls_decoded(scores)
    assert all(row["blocks_verified"] == 0 for row in rows)


def test_a_record_frame_with_the_wrong_bytes_does_not_count(codec, tmp_path):
    profiles, data, wave, facts = screen.transmission(codec, 6)
    other = dict(data, **{"record-A": bytes(128)})
    rows = screen.score(codec, profiles, other, wave, facts, recording(tmp_path, wave, 0.5))
    # Frame 2 decodes every block, but its first block is not the record the scorer was told.
    assert rows[2]["blocks_valid"] == facts["blocks"] and not rows[2]["first_block_holds_the_payload"]
    assert rows[2]["wrong_valid_blocks"] == 1
    assert not screen.verdict({"phone": rows}, 6)


def test_only_a_scrambled_profile_is_a_candidate(codec):
    for candidate in (0, 1, 5, 11):
        with pytest.raises(ValueError):
            screen.transmission(codec, candidate)


def test_a_capture_shorter_than_the_transmission_is_refused(codec, tmp_path):
    profiles, data, wave, facts = screen.transmission(codec, 6)
    path = tmp_path / "short.pcm"
    np.zeros((len(wave) - 1, 2), "<i2").tofile(path)
    with pytest.raises(ValueError):
        screen.score(codec, profiles, data, wave, facts, path)
