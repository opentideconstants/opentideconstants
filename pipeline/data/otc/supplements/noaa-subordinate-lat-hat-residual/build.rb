#!/usr/bin/env ruby
require 'digest'
require 'optparse'
require 'pathname'

# Publish existing DT4b drafts against their exact current native inputs. The
# producer authenticates the computations, tuning evidence and astronomy runs.
options = {}
OptionParser.new do |parser|
    %w[native subordinate-drafts tuning congen-run fes-congen-run out].each do |name|
        parser.on("--#{name} PATH") { |value| options[name] = File.expand_path(value) }
    end
end.parse!
missing = %w[native subordinate-drafts tuning congen-run out] - options.keys
abort "Missing options: #{missing.join(', ')}" unless missing.empty?
root = Pathname.new(__dir__).join('../../../../..').realpath
arguments = ['--cached-subordinate']
options.each { |name, value| arguments.concat(["--#{name}", value]) }
inputs = options.slice('native', 'subordinate-drafts', 'tuning')
inputs['native-meta'] = options.fetch('native').sub(/\.jsonl\z/, '.meta.json')
inputs.each do |name, path|
    arguments.concat(["--#{name}-sha256", Digest::SHA256.file(path).hexdigest])
end
environment = {'PYTHONPATH' => root.join('pipeline/scripts/gesla').to_s}
exec environment, 'bash', root.join('pipeline/bin/build-otc').to_s, *arguments
