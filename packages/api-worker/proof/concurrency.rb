require 'digest'
require 'fileutils'
require 'json'
require 'net/http'
require 'open3'
require 'optparse'
require 'socket'
require 'tmpdir'

options = {}
OptionParser.new do |parser|
    %w[release-dir evidence label baseline].each do |name|
        parser.on("--#{name} VALUE") do |value|
            options[name] = name == 'label' ? value : File.expand_path(value)
        end
    end
end.parse!
abort 'Required: --release-dir --evidence --label red|green' unless options['release-dir'] && options['evidence'] && %w[red green].include?(options['label'])
Dir.chdir(File.expand_path('..', __dir__))
FileUtils.mkdir_p(options['evidence'])
native = File.join(options['release-dir'], 'OTC_20261011.jsonl')
abort 'Expected the pinned seven-record SDK subset' unless Digest::SHA256.file(native).hexdigest == '58caefc7a17a3a2e6caadbe2a08d4772254baedbd3a537dc6a28b652a03396df'
label = options.fetch('label')
log = File.open(File.join(options['evidence'], "#{label}.log"), 'w')
runtime = File.open(File.join(options['evidence'], "#{label}-runtime.log"), 'w')
log.sync = runtime.sync = true
report = lambda do |message|
    puts message
    log.puts message
end
request = lambda do |path|
    Net::HTTP.start('127.0.0.1', 8792, nil, nil, nil, nil, read_timeout: 60) do |http|
        message = Net::HTTP::Get.new(path)
        message['CF-Connecting-IP'] = '192.0.2.92'
        http.request(message)
    end
end
paths = [
    '/stations?q=CHARLESTON&limit=5',
    '/stations/OTC-58e73c43/timeline?' + URI.encode_www_form(from: '2027-01-01T00:00:00-08:00', to: '2027-01-02T00:00:00-08:00', datum: 'mean_level', units: 'meters', step: 21600),
    '/stations/OTC-00bdf4b5/currents?' + URI.encode_www_form(from: '2027-01-01T00:00:00-08:00', to: '2027-01-02T00:00:00-08:00', bin: 14, units: 'knots', step: 21600)
]
pid = nil
begin
    Dir.mktmpdir('cold-open-', options['evidence']) do |state|
        TCPServer.open('127.0.0.1', 8792).close
        command = %w[./node_modules/.bin/wrangler dev --local --ip 127.0.0.1 --port 8792 --var ENABLE_LOCAL_QUALIFICATION:true]
        command.concat(['--persist-to', state])
        pid = Process.spawn({ 'WRANGLER_SEND_METRICS' => 'false' }, *command, out: runtime, err: runtime, pgroup: true)
        deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 30
        loop do
            raise 'Owned runtime exited during startup' if Process.waitpid(pid, Process::WNOHANG)
            begin
                TCPSocket.open('127.0.0.1', 8792).close
                break
            rescue Errno::ECONNREFUSED
                raise 'Runtime startup timed out' if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
                sleep 0.1
            end
        end
        failed = request.call(paths.first)
        expected_error = { 'ok' => false, 'error' => { 'code' => 'release_not_found',
            'message' => 'release 20261011 not found at http://127.0.0.1:8792/', 'fields' => {} } }
        raise 'Missing release did not return the canonical SDK error' unless failed.code == '400' && JSON.parse(failed.body) == expected_error &&
            failed['Attribution'].nil? && failed['DATESTAMP'].nil?
        report.call("#{label.upcase} missing-release initialization: HTTP 400, canonical release_not_found, metadata absent")
        %w[OTC_20261011.jsonl OTC_20261011.meta.json OTC_20261011.sha256].each do |name|
            stdout, stderr, status = Open3.capture3({ 'WRANGLER_SEND_METRICS' => 'false' }, './node_modules/.bin/wrangler', 'r2', 'object', 'put', "otc-qualification/#{name}", '--file', File.join(options['release-dir'], name), '--local', '--persist-to', state)
            runtime.puts stdout, stderr
            raise "Local R2 staging failed: #{name}" unless status.success?
        end
        gate = Queue.new
        threads = paths.map do |path|
            Thread.new do
                gate.pop
                response = request.call(path)
                raise "Common route failed: HTTP #{response.code}" unless response.code == '200'
                body = JSON.parse(response.body)
                raise 'Canonical success body missing' unless body['ok'] && body.key?('result')
                { 'body' => body, 'attribution' => response.fetch('Attribution'), 'datestamp' => response.fetch('DATESTAMP') }
            end
        end
        paths.size.times { gate << true }
        cold = threads.map(&:value)
        observe = lambda do
            response = request.call('/__qualification/sdk')
            body = JSON.parse(response.body)
            # The existing full-release probe's station is absent from this subset.
            # Its error response still exposes the real transport byte counters.
            raise 'Existing probe did not reach station lookup' unless response.code == '500' && body['operation'] == 'requireStation' && body['cold'] == false
            body.fetch('served')
        end
        served = observe.call
        bytes = served.fetch('OTC_20261011.jsonl')
        report.call("#{label.upcase} concurrent common routes: 3 HTTP 200 after rejected initialization")
        report.call("#{label.upcase} native transport: #{bytes} bytes; #{bytes.fdiv(File.size(native))} acquisitions")
        warm = paths.map do |path|
            response = request.call(path)
            raise "Warm route failed: HTTP #{response.code}" unless response.code == '200'
            { 'body' => JSON.parse(response.body), 'attribution' => response.fetch('Attribution'), 'datestamp' => response.fetch('DATESTAMP') }
        end
        raise 'Warm canonical responses changed' unless warm == cold
        raise 'Warm requests reacquired release data' unless observe.call == served
        report.call("#{label.upcase} settled reuse: unchanged canonical responses and no additional release bytes")
        if options['baseline']
            raise 'Canonical responses changed from pre-fix runtime' unless JSON.parse(File.read(options['baseline'])).fetch('responses') == cold
            report.call("#{label.upcase} all 3 canonical responses and metadata headers equal pre-fix runtime")
        end
        File.write(File.join(options['evidence'], "#{label}-result.json"), JSON.pretty_generate({ responses: cold, served: served }) + "\n")
        raise 'Expected exactly one underlying native acquisition' unless bytes == File.size(native)
        report.call('PASS one shared initialization; rejected initialization retries; settled instance reused')
    ensure
        if pid
            begin
                Process.kill('TERM', -pid)
                Process.waitpid(pid)
            rescue Errno::ESRCH, Errno::ECHILD
                # Only the runtime started by this proof is cleaned up.
            end
            pid = nil
        end
    end
ensure
    runtime.close
    log.close
end
