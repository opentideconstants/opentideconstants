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
                        entry['status'] = 'exported'
                        station
                    rescue ArgumentError, KeyError => error
                        entry['status'] = native || row['subordinate_offsets'] ? 'unrepresentable' : 'no_prediction'
                        entry['reason'] = error.message
                        nil
                    end
                end
                # References must resolve to an actually exported reference, never an omitted record.
                references = stations.select { |station| station['type'] == 'reference' }.map { |station| station.fetch('id') }
                stations.reject! do |station|
                    next false unless station['type'] == 'subordinate' && !references.include?(station.fetch('offsets').fetch('reference'))
                    entry = @report.fetch('records').find { |record| record['station_id'] == station['id'] }
                    entry.merge!('status' => 'unrepresentable', 'reason' => 'subordinate reference is not exported')
                    true
                end
                station_directory = File.join(temporary, "OTC_#{@date}-stations")
                FileUtils.mkdir_p(station_directory)
                stations.each { |station| File.write(File.join(station_directory, "#{station.fetch('id')}.json"), JSON.generate(station) + "\n") }
                write_database(stations, temporary)
                @report['exported'] = stations.size
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
            raise ArgumentError, 'no native prediction record' unless selected
            raise ArgumentError, 'current records are outside scalar tide format' if selected['quantity'] == 'current' || row['type'] == 'current'
            datum = selected.fetch('datum') { raise ArgumentError, 'native datum/chart tie unavailable' }
            chart = datum.fetch('chart_datum') { raise ArgumentError, 'native chart datum unavailable' }.upcase
            datums = datum.fetch('named', {}).transform_keys(&:upcase)
            datums['MSL'] = datum['msl_offset_m'] if !datums.key?('MSL') && datum.key?('msl_offset_m')
            raise ArgumentError, 'native MSL/chart datum values unavailable' unless datums.key?('MSL') && datums.key?(chart)
            country = @countries.fetch(row.fetch('country')) { raise ArgumentError, 'country has no ISO mapping' }
            continent = CONTINENTS.fetch(country.fetch('Continent')) { raise ArgumentError, 'country has no continent mapping' }
            licence = @licences.fetch(selected.fetch('licence_id'))
            source_name = native && !subordinate ? native.fetch('source') : 'noaa'
            source_id = native && !subordinate ? native.fetch('source_record_id') : Array(row.fetch('aliases').fetch('noaa')).first
            details = {'set_id' => subordinate ? nil : native.fetch('set_id'), 'datum' => datum,
                       'fallback' => selected['fallback'], 'qc_status' => selected['qc_status'], 'qc_flags' => selected['qc_flags'],
                       'provenance' => selected['provenance'], 'source_version' => selected['source_version'],
                       'recommended_is_fallback' => row['recommended_is_fallback'], 'native_country' => row['country'],
                       'units' => 'meters', 'phase' => 'Greenwich degrees'}
            notice = JSON.generate(details)
            attribution = licence.fetch('attribution')
            station = {'id' => row.fetch('station_id'), 'name' => row.fetch('name'), 'type' => subordinate ? 'subordinate' : 'reference',
                       'latitude' => row.fetch('lat'), 'longitude' => row.fetch('lon'), 'timezone' => row.fetch('timezone'),
                       'continent' => continent, 'country' => country.fetch('official_name_en'),
                       'source' => {'name' => source_name, 'id' => source_id, 'url' => SOURCE_URLS.fetch(source_name == 'gesla-fit' ? 'gesla' : source_name),
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

        def write_database(stations, temporary)
            names = stations.flat_map { |station| station.fetch('harmonic_constituents').map { |term| term.fetch('name') } }.uniq.sort
            datums = stations.flat_map { |station| station.fetch('datums').keys }.uniq.sort
            records = stations.sort_by { |station| station.fetch('id').b }.map do |station|
                record = station.slice('id', 'name', 'latitude', 'longitude', 'timezone', 'country', 'continent', 'chart_datum', 'license', 'disclaimers')
                record['country_code'] = @countries.fetch(station.fetch('source').fetch('otc').fetch('native_country')).fetch('ISO3166-1-Alpha-2')
                record['type'] = station.fetch('type') == 'reference' ? 'Reference' : 'Subordinate'
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
