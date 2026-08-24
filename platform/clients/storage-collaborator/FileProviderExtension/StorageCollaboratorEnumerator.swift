@preconcurrency import FileProvider
import StorageCollaboratorKit

final class StorageCollaboratorEnumerator: NSObject, NSFileProviderEnumerator, @unchecked Sendable {
  let container: NSFileProviderItemIdentifier
  let api: APIClient
  let workspaceID: String

  init(container: NSFileProviderItemIdentifier, api: APIClient, workspaceID: String) {
    self.container = container
    self.api = api
    self.workspaceID = workspaceID
  }

  func invalidate() {}

  func enumerateItems(for observer: NSFileProviderEnumerationObserver, startingAt page: NSFileProviderPage) {
    Task {
      do {
        observer.didEnumerate(try await api.children(of: container.rawValue).map { StorageCollaboratorItem($0) })
        observer.finishEnumerating(upTo: nil)
      } catch { observer.finishEnumeratingWithError(error) }
    }
  }

  func enumerateChanges(for observer: NSFileProviderChangeObserver, from anchor: NSFileProviderSyncAnchor) {
    Task {
      do {
        let cursor = Int64(String(data: anchor.rawValue, encoding: .utf8) ?? "0") ?? 0
        let page = try await api.changes(workspaceID: workspaceID, cursor: cursor)
        observer.didUpdate(page.changes.filter { $0.type != "deleted" }.map(\.item).map { StorageCollaboratorItem($0) })
        observer.didDeleteItems(withIdentifiers: page.changes.filter { $0.type == "deleted" }.map { .init($0.item.id) })
        observer.finishEnumeratingChanges(upTo: .init(Data(String(page.cursor).utf8)), moreComing: page.hasMore)
      } catch { observer.finishEnumeratingWithError(error) }
    }
  }

  func currentSyncAnchor(completionHandler: @escaping (NSFileProviderSyncAnchor?) -> Void) {
    completionHandler(.init(Data("0".utf8)))
  }
}
