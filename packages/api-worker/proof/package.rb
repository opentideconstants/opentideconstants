require 'digest'
require 'fileutils'
require 'json'
require 'net/http'
require 'open3'
require 'optparse'
require 'socket'
require 'zlib'

options = {}
OptionParser.new do |parser|
    parser.on('--sdk PATH') { |value| options[:sdk] = File.expand_path(value) }
    parser.on('--prepare-sdk') { options[:prepare_sdk] = true }
    parser.on('--release-dir PATH') { |value| options[:release] = File.expand_path(value) }
    parser.on('--evidence PATH') { |value| options[:evidence] = File.expand_path(value) }
end.parse!
abort 'Required: --sdk PATH' unless options[:sdk]
if options[:prepare_sdk]
    sdk_tree, status = Open3.capture2('git', '-C', options[:sdk], 'rev-parse', 'HEAD^{tree}')
    abort 'SDK must match merged eecc663 tree' unless status.success? && sdk_tree.strip == 'f4ff63bf3eeda12b251be7744075ef94437fb5e1'
    state, status = Open3.capture2('git', '-C', options[:sdk], 'status', '--porcelain', '--untracked-files=no')
    abort 'SDK source must be clean' unless status.success? && state.empty?
    abort 'Accepted SDK build missing' unless File.file?(File.join(options[:sdk], 'js/dist/index.js'))
    destination = File.expand_path('../dist/sdk', __dir__)
    FileUtils.mkdir_p(destination)
    # Pack the already-qualified build; do not rerun SDK compilation or conformance.
    ok = system('npm', 'pack', '--ignore-scripts', '--pack-destination', destination, chdir: File.join(options[:sdk], 'js'))
    abort 'SDK pack failed' unless ok
    exit
end
abort 'Required: --release-dir PATH --evidence PATH' unless options[:release] && options[:evidence]

Dir.chdir(File.expand_path('..', __dir__))
FileUtils.mkdir_p('dist/retrieved')
log = File.open(File.join(options[:evidence], 'package.log'), 'a')
log.sync = true
result_path = File.join(options[:evidence], 'package.json')
result = {
    task: 'API-W micro-task 1.1',
    status: 'RUNNING',
    sdk_root: options[:sdk],
    sdk_source_tree: 'f4ff63bf3eeda12b251be7744075ef94437fb5e1',
    constraints: {
        source: 'https://developers.cloudflare.com/workers/platform/limits/',
        worker_uncompressed_limit_bytes: 64 * 1024 * 1024,
        compressed_size_limit: nil,
        isolate_memory_mb: 128,
        qualification: 'Local simulation does not prove deployed limits, latency, cost, or SDK prediction.'
    },
    objects: []
}
run = lambda do |*command|
    log.puts "$ #{command.join(' ')}"
    stdout, stderr, status = Open3.capture3({ 'WRANGLER_SEND_METRICS' => 'false' }, *command)
    log.puts stdout, stderr, "exit=#{status.exitstatus}"
    raise "Command failed (#{status.exitstatus}): #{command.join(' ')}" unless status.success?
    stdout
end
measure = lambda do |paths|
    compressor = Zlib::Deflate.new(Zlib::DEFAULT_COMPRESSION, Zlib::MAX_WBITS + 16)
    bytes = 0
    paths.each do |path|
        File.open(path, 'rb') do |file|
            while (chunk = file.read(1024 * 1024))
                bytes += compressor.deflate(chunk).bytesize
            end
        end
    end
    bytes += compressor.finish.bytesize
    compressor.close
    bytes
end

