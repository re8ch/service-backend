# Storage Collaborator for Apple platforms

`StorageCollaboratorKit` is the shared, Swift Concurrency networking/model/sync package.
`FileProviderExtension` is shared source for iOS and macOS replicated File
Provider extension targets. The production Xcode project should contain one
SwiftUI app target plus platform-specific extension targets using App Group
`group.com.re8ch.storagecollaborator`; bundle IDs and team/signing are intentionally not
guessed or written to App Store Connect.

Tokens must move from the shared defaults placeholder to an App Group Keychain
access group before distribution. Background content transfers use system File
Provider callbacks; long app-owned management transfers should use a background
`URLSession` and `BGProcessingTask`. Search begins with filename/metadata API
results; Spotlight indexing is supplied by File Provider item metadata.

