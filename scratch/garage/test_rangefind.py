"""Two-way ranging on generated recordings with a known distance."""

import numpy as np
import pytest

import rangefind as R


def _recordings(distance_m, mac_start_s, phone_start_s, ppm=0.0, echo=False, seed=0):
    """What each device would record: own chirps at its self-distance, the other's
    after the flight time, on clocks that start at different moments."""
    c = R.speed_of_sound(20.0)
    rng = np.random.default_rng(seed)
    mac_wave = R.train(True, R.MAC_CHIRPS, 0.5, 0.25)
    phone_wave = R.train(False, R.PHONE_CHIRPS, R.PHONE_LEAD_S, 0.25)
    total = int(20 * R.SR)

    def place(wave, start_s, delay_m, gain, scale=1.0):
        out = np.zeros(total)
        t0 = (start_s + delay_m / c) * R.SR
        idx = np.arange(len(wave)) * scale + t0
        whole = np.floor(idx).astype(int)
        frac = idx - whole
        np.add.at(out, whole, wave * (1 - frac) * gain)
        np.add.at(out, whole + 1, wave * frac * gain)
        if echo:  # a reflection 1.5 ms later and stronger than the direct sound
            shift = int(0.0015 * R.SR)
            out[shift:] += 1.3 * out[:-shift].copy()
        return out

    scale = 1.0 + ppm * 1e-6
    mac = place(mac_wave, mac_start_s, R.D_MAC_SELF_M, 1.0) + place(phone_wave, phone_start_s, distance_m, 0.03, scale)
    phone = (place(phone_wave, phone_start_s, R.D_PHONE_SELF_M, 1.0, scale)
             + place(mac_wave, mac_start_s, distance_m, 0.03))
    noise = lambda: rng.normal(size=total) * 1e-4  # noqa: E731
    # each device's recording starts at its own unknown moment
    return (mac + noise())[int(0.37 * R.SR):], (phone + noise())[int(1.91 * R.SR):]


@pytest.mark.parametrize("distance", [0.10, 0.30, 0.76, 2.0])
def test_recovers_a_known_distance(distance):
    mac, phone = _recordings(distance, mac_start_s=1.0, phone_start_s=3.13)
    estimate, detail = R.estimate(mac, phone, 20.0)
    assert detail["pairs"] >= 4
    assert estimate == pytest.approx(distance, abs=0.01)
    assert detail["spread_m"] < 0.01


def test_a_28_ppm_clock_offset_moves_the_answer_by_under_a_centimetre():
    mac, phone = _recordings(0.76, mac_start_s=1.0, phone_start_s=3.13, ppm=28.0)
    estimate, _ = R.estimate(mac, phone, 20.0)
    assert estimate == pytest.approx(0.76, abs=0.01)


def test_a_stronger_later_reflection_does_not_pull_the_arrival_late():
    mac, phone = _recordings(0.76, mac_start_s=1.0, phone_start_s=3.13, echo=True)
    estimate, _ = R.estimate(mac, phone, 20.0)
    assert estimate == pytest.approx(0.76, abs=0.02)


def test_silence_gives_no_estimate():
    quiet = np.random.default_rng(1).normal(size=10 * R.SR) * 1e-4
    estimate, detail = R.estimate(quiet, quiet, 20.0)
    assert estimate is None
