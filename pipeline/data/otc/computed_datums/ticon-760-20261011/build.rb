#!/usr/bin/env ruby
# frozen_string_literal: true

require 'json'
require 'digest'

# Import accepted computed data; never run an extrema engine or reinterpret its authentication.
class TiconReleaseProfile
    def initialize(directory = __dir__)
        @directory = File.expand_path(directory)
        @profile = JSON.parse(File.read(File.join(@directory, 'profile.json')))
    end

    def path(relative)
        candidate = File.expand_path(relative, @directory)
        raise "Profile path outside directory: #{relative}" unless candidate.start_with?(@directory + '/')
        candidate
    end

    def check(condition, message)
        raise message unless condition
    end

    def json(relative)
        JSON.parse(File.read(path(relative)))
    end

    def drafts(relative, key, expected)
        rows = File.foreach(path(relative)).map { |line| JSON.parse(line) }
        check(rows.size == expected && rows.map { |row| row.fetch(key) }.uniq.size == expected,
            "Duplicate, missing or extra cohort IDs: #{relative}")
        rows.to_h { |row| [row.fetch(key), row] }
    end

    def verify
        @profile.fetch('inputs').each do |relative, expected|
            check(File.file?(path(relative)), "Missing profile input: #{relative}; hydrate Git LFS inputs")
            check(Digest::SHA256.file(path(relative)).hexdigest == expected, "Profile SHA-256 mismatch: #{relative}")
        end
        receipt = json('prior/subordinate-accepted-receipt.json')
        freeze = json('prior/subordinate-source-freeze.json').fetch('files')
        command = json('prior/subordinate-writer-command.json')
        check(receipt.fetch('source_head') == @profile.fetch('accepted_subordinate_source_head') &&
            receipt.fetch('source_sha256') == @profile.fetch('inputs').fetch('prior/baseline/OTC_20261011.jsonl') &&
            receipt.fetch('candidate_sha256') == @profile.fetch('accepted_subordinate_native_sha256') &&
            receipt.dig('counts', 'augmented_station_ids') == 1182 && receipt.dig('counts', 'new_computed_levels') == 2364 &&
            receipt.fetch('normal_datum_gate_accepted') == false && command.fetch('exit') == 0,
            'Accepted subordinate receipt binding mismatch')
        @profile.fetch('accepted_subordinate_sources').each do |name, hash|
            check(freeze.fetch(name) == hash, "Historical source freeze mismatch: #{name}")
        end
        args = command.fetch('argv')
        {'--native-sha256' => 'prior/baseline/OTC_20261011.jsonl',
         '--native-meta-sha256' => 'prior/baseline/OTC_20261011.meta.json',
         '--subordinate-drafts-sha256' => 'prior/subordinate-drafts.jsonl',
         '--tuning-sha256' => 'prior/subordinate-tuning.json'}.each do |flag, relative|
            index = args.index(flag)
            check(index && args[index + 1] == @profile.fetch('inputs').fetch(relative), "Historical writer binding mismatch: #{flag}")
        end
        @subordinate = drafts('prior/subordinate-drafts.jsonl', 'station_id', 1182)
        @prior_ticon = drafts('prior/ticon-758-drafts.jsonl', 'set_id', 758)
        @new_ticon = drafts('drafts.jsonl', 'set_id', 760)
        @extension_ids = json('convention-unverified-sets.json')
        check(@extension_ids.size == 658 && @extension_ids.uniq.size == 658 &&
            (@extension_ids - @new_ticon.keys).empty?, 'Convention-unverified exact cohort mismatch')
        check(/\A[0-9a-f]{64}\z/.match?(@profile.fetch('expected_native_sha256').to_s),
            'Final output SHA-256 pin required')
        check((@prior_ticon.keys & @new_ticon.keys).empty?, 'Prior and new TICON cohorts overlap')
        check(@new_ticon.values.map { |row| row.fetch('station_id') }.uniq.size == 549, 'New TICON station count mismatch')
        tuning = json('prior/subordinate-tuning.json')
        check(tuning.fetch('passed') == false && tuning.fetch('accepted') == false, 'Failed T5s receipt required')
        @tuning = tuning.fetch('levels')
        @tuning.each_value { |stats| check(stats.fetch('count') == 6 && stats.fetch('passed') == false, 'All six failed T5s comparisons required') }
        @d4 = @profile.fetch('d4_chart_sets').to_h { |row| [row.fetch('set_id'), row] }
        check(@d4.size == 330 && @profile.fetch('d4_chart_sets').size == 330 && (@d4.keys - @prior_ticon.keys).empty?, 'D4 exact cohort mismatch')
        @new_ticon.each_value do |row|
            check(row.fetch('native_json_sha256') == @profile.fetch('inputs').fetch('baseline/OTC_20261011.jsonl'), 'New TICON source binding mismatch')
            verify_pair(row, row.fetch('draft_datum'), 'harmonic_extremes')
        end
        seen = {'subordinate_pairs' => 0, 'prior_ticon_pairs' => 0, 'd4_chart_sets' => 0, 'new_ticon_pairs' => 0}
        File.foreach(path('baseline/OTC_20261011.jsonl')) do |line|
            station = JSON.parse(line)
            id = station.fetch('station_id')
            if (row = @subordinate[id])
                datum = station.fetch('subordinate_offsets').fetch('datum')
                verify_pair(row, datum, 'subordinate_offsets')
                check(row.dig('raw', 'wrapper_sha256') == @profile.fetch('accepted_subordinate_sources').fetch('pipeline/scripts/otc/otc_pipeline/lat_hat_strict.rb'), 'Historical wrapper binding mismatch')
                %w[lat hat].each do |level|
                    check(datum.dig('basis', level, 'uncertainty_m') == @tuning.fetch(level).fetch('sample_sd_m') &&
                        datum.dig('basis', level, 'uncertainty_basis') == 'calibrated', "Subordinate tuning mismatch: #{id}:#{level}")
                end
                seen['subordinate_pairs'] += 1
            end
            station.fetch('constant_sets', []).each do |set|
                set_id = set.fetch('set_id')
                datum = set.fetch('datum', {})
                if (row = @prior_ticon[set_id])
                    check(row.fetch('station_id') == id && row.fetch('native_json_sha256') == @profile.fetch('accepted_subordinate_native_sha256'), 'Prior TICON source binding mismatch')
                    verify_pair(row, datum, 'harmonic_extremes')
                    seen['prior_ticon_pairs'] += 1
                end
                if (row = @d4[set_id])
                    check(row.fetch('station_id') == id && datum['chart_datum'] == 'lat' && datum.dig('named', 'lat') == row.fetch('lat_m'), "D4 chart value mismatch: #{set_id}")
                    seen['d4_chart_sets'] += 1
                end
                if (row = @new_ticon[set_id])
                    check(row.fetch('station_id') == id && !datum.fetch('named', {}).key?('lat') && !datum.fetch('named', {}).key?('hat'), "New TICON input already changed: #{set_id}")
                    seen['new_ticon_pairs'] += 1
                end
            end
        end
        expected = @profile.fetch('expected_counts')
        seen.each { |name, count| check(count == expected.fetch(name), "Imported cohort mismatch: #{name}=#{count}") }
        seen.merge('new_ticon_stations' => 549, 'convention_unverified_sets' => @extension_ids.size)
    end

    def verify_pair(row, datum, method)
        draft = row.fetch('draft_datum')
        raw = row.fetch('raw')
        check(raw.fetch('accepted') == false, 'Computed datum acceptance changed')
        check(datum['zero'] == draft['zero'] && datum['zero_name'] == draft['zero_name'], 'Computed datum frame changed')
        %w[lat hat].each do |level|
            value = raw.fetch("#{level}_m")
            check(value.is_a?(Numeric) && value.finite? && draft.fetch('named').fetch(level) == value &&
                datum.fetch('named').fetch(level) == value, "Cached #{level} value changed")
            basis = datum.fetch('basis').fetch(level)
            check(basis['kind'] == 'computed' && basis['method'] == method && basis['epoch'] == draft.fetch('basis').fetch(level).fetch('epoch') &&
                basis.dig('fallback', 'reason') == 'datum_gate_failed', "Computed #{level} provenance changed")
            if method == 'harmonic_extremes'
                check(!basis.key?('uncertainty_m') && row.fetch('normal_datum_gate_accepted') == false, 'TICON unknown uncertainty or acceptance changed')
            end
        end
    end

    def verify_extension_output(native)
        digest = Digest::SHA256.new
        seen = []
        File.foreach(native) do |line|
            station = JSON.parse(line)
            station.fetch('constant_sets', []).each do |set|
                id = set.fetch('set_id')
                next unless @extension_ids.include?(id)
                seen << id
                datum = set.fetch('datum')
                verify_pair(@new_ticon.fetch(id), datum, 'harmonic_extremes')
                check(datum['chart_datum'] == 'lat', "Missing extension chart pointer: #{id}")
                old = @new_ticon.fetch(id).fetch('draft_datum').fetch('basis').fetch('lat')
                basis = datum.fetch('basis').fetch('lat')
                check(basis.fetch('flags') == old.fetch('flags', []) + ['convention_unverified'], "Extension flags changed: #{id}")
                %w[basis otc_finding].each do |field|
                    text = basis.fetch('fallback').fetch(field)
                    check(text.start_with?(old.fetch('fallback').fetch(field)) &&
                        text.include?('approximate chart-reference stand-in') && text.end_with?('; convention unverified'),
                        "Extension qualification missing: #{id}:#{field}")
                end
                check(basis.fetch('fallback').fetch('basis').include?('(nonofficial)'), "Extension official claim: #{id}")
                restored = JSON.parse(JSON.generate(basis))
                old.key?('flags') ? restored['flags'] = old.fetch('flags') : restored.delete('flags')
                %w[basis otc_finding].each { |field| restored.fetch('fallback')[field] = old.fetch('fallback').fetch(field) }
                check(restored == old, "Unapproved LAT basis change: #{id}")
                datum.delete('chart_datum')
                datum.fetch('basis')['lat'] = old
            end
            digest << JSON.generate(station) << "\n"
        end
        check(seen.sort == @extension_ids.sort, 'Extension output cohort mismatch')
        check(digest.hexdigest == @profile.fetch('integration_structure_sha256'),
            'Extension changed numeric, source, default or other unapproved material')
        digest.hexdigest
    end

    def build(output)
        counts = verify
        output = File.expand_path(output)
        check(!File.exist?(output) || (File.directory?(output) && Dir.empty?(output)), 'Output directory must be empty')
        repo = File.expand_path('../../../../..', @directory)
        pipeline = File.join(repo, 'pipeline')
        env = {'PYTHONDONTWRITEBYTECODE' => '1', 'OPENBLAS_NUM_THREADS' => '1', 'OMP_NUM_THREADS' => '1',
            'PYTHONPATH' => [File.join(pipeline, 'scripts/otc'), File.join(pipeline, 'scripts/gesla')].join(File::PATH_SEPARATOR),
            'UV_OFFLINE' => '1'}
        pins = @profile.fetch('inputs')
        command = [File.join(pipeline, 'bin/build-otc'), '--cached-harmonic-mean',
            '--native', path('baseline/OTC_20261011.jsonl'), '--native-sha256', pins.fetch('baseline/OTC_20261011.jsonl'),
            '--native-meta-sha256', pins.fetch('baseline/OTC_20261011.meta.json'),
            '--harmonic-mean-drafts', path('drafts.jsonl'), '--harmonic-mean-drafts-sha256', pins.fetch('drafts.jsonl'),
            '--convention-unverified-sets', path('convention-unverified-sets.json'),
            '--convention-unverified-sets-sha256', pins.fetch('convention-unverified-sets.json'),
            '--congen-run', path('astro'), '--out', output]
        check(system(env, *command), 'Cached release producer failed')
        native = File.join(output, 'OTC_20261011.jsonl')
        structural_sha256 = verify_extension_output(native)
        check(Digest::SHA256.file(native).hexdigest == @profile.fetch('expected_native_sha256'), 'Produced native hash differs from accepted candidate')
        check(Digest::SHA256.file(File.join(output, 'OTC_20261011.meta.json')).hexdigest == pins.fetch('baseline/OTC_20261011.meta.json'), 'Produced metadata changed')
        result = counts.merge('native_sha256' => Digest::SHA256.file(native).hexdigest,
            'preserved_integration_structure_sha256' => structural_sha256)
        File.write(File.join(output, 'PROFILE_REPORT.json'), JSON.pretty_generate(result) + "\n")
        result
    end
end

if $PROGRAM_NAME == __FILE__
    begin
        profile = TiconReleaseProfile.new
        result = if ARGV == ['--verify']
            profile.verify
        elsif ARGV.length == 2 && ARGV.first == '--out'
            profile.build(ARGV.last)
        else
            raise 'Usage: ruby build.rb --verify | --out EMPTY_DIRECTORY'
        end
        puts JSON.generate(result)
    rescue StandardError => error
        warn error.message
        exit 1
    end
end
