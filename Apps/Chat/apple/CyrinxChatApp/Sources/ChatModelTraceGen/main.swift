import CyrinxChatApp
import CyrinxChatKit
import Foundation

// Writes the `happyPair` and `peerLoss` model-trace JSON-lines files (the
// C3-29/C3-30 design brief's pinned model-trace schema) for a given `--seed`
// to a given `--out` directory, driving `ChatModel` attached to client A per
// the brief: "Driver: scenario happyPair and peerLoss at seed 1, model
// attached to client A, scripted sends exactly as the §3 tables." Mirrors
// `CyrinxChatKit`'s own `chat-trace-gen` (Apps/Chat/CyrinxChatKit/Sources/
// ChatTraceGen/main.swift) in shape and argument parsing.
//
// Per the C3-29 task brief, this tool does NOT write into
// Apps/Chat/fixtures/ itself -- the orchestrator's verify stage copies its
// output there as the committed golden model-traces.
//
// Usage: chat-model-trace-gen --seed <u64> --out <dir>

func printUsageAndExit() -> Never {
    FileHandle.standardError.write(Data("usage: chat-model-trace-gen --seed <u64> --out <dir>\n".utf8))
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
// `peerLoss` only -- mirrors `chat-trace-gen`'s own identical scope note.
let scenariosToWrite: [(scenario: ChatScenario, filename: String)] = [
    (.happyPair, "happyPair.jsonl"),
    (.peerLoss, "peerLoss.jsonl"),
]

for entry in scenariosToWrite {
    do {
        let recorder = ChatModelTraceRecorder()
        // A fixed `now` provider keeps every field this recorder writes
        // fully reproducible for a given (scenario, seed) -- the brief's
        // pinned model-trace schema never carries `sentAtWallClockMs`
        // anyway (see `ChatModelTraceRecorder`'s doc comment), so this only
        // matters for hygiene, not correctness of the byte-identity gate.
        try await ChatModelScenarioDriver.run(scenario: entry.scenario, seed: seed, attachTo: .a) {
            transport in
            let model = ChatModel(transport: transport, now: { 0 })
            model.traceRecorder = recorder
            return model
        }
        let outPath = (outDir as NSString).appendingPathComponent(entry.filename)
        try recorder.joinedText().write(toFile: outPath, atomically: true, encoding: .utf8)
        print("wrote \(recorder.lines.count) record(s) to \(outPath)")
    } catch {
        FileHandle.standardError.write(
            Data("failed to run scenario \(entry.scenario.rawValue): \(error)\n".utf8)
        )
        exit(1)
    }
}