pid = nil
begin
    result[:gate] = 'pins and SDK build'
    raise 'SDK source changed' unless run.call('git', '-C', result[:sdk_root], 'rev-parse', 'HEAD^{tree}').strip == result[:sdk_source_tree]
    raise 'SDK tracked state changed' unless run.call('git', '-C', result[:sdk_root], 'status', '--short', '--untracked-files=no').empty?
    raise 'SDK build missing' unless File.file?(File.join(result[:sdk_root], 'js/dist/index.js'))
    names = %w[OTC_20261011.jsonl OTC_20261011.meta.json OTC_20261011.sha256]
    checksums = File.readlines(File.join(options[:release], names.last)).to_h do |line|
        hash, name = line.split
        [name, hash]
    end
    expected_jsonl = '7695d765a6787fb21c51d41cb6449b6699f019489c25e7a3dbbb6aa2aad7d1e5'
    raise 'Release SHA pin mismatch' unless checksums[names.first] == expected_jsonl
    names.each do |name|
        path = File.join(options[:release], name)
        hash = Digest::SHA256.file(path).hexdigest
        raise "Release checksum mismatch: #{name}" if checksums[name] && hash != checksums[name]
        raise 'Release size mismatch' if name == names.first && File.size(path) != 475_953_736
        result[:objects] << { name: name, bytes: File.size(path), source_sha256: hash }
    end
    result[:versions] = {
        ruby: RUBY_VERSION,
        node: run.call('node', '--version').strip,
        npm: run.call('npm', '--version').strip,
        wrangler: run.call('./node_modules/.bin/wrangler', '--version').strip,
        workerd: run.call('./node_modules/.bin/workerd', '--version').strip
    }
    result[:gate] = 'package measurement'
    result[:release_payload_gzip_bytes] = measure.call(names.map { |name| File.join(options[:release], name) })
    result[:release_payload_bytes] = result[:objects].sum { |item| item[:bytes] }
    result[:storage] = 'R2: unchanged release payload exceeds 64 MiB uncompressed Worker limit'
    result[:bundle_output] = run.call('./node_modules/.bin/wrangler', 'deploy', '--dry-run', '--outdir', 'dist/bundle')
    bundle = Dir.glob('dist/bundle/*.js')
    raise 'Missing Worker bundle' if bundle.empty?
    result[:worker_bundle_bytes] = bundle.sum { |path| File.size(path) }
    result[:worker_bundle_gzip_bytes] = measure.call(bundle)
    raise 'Worker bundle exceeds script limit' if result[:worker_bundle_bytes] > result[:constraints][:worker_uncompressed_limit_bytes]

    result[:gate] = 'local R2 put/get'
    result[:objects].each do |item|
        key = "otc-qualification/#{item[:name]}"
        source = File.join(options[:release], item[:name])
        destination = File.join('dist/retrieved', item[:name])
        run.call('./node_modules/.bin/wrangler', 'r2', 'object', 'put', key, '--file', source, '--local', '--persist-to', '.wrangler/state')
        run.call('./node_modules/.bin/wrangler', 'r2', 'object', 'get', key, '--file', destination, '--local', '--persist-to', '.wrangler/state')
        item[:r2_get_sha256] = Digest::SHA256.file(destination).hexdigest
        raise "R2 get checksum mismatch: #{item[:name]}" unless item[:r2_get_sha256] == item[:source_sha256]
        puts "Local R2 put/get verified: #{item[:name]} (#{item[:bytes]} bytes)"
    end

    result[:gate] = 'local Worker startup'
    # Refuse an occupied port so an unrelated existing server cannot satisfy the proof.
    TCPServer.open('127.0.0.1', 8787).close
    command = %w[npx wrangler dev --local --ip 127.0.0.1 --port 8787 --persist-to .wrangler/state --var ENABLE_LOCAL_QUALIFICATION:true]
    log.puts "$ #{command.join(' ')}"
    pid = Process.spawn({ 'WRANGLER_SEND_METRICS' => 'false' }, *command, out: log, err: log, pgroup: true)
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 30
    loop do
        raise 'Worker exited during startup' if Process.waitpid(pid, Process::WNOHANG)
        begin
            TCPSocket.open('127.0.0.1', 8787).close
            break
        rescue Errno::ECONNREFUSED
            raise 'Worker startup timed out' if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
            sleep 0.1
        end
    end
    result[:gate] = 'full release HTTP retrieval'
    result[:objects].each do |item|
        digest = Digest::SHA256.new
        bytes = 0
        started = Process.clock_gettime(Process::CLOCK_MONOTONIC)
        Net::HTTP.start('127.0.0.1', 8787, nil, nil, nil, nil, read_timeout: 120) do |http|
            http.request_get("/#{item[:name]}") do |response|
                raise "HTTP #{response.code}: #{item[:name]}" unless response.code == '200'
                response.read_body do |chunk|
                    digest.update(chunk)
                    bytes += chunk.bytesize
                end
            end
        end
        item[:worker_http_bytes] = bytes
        item[:worker_http_sha256] = digest.hexdigest
        item[:local_retrieval_seconds] = Process.clock_gettime(Process::CLOCK_MONOTONIC) - started
        raise "Worker HTTP mismatch: #{item[:name]}" unless bytes == item[:bytes] && digest.hexdigest == item[:source_sha256]
        log.puts JSON.generate(item)
        puts "Worker HTTP verified: #{item[:name]} (#{bytes} bytes)"
    end
    result[:status] = 'PASS'
    result[:gate] = 'full unchanged release readable through actual local Worker/R2'
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
    File.write(result_path, JSON.pretty_generate(result) + "\n")
    log.puts JSON.pretty_generate(result)
    log.close
end
exit(result[:status] == 'PASS' ? 0 : 1)
