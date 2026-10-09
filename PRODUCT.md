# Product

## Register

brand

## Platform

web

## Users

Primary: developers who build marine and tide software (tide-prediction engines, chartplotter plugins, OpenCPN and Signal K add-ons, marine apps, calendar feeds such as webcaltides). They come to answer three questions fast: what is in the file, how do I fetch and verify it, and can I trust the numbers at the stations I care about. They read JSON, check SHA-256 sums and pin versions.

Secondary: oceanographers, hydrographers and data people. They read the method, the conventions and the per-station validation, and they cite the DOI.

Third: sailors and boaters who want to know why their tide times are now right (or were wrong before), usually arriving from a forum post or from webcaltides.

## Product Purpose

OpenTideConstants (OTC) is one open dataset of tidal harmonic constants, rebuilt automatically from the latest upstream data and released under CC BY 4.0 with one concept DOI for all releases. It combines constants fitted from GESLA sea-level records (replacing TICON), NOAA CO-OPS constants and offsets (replacing the XTide harmonics for US data), and constants from agencies that publish them openly (Kartverket today). Every release publishes the accuracy of every station against official predictions, declares the convention of every constant set, and carries per-record provenance and checksums.

Success: developers switch their pipelines from TICON/XTide files to `OTC_latest.json`; researchers cite the DOI; issues about specific stations arrive in the public tracker.

There is no release yet. The site must say so plainly and never show a download or accuracy figure that does not exist.

## Positioning

The tidal constants you can check: every station's accuracy, every constant's convention and every record's provenance are published with each release.

## Conversion & proof

- Primary CTA: Data & downloads (format, file table, `OTC_latest.json`). Before the first release: read the format and watch the repository.
- Secondary CTA: Read the method (QC rules, time-base audit, gates).
- The line a visitor remembers after 10 seconds: "Open tidal constants, with the accuracy of every station published."
- Belief ladder: (1) today's constants are silently wrong in places, and nobody publishes how wrong; (2) OTC checks every input record and publishes the evidence; (3) the format is clean, versioned and verifiable; (4) it will stay current without a person in the loop; (5) it is safe to depend on (DOI, immutable dated files, stable station ids).
- Proof on hand: measured problems in today's data (TICON-4 WSV phases about 95 min late; GESLA-4.1 WSV files about 30 min early; Hirtshals record with no tide; undeclared conventions). Proof-of-concept results at 11 stations (our fit matches or beats TICON-4 at 10; Helgoland 88-90 to 6.7 min, Seattle 6.8 to 2.5 min, Leeville 29 to 13 min). These are labelled as proof of concept everywhere. No testimonials, logos or press yet.

## Brand Personality

Precise, transparent, collegial. Confident without marketing gloss: it states measurements with their labels and admits what is not yet measured. It talks to peers. Emotional goal: the reader feels they are holding an instrument that has been calibrated, and that they are allowed to look inside it.

## Anti-references

- Ocean-blue SaaS: gradient waves, teal-to-navy heroes, "dive into data" copy, stock photos of surf.
- Startup hero-metric pages: giant station counts, "99.9%" style stats, logo walls.
- Terminal-cosplay dark mode used only to look "for developers".
- The editorial-magazine lane: italic display serif, broadsheet rules, monochrome restraint for its own sake.
- Academic portal clutter: dense sidebars, PDF-first downloads, unlabelled tables.

## Design Principles

1. Show the evidence, not the claim. Every number on the site has a label saying where it comes from (measured, proof of concept, or coming with the first release).
2. The file is the product. The format, the file names and the fetch-and-verify path are first-class content, not an appendix.
3. Plain status. When something does not exist yet, say so in the same visual weight as everything else; never fake a download.
4. Instrument-grade clarity. Tabular figures, exact units, stable names, no decoration that competes with data.
5. Respect three audiences in order: developers first, researchers second, sailors third, without hiding anything from any of them.

## Accessibility & Inclusion

WCAG 2.2 AA as the floor: body text at least 4.5:1, large text at least 3:1, visible focus, skip link, keyboard-operable navigation, tables with headers and captions that scroll inside their wrapper rather than the page. Reduced-motion users get no movement. Colour never carries meaning alone (status words accompany status colours). Works at 320 px wide with no horizontal page scroll. Many readers are non-native English speakers: short sentences, one term per concept.
