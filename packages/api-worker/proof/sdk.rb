require 'digest'
require 'json'
require 'net/http'
require 'open3'
require 'optparse'
require 'socket'
require 'time'

options = {}
OptionParser.new do |parser|
    %w[sdk release-dir url evidence].each do |name|
        parser.on("--#{name} VALUE") { |value| options[name] = value }
    end
end.parse!
abort 'Required: --sdk --release-dir --url --evidence' unless options.size == 4
Dir.chdir(File.expand_path('..', __dir__))
url = URI(options['url'])
abort 'Qualification requires http://127.0.0.1:8787' unless url.to_s == 'http://127.0.0.1:8787'
evidence = options['evidence']
result = { task: 'API-W micro-task 2.1', status: 'RUNNING', limitation: 'Local workerd does not prove deployed limits, latency, or costs.' }
log = File.open(File.join(evidence, 'sdk-red.log'), 'a')
log.sync = true
pid = nil
begin
    result[:gate] = 'native baseline'
    file = File.join(options['release-dir'], 'OTC_20261011.jsonl')
    result[:release_sha256] = Digest::SHA256.file(file).hexdigest
    raise 'Full release pin mismatch' unless result[:release_sha256] == '7695d765a6787fb21c51d41cb6449b6699f019489c25e7a3dbbb6aa2aad7d1e5'
    station = 'OTC-ee4593cd'
    selected_set = "#{station}/noaa/9414290"
    arguments = { station_id: station, set_id: selected_set, datum: 'mean_level', units: 'meters' }
    requests = [
        { op: 'open', args: { file: file, mode: 'stream', auto_update: false } },
        { op: 'require_station', args: { station_id: station } },
        { op: 'station_by_alias', args: { system: 'noaa', alias_id: '9414290' } },
        { op: 'heights_at', args: arguments.merge(times: [1791673200000, 1791694800000].map { |ms| Time.at(ms / 1000.0).iso8601 }) },
        { op: 'extremes', args: arguments.merge(start: Time.at(1791673200).iso8601, stop: Time.at(1791759600).iso8601) },
        { op: 'close' }
    ]
    command = [ENV.fetch('PYTHON', 'python3'), File.join(options['sdk'], 'conformance/runners/python/runner.py')]
    log.puts "$ #{command.join(' ')}"
    log.puts "stdin: #{JSON.generate(requests)}"
    stdout, stderr, status = Open3.capture3(*command, stdin_data: requests.map { |request| JSON.generate(request) + "\n" }.join)
    File.write(File.join(evidence, 'sdk-baseline.jsonl'), stdout)
    log.puts stderr, "native exit=#{status.exitstatus}"
    raise "Native runner exited #{status.exitstatus}" unless status.success?
    responses = stdout.lines.map { |line| JSON.parse(line) }
    raise 'Native runner handshake absent' unless responses.shift.key?('hello')
    raise 'Native runner reply count mismatch' unless responses.size == requests.size
    responses.each_with_index do |response, index|
        raise "Native #{requests[index][:op]} failed: #{JSON.generate(response)}" unless response['ok']
    end
    raise 'Native alias mismatch' unless responses[2].dig('result', 'station_id') == station
    expected = {
        'heights' => responses[3].dig('result', 'prediction'),
        'extremes' => responses[4].dig('result', 'prediction')
    }
    expected.each do |kind, prediction|
        raise "Native #{kind} missing pinned set/items" unless prediction && prediction['set_id'] == selected_set && !prediction['items'].empty?
    end
    result[:native] = { station_id: station, set_id: selected_set, height_count: expected['heights']['items'].size, extreme_count: expected['extremes']['items'].size }
    puts "Native full-release baseline passed: #{result[:native][:height_count]} heights, #{result[:native][:extreme_count]} extremes"

    result[:gate] = 'real workerd startup'
    TCPServer.open(url.host, url.port).close
    command = %w[npx wrangler dev --local --ip 127.0.0.1 --port 8787 --persist-to .wrangler/state --var ENABLE_LOCAL_QUALIFICATION:true]
    log.puts "$ #{command.join(' ')}"
    pid = Process.spawn({ 'WRANGLER_SEND_METRICS' => 'false' }, *command, out: log, err: log, pgroup: true)
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 30
    loop do
        raise 'Worker exited during startup' if Process.waitpid(pid, Process::WNOHANG)
        begin
            TCPSocket.open(url.host, url.port).close
            break
        rescue Errno::ECONNREFUSED
            raise 'Worker startup timed out' if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
            sleep 0.1
        end
    end
    result[:gate] = 'untouched SDK inside workerd'
    result[:workerd] = []
    %w[cold warm].each do |phase|
        started = Process.clock_gettime(Process::CLOCK_MONOTONIC)
        response = Net::HTTP.start(url.host, url.port, nil, nil, nil, nil, read_timeout: 180) do |http|
            http.get('/__qualification/sdk')
        end
        body = JSON.parse(response.body)
        body['phase'] = phase
        body['http_status'] = response.code.to_i
        body['round_trip_seconds'] = Process.clock_gettime(Process::CLOCK_MONOTONIC) - started
        result[:workerd] << body
        log.puts JSON.generate(body)
        raise "Workerd #{body['operation']}: #{JSON.generate(body['error'])}" unless response.code == '200' && body['ok']
        raise 'Cold/warm state mismatch' unless body['cold'] == (phase == 'cold')
        expected.each do |kind, prediction|
            actual = body.fetch(kind)
            raise "#{phase} #{kind} count mismatch" unless actual.size == prediction['items'].size
            actual.zip(prediction['items']).each do |observed, native|
                native_ms = Time.iso8601(native.fetch('time')).to_f * 1000
                raise "#{phase} #{kind} time mismatch" unless (observed.fetch('time_ms') - native_ms).abs <= 1000
                raise "#{phase} #{kind} height mismatch" unless (observed.fetch('height_m') - native.fetch('height_m')).abs <= 0.00001
                raise "#{phase} extreme type mismatch" if kind == 'extremes' && observed['type'] != native['type']
            end
        end
        puts "#{phase} workerd predictions match native"
    end
    result[:status] = 'PASS'
    result[:gate] = 'native/workerd full-release predictions match'
    File.write(File.join(evidence, 'sdk-green.log'), JSON.pretty_generate(result) + "\n")
rescue StandardError => error
    result[:status] = 'BLOCKED'
    result[:error] = "#{error.class}: #{error.message}"
    warn result[:error]
ensure
    if pid
        begin
            Process.kill('TERM', -pid)
            Process.waitpid(pid)
        rescue Errno::ESRCH, Errno::ECHILD
            # The owned runtime has already exited.
        end
    end
    File.write(File.join(evidence, 'sdk-result.json'), JSON.pretty_generate(result) + "\n")
    log.puts JSON.pretty_generate(result)
    log.close
end
exit(result[:status] == 'PASS' ? 0 : 1)
