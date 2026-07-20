import CyrinxChatKit
import Foundation

// Writes the `happyPair` and `peerLoss` JSON-lines traces (CONTRACT.md §3,
// §4) for a given `--seed` to a given `--out` directory. Per the C3-28
// design brief, this tool does NOT write into Apps/Chat/fixtures/ itself --
// the verify stage copies its output there as the committed golden traces.
//
// Usage: chat-trace-gen --seed <u64> --out <dir>

func printUsageAndExit() -> Never {
    FileHandle.standardError.write(Data("usage: chat-trace-gen --seed <u64> --out <dir>\n".utf8))
    exit(1)
}

func parseArguments() -> (seed: UInt64, outDir: String) {
    var seed: UInt64?
    var outDir: String?
    var iterator = CommandLine.arguments.dropFirst().makeIterator()
    while let arg = iterator.next() {
        switch arg {
        case "--seed":
            guard let value = iterator.next(), let parsed = UInt64(value) else { printUsageAndExit() }
            seed = parsed
        case "--out":
            guard let value = iterator.next() else { printUsageAndExit() }
            outDir = value
        default:
            FileHandle.standardError.write(Data("unknown argument: \(arg)\n".utf8))
            printUsageAndExit()
        }
    }
    guard let seed, let outDir else { printUsageAndExit() }
    return (seed, outDir)
}

let (seed, outDir) = parseArguments()

do {
    try FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)
} catch {
    FileHandle.standardError.write(Data("failed to create output directory \(outDir): \(error)\n".utf8))
    exit(1)
}

// CONTRACT.md §3's preamble: golden traces are pinned for `happyPair` and
// `peerLoss` only (the other four scenarios are still fully implemented and
// tested, but do not land committed trace fixtures -- see
// Apps/Chat/README.md's directory map).
let scenariosToWrite: [(scenario: ChatScenario, filename: String)] = [
    (.happyPair, "happyPair.jsonl"),
    (.peerLoss, "peerLoss.jsonl"),
]

for entry in scenariosToWrite {
    do {
        let records = try await ChatScenarioRunner.runToCompletion(scenario: entry.scenario, seed: seed)
        let contents = records.map { $0.canonicalJSONLine() }.joined(separator: "\n") + "\n"
        let outPath = (outDir as NSString).appendingPathComponent(entry.filename)
        try contents.write(toFile: outPath, atomically: true, encoding: .utf8)
        print("wrote \(records.count) record(s) to \(outPath)")
    } catch {
        FileHandle.standardError.write(
            Data("failed to run scenario \(entry.scenario.rawValue): \(error)\n".utf8)
        )
        exit(1)
    }
}
