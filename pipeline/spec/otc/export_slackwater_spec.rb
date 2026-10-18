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

    it 'keeps the actual untied subordinate ratio record excluded' do
        entry = report.fetch('records').find { |item| item['station_id'] == 'OTC-01333ad1' }
        expect(entry.fetch('status')).to eq('unrepresentable')
        expect(entry.fetch('reason')).to eq('native MSL/chart datum values unavailable')
        expect(File.exist?(File.join(@directory, 'OTC_20261011-stations/OTC-01333ad1.json'))).to be(false)
    end
end
