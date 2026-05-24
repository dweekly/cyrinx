import wave
import struct
import math
import os

def main():
    wav_path = "artifacts/haptic_proof.wav"
    try:
        with wave.open(wav_path, 'r') as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            raw_data = wf.readframes(n_frames)
            samples = struct.unpack(f"<{n_frames}h", raw_data)
    except Exception as e:
        print(f"Error opening wave: {e}")
        return

    # Convert to float and extract blocks
    float_samples = [s / 32768.0 for s in samples]
    
    # 1. Baseline Block (0.5s to 1.0s)
    baseline_start = int(0.5 * framerate)
    baseline_end = int(1.0 * framerate)
    baseline_block = float_samples[baseline_start:baseline_end]
    
    # 2. Haptic Block (1.5s to 2.0s)
    haptic_start = int(1.5 * framerate)
    haptic_end = int(2.0 * framerate)
    haptic_block = float_samples[haptic_start:haptic_end]
    
    # Define filters:
    # Low-pass filter (moving average of M samples to capture seismic low-frequencies)
    # High-pass filter (first-difference to capture acoustic rattle/buzz)
    M = 48 # cuts off roughly at 1000 Hz at 48kHz
    
    def apply_filters(block):
        low_pass = []
        high_pass = []
        
        # Moving average
        window_sum = sum(block[:M])
        low_pass.append(window_sum / M)
        for idx in range(M, len(block)):
            window_sum = window_sum - block[idx - M] + block[idx]
            low_pass.append(window_sum / M)
            
        # First difference
        for idx in range(1, len(block)):
            high_pass.append(block[idx] - block[idx - 1])
            
        # Calculate block energies (sum of squares)
        low_energy = sum(s ** 2 for s in low_pass) / len(low_pass)
        high_energy = sum(s ** 2 for s in high_pass) / len(high_pass)
        
        return low_energy, high_energy

    base_low, base_high = apply_filters(baseline_block)
    hap_low, hap_high = apply_filters(haptic_block)
    
    print("======================================================================")
    print("   🔊 PURE PYTHON SEISMIC FILTERS ANALYSIS (NO DEPENDENCIES) 🔊")
    print("======================================================================")
    print(f"  BAND                  | BASELINE ENERGY | HAPTIC ENERGY | FOLD INCREASE")
    print("----------------------------------------------------------------------")
    print(f"  Low-Pass (Seismic)    |   {base_low:e}   |  {hap_low:e}  |  {hap_low/base_low:6.1f}x")
    print(f"  High-Pass (Acoustic)  |   {base_high:e}   |  {hap_high:e}  |  {hap_high/base_high:6.1f}x")
    print("----------------------------------------------------------------------")
    
    print("\n💡 SCIENTIFIC CRITIQUE & CONCLUSION:")
    # If the relative increase of high-pass is massive compared to low-pass, the mic is picking up airborne buzz
    ratio_low = hap_low / base_low
    ratio_high = hap_high / base_high
    
    print(f"  * Seismic low-frequency energy increased by: {ratio_low:.1f}x")
    print(f"  * Airborne high-frequency energy increased by: {ratio_high:.1f}x\n")
    
    if ratio_low > ratio_high:
        print("✅ FINDING: The seismic low-frequency component grew MORE than the high-frequency")
        print("   airborne buzz. This physically proves that structural mechanical conduction")
        print("   is the dominant coupling mechanism!")
    else:
        print("⚠️ CRITIQUE: The high-frequency airborne rattle grew MORE than the low-frequency.")
        print("   This proves the original finding was FLAWED and OVERSTATED: the microphone")
        print("   is primarily capturing the airborne acoustic buzz of the haptic motor,")
        print("   rather than a pure structural seismic waveguide.")

if __name__ == "__main__":
    main()
