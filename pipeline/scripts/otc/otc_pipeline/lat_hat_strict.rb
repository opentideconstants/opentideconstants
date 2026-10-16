# frozen_string_literal: true

# Thin process boundary to the existing strict reference engine. No astronomy
# or root-search arithmetic lives here. Monthly windows bound retained events;
# the engine pads and reconciles New Year independently on every call.
require 'json'
require 'time'

root = ENV.fetch('OTC_ENGINE_WORKTREE')
require File.join(File.realpath(root), 'lib/harmonics_strict')

request = JSON.parse($stdin.read)
astro = Harmonics::Strict::AstroTable.from_h(request.fetch('astro_table'))
engine = Harmonics::Engine.allocate
start = Time.iso8601(request.fetch('start')).utc
stop = Time.iso8601(request.fetch('end')).utc
raise ArgumentError, 'start must precede end' unless start < stop
constituents = request.fetch('constituents')
offset = request.fetch('msl_offset_m')
counts = { 'low' => 0, 'high' => 0 }
selected = {}
cursor = start
chunks = 0
while cursor < stop
    boundary = cursor.month == 12 ? Time.utc(cursor.year + 1, 1, 1) : Time.utc(cursor.year, cursor.month + 1, 1)
    boundary = [boundary, stop].min
    events = engine.extremes_strict(constituents, cursor, boundary, datum_term: offset, astro: astro)
    events.each do |event|
        type = event.fetch('type')
        counts[type] += 1
        previous = selected[type]
        better = previous.nil? || (type == 'low' ? event['height'] < previous['height'] : event['height'] > previous['height'])
        selected[type] = event if better
    end
    cursor = boundary
    chunks += 1
    warn JSON.generate({ 'completed_until' => cursor.iso8601, 'chunks' => chunks, 'event_counts' => counts })
end
raise ArgumentError, 'no high and low water extrema' unless selected.key?('low') && selected.key?('high')
puts JSON.generate({ 'lat_m' => selected.fetch('low').fetch('height'),
                     'hat_m' => selected.fetch('high').fetch('height'),
                     'lat_time' => selected.fetch('low').fetch('time').iso8601,
                     'hat_time' => selected.fetch('high').fetch('time').iso8601,
                     'low_count' => counts['low'], 'high_count' => counts['high'], 'chunks' => chunks,
                     'start' => start.iso8601, 'end' => stop.iso8601, 'accepted' => false })
