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
        .library(name: "Cyrinx", targets: ["Cyrinx"])
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
        .testTarget(
            name: "CyrinxTests",
            dependencies: ["Cyrinx", "CCyrinx"],
            path: "Tests/CyrinxTests"
        )
    ]
)
