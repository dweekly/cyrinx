import numpy as np
import math
import sys

def generate_ambient_noise_profile(fs=48000, duration=1.0, noise_floor_db=-60.0):
    """
    Generates a realistic MacBook/Pixel ambient room-tone noise profile.
    Includes white noise floor at noise_floor_db, plus several sharp, narrow-band 
    interferers:
      - HVAC/fan mechanical rumble: 120 Hz (moderate amplitude)
      - AC adapter/coil whine: 8000 Hz (sharp peak)
      - Ultrasonic occupancy sensor/dog whistle: 19200 Hz (very sharp, strong peak in acoustic band)
    """
    n_samples = int(fs * duration)
    t = np.linspace(0, duration, n_samples, endpoint=False)
    
    # 1. Base thermal/ambient noise floor (white noise)
    noise_variance = 10 ** (noise_floor_db / 10.0)
    signal = np.random.normal(0, np.sqrt(noise_variance), n_samples)
    
    # 2. Add narrow-band persistent interferers
    # Peak 1: 120 Hz HVAC hum (-42 dB amplitude)
    signal += (10 ** (-42 / 20.0)) * np.sin(2 * np.pi * 120.0 * t)
    
    # Peak 2: 8000 Hz system coil whine (-35 dB amplitude)
    signal += (10 ** (-35 / 20.0)) * np.sin(2 * np.pi * 8000.0 * t)
    
    # Peak 3: 19200 Hz ultrasonic noise source in Cyrinx band (-30 dB amplitude)
    signal += (10 ** (-30 / 20.0)) * np.sin(2 * np.pi * 19200.0 * t)
    
    return signal

