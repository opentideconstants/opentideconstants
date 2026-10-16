require 'digest'
require 'fileutils'
require 'json'
require 'optparse'

# Extend an existing pinned release command. The producer's existing inventory
# ingress consumes this capture; no special provider or runtime path is added.
options = {}
OptionParser.new do |parser|
    parser.banner = 'ruby prepare_inputs.rb --profile EXISTING_COMMAND.json --primary-pdf LOCAL_PDF --out NEW_DIRECTORY'
    parser.on('--profile PATH') { |value| options[:profile] = File.expand_path(value) }
    parser.on('--primary-pdf PATH') { |value| options[:pdf] = File.expand_path(value) }
    parser.on('--out PATH') { |value| options[:out] = File.expand_path(value) }
end.parse!
abort 'Require --profile, --primary-pdf and --out' unless %i[profile pdf out].all? { |key| options[key] }
abort 'Output directory must not already exist' if File.exist?(options[:out])

root = File.dirname(__FILE__)
definition = JSON.parse(File.read(File.join(root, 'input-profile.json')))
profile = JSON.parse(File.read(options[:profile]))
argv = profile.fetch('argv').dup
argument = lambda do |flag|
    index = argv.index(flag)
    raise "Profile requires #{flag}" unless index && argv[index + 1]

    argv.fetch(index + 1)
end
verified = lambda do |path, digest|
    raise "Input SHA-256 mismatch: #{path}" unless Digest::SHA256.file(path).hexdigest == digest

    JSON.parse(File.read(path))
end
parent_inventory = argument.call('--source-inventory')
old_digest = definition.fetch('parent_source_inventory_sha256')
raise 'Unexpected parent inventory pin' unless argument.call('--source-inventory-sha256') == old_digest
inventory = verified.call(parent_inventory, old_digest)
parent_findings = argument.call('--source-quality-findings')
findings_digest = definition.fetch('parent_source_quality_findings_sha256')
raise 'Unexpected parent quality pin' unless argument.call('--source-quality-findings-sha256') == findings_digest
findings = verified.call(parent_findings, findings_digest)
capture_path = File.join(root, definition.fetch('capture').fetch('path'))
capture_digest = definition.fetch('capture').fetch('sha256')
verified.call(capture_path, capture_digest)
pdf_digest = definition.fetch('primary_document').fetch('sha256')
raise 'Primary document SHA-256 mismatch' unless Digest::SHA256.file(options[:pdf]).hexdigest == pdf_digest
raise 'Primary document size mismatch' unless File.size(options[:pdf]) == definition.fetch('primary_document').fetch('size')
identity = definition.slice('source', 'version', 'upstream_record_id', 'granularity')
key = identity.values_at('source', 'version', 'upstream_record_id')
raise 'Source identity already present' if inventory.fetch('identities').any? { |row| row.values_at('source', 'version', 'upstream_record_id') == key }
descriptors = [options[:pdf], capture_path].map do |path|
    { 'path' => path, 'sha256' => Digest::SHA256.file(path).hexdigest, 'size' => File.size(path) }
end
descriptors.each do |descriptor|
    existing = inventory.fetch('input_files').find { |row| row.fetch('path') == descriptor.fetch('path') }
    raise 'Existing input path differs' if existing && existing != descriptor

    inventory.fetch('input_files') << descriptor unless existing
end
inventory.fetch('identities') << identity.merge('input_sha256' => [pdf_digest, capture_digest])
FileUtils.mkdir_p(options[:out])
write = lambda do |name, document|
    path = File.join(options[:out], name)
    File.write(path, JSON.pretty_generate(document) + "\n")
    [path, Digest::SHA256.file(path).hexdigest]
end
inventory_path, new_digest = write.call('source-inventory.json', inventory)
raise 'Quality findings parent mismatch' unless findings.fetch('inventory_sha256') == old_digest
findings['inventory_sha256'] = new_digest
findings.fetch('label_facts').each do |fact|
    provenance = fact.fetch('inventory_provenance')
    raise 'Quality finding provenance parent mismatch' unless provenance.fetch('inventory_sha256') == old_digest

    provenance['inventory_sha256'] = new_digest
end
findings_path, new_findings_digest = write.call('source-quality-findings.json', findings)
replacements = {
    '--source-inventory' => inventory_path, '--source-inventory-sha256' => new_digest,
    '--source-quality-findings' => findings_path, '--source-quality-findings-sha256' => new_findings_digest,
    '--out' => File.join(options[:out], 'native-build')
}
changes = replacements.map do |flag, value|
    index = argv.index(flag)
    raise "Profile requires #{flag}" unless index

    previous = argv.fetch(index + 1)
    argv[index + 1] = value
    { 'argument' => flag, 'old' => previous, 'new' => value }
end
cwd = File.expand_path('../../../../scripts/otc', root)
environment = profile.fetch('env').merge('UV_PROJECT_ENVIRONMENT' => File.join(options[:out], 'venv'))
write.call('build-command.json', { 'cwd' => cwd, 'argv' => argv, 'env' => environment })
write.call('input-lineage.json', {
    'parent_profile' => { 'path' => options[:profile], 'sha256' => Digest::SHA256.file(options[:profile]).hexdigest },
    'parent_inventory' => { 'path' => parent_inventory, 'sha256' => old_digest },
    'parent_quality_findings' => { 'path' => parent_findings, 'sha256' => findings_digest },
    'new_input_descriptors' => descriptors, 'new_identity' => identity,
    'argument_changes' => changes,
    'cwd_change' => { 'old' => profile.fetch('cwd'), 'new' => cwd },
    'environment_changes' => { 'UV_PROJECT_ENVIRONMENT' => { 'old' => profile.fetch('env')['UV_PROJECT_ENVIRONMENT'], 'new' => environment.fetch('UV_PROJECT_ENVIRONMENT') } },
    'quality_change' => 'Only the inventory_sha256 at the document root and six inventory_provenance objects is rebound. All six metric findings and scientific verdicts are unchanged.',
    'country_cache_changed' => false,
    'qualification' => definition.fetch('qualification')
})
puts "Prepared #{File.join(options[:out], 'build-command.json')}"
puts "Inventory #{old_digest} -> #{new_digest}; exactly one source identity appended."
puts "Quality #{findings_digest} -> #{new_findings_digest}; only seven inventory digest references rebound."
