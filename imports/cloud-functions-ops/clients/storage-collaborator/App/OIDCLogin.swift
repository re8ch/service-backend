import AuthenticationServices
import CryptoKit
import Foundation
import Security
import UIKit

@MainActor
final class OIDCLogin: NSObject, ASWebAuthenticationPresentationContextProviding {
  struct TokenResponse: Decodable {
    let accessToken: String
    let refreshToken: String?
    let expiresIn: Int?
    enum CodingKeys: String, CodingKey {
      case accessToken = "access_token"
      case refreshToken = "refresh_token"
      case expiresIn = "expires_in"
    }
  }

  static let issuer = URL(string: "https://dex.re8ch.com")!
  static let transportBase = URL(string: "https://dex.re8ch.com:8443")!
  static let clientID = "storage-collaborator"
  static let redirectURI = "com.re8ch.storagecollaborator:/oauth/callback"
  private var session: ASWebAuthenticationSession?

  func signIn() async throws -> TokenResponse {
    let verifier = Self.randomURLSafe(count: 48)
    let challenge = Self.base64URL(Data(SHA256.hash(data: Data(verifier.utf8))))
    var parts = URLComponents(url: Self.transportBase.appendingPathComponent("auth"), resolvingAgainstBaseURL: false)!
    parts.queryItems = [
      .init(name: "client_id", value: Self.clientID),
      .init(name: "redirect_uri", value: Self.redirectURI),
      .init(name: "response_type", value: "code"),
      .init(name: "scope", value: "openid profile email groups offline_access"),
      .init(name: "code_challenge", value: challenge),
      .init(name: "code_challenge_method", value: "S256"),
    ]
    let callback: URL = try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<URL, Error>) in
      let auth = ASWebAuthenticationSession(url: parts.url!, callbackURLScheme: "com.re8ch.storagecollaborator") { url, error in
        if let error { continuation.resume(throwing: error); return }
        guard let url else { continuation.resume(throwing: URLError(.badServerResponse)); return }
        continuation.resume(returning: url)
      }
      auth.presentationContextProvider = self
      auth.prefersEphemeralWebBrowserSession = false
      session = auth
      auth.start()
    }
    guard let code = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "code" })?.value else {
      throw URLError(.badServerResponse)
    }
    var request = URLRequest(url: Self.transportBase.appendingPathComponent("token"))
    request.httpMethod = "POST"
    request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
    let form = ["grant_type": "authorization_code", "code": code, "redirect_uri": Self.redirectURI, "client_id": Self.clientID, "code_verifier": verifier]
    request.httpBody = form.map { key, value in "\(Self.escape(key))=\(Self.escape(value))" }.joined(separator: "&").data(using: String.Encoding.utf8)
    let (data, response) = try await URLSession.shared.data(for: request)
    guard let http = response as? HTTPURLResponse, 200..<300 ~= http.statusCode else { throw URLError(.userAuthenticationRequired) }
    return try JSONDecoder().decode(TokenResponse.self, from: data)
  }

  func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
    UIApplication.shared.connectedScenes.compactMap { ($0 as? UIWindowScene)?.keyWindow }.first ?? ASPresentationAnchor()
  }

  private static func randomURLSafe(count: Int) -> String {
    var bytes = [UInt8](repeating: 0, count: count)
    _ = SecRandomCopyBytes(kSecRandomDefault, count, &bytes)
    return base64URL(Data(bytes))
  }
  private static func base64URL(_ data: Data) -> String { data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "") }
  private static func escape(_ value: String) -> String { value.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed.subtracting(CharacterSet(charactersIn: "+&=")))! }
}
