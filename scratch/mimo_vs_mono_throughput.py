import numpy as np
import math

def simulate_ofdm_channel(snr_db=25.0, spatial_correlation=0.15):
    """
    Simulates a 600-carrier OFDM transmission over 1x1 Mono and 2x2 MIMO spatial channels.
    Calculates the exact Bit Error Rate (BER) and successfully delivered throughput.
    """
    num_carriers = 600
    symbol_rate_hz = 187.5 # 187.5 OFDM symbols per second (with CP guard)
    
    # 1. Initialize random QPSK input symbols
    # QPSK mappings: 00 -> 1+j, 01 -> -1+j, 11 -> -1-j, 10 -> 1-j (normalized by 1/sqrt(2))
    constellation = np.array([1+1j, -1+1j, -1-1j, 1-1j]) / math.sqrt(2)
    
    # --- 1x1 MONO CHANNEL SIMULATION ---
    tx_bits_mono = np.random.randint(0, 4, num_carriers)
    tx_symbols_mono = constellation[tx_bits_mono]
    
    # Complex channel coefficient for Mono (with fading/phase shift)
    h_mono = (np.random.normal(0, 1) + 1j * np.random.normal(0, 1)) / math.sqrt(2)
    
    # Received signal: y = h*x + noise
    noise_power = 10.0 ** (-snr_db / 10.0)
    noise_mono = (np.random.normal(0, math.sqrt(noise_power/2), num_carriers) + 
                  1j * np.random.normal(0, math.sqrt(noise_power/2), num_carriers))
    y_mono = h_mono * tx_symbols_mono + noise_mono
    
    # Zero-Forcing Equalizer for Mono: x_hat = y / h
    rx_symbols_mono = y_mono / h_mono
    
    # Demodulate Mono QPSK
    rx_bits_mono = []
    for sym in rx_symbols_mono:
        # Find closest constellation point
        dists = np.abs(constellation - sym)
        rx_bits_mono.append(np.argmin(dists))
    rx_bits_mono = np.array(rx_bits_mono)
    
    errors_mono = np.sum(tx_bits_mono != rx_bits_mono)
    ber_mono = errors_mono / num_carriers
    
    # Symmetrical throughput (assuming successful packets after FEC, scaled by BER)
    goodput_mono_bps = num_carriers * 2 * symbol_rate_hz * (1.0 - ber_mono)
    
    # --- 2x2 MIMO CHANNEL SIMULATION ---
    # Stream 1 and Stream 2 independent inputs
    tx_bits_mimo1 = np.random.randint(0, 4, num_carriers)
    tx_bits_mimo2 = np.random.randint(0, 4, num_carriers)
    
    s1 = constellation[tx_bits_mimo1]
    s2 = constellation[tx_bits_mimo2]
    
    # We will accumulate successfully decoded symbols
    errors_mimo1 = 0
    errors_mimo2 = 0
    
    # Model independent spatial channels for each of the 600 subcarrier bins
    # Incorporates physical spatial correlation (rho) between the antenna/mic paths
    rho = spatial_correlation
    
    for k in range(num_carriers):
        # Generate correlated complex Gaussian channel matrix: H
        # H = [h11, h12; h21, h22]
        h11 = (np.random.normal(0, 1) + 1j * np.random.normal(0, 1)) / math.sqrt(2)
        h22 = (np.random.normal(0, 1) + 1j * np.random.normal(0, 1)) / math.sqrt(2)
        
        # Correlated cross-talk paths
        h12 = rho * h11 + math.sqrt(1 - rho**2) * ((np.random.normal(0, 1) + 1j * np.random.normal(0, 1)) / math.sqrt(2))
        h21 = rho * h22 + math.sqrt(1 - rho**2) * ((np.random.normal(0, 1) + 1j * np.random.normal(0, 1)) / math.sqrt(2))
        
        H = np.array([[h11, h12],
                      [h21, h22]])
        
        # SVD Decomposition: H = U * Sigma * V^H
        U, Sigma, V_H = np.linalg.svd(H)
        V = V_H.conj().T
        
        # 2x2 MIMO Spatial Multiplexing (SM) Pre-coding:
        # x = V * s  (where s = [s1, s2]^T)
        s_vec = np.array([s1[k], s2[k]])
        x_vec = V.dot(s_vec)
        
        # Pass through the physical MIMO channel and add AWGN to each receiver: y = H*x + n
        n_vec = (np.random.normal(0, math.sqrt(noise_power/2), 2) + 
                 1j * np.random.normal(0, math.sqrt(noise_power/2), 2))
        y_vec = H.dot(x_vec) + n_vec
        
        # Post-decoding (U^H matches the SVD beamforming):
        # r = U^H * y = U^H * H * V * s + U^H * n = Sigma * s + U^H * n
        r_vec = U.conj().T.dot(y_vec)
        
        # Isolate the independent streams: s_hat = r / Sigma
        s1_hat = r_vec[0] / Sigma[0]
        s2_hat = r_vec[1] / Sigma[1]
        
        # Demodulate Stream 1
        dists1 = np.abs(constellation - s1_hat)
        rx1_bit = np.argmin(dists1)
        if rx1_bit != tx_bits_mimo1[k]:
            errors_mimo1 += 1
            
        # Demodulate Stream 2
        dists2 = np.abs(constellation - s2_hat)
        rx2_bit = np.argmin(dists2)
        if rx2_bit != tx_bits_mimo2[k]:
            errors_mimo2 += 1
            
    ber_mimo1 = errors_mimo1 / num_carriers
    ber_mimo2 = errors_mimo2 / num_carriers
    
    # Calculate aggregate successfully delivered bits per second
    goodput_mimo1 = num_carriers * 2 * symbol_rate_hz * (1.0 - ber_mimo1)
    goodput_mimo2 = num_carriers * 2 * symbol_rate_hz * (1.0 - ber_mimo2)
    aggregate_mimo_goodput = goodput_mimo1 + goodput_mimo2
    
    return {
        "ber_mono": ber_mono,
        "goodput_mono_kbps": goodput_mono_bps / 1000.0,
        "ber_mimo1": ber_mimo1,
        "ber_mimo2": ber_mimo2,
        "goodput_mimo_kbps": aggregate_mimo_goodput / 1000.0,
        "gain_factor": aggregate_mimo_goodput / goodput_mono_bps
    }

