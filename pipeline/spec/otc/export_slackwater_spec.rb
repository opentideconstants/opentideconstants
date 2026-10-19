# frozen_string_literal: true

require 'rspec'
require 'json'
require 'open3'
require 'tmpdir'
require 'fileutils'
require 'rbconfig'
require 'digest'

RSpec.describe 'the qualified native Slackwater file export' do
    before(:context) do
        keys = %w[OTC_SWAP_INPUT OTC_SWAP_RELEASE OTC_SWAP_DATABASE_SCHEMA OTC_SWAP_STATION_SCHEMA OTC_SWAP_COUNTRY_CODES OTC_SWAP_FLATC]
        skip 'Set OTC_SWAP_* paths to the real qualified release and pinned external contracts' if keys.none? { |key| ENV.key?(key) }
        @input, @release, @database_schema, @station_schema, @countries, @flatc = keys.map { |key| ENV.fetch(key) }
        @rows = File.foreach(@input).map { |line| JSON.parse(line) }
        @directory = Dir.mktmpdir('otc-swap-spec-')
        script = File.expand_path('../../scripts/otc/export_slackwater.rb', __dir__)
        @log, @status = Open3.capture2e(RbConfig.ruby, script, '--input', @input, '--release', @release,
                                      '--output-dir', @directory, '--date', '20261011', '--database-schema', @database_schema,
                                      '--station-schema', @station_schema, '--country-codes', @countries, '--flatc', @flatc)
    end

    after(:context) { FileUtils.remove_entry(@directory) if @directory }

    def report
        expect([0, 2]).to include(@status.exitstatus), @log
        JSON.parse(File.read(File.join(@directory, 'OTC_20261011-swap-report.json')))
    end

    it 'preserves native default admission for the actual selected ordinary references without inventing scores' do
        log, status = Open3.capture2e(@flatc, '--json', '--strict-json', '--defaults-json', '-o', @directory,
                                     @database_schema, '--', File.join(@directory, 'OTC_20261011.tcdb'))
        expect(status.success?).to be(true), log
        database = JSON.parse(File.read(File.join(@directory, 'OTC_20261011.json')))
        %w[OTC-000d594d OTC-00af59c3 OTC-00b96c29].each do |id|
            row = @rows.find { |item| item['station_id'] == id }
            native = row.fetch('constant_sets').find { |item| item['set_id'] == row['recommended_set_id'] }
            expect(native.fetch('qc_status')).to eq('ok')
            expect(native['fallback']).to be_nil
            decoded = database.fetch('stations').find { |item| item['id'] == id }
            expect(decoded.fetch('accepted')).to be(true)
            expect(decoded.fetch('score')).to eq(0)
            station = JSON.parse(File.read(File.join(@directory, 'OTC_20261011-stations', "#{id}.json")))
            expect(station).not_to have_key('accepted')
            expect(station.fetch('source').fetch('otc').fetch('qc_status')).to eq(native.fetch('qc_status'))
        end
        flagged = database.fetch('stations').find { |item| item['id'] == 'OTC-0012a4f2' }
        expect(flagged.fetch('accepted')).to be(false)
        expect(flagged.fetch('score')).to eq(0)
    end

    it 'accounts for every native station and alternative without claiming partial output complete' do
        document = report
        expect(document.fetch('input_sha256')).to eq(Digest::SHA256.file(@input).hexdigest)
        expect(document.fetch('records').map { |entry| entry.fetch('station_id') }).to eq(@rows.map { |row| row.fetch('station_id') })
        expect(document.fetch('status')).to eq('partial')
        expect(@status.exitstatus).to eq(2)
        expect(File.binread(File.join(@directory, 'OTC_20261011.tcdb'), 8)[4, 4]).to eq('TCDB')
    end

    it 'preserves the three actual recommendations, coefficients, datum meaning and notices' do
        %w[OTC-000d594d OTC-00be05d2 OTC-06676dd2].each do |id|
            row = @rows.find { |item| item['station_id'] == id }
            native = row.fetch('constant_sets').find { |item| item['set_id'] == row['recommended_set_id'] }
            station = JSON.parse(File.read(File.join(@directory, 'OTC_20261011-stations', "#{id}.json")))
            expect(station.fetch('source').fetch('otc').fetch('set_id')).to eq(native.fetch('set_id'))
            expect(station.fetch('harmonic_constituents')).to eq(native.fetch('constituents').map { |term| {'name' => term.fetch('name'), 'amplitude' => term.fetch('amplitude_m'), 'phase' => term.fetch('phase_deg')} })
            native.fetch('datum').fetch('named').each { |name, value| expect(station.fetch('datums').fetch(name.upcase)).to eq(value) }
            expect(station.fetch('chart_datum')).to eq(native.fetch('datum').fetch('chart_datum').upcase)
            expect(station.fetch('source').fetch('otc').fetch('datum')).to eq(native.fetch('datum'))
            expect(station.fetch('disclaimers')).to include(native.fetch('set_id'))
        end
    end

    it 'exports actual untied references as labelled harmonic mean-relative predictions without inventing datums' do
        %w[OTC-0012a4f2 OTC-00196c3e OTC-000180a3 OTC-000cb5d3 OTC-00252765 OTC-00b6ed94].each do |id|
            row = @rows.find { |item| item['station_id'] == id }
            native = row.fetch('constant_sets').find { |item| item['set_id'] == row['recommended_set_id'] }
            entry = report.fetch('records').find { |item| item['station_id'] == id }
            expect(entry.fetch('status')).to eq('exported')
            station = JSON.parse(File.read(File.join(@directory, 'OTC_20261011-stations', "#{id}.json")))
            datum = native.fetch('datum', {})
            expected = datum.fetch('named', {}).transform_keys(&:upcase)
            expected['MSL'] = datum['msl_offset_m'] if !expected.key?('MSL') && datum.key?('msl_offset_m')
            expect(station.fetch('datums')).to eq(expected)
            expect(station.fetch('chart_datum')).to eq('')
            expect(station.fetch('source').fetch('name')).to eq(native.fetch('source'))
            expect(station.fetch('source').fetch('id')).to eq(native.fetch('source_record_id'))
            details = station.fetch('source').fetch('otc')
            expect(details.fetch('datum')).to eq(native['datum'])
            expect(details.fetch('fallback')).to eq(native['fallback'])
            expect(details.fetch('datum_fallback')).to eq(entry.fetch('datum_fallback'))
            expect(details.fetch('datum_fallback')).to include('reason' => 'chart_datum_unavailable', 'reference' => 'harmonic_mean_relative')
            expect(station.fetch('disclaimers')).to include('No absolute or chart-datum height is established; predictions are relative to the harmonic mean.')
            expect(station.fetch('harmonic_constituents')).to eq(native.fetch('constituents').map { |term| {'name' => term.fetch('name'), 'amplitude' => term.fetch('amplitude_m'), 'phase' => term.fetch('phase_deg')} })
        end
        entry = report.fetch('records').find { |item| item['station_id'] == 'OTC-0012a4f2' }
        expect(entry.fetch('native').fetch('fallback').fetch('reason')).to eq('reliability_withheld')
    end

    it 'uses the actual published reference frame for untied subordinates and preserves the local metadata' do
        %w[OTC-0013852b OTC-0031a9c4 OTC-00605f6b OTC-01333ad1].each do |id|
            row = @rows.find { |item| item['station_id'] == id }
            offsets = row.fetch('subordinate_offsets')
            reference = @rows.find { |item| item['station_id'] == offsets.fetch('reference_station_id') }
            native = reference.fetch('constant_sets').find { |item| item['set_id'] == reference['recommended_set_id'] }
            entry = report.fetch('records').find { |item| item['station_id'] == id }
            expect(entry.fetch('status')).to eq('exported')
            station = JSON.parse(File.read(File.join(@directory, 'OTC_20261011-stations', "#{id}.json")))
            ref_station = JSON.parse(File.read(File.join(@directory, 'OTC_20261011-stations', "#{reference.fetch('station_id')}.json")))
            expect(station.fetch('datums')).to eq(ref_station.fetch('datums'))
            expect(station.fetch('chart_datum')).to eq(ref_station.fetch('chart_datum'))
            expect(station.fetch('harmonic_constituents')).to eq([])
            expect(station.fetch('offsets')).to eq('reference' => reference.fetch('station_id'),
                'height' => {'type' => offsets.fetch('height_adjusted_type') == 'R' ? 'ratio' : 'fixed', 'high' => offsets.fetch('height_offset_high'), 'low' => offsets.fetch('height_offset_low')},
                'time' => {'high' => offsets.fetch('time_offset_high_min'), 'low' => offsets.fetch('time_offset_low_min')})
            details = station.fetch('source').fetch('otc')
            expect(details.fetch('datum')).to eq(offsets['datum'])
            expect(details.fetch('datum_fallback')).to include('reason' => 'subordinate_local_datum_unavailable',
                'reference' => 'reference_station_chart_relative', 'reference_station_id' => reference.fetch('station_id'),
                'reference_set_id' => native.fetch('set_id'), 'chart_datum' => native.fetch('datum').fetch('chart_datum').upcase)
            expect(details.fetch('datum_fallback')).to eq(entry.fetch('datum_fallback'))
            expect(station.fetch('disclaimers')).to include('Subordinate local vertical tie is unavailable.')
            expect(station.fetch('source').fetch('published_harmonics')).to be(false)
        end
    end

    it 'preserves the existing local frame for every fully tied subordinate' do
        @rows.select { |row| row['type'] == 'subordinate' && row['subordinate_offsets'] }.each do |row|
            datum = row.fetch('subordinate_offsets')['datum']
            next unless datum && datum['chart_datum'] && datum.fetch('named', {}).key?(datum['chart_datum']) && (datum.fetch('named', {}).key?('msl') || datum.key?('msl_offset_m'))
            path = File.join(@directory, 'OTC_20261011-stations', "#{row.fetch('station_id')}.json")
            next unless File.exist?(path)
            station = JSON.parse(File.read(path))
            expect(station.dig('source', 'otc', 'datum_fallback')).to be_nil
            expect(station.dig('source', 'otc', 'datum')).to eq(datum)
        end
    end

    it 'exports real signed major-axis current bins and the exact subordinate reference' do
        %w[OTC-00bdf4b5 OTC-017aa91a].each do |id|
            row = @rows.find { |item| item['station_id'] == id }
            native = row.fetch('constant_sets').find { |set| set['set_id'] == row['recommended_set_id'] }
            bin = native.fetch('current_bins').find { |item| item['bin'] == row.fetch('default_current_bin') }
            station = JSON.parse(File.read(File.join(@directory, 'OTC_20261011-stations', "#{id}.json")))
            sigma = Math.cos((bin.fetch('azimuth_deg') - bin.fetch('mean_flood_dir_deg')) * Math::PI / 180) < 0 ? -1 : 1
            expect(station.fetch('kind')).to eq('current')
            expect(station.fetch('current')).to include('mean_flow' => sigma * bin.fetch('mean_major_ms') / 0.5144444444444445,
                                                        'flood_direction' => bin.fetch('mean_flood_dir_deg'), 'ebb_direction' => bin.fetch('mean_ebb_dir_deg'))
            expect(station.fetch('harmonic_constituents')).to eq(bin.fetch('constituents').map { |term| {'name' => term.fetch('name'), 'amplitude' => term.fetch('major_amplitude_ms') / 0.5144444444444445, 'phase' => (term.fetch('major_phase_deg') + (sigma < 0 ? 180 : 0)) % 360} })
            expect(station.fetch('disclaimers')).to include('signed_major_axis')
        end
        station = JSON.parse(File.read(File.join(@directory, 'OTC_20261011-stations/OTC-0034ae14.json')))
        expect(station.fetch('current').fetch('offsets')).to include('reference' => 'OTC-0cfcf1db', 'flood_time' => -135, 'ebb_time' => -162,
                                                                   'flood_speed_ratio' => 0.4, 'ebb_speed_ratio' => 0.3,
                                                                   'slack_before_flood' => -94, 'slack_before_ebb' => -65)
        expect(station.fetch('harmonic_constituents')).to eq([])
    end
end
