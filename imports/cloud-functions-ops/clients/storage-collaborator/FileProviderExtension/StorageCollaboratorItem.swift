import FileProvider
import UniformTypeIdentifiers
import StorageCollaboratorKit

final class StorageCollaboratorItem: NSObject, NSFileProviderItem {
  let remote: RemoteItem
  let systemIdentifier: NSFileProviderItemIdentifier?
  init(_ remote: RemoteItem, systemIdentifier: NSFileProviderItemIdentifier? = nil) { self.remote=remote; self.systemIdentifier=systemIdentifier }
  var itemIdentifier:NSFileProviderItemIdentifier{systemIdentifier ?? .init(remote.id)}
  var parentItemIdentifier:NSFileProviderItemIdentifier{remote.parentId.map { NSFileProviderItemIdentifier($0) } ?? .rootContainer}
  var filename:String{remote.name}
  var contentType:UTType{remote.kind == .folder ? UTType.folder : UTType.data}
  var documentSize:NSNumber?{NSNumber(value:remote.size)}
  var contentModificationDate:Date?{remote.modifiedAt}
  var itemVersion:NSFileProviderItemVersion{.init(contentVersion:Data(remote.etag.utf8),metadataVersion:Data(String(remote.version).utf8))}
  var capabilities:NSFileProviderItemCapabilities{remote.kind == .folder ? [.allowsReading,.allowsWriting,.allowsContentEnumerating,.allowsRenaming,.allowsReparenting,.allowsDeleting] : [.allowsReading,.allowsWriting,.allowsRenaming,.allowsReparenting,.allowsDeleting]}
}
