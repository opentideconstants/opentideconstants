# frozen_string_literal: true

# Thin process boundary to the existing strict reference engine. No astronomy
# or root-search arithmetic lives here. Monthly windows bound retained events;
# the engine pads and reconciles New Year independently on every call.
require 'json'
require 'time'

root = ENV.fetch('OTC_ENGINE_WORKTREE')
require File.join(File.realpath(root), 'lib/harmonics_strict')

def compute_extrema(request, astro)
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
        events = if request.key?('subordinate_offsets')
                     engine.subordinate_extremes_strict(constituents, offset, request.fetch('subordinate_offsets'), cursor, boundary, astro: astro)
                 else
                     engine.extremes_strict(constituents, cursor, boundary, datum_term: offset, astro: astro)
                 end
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
    { 'lat_m' => selected.fetch('low').fetch('height'),
      'hat_m' => selected.fetch('high').fetch('height'),
      'lat_time' => selected.fetch('low').fetch('time').iso8601,
      'hat_time' => selected.fetch('high').fetch('time').iso8601,
      'low_count' => counts['low'], 'high_count' => counts['high'], 'chunks' => chunks,
      'start' => start.iso8601, 'end' => stop.iso8601, 'accepted' => false }
end

request = JSON.parse($stdin.read)
unless request.is_a?(Array)
    astro = Harmonics::Strict::AstroTable.from_h(request.fetch('astro_table'))
    puts JSON.generate(compute_extrema(request, astro))
    exit
end

# Internal callers submit one matching reference/padding group per batch.
# Preserve exact ordered inputs and padded endpoints; never widen a window.
tables = {}
cache = {}
hits = 0
original = Harmonics::Strict.method(:extreme_candidates)
Harmonics::Strict.define_singleton_method(:extreme_candidates) do |table, constituents, start, stop, datum_term|
    key = [table, Marshal.dump(constituents), start.to_r, stop.to_r, Marshal.dump(datum_term)]
    if cache.key?(key)
        hits += 1
    else
        cache[key] = Marshal.dump(original.call(table, constituents, start, stop, datum_term))
    end
    # Each engine call owns its candidate objects, even if a future engine
    # consumer mutates them. Serialization does not alter numerical values.
    Marshal.load(cache.fetch(key))
end
begin
    results = request.map do |item|
        raw_table = item.fetch('astro_table')
        table_key = JSON.generate(raw_table)
        astro = tables[table_key] ||= Harmonics::Strict::AstroTable.from_h(raw_table)
        compute_extrema(item, astro)
    end
    puts JSON.generate(results)
    warn JSON.generate({ 'reference_cache' => { 'misses' => cache.length, 'hits' => hits } })
ensure
    Harmonics::Strict.define_singleton_method(:extreme_candidates, original)
    cache.clear
    tables.clear
end
