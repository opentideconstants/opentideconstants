# Malibu Rapids current station 4375

This input adds one historical, unvalidated current identity as fallback metadata.
It is distinct from Malibu Islet water-level station 07860 and Malibu Inner.
The generic original-source inventory reader consumes `gauge_chs-table4375.json`.
No SDK, schema, station-merging rule or predictive adapter changes are needed.

The independent source is CHS *Canadian Tide and Current Tables 2021*, Volume 5,
Table 4, printed page 129. `primary-citation.json` records the source URL, PDF
hash, edition, page and limitations. The PDF is an external proof input and is
not bundled here. The whole-minute source coordinates remain unchanged. Country
CAN is a labelled interpretation from the official BC geographical-name record
for Malibu Rapids; its more precise feature coordinates are not substituted.

This candidate contains identity, position, provenance and a known rights notice.
CHS table timing, direction and speed values remain in private primary evidence
pending clarification of the standing restriction on bundled CHS data. This is
not a validated prediction model, continuous speed curve or accuracy claim.
There are no acquired harmonic constants, published sets or recommended set.

Printed page 121 requires written CHS permission before reproduction. This input
preserves that notice and asserts no grant or commercial-use permission. It does
not inherit IWLS or open-government licence terms. No comparator supplies data.

## Extend the pinned build inputs

Run `ruby prepare_inputs.rb --profile EXISTING_COMMAND.json --primary-pdf LOCAL_PDF
--out NEW_DIRECTORY`. Use an accepted full build command with its original pinned
inventory and six quality findings. The helper checks those parent hashes, checks
the separately acquired primary PDF and committed capture, appends one identity,
and writes a new inventory and build command. It never replaces the originals.

All existing build arguments remain except the two new input paths and their
hashes, the output directory, the current producer directory and an isolated uv
environment. Only seven quality-finding inventory references are rebound; metric
findings remain unchanged. The country cache is unchanged. `input-lineage.json`
records every replacement. Use the generated `cwd`, `argv` and `env` to execute
the existing producer once with cached inputs, then validate its output.
