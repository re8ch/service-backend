import Foundation

public struct Workspace: Codable, Sendable, Identifiable, Hashable {
  public let id: String
  public let name: String
  public let rootItemId: String
  public let quotaBytes: Int64?
}

public struct RemoteItem: Codable, Sendable, Identifiable, Hashable {
  public let id: String
  public let workspaceId: String
  public let parentId: String?
  public let name: String
  public let kind: Kind
  public let size: Int64
  public let contentType: String?
  public let etag: String
  public let version: Int64
  public let modifiedAt: Date
  public let deleted: Bool?
  public enum Kind: String, Codable, Sendable { case file, folder }
}

public struct Change: Codable, Sendable { public let cursor: Int64; public let type: String; public let item: RemoteItem }
public struct ChangePage: Codable, Sendable { public let changes: [Change]; public let cursor: Int64; public let hasMore: Bool }
public struct ItemPage: Codable, Sendable { public let items: [RemoteItem] }
public struct WorkspacePage: Codable, Sendable { public let workspaces: [Workspace] }

public enum StorageCollaboratorError: Error, Sendable {
  case invalidResponse, unauthorized, notFound, conflict(RemoteItem?), server(Int, String)
}
