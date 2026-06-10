import numpy as np

def inspect_bin():
    bin_path = "artifacts/android_loopback_capture.bin"
    import os
    if not os.path.exists(bin_path):
        print(f"Error: {bin_path} does not exist.")
        return
        
    with open(bin_path, "rb") as f:
        raw_bytes = f.read()
    samples = np.frombuffer(raw_bytes, dtype=np.float32)
    sample_rate = 48000
    
    print(f"Loaded {len(samples)} samples ({len(samples)/sample_rate:.3f}s)")
    
    win_size = int(sample_rate * 0.1) # 100ms
    step_size = int(sample_rate * 0.05) # 50ms
    
    print("\nTIMELINE PEAK FREQUENCY TRACKING:")
    print("---------------------------------------")
    print("  TIME (s)   |  PEAK FREQ (Hz)  |  RMS")
    print("---------------------------------------")
    
    for i in range(0, len(samples) - win_size, step_size):
        chunk = samples[i : i + win_size]
        rms = np.sqrt(np.mean(chunk**2))
        
        # Run FFT
        hanning = np.hanning(len(chunk))
        fft_vals = np.abs(np.fft.rfft(chunk * hanning))
        freqs = np.fft.rfftfreq(len(chunk), d=1.0/sample_rate)
        
        peak_idx = np.argmax(fft_vals)
        peak_freq = freqs[peak_idx]
        
        if rms > 0.002:
            print(f"   {i/sample_rate:5.2f}s    |    {peak_freq:8.1f} Hz  |  {rms:.6f}")

if __name__ == "__main__":
    inspect_bin()
