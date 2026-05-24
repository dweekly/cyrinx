import wave
import struct
import math
import subprocess
import time
import os
import sys

def main():
    print("======================================================================")
    print("         🔊 CYRINX SEISMIC TAPPING MODEM: PROOF OF CONCEPT 🔊")
    print("======================================================================")
    print("\nThis script demonstrates how physical vibrations from the Pixel's")
    print("haptic subsystems conduct directly through a shared solid surface")
    print("(chassis-to-chassis or same desk) and are captured by the MacBook's")
    print("internal microphone as seismic audio input.\n")
    print("👉 IMPORTANT ACTION REQUIRED:")
    print("Place your Pixel 7a phone resting directly on the Mac's aluminum frame")
    print("(next to the trackpad or keyboard) or on the exact same hard wooden table.")
    print("Keep the environment as quiet as possible during this test.\n")
    
    input("Press ENTER when the phone is positioned to begin the benchmark... ")
    
    print("\n[1/3] Preparing recording pipeline (FFmpeg AVFoundation)...")
    wav_path = "artifacts/haptic_proof.wav"
    if os.path.exists(wav_path):
        try:
            os.remove(wav_path)
        except Exception:
            pass

    # Launch FFmpeg recording asynchronously (6 seconds)
    print("[2/3] Recording started. Keep everything static...")
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "avfoundation", "-i", ":2", "-t", "6", wav_path
    ]
    ffmpeg_process = subprocess.Popen(ffmpeg_cmd)
    
    # Wait 1.5 seconds for the baseline, then trigger a robust haptic pattern on the Pixel
    time.sleep(1.5)
    print("⚡ TRIGGERING PIXEL HAPTICS NOW (2000ms vibration) ⚡")
    
    adb_cmd = [
        "adb", "shell", "am", "start", "-n", "com.dweekly.cyrinxhil/com.dweekly.cyrinxhil.MainActivity",
        "--es", "cmd", "vibrate", "--ei", "duration_ms", "2000"
    ]
    try:
        subprocess.run(adb_cmd, capture_output=True, text=True)
    except Exception as e:
        print(f"Warning: Failed to trigger ADB vibration: {e}")
    
    # Wait for the recording to finish
    ffmpeg_process.wait()
    print("[3/3] Recording complete! Analyzing data...\n")
    
    if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1000:
        print("Error: Audio file was not recorded successfully.")
        return
        
    # Read the WAV file using standard python library
    try:
        with wave.open(wav_path, 'r') as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            
            if sampwidth != 2 or n_channels != 1:
                print(f"Unsupported format: channels={n_channels}, width={sampwidth}. Expected 16-bit Mono.")
                return
                
            raw_data = wf.readframes(n_frames)
            
        samples = struct.unpack(f"<{n_frames}h", raw_data)
    except Exception as e:
        print(f"Error parsing WAV file: {e}")
        return

    # Process samples into 100ms blocks
    block_size = int(framerate * 0.1) # 100ms
    num_blocks = int(len(samples) / block_size)
    
    rms_levels = []
    max_rms = 0.0001
    
    for i in range(num_blocks):
        block_samples = samples[i * block_size : (i + 1) * block_size]
        # Calculate Root Mean Square (RMS) normalized to 0.0 ... 1.0 range
        sum_sq = sum((s / 32768.0) ** 2 for s in block_samples)
        rms = math.sqrt(sum_sq / len(block_samples))
        rms_levels.append(rms)
        if rms > max_rms:
            max_rms = rms

    # Render a beautiful ASCII visualization of the amplitude envelope
    print("----------------------------------------------------------------------")
    print("   TIME   |  RMS LEVEL  |  SEISMIC AMPLITUDE ENVELOPE")
    print("----------------------------------------------------------------------")
    
    vibration_detected = False
    baseline_rms = sum(rms_levels[:10]) / 10.0 if len(rms_levels) >= 10 else 0.001
    
    for idx, rms in enumerate(rms_levels):
        t_sec = idx * 0.1
        # Normalize bar width
        bar_width = int((rms / max_rms) * 50)
        bar = "█" * bar_width
        
        # Highlight when vibration threshold is crossed
        marker = ""
        # If amplitude rises significantly above baseline, flag it as haptic coupling
        if rms > baseline_rms * 2.5 and t_sec >= 1.2 and t_sec <= 4.0:
            marker = "  [⚡ Haptic Tap Detected!]"
            vibration_detected = True
            
        print(f"  {t_sec:3.1f}s   |   {rms:.4f}    |  {bar}{marker}")
        
    print("----------------------------------------------------------------------")
    if vibration_detected:
        print("\n🎉 SUCCESS: The MacBook Pro's microphone successfully detected the")
        print("structural mechanical coupling of the Pixel's haptic motor!")
        print("This proves that physical vibration can serve as a covert seismic")
        print("carrier wave when devices share a resonant boundary.")
    else:
        print("\nNotice: The haptic vibration was not clearly distinguished from")
        print("ambient room noise. Try placing the phone closer to the microphone")
        print("grill and keeping the room completely silent.")
        
if __name__ == "__main__":
    main()
