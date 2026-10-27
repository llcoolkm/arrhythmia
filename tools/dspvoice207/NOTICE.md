# Source and reference notices

The filter and envelope assembly and mathematical table formulas added in
this package are Arrhythmia's own work, licensed under MIT. The package
reuses the earlier FM instrument's own source and public build support. It
derives required firmware material from the user's local official input;
that material is not distributed in this source set.

The earlier FM reference and provenance notices remain in
[VERSION 206 NOTICE.md](../dspvoice206/NOTICE.md). No additional external
source or ROM table is included by the VERSION 207 changes. The curves are
Arrhythmia's own design, not a claim to reproduce Yamaha's envelope laws.

The assembler frontend and build recipe are shared with the 0.1.1 source
package. Their separate licences, copyrights and source-distribution
conditions are retained in [UPSTREAM.md](../dspvoice/toolchain/UPSTREAM.md)
and [NOTICES.md](../dspvoice/toolchain/NOTICES.md); the MIT notice does not
replace those dependency licences. No compiled assembler is supplied.