def analyze_and_generate_notch_mask(audio_data, fs=48000, n_fft=1024, threshold_db=9.0):
    """
    Analyzes the audio data, computes the average power spectral density (PSD),
    identifies persistent narrow-band tones, and generates an OFDM notch mask.
    """
    # Compute FFT
    window = np.hanning(len(audio_data))
    fft_vals = np.fft.rfft(audio_data * window)
    psd = np.abs(fft_vals) ** 2
    # Normalize and convert to dB
    psd_db = 10 * np.log10(psd + 1e-12)
    
    # Frequency bins
    freqs = np.fft.rfftfreq(len(audio_data), 1/fs)
    
    # To detect narrow peaks, we compute a median-filtered background noise floor
    # We use a moving median of 51 bins to estimate the local background noise floor
    kernel_size = 51
    # Pad to handle edges
    padded = np.pad(psd_db, kernel_size // 2, mode='edge')
    noise_floor_est = np.array([
        np.median(padded[i : i + kernel_size]) for i in range(len(psd_db))
    ])
    
    # Find bins exceeding the local noise floor by the threshold
    exceeds_threshold = (psd_db - noise_floor_est) > threshold_db
    
    # Map to Cyrinx active subcarriers
    # Cyrinx OFDM operates in the ultrasonic band: e.g., 18000 Hz to 22000 Hz
    f_min, f_max = 18000.0, 22000.0
    cyrinx_bins = []
    cyrinx_freqs = []
    
    # We divide the 18-22 kHz band into 40 active subcarriers for this simulation
    n_subcarriers = 40
    for i in range(n_subcarriers):
        freq = f_min + (f_max - f_min) * (i / (n_subcarriers - 1))
        # Find closest FFT bin
        bin_idx = np.abs(freqs - freq).argmin()
        cyrinx_bins.append(bin_idx)
        cyrinx_freqs.append(freq)
        
    # Generate notch mask (1 for active, 0 for notched/disabled)
    notch_mask = np.ones(n_subcarriers, dtype=int)
    interfered_carriers = []
    
    # Check neighborhood around each subcarrier bin in the FFT
    # (Since OFDM subcarriers have some bandwidth, we check +/- 2 bins)
    for i, bin_idx in enumerate(cyrinx_bins):
        neighborhood = exceeds_threshold[max(0, bin_idx - 2) : min(len(exceeds_threshold), bin_idx + 3)]
        if np.any(neighborhood):
            notch_mask[i] = 0
            interfered_carriers.append((i, cyrinx_freqs[i], psd_db[bin_idx] - noise_floor_est[bin_idx]))
            
    return freqs, psd_db, noise_floor_est, cyrinx_freqs, notch_mask, interfered_carriers

def main():
    print("======================================================================")
    print("        🧹 POC 4: AMBIENT ROOM TONE SENSING & NOTCHER 🧹")
    print("======================================================================")
    print("\nThis script demonstrates how Cyrinx detects persistent narrow-band")
    print("interference (like HVAC, fans, or coil whine) in the environment by")
    print("listening ambiently and dynamically generating an OFDM subcarrier notch mask.\n")
    
    fs = 48000
    duration = 1.0
    
    # Try to import sounddevice for actual hardware measurement if available
    live_measured = False
    try:
        import sounddevice as sd
        print("🎤 Microphones detected! Attempting a 1-second live room-tone recording...")
        # Record 1 second of audio
        recording = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype='float32')
        sd.wait()
        audio_data = recording.flatten()
        live_measured = True
        print("✅ Live recording successful!")
    except Exception as e:
        print("⚠️  Live recording unavailable (missing 'sounddevice' or permission).")
        print("🔋 Falling back to high-fidelity synthetic MacBook/Pixel room-tone profile.")
        audio_data = generate_ambient_noise_profile(fs=fs, duration=duration)
        
    # Analyze the spectrum and generate the notch mask
    threshold_db = 8.0
    freqs, psd_db, noise_floor_est, cyrinx_freqs, notch_mask, interfered = analyze_and_generate_notch_mask(
        audio_data, fs=fs, threshold_db=threshold_db
    )
    
    print("\n🔍 SPECTRAL ANALYSIS RESULTS:")
    print("----------------------------------------------------------------------")
    print(f"  * Total Frequencies Scanned: {len(freqs)} bins (0 - {fs/2/1000.0:.1f} kHz)")
    print(f"  * Detected Narrow-band Interferers in Cyrinx Band (18 - 22 kHz):")
    if not interfered:
        print("    - None (Clean acoustic environment!)")
    else:
        for idx, freq, excess in interfered:
            print(f"    - Subcarrier #{idx:02d} at {freq/1000.0:6.3f} kHz: Peak of +{excess:.1f} dB above local floor!")
            
    print("\n🧱 DYNAMIC OFDM SUBCARRIER NOTCH MASK:")
    print("----------------------------------------------------------------------")
    
    # ASCII Visualizer for the Cyrinx Band (18 kHz to 22 kHz)
    print("   Carrier # | Freq (kHz) | Notch Mask | PSD vs Local Floor")
    print("   --------------------------------------------------------")
    for i in range(len(cyrinx_freqs)):
        freq = cyrinx_freqs[i]
        bin_idx = np.abs(freqs - freq).argmin()
        psd_val = psd_db[bin_idx]
        floor_val = noise_floor_est[bin_idx]
        diff = psd_val - floor_val
        
        status = "ACTIVE [1]" if notch_mask[i] == 1 else "NOTCHED [0]"
        symbol = "✔" if notch_mask[i] == 1 else "❌"
        
        # Build mini-bar for visual
        bar_len = max(0, int((diff + 5) / 2))
        bar = ("#" * bar_len) if notch_mask[i] == 0 else ("." * min(5, bar_len))
        
        print(f"     #{i:02d}     |   {freq/1000.0:5.2f}    |  {symbol} {status:<10} | {diff:+6.1f} dB {bar}")
        
    total_active = np.sum(notch_mask)
    print("----------------------------------------------------------------------")
    print(f"📊 SUMMARY: Active Carriers: {total_active} / {len(notch_mask)} | Notched Carriers: {len(notch_mask) - total_active}")
    print("----------------------------------------------------------------------")
    print("\n💡 PROOF OF CONCEPT CONCLUSION:")
    print("By scanning the room tone and applying a median-filtered baseline, Cyrinx")
    print("can identify narrow spikes, notch them out perfectly, and avoid transmitting")
    print("data on heavily corrupted channels, protecting transmission from block errors.")
    print("======================================================================\n")

if __name__ == "__main__":
    main()
