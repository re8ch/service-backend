// swift-tools-version: 6.2
import PackageDescription
let package = Package(
  name: "StorageCollaboratorKit",
  platforms: [.iOS(.v18), .macOS(.v15)],
  products: [.library(name: "StorageCollaboratorKit", targets: ["StorageCollaboratorKit"])],
  targets: [
    .target(name: "StorageCollaboratorKit"),
    .testTarget(name: "StorageCollaboratorKitTests", dependencies: ["StorageCollaboratorKit"])
  ]
)
