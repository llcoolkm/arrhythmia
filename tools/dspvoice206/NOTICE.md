# Source and reference notices

The assembly, table formulas, curated ratio banks and adapter in this package
are Arrhythmia's own work, licensed under MIT. The package derives required
firmware material from the user's local official input; that material is not
distributed in this source set.

The FM comparison reference was Google's Music Synthesizer for Android
([msfa](https://github.com/google/music-synthesizer-for-android)), revision
`f67d41d313b7dc85f6fb99e79e515cc9d208cfff`. Its engine is Apache-2.0 licensed;
the checked `COPYING` SHA-256 is
`cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`.
It was an external comparison oracle. No msfa source, Yamaha ROM table or
Dexed implementation is distributed here. The envelope and ratio tables are
the Arrhythmia curves, not a claim to reproduce Yamaha's envelope laws.

The assembler frontend and build recipe are shared with the 0.1.1 source
package. They link external dependencies when built. Their separate licences,
copyrights and source-distribution conditions are retained in
[UPSTREAM.md](../dspvoice/toolchain/UPSTREAM.md) and
[NOTICES.md](../dspvoice/toolchain/NOTICES.md); the MIT notice does not replace
those dependency licences. No compiled assembler is supplied.
