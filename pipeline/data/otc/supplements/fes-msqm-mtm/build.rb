#!/usr/bin/env ruby
require "json"
require "optparse"
require "pathname"

# Inputs are the previously accepted native release, retained extraction and
# authenticated congen runs. This recipe never samples the FES grids.
options = {}
OptionParser.new do |parser|
    %w[native fes-extraction congen-run fes-congen-run out].each do |name|
        parser.on("--#{name} PATH") { |value| options[name] = File.expand_path(value) }
    end
end.parse!
missing = %w[native fes-extraction congen-run fes-congen-run out] - options.keys
abort "Missing options: #{missing.join(', ')}" unless missing.empty?
root = Pathname.new(__dir__).join("../../../../..").realpath
profile = JSON.parse(File.read(File.join(__dir__, "profile.json")))
unless File.file?(File.join(options.fetch("fes-congen-run"), "PROVENANCE.json"))
    ok = system("bash", root.join("pipeline/scripts/otc/tools/congen_tables.sh").to_s,
                "--fes", options.fetch("congen-run"), options.fetch("fes-congen-run"))
    abort "FES astronomy generation failed" unless ok
end
arguments = ["--cached-fes-astronomy"]
options.each { |name, value| arguments.concat(["--#{name}", value]) }
%w[native_sha256 native_meta_sha256 fes_extraction_sha256].each do |key|
    arguments.concat(["--#{key.tr('_', '-')}", profile.fetch(key)])
end
environment = {"PYTHONPATH" => root.join("pipeline/scripts/gesla").to_s}
exec environment, "bash", root.join("pipeline/bin/build-otc").to_s, *arguments
