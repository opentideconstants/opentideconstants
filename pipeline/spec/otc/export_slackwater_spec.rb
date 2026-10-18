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

    it 'reports datum-null FES records as unrepresentable and preserves their fallback evidence' do
        entry = report.fetch('records').find { |item| item['station_id'] == 'OTC-0012a4f2' }
        expect(entry.fetch('status')).to eq('unrepresentable')
        expect(entry.fetch('reason')).to include('datum')
        expect(entry.fetch('native').fetch('fallback').fetch('reason')).to eq('reliability_withheld')
        expect(File.exist?(File.join(@directory, 'OTC_20261011-stations/OTC-0012a4f2.json'))).to be(false)
    end
end
