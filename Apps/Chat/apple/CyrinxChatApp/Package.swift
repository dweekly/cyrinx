// swift-tools-version: 6.0
//
// Standalone Swift package for the C3-29 Apple chat app's platform-neutral
// model layer: `ChatModel` (@MainActor @Observable), launch-arg parsing,
// the model-trace recorder, and the `chat-model-trace-gen` executable.
// Depends on `CyrinxChatKit` (the C3-28 contract/simulator package, a
// sibling directory) for `ChatTransportClient`/`ChatEvent`/etc -- this
// package owns display projection ONLY, never transport logic (Apps/Chat/
// README.md's "UI models ... own display state, while CyrinxTransport owns
// protocol state").
//
// swift-tools-version copied verbatim from Apps/Chat/CyrinxChatKit/
// Package.swift / the repository root Package.swift.
//
// Platform minimums: iOS 17 matches CyrinxChatKit/the root package
// verbatim. macOS is bumped to 14 (from CyrinxChatKit's 13) -- a
// deliberate, narrow deviation, not an invented value: the C3-29 task
// brief pins `ChatModel` as `@MainActor @Observable`, and the Observation
// framework's `@Observable` macro requires macOS 14 / iOS 17 (both
// released simultaneously; iOS 17 was already this repo's floor, so only
// macOS's minimum actually needs to move). Verified empirically: `swift
// build` fails with "'Observable()' is only available in macOS 14.0 or
// newer" under a macOS 13 platform minimum. `CyrinxChatKit` itself has no
// SwiftUI/Observation dependency and correctly stays at macOS 13 -- this
// bump is scoped to this package only.
import PackageDescription

let package = Package(
    name: "CyrinxChatApp",
    platforms: [
        .macOS(.v14),
        .iOS(.v17),
    ],
    products: [
        .library(name: "CyrinxChatApp", targets: ["CyrinxChatApp"]),
        .executable(name: "chat-model-trace-gen", targets: ["ChatModelTraceGen"]),
    ],
    dependencies: [
        .package(path: "../../CyrinxChatKit")
    ],
    targets: [
        .target(
            name: "CyrinxChatApp",
            dependencies: [
                .product(name: "CyrinxChatKit", package: "CyrinxChatKit")
            ],
            path: "Sources/CyrinxChatApp"
        ),
        .executableTarget(
            name: "ChatModelTraceGen",
            dependencies: [
                "CyrinxChatApp",
                .product(name: "CyrinxChatKit", package: "CyrinxChatKit"),
            ],
            path: "Sources/ChatModelTraceGen"
        ),
        .testTarget(
            name: "CyrinxChatAppTests",
            dependencies: [
                "CyrinxChatApp",
                .product(name: "CyrinxChatKit", package: "CyrinxChatKit"),
            ],
            path: "Tests/CyrinxChatAppTests"
        ),
    ]
)
