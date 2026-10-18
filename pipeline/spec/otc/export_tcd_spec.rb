# frozen_string_literal: true

require 'rspec'
require 'json'
require 'open3'
require 'tmpdir'
require 'fileutils'
require 'rbconfig'
require 'digest'
require 'tcd'

RSpec.describe 'the real native release TCD export' do
    before(:context) do
        keys = %w[OTC_TCD_INPUT OTC_TCD_RELEASE]
        skip 'Set OTC_TCD_INPUT and OTC_TCD_RELEASE to the qualified native release' if keys.none? { |key| ENV.key?(key) }
        @input, @release = keys.map { |key| ENV.fetch(key) }
        @rows = File.foreach(@input).map { |line| JSON.parse(line) }
        @metadata = JSON.parse(File.read(@release))
        @date = @metadata.fetch('release').fetch('datestamp')
        @directory = Dir.mktmpdir('otc-tcd-spec-')
        script = File.expand_path('../../scripts/otc/export_tcd.rb', __dir__)
        @log, @status = Open3.capture2e(RbConfig.ruby, script, '--input', @input,
                                      '--release', @release, '--output-dir', @directory, '--date', @date)
    end

    after(:context) { FileUtils.remove_entry(@directory) if @directory }

    def report
        expect([0, 2]).to include(@status.exitstatus), @log
        JSON.parse(File.read(File.join(@directory, "OTC_#{@date}-tcd-report.json")))
    end

    def read_output(mode)
        suffix = mode == 'complete' ? '-complete' : ''
        TCD.open(File.join(@directory, "OTC_#{@date}#{suffix}.tcd")) { |db| yield db }
    end

    it 'writes both real binaries, binds inputs and accounts for every native station and set' do
        document = report
        expect(document.fetch('input_sha256')).to eq(Digest::SHA256.file(@input).hexdigest)
        expect(document.fetch('release_sha256')).to eq(Digest::SHA256.file(@release).hexdigest)
        expect(document.fetch('stations').map { |row| row.fetch('station_id') }).to match_array(@rows.map { |row| row.fetch('station_id') })
        expected = @rows.flat_map { |row| row.fetch('constant_sets').map { |set| set.fetch('set_id') } }
        actual = document.fetch('outputs').fetch('complete').fetch('records').filter_map { |row| row['set_id'] }
        expect(actual.uniq).to match_array(expected)
        %w[recommended complete].each do |mode|
            entries = document.fetch('outputs').fetch(mode).fetch('records')
            read_output(mode) { |db| expect(db.station_count).to eq(entries.count { |entry| entry['status'] == 'exported' }) }
        end
    end

    it 'retains stable native IDs for default current bins and distinct IDs for every additional bin' do
        entries = report.fetch('outputs').fetch('complete').fetch('records')
        read_output('complete') do |db|
            entries.select { |entry| entry['status'] == 'exported' && (entry['bin'] || entry['kind'] == 'current_offset') }.each do |entry|
                row = @rows.find { |item| item['station_id'] == entry.fetch('station_id') }
                number = entry['bin'] || entry.fetch('native_metadata').fetch('bin')
                expected = number == row.fetch('default_current_bin') ? row.fetch('station_id') : "#{row.fetch('station_id')}_bin_#{number}"
                expect(db.stations[entry.fetch('record_index')].station_id).to eq(expected)
            end
        end
    end

    it 'preserves all exported coefficients and their already-normalized Greenwich phases' do
        sets = @rows.flat_map { |row| row.fetch('constant_sets') }.to_h { |set| [set.fetch('set_id'), set] }
        entries = report.fetch('outputs').fetch('complete').fetch('records')
        read_output('complete') do |db|
            names = db.constituents.map(&:name)
            entries.select { |entry| entry['status'] == 'exported' && entry['set_id'] }.each do |entry|
                station = db.stations[entry.fetch('record_index')]
                native = sets.fetch(entry.fetch('set_id'))
                next if native['quantity'] == 'current'
                expect(station.zone_offset).to eq(0)
                expect(station.datum_offset).to be_within(0.000051).of(native.fetch('datum', {}).fetch('msl_offset_m', 0))
                datum = native.fetch('datum', {})
                if datum['chart_datum'] && !datum['zero_name'] && datum.key?('msl_offset_m')
                    expect(station.datum).to eq('Native zero (unspecified)')
                end
                native.fetch('constituents').each do |term|
                    index = names.index(term.fetch('name'))
                    expect(index).not_to be_nil
                    expect(station.amplitudes[index]).to be_within(0.000051).of(term.fetch('amplitude_m'))
                    next if station.amplitudes[index].zero?

                    distance = (station.epochs[index] - term.fetch('phase_deg') + 180) % 360 - 180
                    expect(distance.abs).to be <= 0.005001
                end
            end
        end
    end

    it 'makes three actual station cells loadable with the native recommendation and fallback labels' do
        entries = report.fetch('outputs').fetch('recommended').fetch('records')
        read_output('recommended') do |db|
            %w[OTC-000d594d OTC-000180a3 OTC-0012535c].each do |id|
                row = @rows.find { |item| item['station_id'] == id }
                expect(row).not_to be_nil
                entry = entries.find { |item| item['station_id'] == id && item['set_id'] == row['recommended_set_id'] }
                expect(entry.fetch('status')).to eq('exported'), entry.inspect
                station = db.stations[entry.fetch('record_index')]
                expect(station.active_constituents).to be > 0
                expect(station.notes).to include(row.fetch('recommended_set_id'))
                expect(station.name).to include('fallback:') if row['recommended_is_fallback']
            end
        end
        extra = report.fetch('outputs').fetch('complete').fetch('records').find { |entry| entry['set_id'] == 'OTC-000180a3/fes2022' }
        expect(extra.fetch('name')).to include('fallback:reliability_withheld:')
    end

    it 'retains the explicit subordinate datum note while preserving the datum-less harmonic warning' do
        entries = report.fetch('outputs').fetch('complete').fetch('records')
        read_output('complete') do |db|
            subordinate = entries.find { |entry| entry['identity'] == 'OTC-0012535c/subordinate' }
            notes = JSON.parse(db.stations[subordinate.fetch('record_index')].notes)
            expect(notes.fetch('datum')).to include('zero' => 'chart_datum', 'zero_name' => 'MLLW', 'chart_datum' => 'mllw')
            expect(notes.fetch('datum_note')).to eq('Subordinate heights use native reference offsets; see native datum metadata.')

            harmonic = entries.find { |entry| entry['set_id'] == 'OTC-0012535c/fes2022' }
            notes = JSON.parse(db.stations[harmonic.fetch('record_index')].notes)
            expect(notes.fetch('datum_note')).to eq('Mean-level-relative harmonics; absolute chart datum is unavailable.')
        end
    end

    it 'keeps valid subordinate references and exports real currents with explicit scalar limitations' do
        entries = report.fetch('outputs').fetch('complete').fetch('records')
        read_output('complete') do |db|
            subordinate = entries.select { |entry| entry['kind'] == 'subordinate' && entry['status'] == 'exported' }
            expect(subordinate).not_to be_empty
            subordinate.each do |entry|
                station = db.stations[entry.fetch('record_index')]
                expect(station.subordinate?).to be(true)
                expect(db.stations[station.reference_station].reference?).to be(true)
            end
        end
        currents = @rows.flat_map { |row| row.fetch('constant_sets') }.select { |set| set['quantity'] == 'current' }
        expect(currents).not_to be_empty
        read_output('recommended') do |db|
            %w[OTC-00bdf4b5 OTC-017aa91a].each do |id|
                row = @rows.find { |item| item['station_id'] == id }
                native = row.fetch('constant_sets').find { |set| set['set_id'] == row['recommended_set_id'] }
                bin = native.fetch('current_bins').find { |item| item['bin'] == row.fetch('default_current_bin') }
                entry = report.fetch('outputs').fetch('recommended').fetch('records').find { |item| item['station_id'] == id && item['status'] == 'exported' }
                expect(entry).not_to be_nil
                station = db.stations[entry.fetch('record_index')]
                expect(station.level_units).to eq('knots')
                expect(station.max_direction).to eq(bin.fetch('mean_flood_dir_deg'))
                expect(station.min_direction).to eq(bin.fetch('mean_ebb_dir_deg'))
                expect(station.datum_offset).to be_within(0.000051).of(bin.fetch('mean_major_ms') / 0.5144444444444445)
                expect(station.notes).to include('signed_major_axis')
            end
            entry = report.fetch('outputs').fetch('recommended').fetch('records').find { |item| item['station_id'] == 'OTC-0034ae14' && item['status'] == 'exported' }
            expect(entry).not_to be_nil
            station = db.stations[entry.fetch('record_index')]
            expect(station.subordinate?).to be(true)
            expect(db.stations[station.reference_station].name).to include('OTC-0cfcf1db')
            expect(station.max_level_multiply).to be_within(0.0001).of(0.4)
            expect(station.min_level_multiply).to be_within(0.0001).of(0.3)
        end
        expect(@status.exitstatus).to eq(2)
    end
end