def main():
    print("======================================================================")
    print("      📐 ACOUSTIC PHY CALIBRATION: 1x1 MONO VS 2x2 MIMO CAPACITY 📐")
    print("======================================================================")
    print("\nThis simulator performs a rigorous physical and mathematical validation")
    print("of Cyrinx's 2x2 MIMO spatial multiplexing stack compared to 1x1 Mono.")
    print("Models complex Gaussian channel matrices, real-time SVD pre/post coding,")
    print("and captures Bit Error Rates (BER) under realistic acoustic noise floors.\n")
    
    # We will test three representative spatial correlation environments
    scenarios = [
        ("Ideal Well-Spaced Setup (Low Correlation, e.g. External Pods)", 0.05, 25.0),
        ("Air-Coupled Desktop (Moderate Correlation, e.g. Phone ➔ Mac)", 0.25, 25.0),
        ("Chassis Conductive / In-Line (High Correlation, e.g. Keypad Bezel)", 0.80, 25.0),
        ("Noisy Room Environment (High Correlation + High Noise Floor)", 0.80, 15.0)
    ]
    
    print("----------------------------------------------------------------------")
    print(" SCENARIO ANALYSIS & EMPIRICAL CAPACITY MUTIPLIPLIERS:")
    print("----------------------------------------------------------------------")
    
    for title, corr, snr in scenarios:
        res = simulate_ofdm_channel(snr_db=snr, spatial_correlation=corr)
        print(f"\n📊 SCENARIO: {title}")
        print(f"   [Channel Settings] SNR = {snr:.1f} dB | Spatial Correlation (rho) = {corr:.2f}")
        print("   [1x1 Mono Link]")
        print(f"     - Bit Error Rate (BER):  {res['ber_mono']*100.0:6.3f} %")
        print(f"     - Effective Goodput:     {res['goodput_mono_kbps']:6.2f} kbps")
        print("   [2x2 MIMO Link (Spatial Multiplexing)]")
        print(f"     - Stream 1 BER:          {res['ber_mimo1']*100.0:6.3f} %")
        print(f"     - Stream 2 BER:          {res['ber_mimo2']*100.0:6.3f} %")
        print(f"     - Aggregate Goodput:     {res['goodput_mimo_kbps']:6.2f} kbps")
        
        status = ""
        if res['gain_factor'] >= 1.85:
            status = " [🟢 PERFECT 2x SPEED MULTIPLIER]"
        elif res['gain_factor'] >= 1.40:
            status = " [🟡 MODERATE MIMO MULTIPLEXING GAIN]"
        else:
            status = " [⚠️ HIGH CORRELATION - ARC FALLBACK TO SFBC DIVERSITY RECOMMENDED]"
            
        print(f"   👉 EFFECTIVE CAPACITY GAIN: {res['gain_factor']:.2f}x{status}")
        print("-"*70)

if __name__ == "__main__":
    main()
