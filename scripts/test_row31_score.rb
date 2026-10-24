# Direct checks for the source-aware Row31 score (ruby scripts/test_row31_score.rb).
require 'json'
require 'digest'

root = File.expand_path('..', __dir__)
card = JSON.parse(File.read(File.join(root, 'scorecard.json')))
row = card.fetch('rows').find { |entry| entry.fetch('id') == '31' }
raise 'Row31 still uses the unamended inherited-reference predicate' unless row['predicate_changed'] == true && row.key?('source_aware_amendment')
require_relative 'score_row31'

evidence_path = File.join(root, row.fetch('source_aware_amendment').fetch('requirements_evidence'))
policy = JSON.parse(File.read(evidence_path))
raise 'Scorecard evidence hash differs' unless Digest::SHA256.file(evidence_path).hexdigest == row.fetch('source_aware_amendment').fetch('requirements_evidence_sha256')
raise 'Strict4600/1753 history was lost' unless policy.fetch('historical_strict').slice('complete', 'incomplete', 'verdict') == {'complete' => 4600, 'incomplete' => 1753, 'verdict' => 'LOSS'}
raise 'Own Gold cells excluded' if policy.fetch('excluded_requirements').any? { |lead, _| lead == 'noaa/9431011' }
raise 'Wrong inherited population' unless policy.fetch('excluded_requirements').size == 2703 && policy.fetch('excluded_requirements').map(&:first).uniq.size == 1752
controls = policy.fetch('real_controls')
controls.each do |control|
    own_keys = control.fetch('needed_keys') - control.fetch('after_available_keys')
    exclusions = policy.fetch('excluded_requirements').select { |lead, _| lead == control.fetch('sw_lead') }.map(&:last)
    after = own_keys - exclusions
    if control.fetch('sw_lead') == 'noaa/9431011'
        raise 'Gold own negative control no longer fails' unless after.sort == %w[navd88 stnd]
    else
        raise "Actual inherited control did not flip: #{control.fetch('sw_lead')}" unless !own_keys.empty? && after.empty?
    end
end
measurement_path = File.join(root, row.fetch('source_aware_amendment').fetch('measurement_evidence'))
raise 'Final measurement hash differs' unless Digest::SHA256.file(measurement_path).hexdigest == row.fetch('source_aware_amendment').fetch('measurement_evidence_sha256')
measurement = JSON.parse(File.read(measurement_path))
gold = measurement.fetch('controls_after').find { |control| control.fetch('sw_lead') == 'noaa/9431011' }
raise 'Gold positive control removed an own requirement' unless (gold.fetch('needed_keys') & %w[navd88 stnd]).sort == %w[navd88 stnd]
raise 'Gold positive control lacks actual own keys' unless (gold.fetch('after_available_keys') & %w[navd88 stnd]).sort == %w[navd88 stnd]
raise 'Availability parity falsely marked full-row WIN' unless measurement.fetch('source_aware').fetch('availability_verdict') == 'TIE' && row.fetch('label') == 'LOSS'
# A malformed policy must fail before it can remove an ordinary datum obligation.
invalid = Marshal.load(Marshal.dump(policy))
invalid.fetch('excluded_requirements')[0][1] = 'mhw'
begin
    Row31Score.validate_policy(invalid)
    raise 'Non-physical requirement accepted'
rescue ArgumentError => error
    raise unless error.message.include?('physical')
end
invalid = Marshal.load(Marshal.dump(policy))
invalid.fetch('excluded_requirements') << invalid.fetch('excluded_requirements').first
begin
    Row31Score.validate_policy(invalid)
    raise 'Duplicate exclusion accepted'
rescue ArgumentError => error
    raise unless error.message.include?('duplicate')
end
raise 'Old strict history changed' unless row.fetch('historical_label') == 'LOSS'
puts 'PASS source-aware Row31 contract, real inherited controls, Gold own negative control, strict history and fail-loud policy guards'
