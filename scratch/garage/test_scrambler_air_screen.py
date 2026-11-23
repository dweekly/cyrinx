"""The scrambler air screen, scored on digital stand-ins for a phone's recording."""

import copy

import numpy as np
import pytest

import scrambler_air_screen as screen

# The six-frame schedule of the first runs, and the digest each retained run recorded for it.
FIRST_SCHEDULE = [["dense control", "C", "dense-0"], ["record A, unscrambled", "P", "record-A"],
                  ["record A, scrambled", "C", "record-A"], ["record B, scrambled", "C", "record-B"],
                  ["record B, unscrambled", "P", "record-B"], ["dense control", "C", "dense-1"]]
RETAINED = {7: "e636f254cea9b90318b5a5c54d1b74b2e920022d9c23d18cf58156f1c5c18821",
            6: "dd7bc624486d0edf5f391ac0dc085e3f1329129fcaf075efaed2257f96b02c57"}


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


@pytest.fixture(scope="module")
def clean(codec, tmp_path_factory):
    """Candidate 6 scored on a clean recording."""
    profiles, data, wave, facts = screen.transmission(codec, 6)
    rows = screen.score(codec, profiles, data, wave, facts, recording(tmp_path_factory.mktemp("clean"), wave, 0.5))
    return facts, rows


def test_clean_recording_recovers_controls_and_scrambled_records_and_loses_unscrambled_ones(clean):
    facts, rows = clean
    blocks = facts["blocks"]
    assert [row["profile"] for row in rows] == [6, 1, 1, 6, 6, 1, 1, 6]
    # Both kinds of dense control and the scrambled records come back whole; the same records
    # unscrambled are lost.
    assert [row["blocks_verified"] for row in rows] == [blocks, blocks, 0, blocks, blocks, 0, blocks, blocks]
    assert all(row["wrong_valid_blocks"] == 0 and row["status"] == 0 for row in rows)


def test_two_clean_phones_pass_and_one_alone_gives_no_answer(clean):
    _, rows = clean
    both = screen.summarize({"one": rows, "two": rows}, 6)
    assert both == dict(passed=True, outcome="passed", records_recovered=True, controls_decoded=True, phones=2)
    alone = screen.summarize({"one": rows}, 6)
    assert not alone["passed"] and alone["outcome"] == "no answer"
    assert alone["records_recovered"] and alone["controls_decoded"]


@pytest.mark.parametrize("control", [0, 1, 6, 7])
def test_a_lost_control_gives_no_answer_even_when_the_records_are_recovered(clean, control):
    _, rows = clean
    damaged = copy.deepcopy(rows)
    damaged[control]["blocks_verified"] -= 1
    summary = screen.summarize({"one": rows, "two": damaged}, 6)
    assert summary["records_recovered"] and not summary["controls_decoded"]
    assert not summary["passed"] and summary["outcome"] == "no answer"


@pytest.mark.parametrize("record", [3, 4])
def test_a_lost_scrambled_record_fails_when_the_controls_decode(clean, record):
    _, rows = clean
    damaged = copy.deepcopy(rows)
    damaged[record]["first_block_holds_the_payload"] = False
    summary = screen.summarize({"one": rows, "two": damaged}, 6)
    assert summary["controls_decoded"] and not summary["records_recovered"]
    assert not summary["passed"] and summary["outcome"] == "failed"


def test_a_recording_that_carries_nothing_gives_no_answer(codec, tmp_path):
    profiles, data, wave, facts = screen.transmission(codec, 6)
    rows = screen.score(codec, profiles, data, wave, facts, recording(tmp_path, wave, 1e-5))
    assert all(row["blocks_verified"] == 0 for row in rows)
    summary = screen.summarize({"one": rows, "two": rows}, 6)
    assert summary["outcome"] == "no answer" and not summary["passed"]
    assert not summary["controls_decoded"] and not summary["records_recovered"]


def test_a_record_frame_with_the_wrong_bytes_does_not_count(codec, tmp_path):
    profiles, data, wave, facts = screen.transmission(codec, 6)
    other = dict(data, **{"record-A": bytes(128)})
    rows = screen.score(codec, profiles, other, wave, facts, recording(tmp_path, wave, 0.5))
    # Frame 3 decodes every block, but its first block is not the record the scorer was told.
    assert rows[3]["blocks_valid"] == facts["blocks"] and not rows[3]["first_block_holds_the_payload"]
    assert rows[3]["wrong_valid_blocks"] == 1
    assert not screen.summarize({"one": rows, "two": rows}, 6)["records_recovered"]


@pytest.mark.parametrize("candidate", [7, 6])
def test_the_first_schedule_still_builds_the_transmission_the_retained_runs_recorded(codec, candidate):
    frames = [[label, candidate if which == "C" else candidate - 5, name] for label, which, name in FIRST_SCHEDULE]
    _, _, _, facts = screen.transmission(codec, candidate, frames)
    assert facts["tx_float32_sha256"] == RETAINED[candidate]
    assert facts["schedule"] == frames


def test_only_a_scrambled_profile_is_a_candidate_and_a_schedule_names_known_things(codec):
    for candidate in (0, 1, 5, 11):
        with pytest.raises(ValueError):
            screen.transmission(codec, candidate)
    with pytest.raises(ValueError):
        screen.transmission(codec, 6, [["dense control", 3, "dense-0"]])
    with pytest.raises(ValueError):
        screen.transmission(codec, 6, [["dense control", 6, "dense-9"]])


def test_a_capture_shorter_than_the_transmission_is_refused(codec, tmp_path):
    profiles, data, wave, facts = screen.transmission(codec, 6)
    path = tmp_path / "short.pcm"
    np.zeros((len(wave) - 1, 2), "<i2").tofile(path)
    with pytest.raises(ValueError):
        screen.score(codec, profiles, data, wave, facts, path)
