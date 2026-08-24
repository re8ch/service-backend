import Foundation

public actor APIClient {
  public struct Configuration: Sendable { public let baseURL: URL; public let token: String; public init(baseURL: URL, token: String) { self.baseURL=baseURL; self.token=token } }
  private let configuration: Configuration
  private let session: URLSession
  private let decoder: JSONDecoder
  public init(configuration: Configuration, session: URLSession = .shared) {
    self.configuration=configuration; self.session=session
    let d=JSONDecoder(); d.dateDecodingStrategy = .iso8601; self.decoder=d
  }
  public func workspaces() async throws -> [Workspace] { try await json("v1/workspaces", as: WorkspacePage.self).workspaces }
  public func item(_ id: String) async throws -> RemoteItem { try await json("v1/items/\(id)", as: RemoteItem.self) }
  public func children(of id: String) async throws -> [RemoteItem] { try await json("v1/items/\(id)/children", as: ItemPage.self).items }
  public func changes(workspaceID: String, cursor: Int64) async throws -> ChangePage { try await json("v1/workspaces/\(workspaceID)?view=changes&cursor=\(cursor)", as: ChangePage.self) }
  public func download(_ item: RemoteItem, to destination: URL) async throws {
    let (temporary,response)=try await session.download(for: request("v1/items/\(item.id)/content")); try check(response, data:nil); try? FileManager.default.removeItem(at: destination); try FileManager.default.moveItem(at:temporary,to:destination)
  }
  public func upload(_ source: URL, item: RemoteItem) async throws -> RemoteItem {
    var r=request("v1/items/\(item.id)", method:"PATCH");r.setValue("application/octet-stream",forHTTPHeaderField:"Content-Type");r.setValue(item.etag,forHTTPHeaderField:"If-Match");let (data,response)=try await session.upload(for:r,fromFile:source);try check(response,data:data);return try decoder.decode(RemoteItem.self,from:data)
  }
  public func create(name:String,kind:RemoteItem.Kind,parentID:String) async throws -> RemoteItem { try await send("v1/items/\(parentID)",method:"POST",body:["name":name,"kind":kind.rawValue],etag:nil) }
  public func move(_ item:RemoteItem,name:String,parentID:String) async throws -> RemoteItem { try await send("v1/items/\(item.id)",method:"PATCH",body:["name":name,"parentId":parentID],etag:item.etag) }
  public func delete(_ item:RemoteItem) async throws { var r=request("v1/items/\(item.id)",method:"DELETE");r.setValue(item.etag,forHTTPHeaderField:"If-Match");let(data,response)=try await session.data(for:r);try check(response,data:data) }
  private func json<T:Decodable>(_ path:String,as:T.Type) async throws -> T { let(data,response)=try await session.data(for:request(path));try check(response,data:data);return try decoder.decode(T.self,from:data) }
  private func send<T:Decodable>(_ path:String,method:String,body:[String:String],etag:String?) async throws -> T { var r=request(path,method:method);r.httpBody=try JSONEncoder().encode(body);r.setValue("application/json",forHTTPHeaderField:"Content-Type");if let etag{r.setValue(etag,forHTTPHeaderField:"If-Match")};let(data,response)=try await session.data(for:r);try check(response,data:data);return try decoder.decode(T.self,from:data) }
  private func request(_ path:String,method:String="GET") -> URLRequest { var r=URLRequest(url:URL(string:path,relativeTo:configuration.baseURL)!);r.httpMethod=method;r.setValue("Bearer \(configuration.token)",forHTTPHeaderField:"Authorization");r.timeoutInterval=60;return r }
  private func check(_ response:URLResponse,data:Data?) throws { guard let h=response as? HTTPURLResponse else{throw StorageCollaboratorError.invalidResponse};switch h.statusCode{case 200..<300:return;case 401:throw StorageCollaboratorError.unauthorized;case 404:throw StorageCollaboratorError.notFound;case 409:throw StorageCollaboratorError.conflict(data.flatMap{try? decoder.decode(RemoteItem.self,from:$0)});default:throw StorageCollaboratorError.server(h.statusCode,data.flatMap{String(data:$0,encoding:.utf8)} ?? "")}}
}
