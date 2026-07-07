import Foundation

#if os(macOS) || os(iOS)
    import Accelerate
    import CoreAudio
#endif

/// Provides native platform hooks to programmatically query and calibrate
/// system audio transducers into optimal linear gain regions.
public final class AcousticCalibration: Sendable {

    /// Calibrates the default hardware input and output devices to their proven
    /// sweet-spots (55% input sensitivity, 80% speaker output) to prevent
    /// near-field ADC clipping and transducer overload.
    public static func optimizeHardwareVolumes() {
        #if os(macOS)
            print("[AcousticCalibration] Performing macOS hardware gain staging calibration...")
            do {
                try setSystemInputVolumeScalar(0.55)
                try setSystemOutputVolumeScalar(0.80)
                print("[AcousticCalibration] Linear calibration completed successfully (Mic: 55%, Spk: 80%)")
            } catch {
                print(
                    "[AcousticCalibration] Warning: Failed to apply native volume overrides: \(error.localizedDescription)"
                )
            }
        #else
            print(
                "[AcousticCalibration] Platform auto-gain calibration is not supported on this platform. Please stage gains manually."
            )
        #endif
    }

    /// Calculates the Total Harmonic Distortion (THD) of a captured audio buffer
    /// given a fundamental target frequency using Apple's Accelerate vDSP framework.
    /// Formula: THD% = sqrt(HarmonicPower / FundamentalPower) * 100
    public static func calculateTHD(samples: [Float], sampleRate: Double, fundamentalHz: Double) -> Float {
        #if os(macOS) || os(iOS)
            let n = samples.count

            // Ensure n is a power of 2 for FFT
            guard n >= 64, (n & (n - 1)) == 0 else {
                print("[AcousticCalibration] THD failed: sample count \(n) must be a power of 2.")
                return 0.0
            }

            let real = samples
            let imag = [Float](repeating: 0.0, count: n)

            guard
                let fft = try? vDSP.DiscreteFourierTransform(
                    count: n,
                    direction: .forward,
                    transformType: .complexComplex,
                    ofType: Float.self
                )
            else {
                print(
                    "[AcousticCalibration] THD failed: Accelerate discrete fourier transform initialization error."
                )
                return 0.0
            }

            var outReal = [Float](repeating: 0.0, count: n)
            var outImag = [Float](repeating: 0.0, count: n)

            fft.transform(
                inputReal: real,
                inputImaginary: imag,
                outputReal: &outReal,
                outputImaginary: &outImag
            )

            // Calculate magnitudes
            var magnitudes = [Float](repeating: 0.0, count: n / 2)
            for i in 0..<n / 2 {
                magnitudes[i] = sqrt(outReal[i] * outReal[i] + outImag[i] * outImag[i])
            }

            // Map frequencies to bin indices
            let binWidth = sampleRate / Double(n)
            let fundamentalBin = Int(round(fundamentalHz / binWidth))

            guard fundamentalBin > 0, fundamentalBin < n / 2 else {
                print(
                    "[AcousticCalibration] THD failed: fundamental frequency \(fundamentalHz) Hz lies out of Nyquist bounds."
                )
                return 0.0
            }

            // Find fundamental energy in a 5-bin window to handle spectral leakage
            let window = 2
            var fundamentalPower = Float(0.0)
            let startBin = max(0, fundamentalBin - window)
            let endBin = min(n / 2 - 1, fundamentalBin + window)
            for b in startBin...endBin {
                fundamentalPower += magnitudes[b] * magnitudes[b]
            }

            // Find harmonic energies (2nd, 3rd, 4th harmonics)
            var harmonicPower = Float(0.0)
            for harmonic in 2...4 {
                let harmonicBin = fundamentalBin * harmonic
                if harmonicBin < n / 2 {
                    let hStart = max(0, harmonicBin - window)
                    let hEnd = min(n / 2 - 1, harmonicBin + window)
                    for b in hStart...hEnd {
                        harmonicPower += magnitudes[b] * magnitudes[b]
                    }
                }
            }

            guard fundamentalPower > 1e-9 else {
                return 0.0
            }

            // THD % = sqrt(harmonicPower / fundamentalPower) * 100
            return sqrt(harmonicPower / fundamentalPower) * 100.0
        #else
            return 0.0
        #endif
    }

    /// Sweeps across multiple loopback captures, analyzes self-THD, and programmatically
    /// resolves the optimal transmitter digital gain cap where THD remains below the 5% limit.
    public static func calibrateOptimalTxGain(
        loopbackSamples: [Float: [Float]],
        sampleRate: Double,
        fundamentalHz: Double
    ) -> Float {
        print("[AcousticCalibration] Calibrating dynamic loopback THD limits...")

        // Standard transmit gain options
        let gains: [Float] = [0.12, 0.20, 0.35, 0.50, 0.70]
        var optimalGain = Float(0.12)

        for g in gains {
            if let samples = loopbackSamples[g] {
                let thd = calculateTHD(samples: samples, sampleRate: sampleRate, fundamentalHz: fundamentalHz)
                print(
                    "[AcousticCalibration] Test Gain: \(g) | Local Loopback THD: \(String(format: "%.2f", thd))%"
                )

                if thd < 5.0 {
                    optimalGain = g
                } else {
                    print(
                        "[AcousticCalibration] Transducer amplifier saturation detected at Gain = \(g) (THD >= 5%)"
                    )
                    break
                }
            }
        }

        print("[AcousticCalibration] Resolved optimal safe linear Tx Gain cap: \(optimalGain)")
        return optimalGain
    }

