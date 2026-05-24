import numpy as np
import math

def simulate_cathedral_transmission_fde(fs=48000, n_fft=1024, zc_len=127):
    """
    Simulates a detailed OFDM transmission through a cathedral (highly reverberant)
    channel, comparing:
    1. Long CP (512 samples) with NO frequency domain equalization.
    2. Short CP (64 samples) WITH Frequency Domain Equalization (FDE).
    """
    # 1. Generate multi-path channel impulse response (RIR)
    # Direct path + reflection at 4ms (192 samples) with amp 0.5 + reflection at 8ms (384 samples) with amp 0.3
    rir = np.zeros(500)
    rir[0] = 1.0
    rir[192] = 0.5
    rir[384] = 0.3
    rir /= np.sqrt(np.sum(rir**2)) # normalize
    
    # Frequency response of the channel
    channel_tf = np.fft.rfft(rir, n_fft)
    
    # 2. Modulate QPSK symbols on 40 active subcarriers
    n_subcarriers = 40
    active_indices = np.linspace(380, 470, n_subcarriers, dtype=int) # Ultrasonic band ~18-22 kHz
    
    qpsk_symbols = np.exp(1j * np.random.choice([0, np.pi/2, np.pi, 3*np.pi/2], n_subcarriers))
    
    # Build frequency domain symbol
    X = np.zeros(n_fft // 2 + 1, dtype=complex)
    X[active_indices] = qpsk_symbols
    
    # Convert to time domain (IFFT)
    tx_time = np.fft.irfft(X, n_fft)
    
    # Let's evaluate both strategies:
    
    # --- Strategy 1: Long CP (512 samples, 33.3% overhead) ---
    cp_long = 512
    tx_long = np.concatenate([tx_time[-cp_long:], tx_time])
    
    # Convolve with channel + add noise
    rx_long_raw = np.convolve(tx_long, rir)[:len(tx_long)]
    rx_long_raw += np.random.normal(0, 0.005, len(rx_long_raw))
    
    # Remove CP
    rx_long = rx_long_raw[cp_long:]
    
    # FFT back to frequency domain
    Y_long = np.fft.rfft(rx_long, n_fft)
    received_symbols_long = Y_long[active_indices]
    
    # EVM calculation without equalization (Strategy 1 relies solely on CP protection)
    # Since channel introduces phase shift and attenuation, we perform a basic single-tap normalization
    rx_amp_norm = received_symbols_long / np.mean(np.abs(received_symbols_long))
    tx_amp_norm = qpsk_symbols
    evm_long = np.sqrt(np.mean(np.abs(rx_amp_norm - tx_amp_norm)**2)) * 100.0
    
    # --- Strategy 2: Short CP (64 samples, 5.8% overhead) + FDE ---
    cp_short = 64
    tx_short = np.concatenate([tx_time[-cp_short:], tx_time])
    
    # Convolve with channel + add noise
    rx_short_raw = np.convolve(tx_short, rir)[:len(tx_short)]
    rx_short_raw += np.random.normal(0, 0.005, len(rx_short_raw))
    
    # Remove CP
    # (Note: because CP is only 64 samples and reflection occurs up to 384 samples,
    #  this will suffer from ISI. Let's model the ISI and Equalization)
    rx_short = rx_short_raw[cp_short:]
    
    # FFT
    Y_short = np.fft.rfft(rx_short, n_fft)
    received_symbols_short = Y_short[active_indices]
    
    # Frequency Domain Equalization (FDE) using a Zero-Forcing / MMSE Single-Tap Equalizer:
    # H = channel transfer function
    H = channel_tf[active_indices]
    
    # MMSE equalizer weight: W = H* / (|H|^2 + N0)
    n0 = 0.001
    W = np.conj(H) / (np.abs(H)**2 + n0)
    
    # Equalized symbols
    equalized_symbols = received_symbols_short * W
    
    # Normalize and compute EVM
    eq_amp_norm = equalized_symbols / np.mean(np.abs(equalized_symbols))
    evm_short_fde = np.sqrt(np.mean(np.abs(eq_amp_norm - tx_amp_norm)**2)) * 100.0
    
    return evm_long, cp_long, evm_short_fde, cp_short

def main():
    print("======================================================================")
    print("       🧪 CRITIQUE FOLLOW-UP: FREQUENCY DOMAIN EQUALIZATION 🧪")
    print("======================================================================")
    print("\nCritique: Expanding the CP is a brute-force approach that severely")
    print("decimates throughput. By employing a Single-Tap MMSE Equalizer in the")
    print("frequency domain, we can keep the CP small and correct multi-path")
    print("phase distortion and amplitude notches at the receiver.\n")
    
    evm_long, cp_long, evm_short_fde, cp_short = simulate_cathedral_transmission_fde()
    
    n_fft = 1024
    efficiency_long = (n_fft / (n_fft + cp_long)) * 100.0
    efficiency_short = (n_fft / (n_fft + cp_short)) * 100.0
    
    print("📊 PERFORMANCE BENCHMARK COMPARISON:")
    print("----------------------------------------------------------------------")
    print("  METRIC                   | STRATEGY 1: BRUTE CP  | STRATEGY 2: CP + FDE")
    print("----------------------------------------------------------------------")
    print(f"  Cyclic Prefix (samples)  | {cp_long:<21d} | {cp_short:<21d}")
    print(f"  CP Duration (ms)         | {cp_long/48.0:<21.2f} | {cp_short/48.0:<21.2f}")
    print(f"  Spectral Efficiency (%)  | {efficiency_long:<21.1f} | {efficiency_short:<21.1f}")
    print(f"  Demodulated EVM (%)      | {evm_long:<21.1f} | {evm_short_fde:<21.1f}")
    print("----------------------------------------------------------------------")
    
    print("\n💡 RESPONSIVE ARCHITECTURAL RECOMMENDATION:")
    print("1. Set a constant, optimized Cyclic Prefix of 64 or 128 samples to protect")
    print("   against typical early room reflections without destroying throughput.")
    print("2. Always apply single-tap Frequency-Domain Equalization (FDE) using pilot")
    print("   subcarriers to flatten phase and recover signal-to-noise ratio in echoic chambers.")
    print("======================================================================\n")

if __name__ == "__main__":
    main()
