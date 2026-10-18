# frozen_string_literal: true

require 'json'
require 'optparse'
require 'digest'
require 'date'
require 'fileutils'
require 'tmpdir'
require 'tcd'

module OTCExportTCD
    # Native adapters already publish Greenwich, Schureman phases, even where
    # the convention describes a publisher's original local time/meridian.
    # See adapters/{jma,linz,kartverket}.py. Applying their offsets here again
    # would change the qualified constants.
    CONVENTIONS = %w[otc-greenwich-schureman-v1 ticon4-greenwich-schureman-v1
                     noaa-harcon-gmt noaa-currents-harcon-gmt kartverket-utc+1
                     linz-nzst-utc+12 jma-local-meridian fes2022b-ocean-greenwich].freeze

    class Exporter
        def initialize(input:, release:, output_dir:, date:)
            @input, @release_path, @output_dir, @date = input, release, output_dir, date
            raise ArgumentError, 'date must be YYYYMMDD' unless /\A\d{8}\z/.match?(date)
            Date.strptime(date, '%Y%m%d')
            @metadata = JSON.parse(File.read(release))
            raise ArgumentError, 'date does not match release datestamp' unless @metadata.fetch('release').fetch('datestamp') == date
            @rows = File.foreach(input).map { |line| JSON.parse(line) }
            @stations = unique_index(@rows, 'station_id')
            @licences = unique_index(@metadata.fetch('licences'), 'licence_id')
            @conventions = unique_index(@metadata.fetch('conventions'), 'convention_id')
            tables = @metadata.fetch('astro_tables')
            raise ArgumentError, 'expected one shared native astronomical table' unless tables.size == 1
            @table = tables.first
            @terms = @table.fetch('constituents').sort.map do |name, row|
                { name: name, speed: row.fetch('speed_deg_per_hour'),
                  equilibrium: row.fetch('v0u_deg'), node_factors: row.fetch('f') }
            end
            @indices = @terms.each_with_index.to_h { |term, index| [term.fetch(:name), index] }
            @report = {
                'input_sha256' => Digest::SHA256.file(input).hexdigest,
                'release_sha256' => Digest::SHA256.file(release).hexdigest,
                'writer_version' => Gem.loaded_specs.fetch('tcd').version.to_s,
                'astro_table_id' => @table.fetch('astro_table_id'),
                'astro_tables_sha256' => @table.fetch('tables_sha256'),
                'year_range' => [@table.fetch('first_year'), @table.fetch('last_year')],
                'stations' => @rows.map do |row|
                    { 'station_id' => row.fetch('station_id'), 'recommended_set_id' => row['recommended_set_id'],
                      'input_record_sha256' => Digest::SHA256.hexdigest(JSON.generate(row)) }
                end,
                'outputs' => {}
            }
        end

        def run
            FileUtils.mkdir_p(@output_dir)
            Dir.mktmpdir('.otc-tcd-', @output_dir) do |temporary|
                %w[recommended complete].each { |mode| export(mode, temporary) }
                partial = @report.fetch('outputs').values.any? { |output| output.fetch('records').any? { |record| record['status'] == 'unrepresentable' } }
                @report['status'] = partial ? 'partial' : 'complete'
                report_name = "OTC_#{@date}-tcd-report.json"
                File.write(File.join(temporary, report_name), JSON.pretty_generate(@report) + "\n")
                Dir.children(temporary).each { |name| FileUtils.mv(File.join(temporary, name), File.join(@output_dir, name)) }
            end
            counts = @report.fetch('outputs').transform_values { |output| output.slice('exported', 'unrepresentable', 'no_prediction') }
            puts JSON.generate(status: @report.fetch('status'), outputs: counts, report: File.join(@output_dir, "OTC_#{@date}-tcd-report.json"))
            @report.fetch('status') == 'partial' ? 2 : 0
        end

        private

        def unique_index(rows, key)
            rows.each_with_object({}) do |row, result|
                id = row.fetch(key)
                raise ArgumentError, "duplicate #{key}: #{id}" if result.key?(id)
                result[id] = row
            end
        end

        def text(value)
            value.to_s.each_codepoint.map do |code|
                if code.zero? || code > 255
                    format('\\u{%X}', code)
                else
                    code.chr(Encoding::UTF_8)
                end
            end.join
        end

        def bytes(value)
            value.encode(Encoding::ISO_8859_1).bytesize
        end

        def bounded(value, label, limit)
            result = text(value)
            raise ArgumentError, "#{label} exceeds #{limit} ISO-8859-1 bytes" if bytes(result) > limit
            result
        end

        def display_name(row, suffix)
            ending = " [#{row.fetch('station_id')}]#{suffix}"
            available = 89 - bytes(ending)
            raise ArgumentError, 'identity and label exceed TCD name capacity' if available < 1
            label = text(row.fetch('name')).encode(Encoding::ISO_8859_1).byteslice(0, available)
            label.encode(Encoding::UTF_8).rstrip + ending
        end

        def writer
            TCD::Writer.new(constituents: @terms, start_year: @table.fetch('first_year'),
                            number_of_years: @table.fetch('last_year') - @table.fetch('first_year') + 1,
                            last_modified: @date, restrictions: ['See station notes'],
                            params: { tzfile_size: 90, restriction_size: 90, datum_size: 90 })
        end

        def common(row, native, suffix, identity)
            licence = @licences.fetch(native.fetch('licence_id'))
            attribution = licence.fetch('attribution')
            raise ArgumentError, 'licence attribution is empty' if attribution.empty?
            datum = native.fetch('datum', {})
            datum_note = if native.key?('reference_station_id')
                             'Subordinate heights use native reference offsets; see native datum metadata.'
                         elsif datum.key?('msl_offset_m')
                             'Native zero retained; no derived chart datum.'
                         else
                             'Mean-level-relative harmonics; absolute chart datum is unavailable.'
                         end
            details = {
                station_id: row.fetch('station_id'), name: row.fetch('name'), identity: identity,
                source: native['source'], source_version: native['source_version'], source_record_id: native['source_record_id'],
                licence: licence, provenance: native['provenance'], qc_status: native['qc_status'],
                qc_flags: native['qc_flags'], time_base: native['time_base'], fallback: native['fallback'],
                datum: datum.slice('zero', 'zero_name', 'chart_datum', 'msl_offset_m'),
                datum_note: datum_note
            }
            if native['current_projection']
                details[:current_projection] = native.fetch('current_projection')
                details[:datum_note] = 'Signed major-axis current mean in knots; no vertical datum. Minor-axis motion and vector speed/direction are omitted.'
            end
            {
                name: display_name(row, suffix), latitude: row.fetch('lat'), longitude: row.fetch('lon'),
                tzfile: bounded(row.fetch('timezone'), 'timezone', 88), country: bounded(row.fetch('country'), 'country', 49),
                source: bounded([native['source'], native['source_version']].compact.join(' '), 'source', 89),
                station_id_context: 'OpenTideConstants', station_id: row.fetch('station_id'),
                notes: bounded(JSON.generate(details), 'notes', 9999), restriction: 'See station notes',
                legalese: bounded(native.fetch('licence_id'), 'licence_id', 69), date_imported: @date.to_i,
                level_units: 'meters'
            }
        end

        def harmonic(row, native, ordinal, bin = nil)
            return current_harmonic(row, native, ordinal, bin) if native['quantity'] == 'current'
            raise ArgumentError, 'not a water-level constant set' unless native['quantity'] == 'water_level'
            convention = @conventions.fetch(native.fetch('convention_id'))
            unless CONVENTIONS.include?(convention.fetch('convention_id')) && convention['astro_table_id'] == @table.fetch('astro_table_id') &&
                   convention['v0_model'] == 'schureman_tcd' && convention['nodal_handling'] == 'f_u_at_prediction'
                raise ArgumentError, 'unsupported native harmonic convention'
            end
            coefficients = native.fetch('constituents')
            raise ArgumentError, 'no harmonic coefficients' if coefficients.empty?
            suffix = if native['fallback']
                         " [fallback:#{native.fetch('fallback').fetch('reason')}:#{ordinal}]"
                     elsif native['set_id'] != row['recommended_set_id']
                         " [alt:#{native.fetch('source')}:#{ordinal}]"
                     else
                         ''
                     end
            record = common(row, native, suffix, native.fetch('set_id'))
            amplitudes = Array.new(@terms.size, 0.0)
            epochs = Array.new(@terms.size, 0.0)
            coefficients.each do |coefficient|
                index = @indices.fetch(coefficient.fetch('name'))
                amplitude = coefficient.fetch('amplitude_m')
                phase = coefficient.fetch('phase_deg')
                raise ArgumentError, 'amplitude exceeds TCD range' unless amplitude.finite? && amplitude.between?(0, 52.4287)
                raise ArgumentError, 'phase outside 0..360' unless phase.finite? && phase.between?(0, 360)
                amplitudes[index], epochs[index] = amplitude, phase
            end
            datum = native.fetch('datum', {})
            # chart_datum points to a named level ABOVE the native zero. It is
            # not the zero's name and must not relabel an unchanged Z0.
            datum_name = datum['zero_name'] || (datum.key?('msl_offset_m') ? 'Native zero (unspecified)' : 'Mean level (relative only)')
            record.merge(record_type: 1, amplitudes: amplitudes, epochs: epochs, zone_offset: 0,
                         datum: bounded(datum_name, 'datum', 89), datum_offset: datum.fetch('msl_offset_m', 0))
        end

        def current_harmonic(row, native, ordinal, bin)
            raise ArgumentError, 'current bin unavailable' unless bin
            azimuth, flood, ebb, mean = bin.values_at('azimuth_deg', 'mean_flood_dir_deg', 'mean_ebb_dir_deg', 'mean_major_ms')
            raise ArgumentError, 'current axis/mean/orientation unavailable' if [azimuth, flood, ebb, mean].any?(&:nil?)
            sigma = Math.cos((azimuth - flood) * Math::PI / 180) < 0 ? -1 : 1
            projected = native.merge('quantity' => 'water_level', 'datum' => {'zero_name' => 'Unknown', 'msl_offset_m' => sigma * mean / 0.5144444444444445},
                                     'current_projection' => {'quantity' => 'signed_major_axis', 'native_station_id' => row.fetch('station_id'), 'bin' => bin, 'sigma' => sigma,
                                                              'meters_per_second_per_knot' => 0.5144444444444445},
                                     'constituents' => bin.fetch('constituents').map do |term|
                                         {'name' => term.fetch('name'), 'amplitude_m' => term.fetch('major_amplitude_ms') / 0.5144444444444445,
                                          'phase_deg' => (term.fetch('major_phase_deg') + (sigma < 0 ? 180 : 0)) % 360}
                                     end)
            export_row = current_row(row, bin.fetch('bin'))
            record = harmonic(export_row, projected, ordinal)
            suffix = " [signed_major_axis bin:#{bin.fetch('bin')}]"
            suffix += " [alt:#{ordinal}]" if native['set_id'] != row['recommended_set_id']
            record.merge(name: display_name(export_row, suffix), level_units: 'knots', direction_units: 'degrees true', min_direction: ebb, max_direction: flood)
        end

        def current_row(row, number)
            return row if number == row.fetch('default_current_bin')

            row.merge('station_id' => "#{row.fetch('station_id')}_bin_#{number}")
        end

        def current_subordinate(row, native, references)
            key = [native.fetch('reference_station_id'), native.fetch('reference_bin')]
            reference = references.fetch(key) { raise ArgumentError, "current reference bin #{key.inspect} has no exported harmonic record" }
            flood, ebb = native.values_at('mean_flood_dir_deg', 'mean_ebb_dir_deg')
            raise ArgumentError, 'current flood/ebb orientation unavailable' if flood.nil? || ebb.nil?
            metadata = native.merge('source' => 'noaa-currents', 'current_projection' => {'quantity' => 'signed_major_axis', 'native_station_id' => row.fetch('station_id'), 'bin' => native})
            record = common(current_row(row, native.fetch('bin')), metadata, " [signed_major_axis bin:#{native.fetch('bin')}]", "#{row.fetch('station_id')}/current-offset/#{native.fetch('bin')}")
            record.merge(record_type: 2, reference_station: reference, level_units: 'knots', direction_units: 'degrees true',
                         min_direction: ebb, max_direction: flood,
                         min_time_add: native.fetch('time_adj_max_ebb_min'), max_time_add: native.fetch('time_adj_max_flood_min'),
                         min_level_add: 0, max_level_add: 0,
                         min_level_multiply: native.fetch('ebb_amp_ratio'), max_level_multiply: native.fetch('flood_amp_ratio'),
                         flood_begins: native['time_adj_slack_before_flood_min'], ebb_begins: native['time_adj_slack_before_ebb_min'])
        end

        def subordinate(row, native, references, alternative)
            ref_id = native.fetch('reference_station_id')
            reference = references.fetch(ref_id) { raise ArgumentError, "reference #{ref_id} has no exported recommended harmonic record" }
            type = native.fetch('height_adjusted_type')
            raise ArgumentError, 'unknown subordinate height adjustment' unless %w[A R].include?(type)
            suffix = alternative ? ' [alt:subordinate:1]' : ''
            record = common(row, native.merge('source' => 'native-subordinate'), suffix, "#{row.fetch('station_id')}/subordinate")
            low, high = native.values_at('height_offset_low', 'height_offset_high')
            # Native strict offsets default omitted height changes to identity.
            low = type == 'R' ? 1 : 0 if low.nil?
            high = type == 'R' ? 1 : 0 if high.nil?
            record.merge(record_type: 2, reference_station: reference,
                         min_time_add: native.fetch('time_offset_low_min'), max_time_add: native.fetch('time_offset_high_min'),
                         min_level_add: type == 'A' ? low : 0, max_level_add: type == 'A' ? high : 0,
                         min_level_multiply: type == 'R' ? low : 1, max_level_multiply: type == 'R' ? high : 1)
        end

        def attempt(entries, database, row, native, kind, identity)
            entry = { 'station_id' => row.fetch('station_id'), 'identity' => identity, 'kind' => kind,
                      'native_metadata' => native.reject { |key, _value| key == 'constituents' } }
            entry['set_id'] = identity if kind == 'harmonic'
            entries << entry
            begin
                record = yield
                entry.merge!('status' => 'exported', 'record_index' => database.stations.size, 'name' => record.fetch(:name))
                database.add_station(record)
                record
            rescue KeyError, ArgumentError => error
                entry.merge!('status' => 'unrepresentable', 'error' => error.message)
                nil
            end
        end

        def export(mode, directory)
            database = writer
            entries, references, offsets, current_offsets = [], {}, [], []
            required_bins = @rows.flat_map do |row|
                row.fetch('current_offsets', []).select { |offset| mode == 'complete' || offset['bin'] == row['default_current_bin'] }
            end.map { |offset| [offset.fetch('reference_station_id'), offset.fetch('reference_bin')] }
            @rows.sort_by { |row| row.fetch('station_id') }.each do |row|
                sets = row.fetch('constant_sets').sort_by { |native| native.fetch('set_id') }
                selected = mode == 'complete' ? sets : sets.select { |native| native['set_id'] == row['recommended_set_id'] }
                selected.each do |native|
                    if native['quantity'] == 'current'
                        bins = native.fetch('current_bins').select do |bin|
                            mode == 'complete' || bin['bin'] == row['default_current_bin'] || required_bins.include?([row.fetch('station_id'), bin['bin']])
                        end
                        bins.each do |bin|
                            identity = "#{native.fetch('set_id')}/bin/#{bin.fetch('bin')}"
                            record = attempt(entries, database, row, native, 'harmonic', identity) { harmonic(row, native, sets.index(native) + 1, bin) }
                            entries.last['set_id'] = native.fetch('set_id')
                            entries.last['bin'] = bin.fetch('bin')
                            if record && native['set_id'] == row['recommended_set_id']
                                references[[row.fetch('station_id'), bin.fetch('bin')]] = database.stations.size - 1
                            end
                        end
                        next
                    end
                    record = attempt(entries, database, row, native, 'harmonic', native.fetch('set_id')) do
                        harmonic(row, native, sets.index(native) + 1)
                    end
                    references[row.fetch('station_id')] = database.stations.size - 1 if record && native['set_id'] == row['recommended_set_id']
                end
                if row['subordinate_offsets'] && (mode == 'complete' || row['recommended_set_id'].nil?)
                    offsets << row
                end
                if row['current_offsets'] && (mode == 'complete' || row['recommended_set_id'].nil?)
                    row.fetch('current_offsets').each do |offset|
                        current_offsets << [row, offset] if mode == 'complete' || offset['bin'] == row['default_current_bin']
                    end
                end
                if selected.empty? && !row['subordinate_offsets'] && !row['current_offsets']
                    entries << { 'station_id' => row.fetch('station_id'), 'kind' => 'identity', 'status' => 'no_prediction',
                                 'error' => 'No native default prediction record; no constants invented' }
                end
            end
            offsets.each do |row|
                attempt(entries, database, row, row.fetch('subordinate_offsets'), 'subordinate', "#{row.fetch('station_id')}/subordinate") do
                    subordinate(row, row.fetch('subordinate_offsets'), references, !row['recommended_set_id'].nil?)
                end
            end
            current_offsets.each do |row, offset|
                attempt(entries, database, row, offset, 'current_offset', "#{row.fetch('station_id')}/current-offset/#{offset.fetch('bin')}") do
                    current_subordinate(row, offset, references)
                end
            end
            suffix = mode == 'complete' ? '-complete' : ''
            name = "OTC_#{@date}#{suffix}.tcd"
            path = File.join(directory, name)
            database.write(path)
            TCD.open(path) do |readback|
                raise ArgumentError, "#{mode} readback count mismatch" unless readback.station_count == database.stations.size
                readback.stations.each { |station| raise ArgumentError, 'empty readback name' if station.name.empty? }
            end
            @report.fetch('outputs')[mode] = {
                'filename' => name, 'sha256' => Digest::SHA256.file(path).hexdigest,
                'exported' => entries.count { |entry| entry['status'] == 'exported' },
                'unrepresentable' => entries.count { |entry| entry['status'] == 'unrepresentable' },
                'no_prediction' => entries.count { |entry| entry['status'] == 'no_prediction' }, 'records' => entries
            }
            currents = entries.select { |entry| entry['kind'] == 'current_offset' || entry.dig('native_metadata', 'quantity') == 'current' }
            exported = currents.select { |entry| entry['status'] == 'exported' }
            default_places = exported.select { |entry| entry['bin'] == @stations.fetch(entry.fetch('station_id'))['default_current_bin'] ||
                                                        entry.dig('native_metadata', 'bin') == @stations.fetch(entry.fetch('station_id'))['default_current_bin'] }.map { |entry| entry.fetch('station_id') }.uniq
            @report.fetch('outputs')[mode].merge!('current_places' => default_places.size, 'current_records' => exported.size,
                                                 'current_additional_records' => exported.size - default_places.size)
        end
    end

    def self.main(arguments)
        options = {}
        parser = OptionParser.new do |opts|
            opts.banner = 'Usage: export_tcd.rb --input stations.jsonl --release release.meta.json --output-dir DIR --date YYYYMMDD'
            opts.on('--input PATH') { |value| options[:input] = value }
            opts.on('--release PATH') { |value| options[:release] = value }
            opts.on('--output-dir PATH') { |value| options[:output_dir] = value }
            opts.on('--date YYYYMMDD') { |value| options[:date] = value }
        end
        parser.parse!(arguments)
        raise ArgumentError, parser.to_s unless options.keys.sort == %i[date input output_dir release] && arguments.empty?
        Exporter.new(**options).run
    rescue ArgumentError, KeyError, JSON::ParserError, SystemCallError => error
        warn "TCD export failed: #{error.message}"
        1
    end
end

exit OTCExportTCD.main(ARGV) if $PROGRAM_NAME == __FILE__