    #if os(macOS)

        /// Sets the default macOS input device's hardware volume scalar (0.0 to 1.0).
        public static func setSystemInputVolumeScalar(_ volume: Float) throws {
            let deviceID = try getDefaultDevice(selector: kAudioHardwarePropertyDefaultInputDevice)
            try setVolumeScalar(deviceID: deviceID, isInput: true, volume: volume)
        }

        /// Sets the default macOS output device's hardware volume scalar (0.0 to 1.0).
        public static func setSystemOutputVolumeScalar(_ volume: Float) throws {
            let deviceID = try getDefaultDevice(selector: kAudioHardwarePropertyDefaultOutputDevice)
            try setVolumeScalar(deviceID: deviceID, isInput: false, volume: volume)
        }

        /// Queries the default macOS input device's hardware volume scalar.
        public static func getSystemInputVolumeScalar() throws -> Float {
            let deviceID = try getDefaultDevice(selector: kAudioHardwarePropertyDefaultInputDevice)
            return try getVolumeScalar(deviceID: deviceID, isInput: true)
        }

        /// Queries the default macOS output device's hardware volume scalar.
        public static func getSystemOutputVolumeScalar() throws -> Float {
            let deviceID = try getDefaultDevice(selector: kAudioHardwarePropertyDefaultOutputDevice)
            return try getVolumeScalar(deviceID: deviceID, isInput: false)
        }

        // MARK: - CoreAudio Internal Helpers

        private static func getDefaultDevice(selector: AudioObjectPropertySelector) throws -> AudioDeviceID {
            var address = AudioObjectPropertyAddress(
                mSelector: selector,
                mScope: kAudioObjectPropertyScopeGlobal,
                mElement: kAudioObjectPropertyElementMain
            )
            var deviceID = AudioDeviceID(kAudioObjectUnknown)
            var size = UInt32(MemoryLayout<AudioDeviceID>.size)

            let status = AudioObjectGetPropertyData(
                AudioObjectID(kAudioObjectSystemObject),
                &address,
                0,
                nil,
                &size,
                &deviceID
            )

            guard status == noErr else {
                throw NSError(
                    domain: "Cyrinx.AcousticCalibration",
                    code: Int(status),
                    userInfo: [
                        NSLocalizedDescriptionKey: "Failed to resolve default device for selector \(selector)"
                    ]
                )
            }
            return deviceID
        }

        private static func setVolumeScalar(deviceID: AudioDeviceID, isInput: Bool, volume: Float) throws {
            var address = AudioObjectPropertyAddress(
                mSelector: kAudioDevicePropertyVolumeScalar,
                mScope: isInput ? kAudioObjectPropertyScopeInput : kAudioObjectPropertyScopeOutput,
                mElement: kAudioObjectPropertyElementMain
            )

            // Ensure volume lies in safe bounds [0.0, 1.0]
            var targetVolume = max(0.0, min(1.0, volume))
            let size = UInt32(MemoryLayout<Float>.size)

            let status = AudioObjectSetPropertyData(
                deviceID,
                &address,
                0,
                nil,
                size,
                &targetVolume
            )

            guard status == noErr else {
                throw NSError(
                    domain: "Cyrinx.AcousticCalibration",
                    code: Int(status),
                    userInfo: [
                        NSLocalizedDescriptionKey:
                            "Failed to write hardware volume property for device \(deviceID)"
                    ]
                )
            }
        }

        private static func getVolumeScalar(deviceID: AudioDeviceID, isInput: Bool) throws -> Float {
            var address = AudioObjectPropertyAddress(
                mSelector: kAudioDevicePropertyVolumeScalar,
                mScope: isInput ? kAudioObjectPropertyScopeInput : kAudioObjectPropertyScopeOutput,
                mElement: kAudioObjectPropertyElementMain
            )

            var volume = Float(0.0)
            var size = UInt32(MemoryLayout<Float>.size)

            let status = AudioObjectGetPropertyData(
                deviceID,
                &address,
                0,
                nil,
                &size,
                &volume
            )

            guard status == noErr else {
                throw NSError(
                    domain: "Cyrinx.AcousticCalibration",
                    code: Int(status),
                    userInfo: [
                        NSLocalizedDescriptionKey:
                            "Failed to read hardware volume property for device \(deviceID)"
                    ]
                )
            }
            return volume
        }

    #endif
}
