import SwiftUI
import FileProvider
import StorageCollaboratorKit

@main struct StorageCollaboratorApp: App {
  @State private var model = AppModel()
  var body: some Scene { WindowGroup { ContentView().environment(model) } }
}

@MainActor @Observable final class AppModel {
  var gateway = "https://storage.service.re8ch.com:8443/"
  var token = ""
  var workspaces:[Workspace] = []
  var status = "Not connected"
  private let oidc = OIDCLogin()
  func signInAndConnect() async {
    do {
      status = "Signing in with RE8CH…"
      let result = try await oidc.signIn()
      token = result.accessToken
      let defaults=UserDefaults(suiteName:"group.com.re8ch.storagecollaborator")
      defaults?.set(result.accessToken,forKey:"deviceToken")
      defaults?.set(result.refreshToken,forKey:"refreshToken")
      defaults?.set(Date().addingTimeInterval(TimeInterval(result.expiresIn ?? 3600)).timeIntervalSince1970,forKey:"tokenExpiresAt")
      await connect()
    } catch { status=String(describing:error) }
  }
  func connect() async {
    guard let url=URL(string:gateway) else { status="Invalid gateway URL"; return }
    do {
      let api=APIClient(configuration:.init(baseURL:url,token:token)); workspaces=try await api.workspaces()
      let defaults=UserDefaults(suiteName:"group.com.re8ch.storagecollaborator"); defaults?.set(gateway,forKey:"gatewayURL");defaults?.set(token,forKey:"deviceToken")
      defaults?.set(Dictionary(uniqueKeysWithValues: workspaces.map { ($0.id, $0.rootItemId) }),forKey:"workspaceRootItems")
      for w in workspaces { let domain=NSFileProviderDomain(identifier:.init(w.id),displayName:w.name); try await NSFileProviderManager.add(domain) }
      status="Synced \(workspaces.count) workspace(s)"
    } catch { status=String(describing:error) }
  }
}

struct ContentView:View {
  @Environment(AppModel.self) private var model
  @State private var query=""
  @State private var showingSettings = false

  var body: some View {
    NavigationStack {
      ScrollView {
        VStack(spacing: 20) {
          hero

          if model.workspaces.isEmpty {
            connectCard
          } else {
            workspaceSection
            filesHint
          }

          statusCard
        }
        .padding()
      }
      .background(Color(.systemGroupedBackground))
      .navigationTitle("Storage Collaborator")
      .toolbar {
        ToolbarItem(placement: .topBarTrailing) {
          Button("Settings", systemImage: "gearshape") { showingSettings = true }
        }
      }
      .searchable(text: $query, prompt: "Search workspaces")
      .sheet(isPresented: $showingSettings) { settings }
    }
  }

  private var hero: some View {
    VStack(spacing: 12) {
      Image(systemName: "externaldrive.fill.badge.icloud")
        .font(.system(size: 52, weight: .semibold))
        .foregroundStyle(.blue)
        .symbolEffect(.pulse, options: .repeat(2), value: model.workspaces.count)
      Text("Your cluster. Your cloud drive.")
        .font(.title2.bold())
      Text("Bring your own Kubernetes cluster and use its storage from Files.app without exposing CephFS to the internet.")
        .font(.subheadline)
        .foregroundStyle(.secondary)
        .multilineTextAlignment(.center)
    }
    .frame(maxWidth: .infinity)
    .padding(.vertical, 18)
  }

  private var connectCard: some View {
    VStack(alignment: .leading, spacing: 14) {
      Label("Connect your DataSpace", systemImage: "point.3.connected.trianglepath.dotted")
        .font(.headline)
      Text("Sign in to discover your Workspace and register Storage Collaborator in Files.app.")
        .font(.subheadline)
        .foregroundStyle(.secondary)
      Button {
        Task { await model.signInAndConnect() }
      } label: {
        Label("Sign in with RE8CH", systemImage: "person.badge.key.fill")
          .frame(maxWidth: .infinity)
      }
      .buttonStyle(.borderedProminent)
      .controlSize(.large)
    }
    .cardStyle()
  }

  private var workspaceSection: some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack {
        Text("Workspaces").font(.headline)
        Spacer()
        Button("Refresh", systemImage: "arrow.clockwise") { Task { await model.connect() } }
      }
      ForEach(model.workspaces.filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) }) { workspace in
        HStack(spacing: 14) {
          Image(systemName: "externaldrive.fill")
            .font(.title2)
            .foregroundStyle(.blue)
            .frame(width: 42, height: 42)
            .background(.blue.opacity(0.12), in: RoundedRectangle(cornerRadius: 11))
          VStack(alignment: .leading, spacing: 3) {
            Text(workspace.name).font(.headline)
            Text("Mounted in Files.app").font(.caption).foregroundStyle(.secondary)
          }
          Spacer()
          Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        }
        .padding(.vertical, 4)
      }
    }
    .cardStyle()
  }

  private var filesHint: some View {
    Label {
      Text("Open Files.app → Browse → More → Edit, then enable Storage Collaborator.")
    } icon: {
      Image(systemName: "folder.fill")
    }
    .font(.subheadline)
    .frame(maxWidth: .infinity, alignment: .leading)
    .cardStyle()
  }

  private var statusCard: some View {
    HStack(spacing: 12) {
      Image(systemName: model.workspaces.isEmpty ? "network.slash" : "network")
        .foregroundStyle(model.workspaces.isEmpty ? Color.secondary : Color.green)
      VStack(alignment: .leading, spacing: 2) {
        Text("Sync & network").font(.caption).foregroundStyle(.secondary)
        Text(model.status).font(.subheadline).textSelection(.enabled)
      }
      Spacer()
    }
    .cardStyle()
  }

  private var settings: some View {
    @Bindable var model = model
    return NavigationStack {
      Form {
        Section("BYOC Gateway") {
          TextField("Gateway URL", text: $model.gateway)
            .textInputAutocapitalization(.never)
            .keyboardType(.URL)
        }
        Section("Advanced device token") {
          SecureField("Device token", text: $model.token)
          Button("Connect with token") { Task { await model.connect() } }
        }
      }
      .navigationTitle("Connection Settings")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .confirmationAction) { Button("Done") { showingSettings = false } }
      }
    }
  }
}

private extension View {
  func cardStyle() -> some View {
    padding(16)
      .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 18))
  }
}
