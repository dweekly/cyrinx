// swift-tools-version: 6.0
//
// Standalone Swift package for the C3-28 chat sample's platform-neutral
// kernel: the envelope v1 codec, the app contract types, and the
// deterministic simulated transport. Per Apps/Chat/README.md's "Simulator-
// first development" and the C3-28 merge gate, this package must NOT depend
// on the Cyrinx SDK package (`Sources/Cyrinx`) -- it has no dependency on
// it below, and must never gain one.
//
// swift-tools-version and platform minimums are copied verbatim from the
// repository root Package.swift (macOS 13 / iOS 17) per the C3-28 design
// brief's instruction to match, not invent, those values.
import PackageDescription

let package = Package(
    name: "CyrinxChatKit",
    platforms: [
        .macOS(.v13),
        .iOS(.v17)
    ],
    products: [
        .library(name: "CyrinxChatKit", targets: ["CyrinxChatKit"]),
        .executable(name: "chat-trace-gen", targets: ["ChatTraceGen"])
    ],
    targets: [
        .target(
            name: "CyrinxChatKit",
            path: "Sources/CyrinxChatKit"
        ),
        .executableTarget(
            name: "ChatTraceGen",
            dependencies: ["CyrinxChatKit"],
            path: "Sources/ChatTraceGen"
        ),
        .testTarget(
            name: "CyrinxChatKitTests",
            dependencies: ["CyrinxChatKit"],
            path: "Tests/CyrinxChatKitTests"
        )
    ]
)
