require 'fileutils'
require 'json'
require 'net/http'
require 'open3'
require 'optparse'
require 'socket'
require 'tmpdir'

options = {}
OptionParser.new do |parser|
    %w[phase sdk release-dir evidence].each do |name|
        parser.on("--#{name} VALUE") { |value| options[name] = name == 'phase' ? value : File.expand_path(value) }
    end
end.parse!
abort 'Required: --phase red|green --sdk PATH --evidence PATH [--release-dir seven-record-subset]' unless
    %w[red green].include?(options['phase']) && options['sdk'] && options['evidence']
Dir.chdir(File.expand_path('..', __dir__))
phase = options['phase']
evidence = options['evidence']
FileUtils.mkdir_p(evidence)
log = File.open(File.join(evidence, "#{phase}.log"), 'w')
runtime_log = File.open(File.join(evidence, "#{phase}-runtime.log"), 'w')
log.sync = runtime_log.sync = true
state = Dir.mktmpdir('open-error-', evidence)
pid = nil
emit = lambda do |message|
    puts message
    $stdout.flush
    log.puts message
end
get = lambda do |path|
    Net::HTTP.start('127.0.0.1', 8795, nil, nil, nil, nil, read_timeout: 60) do |http|
        http.request(Net::HTTP::Get.new(path, 'CF-Connecting-IP' => '192.0.2.95', 'Accept' => 'application/json'))
    end
end
begin
    TCPServer.open('127.0.0.1', 8795).close
    command = ['./node_modules/.bin/wrangler', 'dev', '--local', '--ip', '127.0.0.1', '--port', '8795',
        '--persist-to', state, '--var', 'ENABLE_LOCAL_QUALIFICATION:true']
    pid = Process.spawn({ 'WRANGLER_SEND_METRICS' => 'false' }, *command, out: runtime_log, err: runtime_log, pgroup: true)
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 30
    loop do
        raise 'Owned runtime exited during startup' if Process.waitpid(pid, Process::WNOHANG)
        begin
            TCPSocket.open('127.0.0.1', 8795).close
            break
        rescue Errno::ECONNREFUSED
            raise 'Runtime startup timed out' if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
            sleep 0.1
        end
    end
    # Use the existing public SDK runner against the same genuinely empty R2 server.
    request = { op: 'open', args: { release: '20261011', mode: 'stream', auto_update: false,
        base_url: 'http://127.0.0.1:8795/', cache_dir: File.join(state, 'native-cache') } }
    command = ['node', File.join(options['sdk'], 'conformance/runners/node/runner.mjs'),
        '--sdk', File.expand_path('node_modules/opentideconstants/dist/index.js')]
    stdout, stderr, status = Open3.capture3(*command, stdin_data: JSON.generate(request) + "\n")
    File.write(File.join(evidence, "#{phase}-native.jsonl"), stdout)
    runtime_log.puts stderr
    raise 'Native open reference failed to run' unless status.success?
    lines = stdout.lines.map { |line| JSON.parse(line) }
    raise 'Native reference handshake absent' unless lines.shift.key?('hello')
    expected = lines.fetch(0)
    raise 'Native open unexpectedly succeeded' unless expected['ok'] == false && expected['error'].key?('fields')
    emit.call("Native SDK open on empty R2: #{JSON.generate(expected)}")
    failures = []
    %w[/stations /openapi.json].each do |path|
        response = get.call(path)
        name = path == '/stations' ? 'stations' : 'openapi'
        File.write(File.join(evidence, "#{phase}-#{name}.body"), response.body)
        actual = begin
            JSON.parse(response.body)
        rescue JSON::ParserError
            nil
        end
        canonical = actual == expected
        metadata_absent = response['Attribution'].nil? && response['DATESTAMP'].nil?
        emit.call("#{phase.upcase} GET #{path}: HTTP#{response.code} canonical_sdk_error=#{canonical} metadata_absent=#{metadata_absent}")
        emit.call("body=#{JSON.generate(actual)}") if actual
        passed = phase == 'red' ? response.code == '500' && !canonical : response.code == '400' && canonical && metadata_absent
        failures << path unless passed
    end
    raise "Unexpected outcomes: #{failures.join(', ')}" unless failures.empty?
    if phase == 'green' && options['release-dir']
        source = options['release-dir']
        count = File.foreach(File.join(source, 'OTC_20261011.jsonl')).count
        raise 'Recovery control requires existing seven-record subset' unless count == 7
        %w[OTC_20261011.jsonl OTC_20261011.meta.json OTC_20261011.sha256].each do |name|
            command = ['./node_modules/.bin/wrangler', 'r2', 'object', 'put', "otc-qualification/#{name}",
                '--file', File.join(source, name), '--local', '--persist-to', state]
            stdout, stderr, status = Open3.capture3({ 'WRANGLER_SEND_METRICS' => 'false' }, *command)
            runtime_log.puts stdout, stderr
            raise "Recovery staging failed: #{name}" unless status.success?
        end
        ['/stations?q=CHARLESTON&limit=1', '/openapi.json'].each do |path|
            response = get.call(path)
            body = JSON.parse(response.body)
            raise "Recovery failed #{path}: #{response.code}" unless response.code == '200'
            raise 'Recovery metadata missing' unless response['DATESTAMP'] == '20261011' && !response['Attribution'].to_s.empty?
            if path.start_with?('/stations')
                raise 'Recovery station mismatch' unless body.dig('result', 'station_ids') == ['OTC-58e73c43']
            else
                raise 'Recovery OpenAPI changed' unless body == JSON.parse(File.read('../../api/openapi.json'))
            end
            emit.call("GREEN recovery #{path}: HTTP200 unchanged result, real Attribution/DATESTAMP present")
        end
    end
    emit.call(phase == 'red' ? 'RED reproduced before fix' : 'GREEN passed')
rescue StandardError => error
    emit.call("FAIL #{error.class}: #{error.message}")
    exit 1
ensure
    if pid
        begin
            Process.kill('TERM', -pid)
            Process.waitpid(pid)
        rescue Errno::ESRCH, Errno::ECHILD
            # The owned process has already exited.
        end
    end
    FileUtils.remove_entry(state)
    runtime_log.close
    log.close
end
