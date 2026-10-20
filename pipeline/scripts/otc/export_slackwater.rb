# frozen_string_literal: true

require 'json'
require 'csv'
require 'date'
require 'digest'
require 'fileutils'
require 'open3'
require 'optparse'
require 'tmpdir'

module OTCExportSlackwater
    SCHEMAS = {
        'database_schema' => '5e0cc616baf47aa8bf8ce2baa679a81881aff7e2ed90027487009145a19496ea',
        'station_schema' => 'cf24a8947fd1d159741aef1de74a0281a0a281d6c05ccfb40cf20db42fcac9f4'
    }.freeze
    CONTINENTS = {'AF' => 'Africa', 'AN' => 'Antarctica', 'AS' => 'Asia', 'EU' => 'Europe',
                  'NA' => 'North America', 'OC' => 'Oceania', 'SA' => 'South America'}.freeze
    SOURCE_URLS = {
        'noaa' => 'https://tidesandcurrents.noaa.gov/', 'linz' => 'https://www.linz.govt.nz/',
        'noaa-currents' => 'https://tidesandcurrents.noaa.gov/',
        'kartverket' => 'https://vannstand.kartverket.no/', 'jma' => 'https://www.data.jma.go.jp/kaiyou/db/tide/suisan/',
        'gesla' => 'https://gesla787883612.wordpress.com/', 'ticon' => 'https://doi.org/10.17882/109129',
        'fes2022' => 'https://doi.org/10.24400/527896/a01-2024.004'
    }.freeze
    CONVENTIONS = %w[otc-greenwich-schureman-v1 ticon4-greenwich-schureman-v1 noaa-harcon-gmt
                     kartverket-utc+1 linz-nzst-utc+12 jma-local-meridian fes2022b-ocean-greenwich].freeze

    class Exporter
        def initialize(options)
            @options = options
            @date = options.fetch('date')
            raise ArgumentError, 'date must be YYYYMMDD' unless /\A\d{8}\z/.match?(@date)
            Date.strptime(@date, '%Y%m%d')
            SCHEMAS.each do |key, hash|
                raise ArgumentError, "#{key} must match pinned e046e800 schema" unless Digest::SHA256.file(options.fetch(key)).hexdigest == hash
            end
            version, status = Open3.capture2e(options.fetch('flatc', 'flatc'), '--version')
            raise ArgumentError, 'flatc 25.9.23 required' unless status.success? && version.strip == 'flatc version 25.9.23'
            @metadata = JSON.parse(File.read(options.fetch('release')))
            raise ArgumentError, 'date differs from release' unless @metadata.fetch('release').fetch('datestamp') == @date
            @rows = File.foreach(options.fetch('input')).map { |line| JSON.parse(line) }
            ids = @rows.map { |row| row.fetch('station_id') }
            raise ArgumentError, 'duplicate station IDs' unless ids.uniq.size == ids.size
            @licences = @metadata.fetch('licences').to_h { |row| [row.fetch('licence_id'), row] }
            @countries = CSV.read(options.fetch('country_codes'), headers: true).to_h { |row| [row.fetch('ISO3166-1-Alpha-3'), row.to_h] }
            @report = {'input_sha256' => Digest::SHA256.file(options.fetch('input')).hexdigest,
                       'release_sha256' => Digest::SHA256.file(options.fetch('release')).hexdigest,
                       'schemas' => SCHEMAS, 'flatc_version' => version.strip, 'licences' => @metadata.fetch('licences'),
                       'country_mapping_sha256' => Digest::SHA256.file(options.fetch('country_codes')).hexdigest,
                       'country_mapping' => 'datasets/country-codes: ISO3166-1-Alpha-3 -> official_name_en, ISO3166-1-Alpha-2, Continent; native country unchanged',
                       'selection' => 'native subordinate relationship when present; otherwise native recommended_set_id; no reranking', 'records' => []}
        end

        def run
            output = @options.fetch('output_dir')
            FileUtils.mkdir_p(output)
            Dir.mktmpdir('.otc-swap-', output) do |temporary|
                stations = @rows.filter_map do |row|
                    sets = row.fetch('constant_sets')
                    native = sets.find { |set| set.fetch('set_id') == row['recommended_set_id'] }
                    entry = {'station_id' => row.fetch('station_id'), 'recommended_set_id' => row['recommended_set_id'],
                             'selected_representation' => row['type'] == 'subordinate' ? 'native_subordinate_offsets' : 'native_recommended_set',
                             'omitted_alternative_set_ids' => sets.reject { |set| row['type'] != 'subordinate' && set['set_id'] == row['recommended_set_id'] }.map { |set| set.fetch('set_id') },
                             'native' => native, 'subordinate_offsets' => row['subordinate_offsets'],
                             'recommended_is_fallback' => row['recommended_is_fallback']}
                    @report.fetch('records') << entry
                    begin
                        station = project(row, native)
                        datum_fallback = station.fetch('source').fetch('otc')['datum_fallback']
                        entry['datum_fallback'] = datum_fallback if datum_fallback
                        entry['status'] = 'exported'
                        station
                    rescue ArgumentError, KeyError => error
                        entry['status'] = native || row['subordinate_offsets'] || row['current_offsets'] ? 'unrepresentable' : 'no_prediction'
                        entry['reason'] = error.message
                        nil
                    end
                end
                @report['support_records'] = []
                stations.select { |station| station.dig('current', 'offsets') }.each do |station|
                    id = station.fetch('current').fetch('offsets').fetch('reference')
                    next if stations.any? { |item| item['id'] == id }
                    detail = station.fetch('source').fetch('otc')
                    row = @rows.find { |item| item['station_id'] == detail.fetch('reference_station_id') }
                    next unless row
                    native = row.fetch('constant_sets').find { |set| set['set_id'] == row['recommended_set_id'] }
                    begin
                        support = project_current(row, native, detail.fetch('reference_bin'))
                        stations << support
                        @report['support_records'] << {'station_id' => row.fetch('station_id'), 'export_id' => id,
                                                       'bin' => detail.fetch('reference_bin'), 'status' => 'exported'}
                    rescue ArgumentError, KeyError => error
                        @report['support_records'] << {'export_id' => id, 'status' => 'unrepresentable', 'reason' => error.message}
                    end
                end
                # References must resolve to an actually exported reference, never an omitted record.
                references = stations.select { |station| station['type'] == 'reference' }.map { |station| station.fetch('id') }
                stations.reject! do |station|
                    reference = station.dig('current', 'offsets', 'reference') || station.dig('offsets', 'reference')
                    next false unless station['type'] == 'subordinate' && !references.include?(reference)
                    entry = @report.fetch('records').find { |record| record['station_id'] == station['id'] }
                    entry.merge!('status' => 'unrepresentable', 'reason' => 'subordinate reference is not exported')
                    true
                end
                annotate_inherited_acceptance(stations)
                station_directory = File.join(temporary, "OTC_#{@date}-stations")
                FileUtils.mkdir_p(station_directory)
                stations.each { |station| File.write(File.join(station_directory, "#{station.fetch('id')}.json"), JSON.generate(station) + "\n") }
                write_database(stations, temporary)
                @report['exported'] = stations.size
                current = stations.select { |station| station['kind'] == 'current' }
                @report['current_places'] = current.count { |station| station['id'] == station.dig('source', 'otc', 'native_station_id') }
                @report['current_support_records'] = current.size - @report['current_places']
                @report['status'] = @report.fetch('records').any? { |record| record['status'] != 'exported' || !record.fetch('omitted_alternative_set_ids').empty? } ? 'partial' : 'complete'
                File.write(File.join(temporary, "OTC_#{@date}-swap-report.json"), JSON.generate(@report) + "\n")
                # Replace this release's station directory so failed/omitted records cannot survive a rerun.
                FileUtils.rm_rf(File.join(output, "OTC_#{@date}-stations"))
                Dir.children(temporary).each { |name| FileUtils.mv(File.join(temporary, name), File.join(output, name), force: true) }
            end
            puts JSON.generate(status: @report.fetch('status'), exported: @report.fetch('exported'), total: @rows.size)
            @report.fetch('status') == 'complete' ? 0 : 2
        end

        private

        def project(row, native)
            raise ArgumentError, 'unsafe station identity' unless /\A[A-Za-z0-9_-]+\z/.match?(row.fetch('station_id'))
            subordinate = row['type'] == 'subordinate'
            selected = subordinate ? row['subordinate_offsets'] : native
            return project_current(row, native) if native&.fetch('quantity') == 'current' || row['current_offsets']
            raise ArgumentError, 'no native prediction record' unless selected
            datum = selected['datum']
            chart = datum && datum['chart_datum']&.upcase
            datums = (datum || {}).fetch('named', {}).transform_keys(&:upcase)
            datums['MSL'] = datum['msl_offset_m'] if !datums.key?('MSL') && datum&.key?('msl_offset_m')
            tied = chart && datums.key?('MSL') && datums.key?(chart)
            unless tied
                unavailable = if !datum
                    ['datum']
                elsif !chart
                    ['chart_datum']
                else
                    ['MSL', chart].uniq.reject { |name| datums.key?(name) }.map { |name| "datums.#{name}" }
                end
                if subordinate
                    reference = @rows.find { |item| item['station_id'] == selected.fetch('reference_station_id') }
                    raise ArgumentError, 'subordinate reference station unavailable' unless reference && reference['type'] != 'subordinate'
                    reference_set = reference.fetch('constant_sets').find { |set| set['set_id'] == reference['recommended_set_id'] }
                    reference_datum = reference_set && reference_set['datum']
                    reference_chart = reference_datum && reference_datum['chart_datum']
                    unless reference_set && reference_set['quantity'] == 'water_level' && reference_chart &&
                           reference_datum.dig('basis', reference_chart, 'kind') == 'published' && reference_datum.key?('msl_offset_m')
                        raise ArgumentError, 'subordinate reference published chart tie unavailable'
                    end
                    frame = project(reference, reference_set)
                    raise ArgumentError, 'subordinate reference MSL/chart values unavailable' if frame['chart_datum'].empty?
                    chart, datums = frame.values_at('chart_datum', 'datums')
                    datum_fallback = {'reason' => 'subordinate_local_datum_unavailable', 'reference' => 'reference_station_chart_relative',
                                      'reference_station_id' => reference.fetch('station_id'), 'reference_set_id' => reference_set.fetch('set_id'),
                                      'unavailable_fields' => unavailable, 'chart_datum' => chart,
                                      'finding' => 'Subordinate local vertical tie is unavailable. These offsets use the reference station chart coordinate; this labelled approximation does not establish an absolute local chart datum.'}
                else
                    datum_fallback = {'reason' => 'chart_datum_unavailable', 'reference' => 'harmonic_mean_relative',
                                      'unavailable_fields' => unavailable,
                                      'finding' => 'No absolute or chart-datum height is established; predictions are relative to the harmonic mean.'}
                    chart = ''
                end
            end
            country = @countries.fetch(row.fetch('country')) { raise ArgumentError, 'country has no ISO mapping' }
            continent = CONTINENTS.fetch(country.fetch('Continent')) { raise ArgumentError, 'country has no continent mapping' }
            licence = @licences.fetch(selected.fetch('licence_id'))
            source_name = native && !subordinate ? native.fetch('source') : 'noaa'
            source_url_name = {'gesla-fit' => 'gesla', 'ticon-4' => 'ticon'}.fetch(source_name, source_name)
            source_id = native && !subordinate ? native.fetch('source_record_id') : Array(row.fetch('aliases').fetch('noaa')).first
            details = {'set_id' => subordinate ? nil : native.fetch('set_id'), 'datum' => datum,
                       'fallback' => selected['fallback'], 'qc_status' => selected['qc_status'], 'qc_flags' => selected['qc_flags'],
                       'provenance' => selected['provenance'], 'source_version' => selected['source_version'],
                       'recommended_is_fallback' => row['recommended_is_fallback'], 'native_country' => row['country'],
                       'units' => 'meters', 'phase' => 'Greenwich degrees'}
            details['datum_fallback'] = datum_fallback if datum_fallback
            notice = JSON.generate(details)
            attribution = licence.fetch('attribution')
            station = {'id' => row.fetch('station_id'), 'name' => row.fetch('name'), 'type' => subordinate ? 'subordinate' : 'reference',
                       'latitude' => row.fetch('lat'), 'longitude' => row.fetch('lon'), 'timezone' => row.fetch('timezone'),
                       'continent' => continent, 'country' => country.fetch('official_name_en'),
                       'source' => {'name' => source_name, 'id' => source_id, 'url' => SOURCE_URLS.fetch(source_url_name),
                                    'published_harmonics' => !subordinate && native['source_type'] == 'official', 'otc' => details},
                       'license' => {'type' => licence.fetch('spdx'), 'url' => licence['url'] || licence.fetch('terms_url'),
                                     'commercial_use' => licence.fetch('commercial_use'), 'notes' => [attribution, licence['restriction'], licence['citation']].compact.join("\n")},
                       'disclaimers' => attribution + "\nOTC native metadata (including datum and fallback limitations): " + notice,
                       'chart_datum' => chart, 'datums' => datums}
            if subordinate
                height_type = {'R' => 'ratio', 'A' => 'fixed'}.fetch(selected.fetch('height_adjusted_type'))
                station['offsets'] = {'reference' => selected.fetch('reference_station_id'),
                                      'height' => {'type' => height_type, 'high' => selected.fetch('height_offset_high'), 'low' => selected.fetch('height_offset_low')},
                                      'time' => {'high' => selected.fetch('time_offset_high_min'), 'low' => selected.fetch('time_offset_low_min')}}
                station['harmonic_constituents'] = []
            else
                raise ArgumentError, 'not water-level constants' unless native.fetch('quantity') == 'water_level'
                raise ArgumentError, 'unsupported phase convention' unless CONVENTIONS.include?(native.fetch('convention_id'))
                station['harmonic_constituents'] = native.fetch('constituents').map do |term|
                    {'name' => term.fetch('name'), 'amplitude' => term.fetch('amplitude_m'), 'phase' => term.fetch('phase_deg')}
                end
                raise ArgumentError, 'no harmonic constituents' if station.fetch('harmonic_constituents').empty?
            end
            station
        end

        def current_identity(row, bin)
            row.fetch('default_current_bin') == bin ? row.fetch('station_id') : "#{row.fetch('station_id')}_bin_#{bin}"
        end

        def project_current(row, native, requested_bin = nil)
            number = requested_bin || row.fetch('default_current_bin')
            subordinate = native.nil?
            selected = if subordinate
                row.fetch('current_offsets').find { |bin| bin['bin'] == number }
            else
                raise ArgumentError, 'unsupported current phase convention' unless native.fetch('convention_id') == 'noaa-currents-harcon-gmt'
                native.fetch('current_bins').find { |bin| bin['bin'] == number }
            end
            raise ArgumentError, "current bin #{number} unavailable" unless selected
            country = @countries.fetch(row.fetch('country'))
            licence = @licences.fetch((native || selected).fetch('licence_id'))
            detail = {'native_station_id' => row.fetch('station_id'), 'set_id' => native && native.fetch('set_id'),
                      'bin' => selected, 'units' => 'knots', 'phase' => 'Greenwich degrees', 'quantity' => 'signed_major_axis',
                      'limitation' => 'Scalar signed major-axis current; minor-axis motion and vector speed/direction are omitted.',
                      'native_country' => row.fetch('country'), 'native_set_metadata' => native && native.reject { |key, _| key == 'current_bins' }}
            current = {'flood_direction' => selected.fetch('mean_flood_dir_deg'), 'ebb_direction' => selected.fetch('mean_ebb_dir_deg')}
            raise ArgumentError, 'current flood/ebb orientation unavailable' if current.values.any?(&:nil?)
            terms = []
            if subordinate
                reference = @rows.find { |item| item['station_id'] == selected.fetch('reference_station_id') }
                raise ArgumentError, 'current reference station unavailable' unless reference
                detail.merge!(selected.slice('reference_station_id', 'reference_bin'))
                current['offsets'] = {'reference' => current_identity(reference, selected.fetch('reference_bin'))}
                {'slack_before_flood' => 'time_adj_slack_before_flood_min', 'slack_before_ebb' => 'time_adj_slack_before_ebb_min',
                 'flood_time' => 'time_adj_max_flood_min', 'ebb_time' => 'time_adj_max_ebb_min',
                 'flood_speed_ratio' => 'flood_amp_ratio', 'ebb_speed_ratio' => 'ebb_amp_ratio'}.each do |target, source|
                    current['offsets'][target] = selected[source] unless selected[source].nil?
                end
            else
                azimuth, mean = selected.values_at('azimuth_deg', 'mean_major_ms')
                raise ArgumentError, 'current axis/mean unavailable' if azimuth.nil? || mean.nil?
                sigma = Math.cos((azimuth - current.fetch('flood_direction')) * Math::PI / 180) < 0 ? -1 : 1
                current['mean_flow'] = sigma * mean / 0.5144444444444445
                detail['conversion'] = {'sigma' => sigma, 'meters_per_second_per_knot' => 0.5144444444444445}
                terms = selected.fetch('constituents').map do |term|
                    {'name' => term.fetch('name'), 'amplitude' => term.fetch('major_amplitude_ms') / 0.5144444444444445,
                     'phase' => (term.fetch('major_phase_deg') + (sigma < 0 ? 180 : 0)) % 360}
                end
                raise ArgumentError, 'no current harmonic constituents' if terms.empty?
            end
            attribution = [licence.fetch('attribution'), licence['restriction'], licence['citation']].compact.join("\n")
            {'id' => current_identity(row, number), 'name' => row.fetch('name'), 'kind' => 'current',
             'type' => subordinate ? 'subordinate' : 'reference', 'latitude' => row.fetch('lat'), 'longitude' => row.fetch('lon'),
             'timezone' => row.fetch('timezone'), 'country' => country.fetch('official_name_en'), 'continent' => CONTINENTS.fetch(country.fetch('Continent')),
             'source' => {'name' => native ? native.fetch('source') : 'noaa-currents', 'id' => native ? native.fetch('source_record_id') : row.fetch('aliases').fetch('noaa'),
                          'url' => SOURCE_URLS.fetch('noaa-currents'), 'published_harmonics' => !subordinate && native['source_type'] == 'official', 'otc' => detail},
             'license' => {'type' => licence.fetch('spdx'), 'url' => licence['url'] || licence.fetch('terms_url'), 'commercial_use' => licence.fetch('commercial_use'), 'notes' => attribution},
             'disclaimers' => attribution + "\nOTC signed_major_axis current: " + JSON.generate(detail),
             'chart_datum' => '', 'datums' => {}, 'harmonic_constituents' => terms, 'current' => current}
        end

        def reference_accepted?(station)
            return false unless station['type'] == 'reference'
            detail = station.fetch('source').fetch('otc')
            if station['kind'] == 'current'
                return false unless station['id'] == detail['native_station_id']
                detail = detail.fetch('native_set_metadata')
            end
            detail['qc_status'] == 'ok' && detail['fallback'].nil?
        end

        def annotate_inherited_acceptance(stations)
            references = stations.select { |station| station['type'] == 'reference' }.to_h { |station| [station.fetch('id'), station] }
            stations.each do |station|
                next unless station['type'] == 'subordinate'
                reference_id = station.dig('current', 'offsets', 'reference') || station.dig('offsets', 'reference')
                reference = references.fetch(reference_id)
                reference_detail = reference.fetch('source').fetch('otc')
                detail = station.fetch('source').fetch('otc')
                acceptance = {
                    'basis' => 'inherited', 'accepted' => reference_accepted?(reference),
                    'reference_export_id' => reference_id,
                    'reference_station_id' => reference_detail['native_station_id'] || reference.fetch('id'),
                    'reference_set_id' => reference_detail.fetch('set_id'),
                    'finding' => 'Admission inherited from the exported reference; independent subordinate accuracy is not established.'
                }
                acceptance['reference_bin'] = detail.fetch('reference_bin') if station['kind'] == 'current'
                detail['acceptance'] = acceptance
                station['disclaimers'] += "\nOTC acceptance: #{JSON.generate(acceptance)}"
                entry = @report.fetch('records').find { |record| record['station_id'] == station.fetch('id') }
                entry['acceptance'] = acceptance if entry
            end
        end

        def write_database(stations, temporary)
            names = stations.flat_map { |station| station.fetch('harmonic_constituents').map { |term| term.fetch('name') } }.uniq.sort
            datums = stations.flat_map { |station| station.fetch('datums').keys }.uniq.sort
            records = stations.sort_by { |station| station.fetch('id').b }.map do |station|
                record = station.slice('id', 'name', 'latitude', 'longitude', 'timezone', 'country', 'continent', 'chart_datum', 'license', 'disclaimers')
                record['country_code'] = @countries.fetch(station.fetch('source').fetch('otc').fetch('native_country')).fetch('ISO3166-1-Alpha-2')
                record['type'] = station.fetch('type') == 'reference' ? 'Reference' : 'Subordinate'
                record['accepted'] = if station['type'] == 'subordinate'
                    station.fetch('source').fetch('otc').fetch('acceptance').fetch('accepted')
                else
                    reference_accepted?(station)
                end
                if station['kind'] == 'current'
                    record['kind'] = 'Current'
                    record['current'] = station.fetch('current').dup
                    %w[flood_direction ebb_direction mean_flow].each do |key|
                        record['current']["#{key}_missing"] = !record['current'].key?(key)
                    end
                    if record['current']['offsets']
                        offsets = record['current']['offsets'].dup
                        %w[slack_before_flood slack_before_ebb flood_time ebb_time flood_speed_ratio ebb_speed_ratio].each do |key|
                            offsets["#{key}_missing"] = !offsets.key?(key)
                        end
                        record['current']['offsets'] = offsets
                    end
                end
                record['source'] = station.fetch('source').reject { |key, _| key == 'otc' }
                record['attribution'] = station.fetch('disclaimers') + "\n" + station.fetch('license').fetch('notes')
                record['constituents'] = station.fetch('harmonic_constituents').map { |term| term.merge('name' => names.index(term.fetch('name'))) }
                record['datums'] = station.fetch('datums').map { |name, value| {'name' => datums.index(name), 'value' => value} }
                if station['offsets']
                    offsets = station.fetch('offsets')
                    record['offsets'] = {'reference' => offsets.fetch('reference'), 'time_high' => offsets.fetch('time').fetch('high'),
                                         'time_low' => offsets.fetch('time').fetch('low'), 'height_type' => offsets.fetch('height').fetch('type').capitalize,
                                         'height_high' => offsets.fetch('height').fetch('high'), 'height_low' => offsets.fetch('height').fetch('low')}
                end
                record
            end
            root_path = File.join(temporary, "OTC_#{@date}.json")
            File.write(root_path, JSON.generate('version' => "OTC_#{@date}", 'stations' => records, 'constituent_names' => names, 'datum_names' => datums))
            log, status = Open3.capture2e(@options.fetch('flatc', 'flatc'), '--binary', '--strict-json', '-o', temporary, @options.fetch('database_schema'), root_path)
            raise ArgumentError, "flatc failed: #{log}" unless status.success?
            File.delete(root_path)
        end
    end
end

if $PROGRAM_NAME == __FILE__
    options = {}
    parser = OptionParser.new do |flags|
        %w[input release output-dir date database-schema station-schema country-codes flatc].each do |key|
            flags.on("--#{key} VALUE") { |value| options[key.tr('-', '_')] = value }
        end
    end
    begin
        parser.parse!
        exit OTCExportSlackwater::Exporter.new(options).run
    rescue ArgumentError, KeyError, SystemCallError, JSON::ParserError, CSV::MalformedCSVError => error
        warn JSON.generate(status: 'error', error: error.message)
        exit 1
    end
end
