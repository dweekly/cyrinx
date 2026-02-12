#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_PATH="${1:-$ROOT_DIR/artifacts/bench/audio-rates-mac.json}"

mkdir -p "$(dirname "$OUT_PATH")"

swift - "$OUT_PATH" <<'SWIFT'
import CoreAudio
import Foundation

struct RateRange: Codable {
    let minimumHz: Double
    let maximumHz: Double
}

struct DeviceProbe: Codable {
    let role: String
    let name: String
    let currentHz: Double
    let supports96k: Bool
    let availableRanges: [RateRange]
}

struct ProbeReport: Codable {
    let generatedAt: String
    let devices: [DeviceProbe]
}

func defaultDeviceID(_ selector: AudioObjectPropertySelector) -> AudioDeviceID? {
    var address = AudioObjectPropertyAddress(
        mSelector: selector,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var deviceID = AudioDeviceID(0)
    var size = UInt32(MemoryLayout<AudioDeviceID>.size)
    let rc = AudioObjectGetPropertyData(
        AudioObjectID(kAudioObjectSystemObject),
        &address,
        0,
        nil,
        &size,
        &deviceID
    )
    return rc == noErr ? deviceID : nil
}

func deviceName(_ id: AudioDeviceID) -> String {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioObjectPropertyName,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var unmanaged: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    let rc = AudioObjectGetPropertyData(id, &address, 0, nil, &size, &unmanaged)
    if rc != noErr {
        return "<unknown>"
    }
    return (unmanaged?.takeRetainedValue() as String?) ?? "<unknown>"
}

func currentRate(_ id: AudioDeviceID) -> Double {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyNominalSampleRate,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var value: Double = 0
    var size = UInt32(MemoryLayout<Double>.size)
    let rc = AudioObjectGetPropertyData(id, &address, 0, nil, &size, &value)
    return rc == noErr ? value : 0
}

func availableRanges(_ id: AudioDeviceID) -> [AudioValueRange] {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyAvailableNominalSampleRates,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var size: UInt32 = 0
    let sizeRC = AudioObjectGetPropertyDataSize(id, &address, 0, nil, &size)
    if sizeRC != noErr || size == 0 {
        return []
    }

    let count = Int(size) / MemoryLayout<AudioValueRange>.stride
    var values = Array(repeating: AudioValueRange(mMinimum: 0, mMaximum: 0), count: count)
    let dataRC = AudioObjectGetPropertyData(id, &address, 0, nil, &size, &values)
    return dataRC == noErr ? values : []
}

func probe(_ role: String, _ selector: AudioObjectPropertySelector) -> DeviceProbe? {
    guard let id = defaultDeviceID(selector) else {
        return nil
    }
    let ranges = availableRanges(id)
    let mappedRanges = ranges.map { RateRange(minimumHz: $0.mMinimum, maximumHz: $0.mMaximum) }
    let supports96k = ranges.contains { $0.mMinimum <= 96_000 && 96_000 <= $0.mMaximum }
    return DeviceProbe(
        role: role,
        name: deviceName(id),
        currentHz: currentRate(id),
        supports96k: supports96k,
        availableRanges: mappedRanges
    )
}

let outputPath = CommandLine.arguments[1]
var devices: [DeviceProbe] = []
if let output = probe("defaultOutput", kAudioHardwarePropertyDefaultOutputDevice) {
    devices.append(output)
}
if let input = probe("defaultInput", kAudioHardwarePropertyDefaultInputDevice) {
    devices.append(input)
}

let report = ProbeReport(
    generatedAt: ISO8601DateFormatter().string(from: Date()),
    devices: devices
)
let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
let data = try encoder.encode(report)
try data.write(to: URL(fileURLWithPath: outputPath))

print("Wrote \(outputPath)")
for device in devices {
    let current = Int(device.currentHz.rounded())
    print("\(device.role): \(device.name)")
    print("  currentHz=\(current) supports96k=\(device.supports96k)")
}
SWIFT
