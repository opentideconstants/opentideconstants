require 'json'
require 'net/http'
require 'optparse'
require 'socket'

options = {}
OptionParser.new do |parser|
    %w[url phase evidence].each do |name|
        parser.on("--#{name} VALUE") { |value| options[name] = value }
    end
end.parse!
abort 'Required: --url http://127.0.0.1:8787 --phase red|green --evidence PATH' unless
    options['url'] == 'http://127.0.0.1:8787' && %w[red green].include?(options['phase']) && options['evidence']
Dir.chdir(File.expand_path('..', __dir__))
phase = options['phase']
log = File.open(File.join(options['evidence'], "limits-#{phase}.log"), 'w')
runtime_log = File.open(File.join(options['evidence'], "limits-#{phase}-runtime.log"), 'w')
log.sync = runtime_log.sync = true
result = { phase: phase, status: 'RUNNING', witnesses: [] }
pid = nil
emit = lambda do |message|
    puts message
    $stdout.flush
    log.puts message
end
stop = lambda do
    if pid
        begin
            Process.kill('TERM', -pid)
            Process.waitpid(pid)
        rescue Errno::ESRCH, Errno::ECHILD
            # Only this probe's already-exited runtime is eligible for cleanup.
        end
        pid = nil
    end
end
start = lambda do |seed_enabled|
    TCPServer.open('127.0.0.1', 8787).close
    command = %w[./node_modules/.bin/wrangler dev --local --ip 127.0.0.1 --port 8787 --persist-to .wrangler/state --var ENABLE_LOCAL_QUALIFICATION:true]
    command += ['--var', 'ENABLE_LIMIT_SEED:true'] if seed_enabled
    pid = Process.spawn({ 'WRANGLER_SEND_METRICS' => 'false' }, *command, out: runtime_log, err: runtime_log, pgroup: true)
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 30
    loop do
        raise 'Owned runtime exited during startup' if Process.waitpid(pid, Process::WNOHANG)
        begin
            TCPSocket.open('127.0.0.1', 8787).close
            break
        rescue Errno::ECONNREFUSED
            raise 'Runtime startup timed out' if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
            sleep 0.1
        end
    end
end
request = lambda do |ip, seed = nil|
    path = seed ? '/__qualification/limits/seed' : '/OTC_20261011.sha256'
    message = seed ? Net::HTTP::Post.new(path) : Net::HTTP::Get.new(path)
    message['CF-Connecting-IP'] = ip
    if seed
        message['Content-Type'] = 'application/json'
        message.body = JSON.generate(seed)
    end
    Net::HTTP.start('127.0.0.1', 8787, nil, nil, nil, nil, read_timeout: 30) { |http| http.request(message) }
end
seed = lambda do |ip, day_count|
    response = request.call(ip, { minute_count: 0, day_count: day_count })
    raise "Real DO seed failed: #{response.code} #{response.body}" unless response.code == '200'
    JSON.parse(response.body)
end

begin
    start.call(phase == 'green')
    # Avoid crossing a real minute boundary during the bounded 101-request sequence.
    remaining = 60 - Time.now.to_f % 60
    sleep remaining + 0.1 if remaining < 10
    %w[192.0.2.1 192.0.2.2 192.0.2.3].each do |ip|
        seed.call(ip, 0) if phase == 'green'
        minute = Time.now.to_i / 60
        statuses = 101.times.map { request.call(ip).code.to_i }
        raise 'Probe crossed a real minute boundary; no result claimed' unless Time.now.to_i / 60 == minute
        raise "First 100 requests not admitted for #{ip}: #{statuses.tally}" unless statuses.first(100).all?(200)
        expected = phase == 'red' ? 200 : 429
        raise "Request101 #{ip}: expected #{expected}, got #{statuses.last}" unless statuses.last == expected
        result[:witnesses] << { ip: ip, first_100: 200, request_101: statuses.last }
        emit.call("#{phase.upcase} #{ip}: requests1-100=200 request101=#{statuses.last}")
    end
    if phase == 'green'
        ip = '192.0.2.4'
        seeded = seed.call(ip, 9999)
        raise 'Wrong real storage seed' unless seeded['day_count'] == 9999 && seeded['minute_count'] == 0
        admitted = request.call(ip)
        denied = request.call(ip)
        raise "Daily boundary failed: #{admitted.code}/#{denied.code}" unless admitted.code == '200' && denied.code == '429'
        raise 'Wrong denial reason' unless JSON.parse(denied.body)['limit'] == 'day'
        emit.call('GREEN seeded actual DO day_count=9999: request10000=200 request10001=429 (day)')
        stop.call
        start.call(false)
        persisted = request.call(ip)
        raise 'Daily counter lost across restart' unless persisted.code == '429' && JSON.parse(persisted.body)['limit'] == 'day'
        raise 'Proof crossed day boundary' unless Time.now.to_i / 86400 == seeded['day']
        emit.call('GREEN runtime restart: same IP remains429 (day); persisted count=10000')
        control = request.call(ip, { minute_count: 0, day_count: 0 })
        raise 'Seed control available without local test flag' unless control.code == '403'
        emit.call('GREEN seed control disabled without explicit local test flag:403')
        result[:seeded_daily_boundary] = true
        result[:restart_persistence] = true
        result[:seed_disabled_by_default] = true
    end
    result[:status] = phase == 'red' ? 'REPRODUCED' : 'PASS'
rescue StandardError => error
    result[:status] = 'FAIL'
    result[:error] = "#{error.class}: #{error.message}"
    emit.call(result[:error])
ensure
    stop.call
    File.write(File.join(options['evidence'], "limits-#{phase}-result.json"), JSON.pretty_generate(result) + "\n")
    File.write(File.join(options['evidence'], 'limits-result.json'), JSON.pretty_generate(result) + "\n")
    log.close
    runtime_log.close
end
exit(result[:status] == 'FAIL' ? 1 : 0)
