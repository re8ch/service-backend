require 'base64'
require 'json'
require 'net/http'
require 'openssl'

def jwt(id, variable)
  encode = ->(bytes) { Base64.urlsafe_encode64(bytes, padding: false) }
  now = Time.now.to_i
  claims = [ {alg: 'RS256', typ: 'JWT'}, {iss: id, iat: now - 60, exp: now + 300} ]
  payload = claims.map { |claim| encode.call(JSON.generate(claim)) }.join('.')
  key = OpenSSL::PKey::RSA.new(ENV.delete(variable))
  "#{payload}.#{encode.call(key.sign('SHA256', payload))}"
end

def api(method, path, token, body = nil)
  uri = URI("https://api.github.com#{path}")
  client = Net::HTTP.new(uri.host, uri.port)
  client.use_ssl = true
  request = method.new(uri)
  request['Authorization'] = "Bearer #{token}"
  request['User-Agent'] = 'maintenance-installation-cutover'
  if body
    request['Content-Type'] = 'application/json'
    request.body = JSON.generate(body)
  end
  response = client.request(request)
  abort "#{request.method} #{uri.path}: HTTP #{response.code}" unless response.is_a?(Net::HTTPSuccess)
  abort 'Unexpected pagination; inspect expanded installation scope' if response['link'].to_s.include?('rel="next"')
  JSON.parse(response.body) unless response.body.to_s.empty?
end

def inventory(app_id, key, allowed)
  app = api(Net::HTTP::Get, '/app', key)
  abort 'Wrong App identity' unless app.fetch('id') == app_id
  installs = api(Net::HTTP::Get, '/app/installations?per_page=100', key)
  installs.to_h do |installation|
    owner = installation.fetch('account').fetch('login')
    abort 'Unexpected account or repository selection' unless allowed.key?(owner) && installation['repository_selection'] == 'selected'
    id = Integer(installation.fetch('id'))
    token = api(Net::HTTP::Post, "/app/installations/#{id}/access_tokens", key, {permissions: {metadata: 'read'}}).fetch('token')
    begin
      data = api(Net::HTTP::Get, '/installation/repositories?per_page=100', token)
      repos = data.fetch('repositories').map { |repo| repo.fetch('full_name') }.sort
      abort 'Incomplete repository inventory' unless repos.length == data.fetch('total_count')
      [owner, {id: id, repos: repos}]
    ensure
      api(Net::HTTP::Delete, '/installation/token', token)
    end
  end
end

repos = JSON.parse(File.read('clusters/re8ch/components/gitops-events/repositories.json')).values + ['compocv/compocv']
expected = repos.group_by { |repo| repo.split('/').first }.transform_values(&:sort)
legacy = jwt(5426272, 'LEGACY_KEY')
enterprise = jwt(5633036, 'ENTERPRISE_KEY')
old = inventory(5426272, legacy, expected)
replacement = inventory(5633036, enterprise, expected)
expected.each do |owner, scope|
  abort "Enterprise repository scope differs for #{owner}" unless replacement.dig(owner, :repos) == scope
end
old.each do |owner, installation|
  abort "Legacy repository scope differs for #{owner}" unless installation[:repos] == expected.fetch(owner)
  puts "#{owner}: verified #{installation[:repos].join(', ')}"
end
mode = ENV.fetch('MODE')
abort 'Unknown cutover mode' unless %w[audit retire].include?(mode)
if mode == 'retire'
  old.each_value { |i| api(Net::HTTP::Delete, "/app/installations/#{i[:id]}", legacy) }
  abort 'Legacy installations remain' unless api(Net::HTTP::Get, '/app/installations?per_page=100', legacy).empty?
  puts 'Legacy installations: 0'
end
