import Cyrinx
import Foundation

@main
struct SimulationBenchMain {
    static func main() throws {
        let args = CommandLine.arguments

        func value(_ flag: String, default defaultValue: String) -> String {
            guard let idx = args.firstIndex(of: flag), (idx + 1) < args.count else {
                return defaultValue
            }
            return args[idx + 1]
        }

        let profileRaw = value("--profile", default: "quiet")
        let profile = SimulationProfile(rawValue: profileRaw) ?? .quietDesktop

        let packets = Int(value("--packets", default: "120")) ?? 120
        let payloadBytes = Int(value("--payload", default: "128")) ?? 128
        let intervalMS = UInt32(value("--interval-ms", default: "20")) ?? 20
        let seed = UInt64(value("--seed", default: "3331495302")) ?? 3331495302

        let options = SimulationOptions(
            packetCount: packets,
            payloadBytes: payloadBytes,
            streamID: 1,
            priority: .normal,
            interPacketIntervalMS: intervalMS,
            seed: seed
        )

        let result = try SimulationRunner.run(profile: profile, options: options)
        let json = try SimulationRunner.makeJSON(result)

        if let outPath = outputPath(args) {
            let url = URL(fileURLWithPath: outPath)
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try json.write(to: url, atomically: true, encoding: .utf8)
            print("wrote=\(outPath)")
        }

        print(json)
    }

    private static func outputPath(_ args: [String]) -> String? {
        guard let idx = args.firstIndex(of: "--out"), (idx + 1) < args.count else {
            return nil
        }
        return args[idx + 1]
    }
}
