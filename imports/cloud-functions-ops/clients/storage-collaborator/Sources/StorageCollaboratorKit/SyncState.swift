import Foundation

public actor SyncState {
  public enum Network: String, Sendable { case online, constrained, offline }
  public struct Snapshot: Sendable { public let network: Network; public let pendingTransfers: Int; public let lastSync: Date?; public let lastError: String? }
  private var network: Network = .online; private var pending=0; private var lastSync:Date?; private var lastError:String?
  public init() {}
  public func transferStarted(){pending += 1}
  public func transferFinished(error: Error?=nil){pending=max(0,pending-1);lastSync=Date();lastError=error.map(String.init(describing:))}
  public func setNetwork(_ value:Network){network=value}
  public func snapshot()->Snapshot{Snapshot(network:network,pendingTransfers:pending,lastSync:lastSync,lastError:lastError)}
}

