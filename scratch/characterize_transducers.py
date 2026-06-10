import wave
import struct
import math
import subprocess
import time
import os
import sys
import numpy as np

def generate_sweep_wav(filepath, start_hz=1000.0, end_hz=22000.0, duration_sec=8.0, sample_rate=48000, amplitude=0.35):
    """
    Generates a linear frequency sweep from start_hz to end_hz, saved as a 16-bit Mono WAV file.
    Includes a 1-second silence at the beginning and end to allow recording synchronization.
    """
    print(f"[Generate] Generating linear sweep {start_hz/1000.0:.1f} kHz to {end_hz/1000.0:.1f} kHz (dur={duration_sec}s)...")
    
    total_frames = int(sample_rate * (duration_sec + 2.0))  # 1s pre-silence, 1s post-silence
    pcm_data = bytearray()
    
    # 1. Pre-silence (1 second)
    pre_silence_count = sample_rate
    for _ in range(pre_silence_count):
        pcm_data.extend(struct.pack("<h", 0))
        
    # 2. Linear sweep
    sweep_count = int(sample_rate * duration_sec)
    f_start = float(start_hz)
    f_end = float(end_hz)
    T = float(duration_sec)
    
    for i in range(sweep_count):
        t = float(i) / float(sample_rate)
        # Phase for linear sweep is the integral of frequency:
        # phi(t) = 2 * pi * (f_start * t + 0.5 * (f_end - f_start) * t^2 / T)
        phase = 2.0 * math.pi * (f_start * t + 0.5 * (f_end - f_start) * (t * t) / T)
        
        # Apply 50ms smooth ramp at start/end to prevent transients/clicks
        ramp_time = 0.05
        envelope = 1.0
        if t < ramp_time:
            envelope = t / ramp_time
        elif t > T - ramp_time:
            envelope = (T - t) / ramp_time
            
        sample = amplitude * envelope * math.sin(phase)
        clamped = max(-1.0, min(1.0, sample))
        val = int(clamped * 32767.0)
        pcm_data.extend(struct.pack("<h", val))
        
    # 3. Post-silence (1 second)
    post_silence_count = sample_rate
    for _ in range(post_silence_count):
        pcm_data.extend(struct.pack("<h", 0))
        
    # Write WAV file
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
        
    print(f"[Generate] Sweep saved to: {filepath} ({len(pcm_data)} bytes)")

def analyze_frequency_response(samples, sample_rate, start_hz=1000.0, end_hz=22000.0, duration_sec=8.0, pre_silence_sec=1.0):
    """
    Analyzes a captured sweep by tracking the sweep frequency dynamically using a sliding STFT.
    This is extremely robust against clock drift, latencies, and parameter mismatches.
    """
    win_size = int(sample_rate * 0.1)     # 100ms window
    step_size = int(sample_rate * 0.02)   # 20ms step size (high overlap for high resolution)
    
    # We will collect (freq, magnitude) pairs from all active sweep windows
    freq_data = []
    
    for i in range(0, len(samples) - win_size, step_size):
        chunk = samples[i : i + win_size]
        rms = np.sqrt(np.mean(chunk**2))
        
        # We only consider windows that are clearly active sweep signals, not silence
        if rms < 0.001:
            continue
            
        # Compute FFT with Hanning window to prevent leakage
        hanning = np.hanning(len(chunk))
        windowed = chunk * hanning
        fft_vals = np.abs(np.fft.rfft(windowed))
        freqs = np.fft.rfftfreq(len(chunk), d=1.0/sample_rate)
        
        peak_idx = np.argmax(fft_vals)
        peak_freq = freqs[peak_idx]
        
        # The peak magnitude in the FFT bin
        # Normalize by window length and Hanning coherent gain (0.5)
        peak_mag = fft_vals[peak_idx] / (len(chunk) * 0.5)
        
        # We only care about frequencies in our sweep range
        if start_hz - 500.0 <= peak_freq <= end_hz + 500.0:
            freq_data.append((peak_freq, peak_mag))
            
    if not freq_data:
        print("❌ Error: No sweep signal detected above the noise floor.")
        return [(f, -100.0) for f in np.arange(start_hz, end_hz + 1.0, 1000.0)]
        
    # Map to discrete target frequencies (1k, 2k, ..., 22k)
    target_freqs = np.arange(start_hz, end_hz + 1.0, 1000.0)
    response_db = []
    
    for f in target_freqs:
        # Find all tracked windows where the peak frequency is within 500 Hz of target
        mags_in_band = [mag for freq, mag in freq_data if abs(freq - f) <= 500.0]
        
        if mags_in_band:
            # Take the maximum magnitude in this band
            best_mag = np.max(mags_in_band)
            db = 20.0 * math.log10(max(1e-6, best_mag))
        else:
            db = -100.0
            
        response_db.append((f, db))
        
    # Normalize dB so that the peak response is 0 dB
    max_db = max(r[1] for r in response_db)
    if max_db < -99.0:
        max_db = 0.0
    normalized_response = [(f, db - max_db) for f, db in response_db]
    
    return normalized_response


