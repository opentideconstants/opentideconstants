#!/usr/bin/env ruby
require 'digest'
require 'optparse'
require 'pathname'

# Preserve only NOAA 8660754's acquired datum response and receipt as raw metadata.
# No predictor, normalized datum, source inventory rebuild or network acquisition.
options = {}
OptionParser.new do |parser|
    %w[native noaa-datums congen-run fes-congen-run out].each do |name|
        parser.on("--#{name} PATH") { |value| options[name] = File.expand_path(value) }
    end
end.parse!
missing = %w[native noaa-datums congen-run out] - options.keys
abort "Missing options: #{missing.join(', ')}" unless missing.empty?
root = Pathname.new(__dir__).join('../../../../..').realpath
arguments = ['--cached-noaa-original-metadata']
options.each { |name, value| arguments.concat(["--#{name}", value]) }
inputs = options.slice('native', 'noaa-datums')
inputs['native-meta'] = options.fetch('native').sub(/\.jsonl\z/, '.meta.json')
inputs['noaa-datums-receipt'] = options.fetch('noaa-datums') + '.meta.json'
inputs.each do |name, path|
    arguments.concat(["--#{name}-sha256", Digest::SHA256.file(path).hexdigest])
end
environment = {'PYTHONPATH' => root.join('pipeline/scripts/gesla').to_s}
exec environment, 'bash', root.join('pipeline/bin/build-otc').to_s, *arguments
