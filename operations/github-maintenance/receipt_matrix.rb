require 'base64'
require 'json'
require 'net/http'
require 'openssl'
require 'time'

def encode(value)
  Base64.urlsafe_encode64(value, padding: false)
end

now = Time.now.to_i
header = encode(JSON.generate(alg: 'RS256', typ: 'JWT'))
claims = encode(JSON.generate(iss: ENV.fetch('APP_ID'), iat: now - 60, exp: now + 300))
payload = "#{header}.#{claims}"
key = OpenSSL::PKey.read(ENV.delete('APP_KEY'))
headers = {'Authorization' => "Bearer #{payload}.#{encode(key.sign('SHA256', payload))}", 'User-Agent' => 'gitops-receipt-observer'}

def read_api(uri, headers)
  response = Net::HTTP.get_response(uri, headers)
  response.value
  [JSON.parse(response.body), response['link']]
end

config, = read_api(URI('https://api.github.com/app/hook/config'), headers)
abort 'Unexpected receipt endpoint' unless config['url'] == 'https://hooks.re8ch.com/events/github/gitops'
repos = JSON.parse(File.read('clusters/re8ch/components/gitops-events/repositories.json'))
uri = URI('https://api.github.com/app/hook/deliveries?per_page=100')
rows = {}
while uri
  entries, links = read_api(uri, headers)
  entries.each do |entry|
    next unless entry['event'] == 'release' && entry['action'] == 'published'
    next unless [200, 201].include?(entry['status_code'])
    repo = repos[entry['repository_id'].to_s]
    next unless repo
    guid = entry.fetch('guid')
    next if rows.key?(guid)
    detail, = read_api(URI("https://api.github.com/app/hook/deliveries/#{Integer(entry.fetch('id'))}"), headers)
    event = detail.fetch('request').fetch('payload')
    next unless event.dig('repository', 'full_name') == repo && event['action'] == 'published'
    owner, name = repo.split('/')
    rows[guid] = {owner: owner, repo: name, delivery: guid, release: Integer(event.fetch('release').fetch('id'))}
  end
  url = links.to_s[/<([^>]+)>; rel="next"/, 1]
  uri = url && URI(url)
  abort 'Unexpected GitHub pagination host' if uri && uri.host != 'api.github.com'
end
abort 'Receipt matrix exceeds Actions capacity' if rows.size > 256
File.open(ENV.fetch('GITHUB_OUTPUT'), 'a') do |out|
  out.puts "matrix=#{JSON.generate(include: rows.values)}"
  out.puts "available=#{!rows.empty?}"
end