def render_ascii_plot(response, title):
    """
    Renders a stunning, professional ASCII chart of the frequency response curves.
    """
    print("\n" + "="*70)
    print(f" 📊 INSTANTANEOUS FREQUENCY RESPONSE PROFILE: {title}")
    print("="*70)
    print("  FREQUENCY  |  RELATIVE RESPONSE (dB)  |  SPECTRUM VISUALIZATION")
    print("-"*70)
    
    for freq, db in response:
        # Standard dB range: -30 dB to 0 dB
        clamped_db = max(-30.0, min(0.0, db))
        # Map -30..0 to 0..30 bars
        bar_count = int((30.0 + clamped_db) * 1.5)
        bar = "█" * bar_count
        
        status = ""
        if db < -18.0:
            status = " [⚠️ SEVERE ROLL-OFF]"
        elif db < -6.0:
            status = " [📉 ATTENUATED]"
        else:
            status = " [🟢 FLAT]"
            
        print(f"  {freq/1000.0:4.1f} kHz  |     {db:6.1f} dB         |  {bar:<45}{status}")
        
    print("-"*70)
    print("💡 ANALYSIS KEY:")
    print("  * 🟢 FLAT ([0 to -6 dB]): Ideal transmission zone for high-rate OFDM symbols.")
    print("  * 📉 ATTENUATED ([-6 to -18 dB]): Demodulatable with +12dB Tx Pre-emphasis.")
    print("  * ⚠️ SEVERE ROLL-OFF ([< -18 dB]): Highly susceptible to noise. Unsafe for data.")
    print("="*70 + "\n")

def run_local_mac_loopback(sweep_path):
    """
    Performs loopback calibration directly on the MacBook Pro.
    """
    print("\n🔊 MODE 1: CHARACTERIZING MACBOOK PRO TRANSDUCERS (LOCAL LOOPBACK)")
    print("Keep the environment quiet. Playing sweep on Mac speakers and recording via Mac mic...")
    
    rec_path = "artifacts/mac_loopback_rec.wav"
    if os.path.exists(rec_path):
        os.remove(rec_path)
        
    # Start FFmpeg recording asynchronously (12 seconds)
    print("[MacLoopback] Initiating AVFoundation microphone capture...")
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "avfoundation", "-i", ":2", "-t", "11", rec_path
    ]
    ffmpeg_process = subprocess.Popen(ffmpeg_cmd)
    
    # Wait for FFmpeg to spin up
    time.sleep(1.0)
    
    # Play the sweep on macOS speakers
    print("[MacLoopback] Playing calibrated sweep...")
    subprocess.run(["afplay", sweep_path])
    
    # Wait for recording to complete
    ffmpeg_process.wait()
    print("[MacLoopback] Recording complete! Processing loopback WAV data...")
    
    if not os.path.exists(rec_path):
        print("❌ Error: Loopback recording failed.")
        return
        
    # Read the WAV file
    with wave.open(rec_path, "r") as wf:
        n_frames = wf.getnframes()
        framerate = wf.getframerate()
        raw_data = wf.readframes(n_frames)
        samples = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0
        
    response = analyze_frequency_response(samples, framerate)
    render_ascii_plot(response, "MacBook Pro Native Loopback (Internal Mic ➔ Internal Speakers)")

