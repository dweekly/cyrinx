import wave
import numpy as np

def main():
    filepath = "artifacts/mac_loopback_rec.wav"
    import os
    if not os.path.exists(filepath):
        print(f"Error: {filepath} does not exist.")
        return
        
    with wave.open(filepath, "r") as wf:
        n_frames = wf.getnframes()
        sample_rate = wf.getframerate()
        raw_data = wf.readframes(n_frames)
        
    samples = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0
    print(f"File: {filepath}")
    print(f"Sample Rate: {sample_rate} Hz")
    print(f"Total Samples: {len(samples)} ({len(samples)/sample_rate:.3f}s)")
    
    # Analyze in 100ms sliding windows
    win_size = int(sample_rate * 0.1) # 100ms
    step_size = int(sample_rate * 0.05) # 50ms overlap
    
    print("\nTIMELINE PEAK FREQUENCY TRACKING:")
    print("---------------------------------------")
    print("  TIME (s)   |  PEAK FREQ (Hz)  |  RMS")
    print("---------------------------------------")
    
    for i in range(0, len(samples) - win_size, step_size):
        chunk = samples[i : i + win_size]
        rms = np.sqrt(np.mean(chunk**2))
        
        # Run FFT
        fft_vals = np.abs(np.fft.rfft(chunk * np.hanning(len(chunk))))
        freqs = np.fft.rfftfreq(len(chunk), d=1.0/sample_rate)
        
        peak_idx = np.argmax(fft_vals)
        peak_freq = freqs[peak_idx]
        
        # Only print windows that have significant energy above silence
        if rms > 0.002:
            print(f"   {i/sample_rate:5.2f}s    |    {peak_freq:8.1f} Hz  |  {rms:.4f}")

if __name__ == "__main__":
    main()
