require 'openssl'
require 'base64'
require 'json'
require 'net/http'

def encode(value)
  Base64.urlsafe_encode64(value, padding: false)
end

now = Time.now.to_i
header = encode(JSON.generate(alg: 'RS256', typ: 'JWT'))
claims = encode(JSON.generate(iss: ENV.fetch('APP_ID'), iat: now - 60, exp: now + 300))
payload = "#{header}.#{claims}"
key = OpenSSL::PKey::RSA.new(ENV.delete('APP_KEY'))
jwt = "#{payload}.#{encode(key.sign('SHA256', payload))}"
projects = JSON.parse(File.read('operations/github-maintenance/gitops-observation-projects.json'))
ready = true
File.open(ENV.fetch('GITHUB_STEP_SUMMARY'), 'a') do |report|
  projects.fetch('projects').each do |project|
    repo = project.fetch('repository')
    uri = URI("https://api.github.com/repos/#{repo}/installation")
    response = Net::HTTP.get_response(uri, {'Authorization' => "Bearer #{jwt}", 'User-Agent' => 'gitops-event-preflight'})
    unless response.is_a?(Net::HTTPSuccess)
      report.puts "- #{repo} — installation lookup HTTP #{response.code}"
      ready = false
      next
    end
    installation = JSON.parse(response.body)
    dispatch = installation.dig('permissions', 'actions') == 'write'
    release = installation.fetch('events').include?('release')
    verdict = "- #{repo} — actions:write=#{dispatch}, release subscription=#{release}"
    report.puts verdict
    puts verdict
    ready &&= dispatch && release
  end
end
abort 'Event installation prerequisites incomplete; see job summary' unless ready
