import numpy as np
import math

def simulate_amplifier_nonlinearity(fs=96000, duration=0.5, boost_db=30.0):
    """
    Simulates a speaker amplifier with third-order non-linearities:
      y(t) = x(t) + alpha * x(t)^2 + beta * x(t)^3
    Receives two boosted ultrasonic carriers:
      f1 = 25000 Hz
      f2 = 26000 Hz
    """
    n_samples = int(fs * duration)
    t = np.linspace(0, duration, n_samples, endpoint=False)
    
    # 1. Base carrier amplitudes (normally 0.05, but boosted to overcome -35 dB roll-off)
    boost_factor = 10 ** (boost_db / 20.0)
    amp = 0.05 * boost_factor
    
    # Input signal: sum of two sine waves at 25 kHz and 26 kHz
    x = amp * np.sin(2 * np.pi * 25000.0 * t) + amp * np.sin(2 * np.pi * 26000.0 * t)
    
    # 2. Amplifier Non-Linearity Coefficients
    # Typical consumer class-D amplifiers have small non-linear distortion (e.g., THD 0.1% to 1%)
    alpha = 0.015  # Quadratic term
    beta = 0.008   # Cubic term
    
    # Apply amplifier transfer function
    y = x + alpha * (x ** 2) + beta * (x ** 3)
    
    # Apply speaker physical roll-off (low pass filter)
    # The speaker heavily attenuates frequencies above 24 kHz by -30 dB, but reproduces 
    # audible frequencies flatly (0 dB).
    # We do a simple FFT, apply the acoustic transducer model, and IFFT back.
    Y = np.fft.rfft(y)
    freqs = np.fft.rfftfreq(n_samples, 1/fs)
    
    transducer_response = np.ones(len(Y))
    for idx, f in enumerate(freqs):
        if f > 24000.0:
            # Drop severely above 24 kHz
            transducer_response[idx] = 10 ** (-30.0 / 20.0)
            
    Y_acoustic = Y * transducer_response
    y_acoustic = np.fft.irfft(Y_acoustic, n_samples)
    
    return freqs, Y, Y_acoustic, y_acoustic

def main():
    print("======================================================================")
    print("    🧪 CRITIQUE FOLLOW-UP: AMPLIFIER NON-LINEARITY & INTERMOD 🧪")
    print("======================================================================")
    print("\nCritique: Pushing high-amplitude signals above 24 kHz under high-gain")
    print("pre-emphasis to overcome transducer roll-off is extremely dangerous.")
    print("Speaker amplifiers are non-linear; boosting ultrasonics generates massive")
    print("intermodulation products (mixing tones) directly in the AUDIBLE band!\n")
    
    fs = 96000
    duration = 0.5
    
    # Simulate with +30 dB pre-emphasis boost
    freqs, Y, Y_acoustic, y_acoustic = simulate_amplifier_nonlinearity(fs, duration, boost_db=30.0)
    
    # Compute PSD of acoustic output to show what actually travels in the air
    psd = np.abs(Y_acoustic) / len(Y_acoustic)
    psd_db = 20 * np.log10(psd + 1e-12)
    
    # Scan specific regions:
    # 1. Audible Intermodulation (f2 - f1 = 1000 Hz)
    idx_1k = np.abs(freqs - 1000.0).argmin()
    val_1k = psd_db[idx_1k]
    
    # 2. Ultrasonic Carrier f1 (25000 Hz)
    idx_25k = np.abs(freqs - 25000.0).argmin()
    val_25k = psd_db[idx_25k]
    
    # 3. Third-order product (2*f1 - f2 = 24000 Hz)
    idx_24k = np.abs(freqs - 24000.0).argmin()
    val_24k = psd_db[idx_24k]
    
    print("📊 ACOUSTIC RADIATION SPECTRUM (WITH 30 dB ULTRASONIC BOOST):")
    print("----------------------------------------------------------------------")
    print("  FREQUENCY  |  SPECTRAL VALUE (dB)  |  BAND CLASSIFICATION  |  STATUS")
    print("----------------------------------------------------------------------")
    print(f"   1.0 kHz   |      {val_1k:6.1f} dB        |  Audible (f2 - f1)    |  🔊 HEAVILY AUDIBLE WHINE!")
    print(f"  24.0 kHz   |      {val_24k:6.1f} dB        |  Acoustic Border      |  ⚠️ High Emission Limit")
    print(f"  25.0 kHz   |      {val_25k:6.1f} dB        |  Ultrasonic Carrier   |  ✔ Attenuated Carrier")
    print("----------------------------------------------------------------------")
    
    print("\n📈 ASCII Spectrum Plot (Audible to Low-Ultrasonic):")
    print("   Freq (kHz) | Level (dB) | Visualization")
    print("   --------------------------------------------------------")
    interesting_freqs = [1.0, 5.0, 10.0, 15.0, 20.0, 24.0, 25.0, 26.0]
    for f_khz in interesting_freqs:
        idx = np.abs(freqs - f_khz * 1000.0).argmin()
        val = psd_db[idx]
        # Map val (-100 to 0) to 25 characters
        chars = int(max(0, (val + 100) / 4))
        bar = "█" * chars
        classification = "Audible" if f_khz <= 20.0 else "Ultrasonic"
        print(f"    {f_khz:4.1f} kHz  |  {val:6.1f} dB | {bar:<25} ({classification})")
    print("   --------------------------------------------------------")
    
    print("\n💡 RESPONSIVE ARCHITECTURAL RECOMMENDATION:")
    print("1. STRICTLY CAP pre-emphasis gains to a maximum of +6 dB to +12 dB to prevent")
    print("   amplifier clipping and non-linear harmonic mixing.")
    print("2. Restrict transmission carrier bands to a maximum of 22.5 kHz. Frequencies")
    print("   above 24 kHz should NEVER be amplified to overcome transducer roll-off, as")
    print("   the resulting intermodulation creates loud audible interference (1 kHz whines).")
    print("======================================================================\n")

if __name__ == "__main__":
    main()