def run_android_to_mac_ota(sweep_path):
    """
    Measures the Android (Pixel 7a) speaker as captured by the MacBook Pro mic over-the-air.
    """
    print("\n📱 MODE 2: CHARACTERIZING PIXEL 7A SPEAKER (ANDROID ➔ MACBOOK MIC)")
    print("Position the Pixel 7a Bottom Speaker immediately next to the MacBook Pro mic (5-15 cm).")
    input("Press ENTER when positioned and ready... ")
    
    rec_path = "artifacts/android_to_mac_ota.wav"
    if os.path.exists(rec_path):
        os.remove(rec_path)
        
    # Push the sweep file to the Android device
    print("[CrossCalibration] Pushing sweep waveform to Pixel 7a storage...")
    subprocess.run(["adb", "push", sweep_path, "/sdcard/Download/sweep.wav"], capture_output=True)
    
    # Ensure MainActivity is launched and focused
    print("[CrossCalibration] Starting HIL Android Companion app...")
    subprocess.run(["adb", "shell", "am", "start", "-n", "com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity"], capture_output=True)
    time.sleep(1.0)
    
    # Start FFmpeg recording asynchronously (12 seconds)
    print("[CrossCalibration] Initiating AVFoundation microphone capture on MacBook Pro...")
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "avfoundation", "-i", ":2", "-t", "11", rec_path
    ]
    ffmpeg_process = subprocess.Popen(ffmpeg_cmd)
    time.sleep(1.5)
    
    # Play the sweep on the Android device using our custom 'sweep' automation command!
    print("[CrossCalibration] Triggering local static AudioTrack playback on Pixel 7a via ADB...")
    adb_cmd = [
        "adb", "shell", "am", "start", "-n", "com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity",
        "--es", "cmd", "sweep", "--ef", "start_hz", "1000", "--ef", "end_hz", "22000", "--ef", "duration_sec", "8.0", "--ef", "amplitude", "0.35"
    ]
    subprocess.run(adb_cmd, capture_output=True)
    
    # Wait for recording to complete
    ffmpeg_process.wait()
    print("[CrossCalibration] Recording complete! Pulling and processing WAV data...")
    
    if not os.path.exists(rec_path):
        print("❌ Error: OTA recording failed.")
        return
        
    # Read the WAV file
    with wave.open(rec_path, "r") as wf:
        n_frames = wf.getnframes()
        framerate = wf.getframerate()
        raw_data = wf.readframes(n_frames)
        samples = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0
        
    response = analyze_frequency_response(samples, framerate)
    render_ascii_plot(response, "Pixel 7a Bottom Speaker ➔ MacBook Pro Mic (OTA)")

def run_android_local_loopback():
    """
    Measures the Android (Pixel 7a) speaker as captured by the Android's own mic (local loopback).
    """
    print("\n📱 MODE 3: CHARACTERIZING PIXEL 7A NATIVE LOOPBACK (OWN MIC ➔ OWN SPEAKER)")
    print("This will execute synchronous hardware capture and playback directly on the mobile companion.")
    input("Press ENTER when ready to start the mobile loopback sweep... ")
    
    # Ensure HIL companion is running
    print("[AndroidLoopback] Starting HIL Android Companion app...")
    subprocess.run(["adb", "shell", "am", "start", "-n", "com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity"], capture_output=True)
    time.sleep(1.0)
    
    # 1. Start raw background capture to a file on Android internal cache directory
    capture_bin = "/data/data/com.dweekly.cyrinxhil/cache/capture.bin"
    tmp_bin = "/data/local/tmp/capture.bin"
    print("[AndroidLoopback] Starting raw microphone capture...")
    subprocess.run([
        "adb", "shell", "am", "start", "-n", "com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity",
        "--es", "cmd", "raw_start", "--es", "capture_path", capture_bin, "--ei", "sample_rate_hz", "48000", "--es", "raw_codec", "basic"
    ], capture_output=True)
    
    time.sleep(1.0)
    
    # 2. Play the sweep on the phone
    print("[AndroidLoopback] Playing loopback sweep via static AudioTrack...")
    subprocess.run([
        "adb", "shell", "am", "start", "-n", "com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity",
        "--es", "cmd", "sweep", "--ef", "start_hz", "1000", "--ef", "end_hz", "22000", "--ef", "duration_sec", "8.0", "--ef", "amplitude", "0.35"
    ], capture_output=True)
    
    # Wait for the 8s sweep + 1s padding to complete
    print("[AndroidLoopback] Recording in progress... Keep the environment silent.")
    time.sleep(9.5)
    
    # 3. Stop capture
    print("[AndroidLoopback] Stopping capture backend...")
    subprocess.run([
        "adb", "shell", "am", "start", "-n", "com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity",
        "--es", "cmd", "raw_stop"
    ], capture_output=True)
    
    time.sleep(1.5) # Allow file flush to storage
    
    # 4. Pull capture file to Mac
    local_bin = "artifacts/android_loopback_capture.bin"
    if os.path.exists(local_bin):
        os.remove(local_bin)
        
    print("[AndroidLoopback] Exporting sandboxed capture file using adb run-as...")
    # Copy from sandboxed app space to public local tmp using app privileges
    subprocess.run(["adb", "shell", "run-as", "com.dweekly.cyrinxhil", "cp", "cache/capture.bin", tmp_bin], capture_output=True)
    
    print("[AndroidLoopback] Pulling exported capture binary to Mac...")
    subprocess.run(["adb", "pull", tmp_bin, local_bin], capture_output=True)
    
    # Clean up phone temp files
    subprocess.run(["adb", "shell", "rm", tmp_bin], capture_output=True)
    subprocess.run(["adb", "shell", "run-as", "com.dweekly.cyrinxhil", "rm", "cache/capture.bin"], capture_output=True)
    
    if not os.path.exists(local_bin) or os.path.getsize(local_bin) < 4000:
        print("❌ Error: Local capture binary was not retrieved successfully.")
        return
        
    # Read the float32 binary file
    print("[AndroidLoopback] Parsing captured Float32LE binary stream...")
    with open(local_bin, "rb") as f:
        raw_bytes = f.read()
    
    sample_count = len(raw_bytes) // 4
    samples = np.frombuffer(raw_bytes, dtype=np.float32)
    
    response = analyze_frequency_response(samples, 48000)
    render_ascii_plot(response, "Pixel 7a Native Loopback (Internal Mic ➔ Internal Speaker)")

