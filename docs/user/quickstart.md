# Circus Arrhythmia FM quick start

1. **Back up.** Turn the unit off, remove the microSD card and copy it to your computer.
2. **Download** the Novation firmware 2.0.1: <https://components.novationmusic.com/api/v2/firmwares/169/file>.
3. **Connect.** Put the card back. Close Novation Components and all other music applications. Connect the unit by
   USB and turn it on.
4. **Install.** Open <https://llcoolkm.github.io/arrhythmia/web/> in Chrome. Press **Connect MIDI**, select the
   downloaded file and press **Install**.
5. **Confirm.** Press **Play** on the unit when it flashes. The installation takes approximately half a minute. The
   unit then restarts.
6. **Add the FM engine.** Open the [loader](https://llcoolkm.github.io/arrhythmia/web/loader.html), connect and
   scan the card. Add the published `fm.dse` 2.0.0 and select it for a pack
   ([engines](synth.md#engines-on-track-8)). On an update from 0.3.0, it replaces `fm.dse` 1.0.0. Save project
   edits, then load the pack on the unit in the stock Packs view.
7. **Play.** Play pads on track 8 to hear the FM synth. Macro 6 sets the FM depth.
8. **Select a sound.** Open the Sample view of track 8 and tap the pads of rows 3 and 4. Each tap selects a sound
   and plays it. Press Down for the same 16 sounds under the second algorithm ([synth](synth.md)).

Troubleshooting, the return to stock and all other procedures: [install guide](install.md).
