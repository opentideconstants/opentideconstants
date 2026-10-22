require 'digest'
require 'fileutils'
require 'json'
require 'net/http'
require 'open3'
require 'optparse'
require 'socket'
require 'time'

options = { 'phase' => 'green', 'url' => 'http://127.0.0.1:8787' }
OptionParser.new do |parser|
    %w[phase url sdk release-dir evidence persist-to].each do |name|
        parser.on("--#{name} VALUE") do |value|
            options[name] = %w[sdk release-dir evidence persist-to].include?(name) ? File.expand_path(value) : value
        end
    end
    parser.on('--ci-subset') { options['ci-subset'] = true }
    parser.on('--skip-stage') { options['skip-stage'] = true }
    parser.on('--reuse-reference') { options['reuse-reference'] = true }
    parser.on('--list-limit-only') { options['list-limit-only'] = true }
end.parse!
abort 'Required: --evidence PATH --phase red|green' unless options['evidence'] && %w[red green].include?(options['phase'])
runtime_url = URI(options['url'])
abort 'Only the local runtime is qualified' unless runtime_url.scheme == 'http' && runtime_url.host == '127.0.0.1' && runtime_url.path.empty? && runtime_url.query.nil? && runtime_url.userinfo.nil?
port = runtime_url.port
Dir.chdir(File.expand_path('..', __dir__))
evidence = File.expand_path(options['evidence'])
FileUtils.mkdir_p(evidence)
phase = options['phase']
persist = options.fetch('persist-to', '.wrangler/state')
tide = 'OTC-58e73c43'
current = 'OTC-00bdf4b5'
window = { 'from' => '2027-01-01T00:00:00-08:00', 'to' => '2027-01-02T00:00:00-08:00' }
cases = [
    ['unknown-station', '/stations/does-not-exist', {}],
    ['list-limit', '/stations', { 'limit' => '1' }],
    ['list-default', '/stations', {}],
    ['openapi', '/openapi.json', {}],
    ['search', '/stations', { 'q' => 'CHARLESTON', 'limit' => '5' }],
    ['near', '/stations', { 'lat' => '32.7808333', 'lon' => '-79.9236111', 'radius_km' => '10', 'limit' => '5' }],
    ['bbox', '/stations', { 'bbox' => '-80,32,-79,33', 'limit' => '5' }],
    ['station', "/stations/#{tide}", {}],
    ['station-extremes', "/stations/#{tide}/extremes", window.merge('datum' => 'mean_level', 'units' => 'feet')],
    ['station-timeline', "/stations/#{tide}/timeline", window.merge('datum' => 'mean_level', 'units' => 'meters', 'step' => '21600')],
    ['position-extremes', '/extremes', window.merge('lat' => '32.7808333', 'lon' => '-79.9236111', 'radius_km' => '1', 'datum' => 'mean_level', 'units' => 'meters')],
    ['position-timeline', '/timeline', window.merge('lat' => '32.7808333', 'lon' => '-79.9236111', 'radius_km' => '1', 'datum' => 'mean_level', 'units' => 'feet', 'step' => '21600')],
    ['currents', "/stations/#{current}/currents", window.merge('bin' => '14', 'step' => '21600', 'units' => 'knots')],
    ['current-events', "/stations/#{current}/current-events", window.merge('bin' => '14', 'units' => 'm/s')],
    ['subordinate-events', '/stations/OTC-0034ae14/current-events', window.merge('bin' => '1', 'units' => 'knots')],
    ['subordinate-refusal', '/stations/OTC-0034ae14/currents', window.merge('bin' => '1', 'step' => '21600', 'units' => 'm/s')]
]
cases.select! { |name, _, _| name.start_with?('list-') } if options['list-limit-only']
runtime_log = File.open(File.join(evidence, "routes-#{phase}-runtime.log"), 'w')
log = File.open(File.join(evidence, "routes-#{phase}.log"), 'w')
runtime_log.sync = log.sync = true
pid = nil
begin
    expected = {}
    attribution = {}
    datestamp = nil
    if phase == 'green'
        raise 'GREEN requires --sdk and --release-dir' unless options['sdk'] && options['release-dir']
        sdk = File.expand_path(options['sdk'])
        source = File.expand_path(options['release-dir'])
        tree, status = Open3.capture2('git', '-C', sdk, 'rev-parse', 'HEAD^{tree}')
        raise 'SDK is not the qualified eecc663 tree' unless status.success? && tree.strip == 'f4ff63bf3eeda12b251be7744075ef94437fb5e1'
        native = File.join(source, 'OTC_20261011.jsonl')
        pin = options['ci-subset'] ? '58caefc7a17a3a2e6caadbe2a08d4772254baedbd3a537dc6a28b652a03396df' : '7695d765a6787fb21c51d41cb6449b6699f019489c25e7a3dbbb6aa2aad7d1e5'
        raise 'Native release pin mismatch' unless Digest::SHA256.file(native).hexdigest == pin
        metadata = File.join(source, 'OTC_20261011.meta.json')
        raise 'Metadata pin mismatch' unless Digest::SHA256.file(metadata).hexdigest == 'a4c949c15be3ed070a2f416353ee6e1cfdf8be3b5af7fa250b6ae105f6db70ba'
        datestamp = JSON.parse(File.read(metadata)).fetch('release').fetch('datestamp')
        qualification = options['ci-subset'] ? 'seven-record native CI subset; not the full release' : 'full pinned 475953736-byte native release'
        log.puts "Input: #{qualification}"
        puts "Input: #{qualification}"
        unless options['skip-stage']
            %w[OTC_20261011.jsonl OTC_20261011.meta.json OTC_20261011.sha256].each do |name|
                command = ['./node_modules/.bin/wrangler', 'r2', 'object', 'put', "otc-qualification/#{name}", '--file', File.join(source, name), '--local', '--persist-to', persist]
                stdout, stderr, status = Open3.capture3({ 'WRANGLER_SEND_METRICS' => 'false' }, *command)
                runtime_log.puts stdout, stderr
                raise "Local R2 staging failed: #{name}" unless status.success?
            end
        end
        reference = lambda do |call|
            opened = call.call('open', { file: native, mode: 'stream', auto_update: false })
            raise 'Python could not open bound native release' unless opened['ok']
            cases.each do |name, path, query|
                ids = []
                if name == 'openapi'
                    expected[name] = JSON.parse(File.read('../../api/openapi.json'))
                elsif name == 'unknown-station'
                    expected[name] = call.call('station', { station_id: 'does-not-exist' })
                elsif name.start_with?('list-')
                    expected[name] = call.call('stations', {})
                    ids = expected[name].fetch('result').fetch('station_ids')
                    ids = ids.first(Integer(query.fetch('limit'))) if query.key?('limit')
                    expected[name]['result']['station_ids'] = ids
                elsif name == 'search'
                    expected[name] = call.call('search', { name: query.fetch('q'), limit: Integer(query.fetch('limit')) })
                    ids = expected[name].fetch('result').fetch('station_ids')
                elsif name == 'near'
                    expected[name] = call.call('near', query.to_h { |key, value| [key, key == 'limit' ? Integer(value) : Float(value)] })
                    ids = expected[name].fetch('result').fetch('station_ids')
                elsif name == 'bbox'
                    bounds = %w[west south east north].zip(query.fetch('bbox').split(',').map { |value| Float(value) }).to_h
                    expected[name] = call.call('bbox', bounds.merge('limit' => Integer(query.fetch('limit'))))
                    ids = expected[name].fetch('result').fetch('station_ids')
                elsif name == 'station'
                    ids = [tide]
                    expected[name] = call.call('require_station', { station_id: tide })
                else
                    id = path.split('/')[2]
                    if name.start_with?('position-')
                        nearest = call.call('nearest', { lat: Float(query.fetch('lat')), lon: Float(query.fetch('lon')), max_km: Float(query.fetch('radius_km')) })
                        id = nearest.fetch('result').fetch('station_id')
                        raise 'No native nearest station' unless id
                    end
                    ids = [id]
                    args = { station_id: id, start: query.fetch('from'), stop: query.fetch('to') }
                    %w[datum units set_id offset_id].each { |key| args[key] = query[key] if query.key?(key) }
                    args[:step_s] = Integer(query['step']) if query.key?('step')
                    args[:bin_number] = Integer(query['bin']) if query.key?('bin')
                    op = name.include?('timeline') ? 'heights' : name.include?('extremes') ? 'extremes' : name.end_with?('events') ? 'current_events' : 'currents'
                    expected[name] = call.call(op, args)
                end
                header = call.call('attribution', { station_ids: ids })
                raise "Native attribution failed: #{name}" unless header['ok']
                attribution[name] = header.fetch('result').fetch('attribution')
            end
            call.call('close', {})
        end
        if options['reuse-reference']
            requests = File.readlines(File.join(evidence, 'python-requests.jsonl')).map { |line| JSON.parse(line) }
            results = File.readlines(File.join(evidence, 'python-results.jsonl')).map { |line| JSON.parse(line) }
            reference.call(lambda do |op, args|
                request = JSON.parse(JSON.generate({ op: op, args: args }))
                raise "Recorded Python request differs: #{op}" unless requests.shift == request
                results.shift || raise('Recorded Python result missing')
            end)
            raise 'Unused recorded reference results' unless requests.empty? && results.empty?
        else
            native_errors = File.open(File.join(evidence, 'python-stderr.log'), 'w')
            native_requests = File.open(File.join(evidence, 'python-requests.jsonl'), 'w')
            native_results = File.open(File.join(evidence, 'python-results.jsonl'), 'w')
            begin
                Open3.popen2({ 'PYTHONDONTWRITEBYTECODE' => '1' }, ENV.fetch('PYTHON', 'python3'), File.join(sdk, 'conformance/runners/python/runner.py'), err: native_errors) do |input, output, thread|
                    hello = output.gets
                    raise 'Python runner handshake absent; see python-stderr.log' unless hello && JSON.parse(hello).key?('hello')
                    call = lambda do |op, args|
                        request = JSON.generate({ op: op, args: args })
                        native_requests.puts request
                        input.puts request
                        input.flush
                        raise "Python #{op} timeout" unless IO.select([output], nil, nil, 180)
                        line = output.gets
                        raise "Python #{op} exited" unless line
                        native_results.puts line
                        JSON.parse(line)
                    end
                    reference.call(call)
                    input.close
                    raise 'Python runner failed' unless thread.value.success?
                end
            ensure
                [native_errors, native_requests, native_results].each(&:close)
            end
        end
    end
    TCPServer.open('127.0.0.1', port).close
    command = ['./node_modules/.bin/wrangler', 'dev', '--local', '--ip', '127.0.0.1', '--port', port.to_s, '--var', 'ENABLE_LOCAL_QUALIFICATION:true']
    command.concat(['--persist-to', persist])
    pid = Process.spawn({ 'WRANGLER_SEND_METRICS' => 'false' }, *command, out: runtime_log, err: runtime_log, pgroup: true)
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 30
    loop do
        raise 'Owned runtime exited during startup' if Process.waitpid(pid, Process::WNOHANG)
        begin
            TCPSocket.open('127.0.0.1', port).close
            break
        rescue Errno::ECONNREFUSED
            raise 'Runtime startup timed out' if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
            sleep 0.1
        end
    end
    fetch_route = lambda do |path|
        Net::HTTP.start('127.0.0.1', port, nil, nil, nil, nil, read_timeout: 180) do |http|
            request = Net::HTTP::Get.new(path)
            request['CF-Connecting-IP'] = '192.0.2.88'
            http.request(request)
        end
    end
    cases.each do |name, path, query|
        path += '?' + URI.encode_www_form(query) unless query.empty?
        response = fetch_route.call(path)
        File.write(File.join(evidence, "#{phase}-#{name}.json"), response.body)
        expected_status = phase == 'red' ? 501 : expected.fetch(name).fetch('ok', true) ? 200 : 400
        line = "#{phase.upcase} #{name}: HTTP #{response.code}"
        log.puts line
        puts line
        raise "#{name}: expected HTTP #{expected_status}, got #{response.code}" unless response.code.to_i == expected_status
        next if phase == 'red'
        body = JSON.parse(response.body)
        want = expected.fetch(name)
        if name.start_with?('list-')
            line = "#{name}: requested_limit=#{query.fetch('limit', 'unlimited')} actual_count=#{body.fetch('result').fetch('station_ids').length} expected_count=#{want.fetch('result').fetch('station_ids').length}"
            log.puts line
            puts line
        end
        compare = lambda do |actual, reference, location|
            if reference.is_a?(Hash)
                raise "#{location}: field set differs" unless actual.is_a?(Hash) && actual.keys.sort == reference.keys.sort
                reference.each { |key, value| compare.call(actual.fetch(key), value, "#{location}/#{key}") }
            elsif reference.is_a?(Array)
                raise "#{location}: item count differs" unless actual.is_a?(Array) && actual.size == reference.size
                reference.each_with_index { |value, index| compare.call(actual[index], value, "#{location}/#{index}") }
            elsif reference.is_a?(Numeric)
                raise "#{location}: native numeric mismatch" unless actual.is_a?(Numeric) && (actual - reference).abs <= 0.00001
            elsif location.end_with?('/time') && reference.is_a?(String)
                raise "#{location}: native event time mismatch" unless (Time.iso8601(actual) - Time.iso8601(reference)).abs <= 1
            else
                raise "#{location}: native value mismatch" unless actual == reference
            end
        end
        compare.call(body, want, name)
        raise "#{name}: DATESTAMP mismatch" unless response['DATESTAMP'] == datestamp
        decoded = URI::DEFAULT_PARSER.unescape(response.fetch('Attribution')).force_encoding(Encoding::UTF_8)
        raise "#{name}: SDK attribution mismatch" unless decoded == attribution.fetch(name)
        log.puts "GREEN #{name}: canonical wire, Python values, attribution and DATESTAMP match"
        puts "GREEN #{name}: canonical wire, Python values, attribution and DATESTAMP match"
    end
    timeline = cases.find { |name, _, _| name == 'station-timeline' }
    if phase == 'green' && timeline
        [['date-only-from', 'from', '2027-01-01'], ['offset-free-to', 'to', '2027-01-02T00:00:00']].each do |name, field, value|
            query = timeline[2].merge(field => value)
            path = timeline[1] + '?' + URI.encode_www_form(query)
            response = fetch_route.call(path)
            body = JSON.parse(response.body)
            File.write(File.join(evidence, "#{phase}-#{name}.json"), response.body)
            line = "#{name}: HTTP #{response.code}, code=#{body.dig('error', 'code') || 'none'}"
            log.puts line
            puts line
            raise "#{name}: missing explicit offset must return invalid_argument" unless response.code.to_i == 400 &&
                body == { 'ok' => false, 'error' => { 'code' => 'invalid_argument', 'message' => "#{field} must be an ISO 8601 timestamp with an explicit offset", 'fields' => {} } }
        end
    end
ensure
    if pid
        begin
            Process.kill('TERM', -pid)
            Process.waitpid(pid)
        rescue Errno::ESRCH, Errno::ECHILD
            # Cleanup is limited to the runtime started by this proof.
        end
    end
    runtime_log.close
    log.close
end