def main():
    import sys
    print("======================================================================")
    print("       🎙️  CYRINX: HARDWARE FREQUENCY RESPONSE CHARACTERIZATION 🎙️")
    print("======================================================================")
    print("\nThis tool runs programmatic physical frequency sweeps to accurately")
    print("characterize the frequency response of consumer microphones and speakers,")
    print("helping resolve physical transmission limits and define optimal bands.\n")
    
    os.makedirs("artifacts", exist_ok=True)
    sweep_path = "artifacts/sweep_1k_24k.wav"
    
    # Generate the sweep file if missing
    if not os.path.exists(sweep_path):
        generate_sweep_wav(sweep_path)
        
    if len(sys.argv) > 1:
        arg = sys.argv[1].strip()
        if arg == "--mac-loopback":
            run_local_mac_loopback(sweep_path)
            return
        elif arg == "--android-ota":
            run_android_to_mac_ota(sweep_path)
            return
        elif arg == "--android-loopback":
            run_android_local_loopback()
            return
        elif arg == "--analyze-file" and len(sys.argv) > 3:
            # Usage: --analyze-file <wav_path> <title>
            wav_path = sys.argv[2]
            title = sys.argv[3]
            if not os.path.exists(wav_path):
                print(f"❌ Error: {wav_path} does not exist.")
                return
            with wave.open(wav_path, "r") as wf:
                n_frames = wf.getnframes()
                framerate = wf.getframerate()
                raw_data = wf.readframes(n_frames)
                samples = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0
            response = analyze_frequency_response(samples, framerate)
            render_ascii_plot(response, title)
            return
        elif arg == "--analyze-bin" and len(sys.argv) > 3:
            # Usage: --analyze-bin <bin_path> <title>
            bin_path = sys.argv[2]
            title = sys.argv[3]
            if not os.path.exists(bin_path):
                print(f"❌ Error: {bin_path} does not exist.")
                return
            with open(bin_path, "rb") as f:
                raw_bytes = f.read()
            samples = np.frombuffer(raw_bytes, dtype=np.float32)
            response = analyze_frequency_response(samples, 48000)
            render_ascii_plot(response, title)
            return
        else:
            print(f"Unknown argument: {arg}")
            print("Supported arguments: --mac-loopback, --android-ota, --android-loopback, --analyze-file <path> <title>, --analyze-bin <path> <title>")
            return

    # Deploy the latest compiled HIL app to Pixel 7a via ADB if run interactively
    print("[Deploy] Re-installing latest compiled HIL app to Pixel 7a via ADB...")
    install_res = subprocess.run([
        "adb", "install", "-r", "Apps/HIL/android/app/build/outputs/apk/debug/app-debug.apk"
    ], capture_output=True, text=True)
    if "Success" in install_res.stdout or "Success" in install_res.stderr:
        print("[Deploy] HIL companion app successfully updated on Pixel 7a.")
    else:
        print(f"⚠️  Warning: Deployment failed. Make sure device is connected and unlocked:\n{install_res.stderr}")
        
    while True:
        print("\nSELECT TRANSDUCER CHARACTERIZATION SCENARIO:")
        print("  [1] MacBook Pro Speakers + Mic (Mac Loopback)")
        print("  [2] Pixel 7a Speaker ➔ MacBook Pro Mic (Cross-OTA)")
        print("  [3] Pixel 7a Speakers + Mic (Android Loopback)")
        print("  [4] Exit")
        
        try:
            choice = input("\nEnter choice (1-4): ").strip()
            if choice == "1":
                run_local_mac_loopback(sweep_path)
            elif choice == "2":
                run_android_to_mac_ota(sweep_path)
            elif choice == "3":
                run_android_local_loopback()
            elif choice == "4":
                print("\nExiting. Thank you!")
                break
            else:
                print("Invalid choice. Please enter a number between 1 and 4.")
        except KeyboardInterrupt:
            print("\nExiting. Thank you!")
            break
        except Exception as e:
            print(f"❌ Error executing scenario: {e}")

if __name__ == "__main__":
    main()
