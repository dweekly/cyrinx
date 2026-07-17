import Foundation

#if os(macOS) || os(iOS)
    import Accelerate
    import CoreAudio
#endif

/// Provides native platform hooks to query system audio gains and analyze
/// measured transducer captures.
public final class AcousticCalibration: Sendable {

    /// Applies the legacy fixed macOS gain-staging values.
    ///
    /// This is an explicit, process-wide hardware mutation. The values are not
    /// a device, route, or geometry calibration; callers should prefer gains
    /// selected from measured clipping and distortion data for the active path.
    public static func optimizeHardwareVolumes() {
        #if os(macOS)
            print("[AcousticCalibration] Applying legacy macOS hardware gain staging...")
            do {
                try setSystemInputVolumeScalar(0.55)
                try setSystemOutputVolumeScalar(0.80)
                print("[AcousticCalibration] Fixed gain staging applied (Mic: 55%, Spk: 80%)")
            } catch {
                print(
                    "[AcousticCalibration] Warning: Failed to apply native volume overrides: "
                        + error.localizedDescription
                )
            }
        #else
            print(
                "[AcousticCalibration] Platform auto-gain calibration is not supported "
                    + "on this platform. Please stage gains manually."
            )
        #endif
    }

    /// Calculates the Total Harmonic Distortion (THD) of a captured audio buffer
    /// given a fundamental target frequency using Apple's Accelerate vDSP framework.
    /// Formula: THD% = sqrt(HarmonicPower / FundamentalPower) * 100
    public static func calculateTHD(
        samples: [Float],
        sampleRate: Double,
        fundamentalHz: Double
    ) -> Float {
        #if os(macOS) || os(iOS)
            let n = samples.count

            // Ensure n is a power of 2 for FFT
            guard n >= 64, (n & (n - 1)) == 0 else {
                print("[AcousticCalibration] THD failed: sample count \(n) must be a power of 2.")
                return 0.0
            }

            guard let transformed = transformedSamples(samples) else {
                print(
                    "[AcousticCalibration] THD failed: Accelerate discrete "
                        + "fourier transform initialization error."
                )
                return 0.0
            }

            // Calculate magnitudes
            let magnitudes = magnitudes(
                real: transformed.real, imaginary: transformed.imaginary, count: n / 2)

            // Map frequencies to bin indices
            let binWidth = sampleRate / Double(n)
            let fundamentalBin = Int(round(fundamentalHz / binWidth))

            guard fundamentalBin > 0, fundamentalBin < n / 2 else {
                print(
                    "[AcousticCalibration] THD failed: fundamental frequency "
                        + "\(fundamentalHz) Hz lies out of Nyquist bounds."
                )
                return 0.0
            }

            // Find fundamental energy in a 5-bin window to handle spectral leakage
            let window = 2
            let fundamentalPower = spectralPower(
                magnitudes,
                centeredAt: fundamentalBin,
                radius: window
            )

            // Find harmonic energies (2nd, 3rd, 4th harmonics)
            var harmonicPower = Float(0.0)
            for harmonic in 2...4 {
                let harmonicBin = fundamentalBin * harmonic
                if harmonicBin < n / 2 {
                    harmonicPower += spectralPower(
                        magnitudes,
                        centeredAt: harmonicBin,
                        radius: window
                    )
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

    #if os(macOS) || os(iOS)
        private static func transformedSamples(
            _ samples: [Float]
        ) -> (real: [Float], imaginary: [Float])? {
            guard
                let transform = try? vDSP.DiscreteFourierTransform(
                    count: samples.count,
                    direction: .forward,
                    transformType: .complexComplex,
                    ofType: Float.self
                )
            else { return nil }
            let inputImaginary = [Float](repeating: 0, count: samples.count)
            var real = [Float](repeating: 0, count: samples.count)
            var imaginary = [Float](repeating: 0, count: samples.count)
            transform.transform(
                inputReal: samples,
                inputImaginary: inputImaginary,
                outputReal: &real,
                outputImaginary: &imaginary
            )
            return (real, imaginary)
        }
    #endif

    private static func magnitudes(
        real: [Float],
        imaginary: [Float],
        count: Int
    ) -> [Float] {
        (0..<count).map { index in
            sqrt(real[index] * real[index] + imaginary[index] * imaginary[index])
        }
    }

    private static func spectralPower(
        _ magnitudes: [Float],
        centeredAt center: Int,
        radius: Int
    ) -> Float {
        let start = max(0, center - radius)
        let end = min(magnitudes.count - 1, center + radius)
        return (start...end).reduce(0) { power, index in
            power + magnitudes[index] * magnitudes[index]
        }
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
                let formattedTHD = String(format: "%.2f", thd)
                print(
                    "[AcousticCalibration] Test Gain: \(g) | Local Loopback THD: "
                        + "\(formattedTHD)%"
                )

                if thd < 5.0 {
                    optimalGain = g
                } else {
                    print(
                        "[AcousticCalibration] Transducer amplifier saturation "
                            + "detected at Gain = \(g) (THD >= 5%)"
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
