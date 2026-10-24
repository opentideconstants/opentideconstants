# Source-aware Row31 key-availability subgate. It does not score prediction correctness.
require 'json'
require 'digest'
require 'optparse'

module Row31Score
    PHYSICAL_KEYS = %w[stnd navd88 prvd02 guvd04 vivd09].freeze
    AUTHORITY_SHA256 = '6ae3fd4cab75ef357ed8529340c537a10670d88a7c62152b3b7105cc7eceba31'.freeze

    module_function

    def validate_policy(policy)
        raise ArgumentError, 'Wrong source-aware predicate' unless policy.fetch('predicate') == 'row31-source-aware-2026-10-23'
        raise ArgumentError, 'Wrong classification authority' unless policy.fetch('classification_manifest_sha256') == AUTHORITY_SHA256
        pairs = policy.fetch('excluded_requirements')
        raise ArgumentError, 'Excluded requirements must be unique; duplicate found' unless pairs.uniq.length == pairs.length
        raise ArgumentError, 'Only named physical requirements may be excluded' unless pairs.all? { |pair| pair.is_a?(Array) && pair.length == 2 && pair.first.is_a?(String) && PHYSICAL_KEYS.include?(pair.last) }
        raise ArgumentError, 'Frozen inherited scope differs' unless pairs.length == 2703 && pairs.map(&:first).uniq.length == 1752
        raise ArgumentError, 'Own Gold Beach requirements cannot be excluded' if pairs.any? { |lead, _| lead == 'noaa/9431011' }
        pairs
    end

    def measure(map, policy)
        pairs = validate_policy(policy)
        rows = map.fetch('rows')
        raise ArgumentError, 'Frozen6353 leaders must remain unique and complete' unless rows.length == 6353 && rows.map { |row| row.fetch('sw_lead') }.uniq.length == 6353
        identity = rows.map { |row| row.slice('sw_lead', 'current_station_ids', 'otc_station_ids', 'needed_keys') }.sort_by { |row| row.fetch('sw_lead') }
        raise ArgumentError, 'Original place/source identity or needed-key map changed' unless Digest::SHA256.hexdigest(JSON.generate(identity)) == policy.fetch('frozen_identity_sha256')
        index = rows.to_h { |row| [row.fetch('sw_lead'), row] }
        raise ArgumentError, 'Exclusion has no original own-place requirement' unless pairs.all? { |lead, key| index.fetch(lead).fetch('needed_keys').include?(key) }
        excluded = pairs.group_by(&:first).transform_values { |values| values.map(&:last) }
        measured = rows.map do |row|
            needed = row.fetch('needed_keys')
            available = row.fetch('after_available_keys')
            strict_missing = needed - available
            raise ArgumentError, 'Stored missing keys disagree with actual key union' unless strict_missing.sort == row.fetch('after_missing_keys').sort
            own_needed = needed - excluded.fetch(row.fetch('sw_lead'), [])
            {'sw_lead' => row.fetch('sw_lead'), 'strict_missing_keys' => strict_missing,
             'excluded_inherited_keys' => excluded.fetch(row.fetch('sw_lead'), []),
             'source_aware_missing_keys' => own_needed - available}
        end
        strict_missing = measured.reject { |row| row.fetch('strict_missing_keys').empty? }
        revised_missing = measured.reject { |row| row.fetch('source_aware_missing_keys').empty? }
        {'native_sha256' => map.fetch('summary').fetch('native_sha256'), 'places' => rows.length,
         'historical_strict' => policy.fetch('historical_strict'),
         'strict_current' => {'complete' => rows.length - strict_missing.length, 'incomplete' => strict_missing.length, 'missing_cells' => strict_missing.sum { |row| row.fetch('strict_missing_keys').length }, 'verdict' => strict_missing.empty? ? 'TIE' : 'LOSS'},
         'source_aware' => {'complete' => rows.length - revised_missing.length, 'incomplete' => revised_missing.length,
             'missing_cells' => revised_missing.sum { |row| row.fetch('source_aware_missing_keys').length },
             'excluded_inherited_cells' => pairs.length, 'affected_leads' => excluded.length,
             'availability_verdict' => revised_missing.empty? ? 'TIE' : 'LOSS'},
         'full_row_runtime_acceptance' => 'UNPROVED', 'rows' => measured}
    end
end

if $PROGRAM_NAME == __FILE__
    args = {}
    OptionParser.new do |parser|
        parser.on('--map PATH') { |value| args[:map] = value }
        parser.on('--output PATH') { |value| args[:output] = value }
    end.parse!
    root = File.expand_path('..', __dir__)
    card = JSON.parse(File.read(File.join(root, 'scorecard.json')))
    row = card.fetch('rows').find { |entry| entry.fetch('id') == '31' }
    binding = row.fetch('source_aware_amendment')
    path = File.join(root, binding.fetch('requirements_evidence'))
    raise 'Requirements evidence SHA differs' unless Digest::SHA256.file(path).hexdigest == binding.fetch('requirements_evidence_sha256')
    result = Row31Score.measure(JSON.parse(File.read(args.fetch(:map))), JSON.parse(File.read(path)))
    File.write(args.fetch(:output), JSON.pretty_generate(result) + "\n")
    puts JSON.generate(result.reject { |key, _| key == 'rows' })
end
