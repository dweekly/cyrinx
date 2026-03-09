// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "cyrinx",
    platforms: [
        .macOS(.v13),
        .iOS(.v17)
    ],
    products: [
        .library(name: "CCyrinx", targets: ["CCyrinx"]),
        .library(name: "Cyrinx", targets: ["Cyrinx"]),
        .executable(name: "cyrinx-example-loopback", targets: ["ExampleLoopback"]),
        .executable(name: "cyrinx-example-multiplex", targets: ["ExampleMultiplex"]),
        .executable(name: "cyrinx-example-large-payload", targets: ["ExampleLargePayload"]),
        .executable(name: "cyrinx-example-android-hil", targets: ["ExampleAndroidHIL"]),
        .executable(name: "cyrinx-sim-bench", targets: ["SimulationBench"])
    ],
    targets: [
        .target(
            name: "CCyrinx",
            path: "Sources/CCyrinx",
            publicHeadersPath: "include",
            cSettings: [
                .headerSearchPath(".")
            ]
        ),
        .target(
            name: "Cyrinx",
            dependencies: ["CCyrinx"],
            path: "Sources/Cyrinx"
        ),
        .executableTarget(
            name: "ExampleLoopback",
            dependencies: ["Cyrinx"],
            path: "Examples/Loopback"
        ),
        .executableTarget(
            name: "ExampleMultiplex",
            dependencies: ["Cyrinx"],
            path: "Examples/Multiplex"
        ),
        .executableTarget(
            name: "ExampleLargePayload",
            dependencies: ["Cyrinx"],
            path: "Examples/LargePayload"
        ),
        .executableTarget(
            name: "ExampleAndroidHIL",
            dependencies: ["Cyrinx"],
            path: "Examples/AndroidHIL"
        ),
        .executableTarget(
            name: "SimulationBench",
            dependencies: ["Cyrinx"],
            path: "Examples/SimulationBench"
        ),
        .testTarget(
            name: "CyrinxTests",
            dependencies: ["Cyrinx", "CCyrinx"],
            path: "Tests/CyrinxTests"
        )
    ]
)
