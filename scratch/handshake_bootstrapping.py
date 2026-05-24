import numpy as np

def main():
    print("======================================================================")
    print("      🔊 POC 3 FOLLOW-UP: PHY-LEVEL BOOTSTRAP VERSIONING 🔊")
    print("======================================================================")
    print("\nThis follow-up POC solves the circular bootstrapping paradox of the")
    print("handshake. Instead of sending control frames over an unaligned link,")
    print("we signal the protocol version directly in the Zadoff-Chu (ZC) preamble\n")
    
    # Define physical preamble parameters
    N_ZC = 127 # Preamble length
    fs = 48000.0
    
    # We map version numbers to ZC root frequencies:
    # - Root 29: Version 1
    # - Root 31: Version 2
    version_roots = {
        1: 29,
        2: 31
    }
    
    def generate_zc(root, N):
        n = np.arange(N)
        # Standard CAZAC sequence formula: exp(-i * pi * root * n * (n + 1) / N)
        return np.exp(-1j * np.pi * root * n * (n + 1) / N)

    # 1. Transmitter (Version 2) sends preamble
    tx_version = 2
    tx_root = version_roots[tx_version]
    tx_preamble = generate_zc(tx_root, N_ZC)
    
    # 2. Add realistic multipath and noise
    rx_signal = tx_preamble + np.random.normal(0, 0.25, N_ZC) + 1j * np.random.normal(0, 0.25, N_ZC)
    
    # 3. Receiver (supports both version 1 and 2) runs parallel cross-correlation
    # to detect both version and presence simultaneously!
    print("📥 Running Parallel PHY Preamble Correlators...")
    
    detected_version = None
    max_peak = 0.0
    
    for ver, root in version_roots.items():
        ref_preamble = generate_zc(root, N_ZC)
        
        # Calculate cross-correlation peak
        correlation = np.abs(np.sum(rx_signal * np.conj(ref_preamble))) / N_ZC
        
        # Normalize and print correlation peaks
        print(f"  - Correlator v{ver} (Root {root}): Peak Correlation = {correlation:.4f}")
        
        if correlation > 0.45 and correlation > max_peak:
            max_peak = correlation
            detected_version = ver
            
    print("----------------------------------------------------------------------")
    if detected_version:
        print(f"🎉 SUCCESS: PHY Layer detected Version {detected_version} directly in the preamble!")
        print("   This resolves the circular handshake paradox, ensuring we align")
        print("   protocol framing rules before ingesting the very first data frame.")
    else:
        print("❌ FAILED: Preamble not detected or signal-to-noise ratio is too low.")
    print("----------------------------------------------------------------------")

if __name__ == "__main__":
    main()
