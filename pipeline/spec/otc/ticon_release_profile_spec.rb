# frozen_string_literal: true

require 'rspec'
require 'json'
require 'open3'
require 'tmpdir'
require 'fileutils'
require 'rbconfig'

RSpec.describe 'the committed TICON release profile' do
    let(:directory) { File.expand_path('../../data/otc/computed_datums/ticon-760-20261011', __dir__) }
    let(:script) { File.join(directory, 'build.rb') }

    it 'verifies the actual hydrated inputs and every prior computed cohort' do
        output, status = Open3.capture2e(RbConfig.ruby, script, '--verify')
        expect(status.success?).to be(true), output
        expect(JSON.parse(output)).to include('subordinate_pairs' => 1182, 'prior_ticon_pairs' => 758,
            'd4_chart_sets' => 330, 'new_ticon_pairs' => 760, 'new_ticon_stations' => 549,
            'convention_unverified_sets' => 658)
    end

    it 'fails when a required release input is absent' do
        Dir.mktmpdir('otc-profile-missing-') do |temporary|
            FileUtils.cp([script, File.join(directory, 'profile.json')], temporary)
            output, status = Open3.capture2e(RbConfig.ruby, File.join(temporary, 'build.rb'), '--verify')
            expect(status.success?).to be(false)
            expect(output).to include('Missing profile input: baseline/OTC_20261011.jsonl')
        end
    end

    it 'rejects truncated real input bytes before importing data' do
        Dir.mktmpdir('otc-profile-corrupt-') do |temporary|
            FileUtils.cp([script, File.join(directory, 'profile.json')], temporary)
            FileUtils.mkdir_p(File.join(temporary, 'baseline'))
            File.binwrite(File.join(temporary, 'baseline/OTC_20261011.jsonl'),
                File.binread(File.join(directory, 'baseline/OTC_20261011.jsonl'), 64))
            output, status = Open3.capture2e(RbConfig.ruby, File.join(temporary, 'build.rb'), '--verify')
            expect(status.success?).to be(false)
            expect(output).to include('Profile SHA-256 mismatch: baseline/OTC_20261011.jsonl')
        end
    end
end
