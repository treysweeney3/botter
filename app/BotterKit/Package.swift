// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "BotterKit",
    platforms: [
        .macOS(.v15),
        .iOS(.v18),
    ],
    products: [
        .library(name: "BotterKit", targets: ["BotterKit"])
    ],
    targets: [
        .target(name: "BotterKit", resources: [.process("Resources")]),
        .testTarget(name: "BotterKitTests", dependencies: ["BotterKit"]),
    ]
)
