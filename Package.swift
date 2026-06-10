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
                .headerSearchPath("."),
                // vendored KISS FFT (BSD-3) for the portable bulk-PHY OFDM core,
                // compiled in double precision to match the numpy oracle.
                .headerSearchPath("kissfft"),
                .define("kiss_fft_scalar", to: "double")
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
        // Test-support C target: a minimal loader for the bulk-PHY golden
        // vectors (PR 1.1). Not a product — used only by CyrinxTests to prove
        // the portable-C side can read/size the golden artifacts.
        .target(
            name: "CGoldenVectors",
            path: "Tests/CGoldenVectors",
            publicHeadersPath: "include",
            cSettings: [
                .headerSearchPath(".")
            ]
        ),
        .testTarget(
            name: "CyrinxTests",
            dependencies: ["Cyrinx", "CCyrinx", "CGoldenVectors"],
            path: "Tests/CyrinxTests"
        )
    ]
)
