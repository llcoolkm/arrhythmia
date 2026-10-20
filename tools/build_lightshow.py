"""Compile a hook patch (C in patches/src/) and write its patch set JSON.

    uv run python -I tools/build_lightshow.py [lightshow-glitter|heapdiag|flashdump|devkit|devkit-dsp|devkit-input|devkit-mode|devkit-play|devkit-load|devkit-clock|devkit-abi3|devkit-tm|devkit-plock|devkit-a2|devkit-o1|arrhythmia-devkit-arm|arrhythmia-firmware-arm|...]

The code is placed in the free flash right after the stock image and reached through two rewritten BL instructions.
The object file must contain no relocations in .text; everything it calls is an absolute firmware address.
"""
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile

BASE = 0x08010000
CODE_AT = 0x0806A570                      # end of the stock 5706 image (word aligned)
FREE_END = 0x08080000                     # image size cap (0x70000 bytes)
# Host table (targets with "host_table"): at CODE_AT, the hook code follows. u32 magic 'CRH1', u32 abi, u32 note()
# Thumb address, u32 built-in module address (0 none); abi 3: then note_port(), param(), ui(), midi().
HOST_TABLE_SIZE = 16
HOST_EXTRA = ("note_port", "param", "ui", "midi")      # abi 3 words 4..7, in this order
HOST_MAGIC = 0x31485243
HOST_ABI = 1
SITES = {"hook_frame": 0x0802596E,
         "hook_sysex": 0x08026E42,
         "hook_boot": 0x080256DE,
         "hook_content": 0x0802AD36,
         "hook_main": 0x0802586C,
         "hook_pulse": 0x08033384,
         # project refresh call sites
         "hook_apply_load": 0x08037EB2,
         "hook_apply_clear": 0x080386A8,
         "hook_apply_pack": 0x08038750,
         # p-lock fix
         "hook_lockplay": 0x080552AC,
         "hook_save_ser": 0x08037F54,
         # AD: the save's post call (after the stock slice-mode reset of Start/Length); track 8's
         # keyboard constructor; the Note view's pad colour (two calls); the macro page constructor's light init
         "hook_save_post": 0x08037FD6, "hook_kb_new": 0x0804C53A,
         "hook_nv_col0": 0x0805D9CE, "hook_nv_col1": 0x0805DC50, "hook_mp_init": 0x080580F8,
         "hook_step_note": 0x0805E41A,
         # AD (track 8 ignores slice mode): the octave-arrow gate's and the expanded Note view's reads of setting C
         "hook_oct_c": 0x080588C0, "hook_xv_c": 0x0803F00A,
         # A1 note tap
         "hook_ring_slot": 0x08040746, "hook_ring_retrig": 0x080407BA, "hook_ring_on": 0x080407E6,
         "hook_ring_force": 0x08040834, "hook_ring_rel": 0x0804086C,
         "hook_fo_choke": 0x080334E6, "hook_fo_edit": 0x0803A7EA,
         "hook_a1_tick": 0x08025874, "hook_a1_boot": 0x08025774,
         # O1 bounded DSP waits: mailbox poll sites
         "hook_mb_reset": 0x0806441E,
         "hook_mb_stop": 0x08064496,
         "hook_mb_tail": 0x08064542,
         # O1 ready-GPIO spin sites
         "hook_rdy_bulk0": 0x08031484,
         "hook_rdy_bulk1": 0x080314CC,
         "hook_rdy_enter1": 0x08031574,
         "hook_rdy_leave1": 0x080315BC,
         "hook_rdy_tail0": 0x080315DC,
         # O1 quarantine: operation boundaries
         "hook_pack_access": 0x08053E26,
         "hook_slot_upload": 0x08053E74,
         "hook_slot_job": 0x080460BA,
         "hook_sx_upload": 0x08043C40,
         "hook_sx_read": 0x08043C98,
         "hook_encode": 0x0802964E,
         "hook_bulk_chunk": 0x080536C0,
         "hook_engine_on": 0x0804679E,
         # A1 activation handshake
         "hook_dsp_ready": 0x080278E2,
         # DSP stream upload, modes 0 and 1
         "hook_dsp_load0": 0x080278CC, "hook_dsp_load1": 0x080278A8}
# input event sites: Thumb-2 B.W tail calls, retargeted to our hooks
BW_SITES = {"hook_fo_eng": 0x08031A34,
            "hook_pad": 0x080295CA, "hook_button": 0x080295D8, "hook_pot": 0x080295E4,
            "hook_pressure": 0x080295F0, "hook_encoder": 0x080295FC,
            # p-lock fix
            "hook_fx": 0x0805A2C6}
STOCK_BW = {0x080295CA: 0x08040108, 0x080295D8: 0x0804012A, 0x080295E4: 0x08040140, 0x080295F0: 0x08040152,
            0x080295FC: 0x0804015E, 0x0805A2C6: 0x0805F2F8, 0x08031A34: 0x080467D2}
STOCK_BL = {0x0802596E: 0x0802960C, 0x08026E42: 0x0802CF90, 0x080256DE: 0x08026134, 0x0802AD36: 0x0802AD40,
            0x0802586C: 0x0802677A, 0x08033384: 0x080333AA, 0x08037EB2: 0x08037366, 0x080386A8: 0x08037366,
            0x08038750: 0x08037366, 0x080552AC: 0x08055324, 0x08037F54: 0x08037A10,
            0x08037FD6: 0x080472E2, 0x0804C53A: 0x08055DEC, 0x0805D9CE: 0x0805DABE, 0x0805DC50: 0x0805DABE,
            0x080580F8: 0x0805862C, 0x0805E41A: 0x0805E5A0, 0x080588C0: 0x0803FC58, 0x0803F00A: 0x0803FC58,
            0x08040746: 0x080406A8, 0x080407BA: 0x080406A8, 0x080407E6: 0x080406A8, 0x08040834: 0x080406A8,
            0x0804086C: 0x080406A8, 0x080334E6: 0x080467D2, 0x0803A7EA: 0x080467D2, 0x08025874: 0x080297DE,
            0x08025774: 0x08026F38,
            0x0806441E: 0x080314D6, 0x08064496: 0x080314D6, 0x08064542: 0x080314D6,
            0x08031484: 0x08023416, 0x080314CC: 0x08023416, 0x08031574: 0x08023416, 0x080315BC: 0x08023416,
            0x080315DC: 0x08023416, 0x08053E26: 0x08040E28, 0x08053E74: 0x08053E8C, 0x080460BA: 0x08053E8C,
            0x08043C40: 0x080536D2, 0x08043C98: 0x08053690, 0x0802964E: 0x08029684, 0x080536C0: 0x08031430,
            0x0804679E: 0x08040874, 0x080278E2: 0x08031700,
            0x080278CC: 0x08031110, 0x080278A8: 0x08031110}
# vtable words retargeted to a hook (stock word = Thumb address)
VT_SITES = {"hook_disp": (0x080696AC, 0x08049B29), "hook_macro": (0x08067E44, 0x08058161),
            "hook_fx": (0x0806834C, 0x0805F2F9),
            # A1 note tap
            "hook_ron": (0x080694B0, 0x0803B7ED), "hook_roff": (0x080694B4, 0x0803B8F1),
            "hook_nm_on": (0x08067B24, 0x0803ABD9), "hook_nm_off": (0x08067B28, 0x0803ACBB),
            "hook_eng_on": (0x080685E0, 0x08031A19), "hook_eng_off": (0x080685E4, 0x08031A23),
            "hook_midi_alloff": (0x080665EC, 0x0803F799),
            # AD: track 8 ignores slice mode (slice converter's and macro pages' observer entries)
            "hook_nm_obs": (0x08067B3C, 0x0803AD3D), "hook_mp_obs": (0x08067E60, 0x080584DB),
            "hook_sv_press": (0x08068294, 0x0805C1AD),
            # O1 quarantine: operation boundaries
            "hook_fs_length": (0x08066E5C, 0x0806438D),
            "hook_fs_alloc": (0x08066E64, 0x080643F5), "hook_fs_reset": (0x08066E68, 0x08064417),
            "hook_fs_bulk": (0x08066E6C, 0x0806444B), "hook_fs_free": (0x08066E78, 0x08064461),
            "hook_fs_start": (0x08066E80, 0x0806446F), "hook_fs_preview": (0x08066E84, 0x080644E5),
            "hook_fs_trim": (0x08066E88, 0x08064513), "hook_fs_stop": (0x08066E94, 0x08064481),
            # scales
            "hook_kb_obs": (0x080696F4, 0x08055F69)}
# function tails replaced by a B.W to a naked hook (stock bytes checked)
TAIL_SITES = {"hook_pack_done": (0x08053E88, "012070bd")}
# Arrhythmia 0.1 profiles: one switch per profile selects the ARM routing/UI (SYNTH), its hook sites and cmd 08;
# tools/dspmeter.py (devkit 7228) and tools/voicestack.py (firmware 7229) stack the DSP layer onto the ARM bases.
OR_HOOKS = ["hook_boot", "hook_frame", "hook_sysex", "hook_pad", "hook_button", "hook_pot", "hook_pressure",
            "hook_encoder", "hook_main", "hook_content", "hook_pulse", "hook_apply_load", "hook_apply_clear",
            "hook_apply_pack", "hook_lockplay", "hook_save_ser", "hook_disp", "hook_macro", "hook_fx",
            "hook_mb_reset", "hook_mb_stop", "hook_mb_tail", "hook_rdy_bulk0", "hook_rdy_bulk1", "hook_rdy_enter1",
            "hook_rdy_leave1", "hook_rdy_tail0", "hook_pack_access", "hook_slot_upload", "hook_slot_job",
            "hook_sx_upload", "hook_sx_read", "hook_encode", "hook_bulk_chunk", "hook_engine_on", "hook_fs_length",
            "hook_fs_alloc",
            "hook_fs_reset", "hook_fs_bulk", "hook_fs_free", "hook_fs_start", "hook_fs_preview", "hook_fs_trim",
            "hook_fs_stop", "hook_pack_done"]
OR_SCALES_HOOKS = ["hook_kb_obs"]
OR_A1_HOOKS = ["hook_ring_slot", "hook_ring_retrig", "hook_ring_on", "hook_ring_force", "hook_ring_rel", "hook_fo_eng",
               "hook_fo_choke", "hook_fo_edit", "hook_a1_tick", "hook_a1_boot", "hook_ron", "hook_roff", "hook_nm_on",
               "hook_nm_off", "hook_eng_on", "hook_eng_off", "hook_midi_alloff", "hook_dsp_ready",
               "hook_dsp_load0", "hook_dsp_load1"]
OR_AD_HOOKS = ["hook_nm_obs", "hook_mp_obs", "hook_save_post", "hook_kb_new", "hook_nv_col0", "hook_nv_col1",
               "hook_mp_init", "hook_sv_press", "hook_step_note", "hook_oct_c", "hook_xv_c"]
OR_PROFILES = {
    # SCALES=1 in every 0.1 profile; each image and ARM base has its own label (build-info block).
    # DUMP_ALLOW (SysEx 7D reads ITCM 0x100-0x3FFF only) is on in every profile except the SCALES=0 checks (dump_open)
    # Arrhythmia 0.1.0 (one octave per keyboard row, scales.c): images 7228 (devkit) / 7229 (firmware),
    # bases 7241 (devkit-arm) / 7242 (firmware-arm, -patch) / 7243 (devkit-synth-arm)
    "arrhythmia-devkit-arm": dict(synth=0, cmd08=1, scales=1, label=7241, version=[0, 0, 0, 7, 9, 0],
                                  image=("arrhythmia-devkit", 7228),
                                  dsp="6013 DSP meter layer (tools/dspmeter.py), PLACEHOLDER until Codex delivers the "
                                      "devkit profile's streams"),
    "arrhythmia-firmware-arm": dict(synth=1, cmd08=0, scales=1, label=7242, version=[0, 0, 0, 7, 9, 0],
                                    image=("arrhythmia-firmware", 7229),
                                    dsp="Codex firmware-profile streams dsp/subtractive-m2 709160f layout with the "
                                        "T60 2 s core 1 (decay K 0x7FB4B6, idle floor 0x400; tools/voicestack.py): "
                                        "core 1 voice decoder, ENABLE 0 at boot, core 0 stock "
                                        "relocated to 0x0807A020, no meter"),
    "arrhythmia-firmware-arm-patch": dict(synth=1, cmd08=0, scales=1, label=7242, version=[0, 0, 0, 7, 9, 0],
                                          a1_patch=1),
    "arrhythmia-devkit-synth-arm": dict(synth=1, cmd08=1, scales=1, label=7243, version=[0, 0, 0, 7, 9, 0]),
    # 0.1.1: the firmware profile + live attack/decay (PATCH_AD); ARM-only base 7251 of arrhythmia-firmware 7261
    # (the same ARM as development builds 7253 to 7255)
    "arrhythmia-firmware-arm-ad": dict(synth=1, cmd08=0, scales=1, ad=1, label=7251, version=[0, 0, 0, 7, 9, 0],
                                       image=("arrhythmia-firmware", 7261)),
    # SCALES=0 check builds (emulator only, never images): the two release profiles with the switch off; they keep the
    # previous cmd 7D (dump_open) so they still rebuild 7210 / 7211 byte for byte
    "arrhythmia-devkit-arm-noscales": dict(synth=0, cmd08=1, label=7210, dump_open=1, version=[0, 0, 0, 7, 9, 0],
                                           image=("arrhythmia-devkit", 7200),
                                           dsp="6013 DSP meter layer (tools/dspmeter.py), PLACEHOLDER until Codex "
                                               "delivers the devkit profile's streams"),
    # the firmware check keeps AD_BLOCKS 1,216 (0.8 s decay); the release profiles use 3,924 (T60 2 s)
    "arrhythmia-firmware-arm-noscales": dict(synth=1, cmd08=0, label=7211, version=[0, 0, 0, 7, 9, 0], ad_blocks=1216,
                                             dump_open=1,
                                             image=("arrhythmia-firmware", 7201),
                                             dsp="Codex firmware-profile streams dsp/subtractive-m2 709160f "
                                                 "(tools/voicestack.py): core 1 voice decoder, "
                                                 "ENABLE 0 at boot, core 0 stock relocated to 0x0807A020, no meter"),
}


def or_target(name, synth, cmd08, label, version, image=None, a1_patch=0, dsp="none", scales=0, ad_blocks=None,
              dump_open=0, ad=0):
    parts = ["Arrhythmia 0.1 " + ("firmware" if not cmd08 else "devkit") + f" profile (SYNTH={synth}, CMD08={cmd08}"
             + (", A1_PATCH=1, emulator only" if a1_patch else "") + "): 6015 (track modes T1, p-lock fix) + O1 "
             "bounded DSP waits with the sticky fault quarantine (SysEx 0D, Rec blinks) + module SDK"]
    parts.append("track-8 synth: settings page (patch controls hidden) and A1 note tap, chord engine, voice table, "
                  "ring admission (fixed block 0x2004F0E0..0x2004F3DF, heap limit 0x2004F0E0)" if synth else
                  "track 8 stock (no page, no tap, heap limit 0x2004F400)")
    parts.append("cmd 08 serves the DSP meter's X windows only" if cmd08 else "cmd 08 refused (status 0, no DSP read)")
    if scales:
        parts.append("scales for tracks 1..7 (Note-view keyboard tables, sub-record at T1 block + 0x288, SysEx 0E"
                     + (", page controls, project-wide scale for the chord engine" if synth else "") +
                     "; struct sc 32 B below the lowest block)")
    if ad:
        parts.append("live attack/decay (PATCH_AD 0xA06000, attack | decay << 7) from track 8's macros 2/3 "
                     "(params 7/8, identity; new projects and Clear + macro 3 on track 8: Length 64), paced 5 ms, "
                     "per-voice lifetime deadlines; track 8 ignores slice mode (macros 2/3, their lock lanes and lights, the save, the "
                     "Note-view keyboard, octave arrows and expanded view)")
    if not dump_open:
        parts.append("cmd 7D reads only ITCM 0x00000100-0x00003FFF (module RAM and code); every other address, "
                     "internal flash included, gets the refusal (13 bytes, no data)")
    floor = (0x2004F0E0 if synth else 0x2004F400) - (0x20 if scales else 0)
    return {"src": "arrhythmia.c", "hooks": OR_HOOKS + (OR_A1_HOOKS if synth else []) + (OR_SCALES_HOOKS if scales else [])
                      + (OR_AD_HOOKS if ad else []),
            "defines": [f"SYNTH={synth}", f"CMD08={cmd08}"] + (["A1_PATCH=1"] if a1_patch else [])
                       + (["SCALES=1"] if scales else []) + ([f"AD_BLOCKS={ad_blocks}u"] if ad_blocks else [])
                       + (["DUMP_ALLOW=0"] if dump_open else []) + (["AD_LIVE=1"] if ad else []),
            "profile": dict({"synth": synth, "cmd08": "meter windows" if cmd08 else "refused", "a1_patch": a1_patch,
                             "image": f"{image[0]} {image[1]}" if image else "none (emulation only)",
                             "dsp_layer": dsp}, **({"scales": 1} if scales else {}),
                            **({} if dump_open else {"cmd7d": "ITCM 0x00000100-0x00003FFF only"}),
                            **({"ad": "PATCH_AD"} if ad else {})),
            "version": version, "abi": 3, "heap_floor": f"0x{floor:08X}",
            "host_table": {"note": "host_note", "note_port": "host_note_port", "param": "host_param",
                           "ui": "host_ui", "midi": "host_midi"},
            "module": "modules/paint.c",
            "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": struct.pack("<H", label).hex(),
                       "note": f"build-info block: build number 5706 -> {label} (Arrhythmia 0.1 ARM-only base"
                               + (f" of {image[0]} {image[1]}" if image else "") + "; emulation only, never flashed)"}],
            "description": "; ".join(parts) + "."}


TARGETS = {
    "lightshow-glitter": {"src": "lightshow.c", "hooks": ["hook_boot", "hook_frame", "hook_sysex"],
                          "version": [0, 0, 0, 7, 4, 0],
                          "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": "7217",
                                     "note": "build-info block: build number 5706 -> 6002 (bootloader installs only "
                                             "when this 38-byte block differs from the installed app's)"}],
                          "description": "Lightshow step 1: glitter and static LED patterns, switched by SysEx "
                                         "F0 00 20 29 00 60 <cmd> ... F7 (00 off, 01 dens decay on, 02 k bit-plane, "
                                         "03 hi lo v... set bytes, 7F status)."},
    "devkit": {"src": "devkit.c", "hooks": ["hook_boot", "hook_frame", "hook_sysex"], "version": [0, 0, 0, 7, 4, 1],
               "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": "7317",
                          "note": "build-info block: build number 5706 -> 6003 (install marker)"}],
               "description": "Devkit: lightshow (glitter, static LED patterns), heap/flash/ITCM diagnostics and "
                              "effect modules uploaded into ITCM over SysEx (cmd 04 write, 05 start)."},
    "devkit-dsp": {"src": "devkit_dsp.c", "hooks": ["hook_boot", "hook_frame", "hook_sysex"],
                   "version": [0, 0, 0, 7, 4, 3],
                   "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": "7517",
                              "note": "build-info block: build number 5706 -> 6005 (install marker)"}],
                   "description": "Devkit plus cmd 08: safe DSP X/Y read under the stock DSP-bus lock, invalidating "
                                  "the cached DSP write address."},
    "devkit-input": {"src": "devkit_input.c",
                     "hooks": ["hook_boot", "hook_frame", "hook_sysex", "hook_pad", "hook_button", "hook_pot",
                               "hook_pressure", "hook_encoder"],
                     "version": [0, 0, 0, 7, 4, 4],
                     "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": "7617",
                                "note": "build-info block: build number 5706 -> 6006 (install marker)"}],
                     "description": "devkit-dsp plus an input event log: the five UI input B.W sites are observed "
                                    "(and passed on unchanged); cmd 09 reads the last 32 events."},
    "devkit-mode": {"src": "devkit_mode.c",
                    "hooks": ["hook_boot", "hook_frame", "hook_sysex", "hook_pad", "hook_button", "hook_pot",
                              "hook_pressure", "hook_encoder"],
                    "version": [0, 0, 0, 7, 4, 5],
                    "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": "7717",
                               "note": "build-info block: build number 5706 -> 6007 (install marker)"}],
                    "description": "devkit-input plus an interactive module mode: Shift + Sample Rec (or cmd 0A) "
                                   "hands pads, buttons and encoders to the running ITCM module (input entry)."},
    "devkit-play": {"src": "devkit_play.c",
                    "hooks": ["hook_boot", "hook_frame", "hook_sysex", "hook_pad", "hook_button", "hook_pot",
                              "hook_pressure", "hook_encoder", "hook_main"],
                    "version": [0, 0, 0, 7, 4, 6],
                    "host_table": {"note": "host_note"}, "module": "modules/paint.c",
                    "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": "7817",
                               "note": "build-info block: build number 5706 -> 6008 (install marker)"}],
                    "description": "devkit-mode plus a built-in module (paint) started and stopped with Shift + "
                                   "Sample Rec, lights that follow ownership, and a module note queue drained in "
                                   "the Main task into the note router (port 0x18 + t)."},
    "devkit-load": {"src": "devkit_load.c",
                    "hooks": ["hook_boot", "hook_frame", "hook_sysex", "hook_pad", "hook_button", "hook_pot",
                              "hook_pressure", "hook_encoder", "hook_main", "hook_content"],
                    "version": [0, 0, 0, 7, 4, 7],
                    "host_table": {"note": "host_note"}, "module": "modules/paint.c",
                    "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": "7917",
                               "note": "build-info block: build number 5706 -> 6009 (install marker)"}],
                    "description": "devkit-play plus an on-unit module picker (Shift + Sample Rec): pad 0 the "
                                   "built-in, pads 1..31 the 1:/MODULES/*.MOD files on the microSD card, read-only "
                                   "through the stock FatFs from the Main task, exclusive with the Content task's "
                                   "jobs; ITCM modules start only if loaded this boot."},
    "devkit-clock": {"src": "devkit_clock.c",
                     "hooks": ["hook_boot", "hook_frame", "hook_sysex", "hook_pad", "hook_button", "hook_pot",
                               "hook_pressure", "hook_encoder", "hook_main", "hook_content", "hook_pulse"],
                     "version": [0, 0, 0, 7, 4, 8], "abi": 2,
                     "host_table": {"note": "host_note"}, "module": "modules/paint.c",
                     "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": "7a17",
                                "note": "build-info block: build number 5706 -> 6010 (install marker)"}],
                     "description": "devkit-load plus a clock entry for modules ('CRM2' header): the sequencer's "
                                    "24-PPQN pulse (BL at 0x08033384, every pulse on which the tracks "
                                    "step, metronome on or off), Play/Stop as START/STOP events, the tempo; host "
                                    "table abi 2; SysEx 0B reports the clock state."},
    "devkit-abi3": {"src": "devkit_abi3.c",
                    "hooks": ["hook_boot", "hook_frame", "hook_sysex", "hook_pad", "hook_button", "hook_pot",
                              "hook_pressure", "hook_encoder", "hook_main", "hook_content", "hook_pulse"],
                    "version": [0, 0, 0, 7, 4, 9], "abi": 3,
                    "host_table": {"note": "host_note", "note_port": "host_note_port", "param": "host_param",
                                   "ui": "host_ui", "midi": "host_midi"},
                    "module": "modules/paint.c",
                    "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": "641b",
                               "note": "build-info block: build number 5706 -> 7012 (ARM-only base of 6012, "
                                       "not for flashing)"}],
                    "description": "devkit-clock plus module ABI 3 (host table abi 3, 32 bytes): host_note_port "
                                   "(router port t: engine, MIDI out, recorder), host_param (track "
                                   "parameter), host_ui (selected track and grid view), "
                                   "host_midi (channel voice messages to the stock MIDI outputs), all queued in "
                                   "the note ring and applied by the Main-task drain."},
    "devkit-tm": {"src": "devkit_tm.c",
                  "hooks": ["hook_boot", "hook_frame", "hook_sysex", "hook_pad", "hook_button", "hook_pot",
                            "hook_pressure", "hook_encoder", "hook_main", "hook_content", "hook_pulse",
                            "hook_apply_load", "hook_apply_clear", "hook_apply_pack"],
                  "version": [0, 0, 0, 7, 5, 0], "abi": 3, "heap_floor": "0x2004F440",
                  "host_table": {"note": "host_note", "note_port": "host_note_port", "param": "host_param",
                                 "ui": "host_ui", "midi": "host_midi"},
                  "module": "modules/paint.c",
                  "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": "651b",
                             "note": "build-info block: build number 5706 -> 7013 (ARM-only base of 6013, "
                                     "not for flashing)"}],
                  "description": "devkit-abi3 plus track modes T1 (persistence only, effective all Sample): the "
                                 "880-byte extension block validated after each project "
                                 "refresh into a 256-byte runtime copy below "
                                 "the clock block (heap limit 0x2004F440); SysEx 0C reads/sets stored modes, "
                                 "frozen while a project operation or Content job is pending."},
    "devkit-plock": {"src": "devkit_plock.c",
                     "hooks": ["hook_boot", "hook_frame", "hook_sysex", "hook_pad", "hook_button", "hook_pot",
                               "hook_pressure", "hook_encoder", "hook_main", "hook_content", "hook_pulse",
                               "hook_apply_load", "hook_apply_clear", "hook_apply_pack", "hook_lockplay",
                               "hook_save_ser", "hook_disp", "hook_macro", "hook_fx"],
                     "version": [0, 0, 0, 7, 5, 2], "abi": 3, "heap_floor": "0x2004F400",
                     "host_table": {"note": "host_note", "note_port": "host_note_port", "param": "host_param",
                                    "ui": "host_ui", "midi": "host_midi"},
                     "module": "modules/paint.c",
                     "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": "671b",
                                "note": "build-info block: build number 5706 -> 7015 (ARM-only base of 6015, "
                                        "not for flashing)"}],
                     "description": "devkit-tm plus the p-lock carry-over fix: after a locked step, the next step "
                                    "without a lock on that lane restores the user's base value, 14-bit macros "
                                    "with their LSB (all 8 tracks x 12 lanes; dispatcher, macro-page and FX-page "
                                    "vtable words, lock-player and save-serialiser BLs; state in the 6013 block's "
                                    "spare 104 bytes plus 64 bytes below it, heap limit 0x2004F400); knob turns on "
                                    "a held lane edit the base through the stock mapping; track-modes edits check "
                                    "BUSY first."},
    "devkit-a2": {"src": "devkit_a2.c",
                  "hooks": ["hook_boot", "hook_frame", "hook_sysex", "hook_pad", "hook_button", "hook_pot",
                            "hook_pressure", "hook_encoder", "hook_main", "hook_content", "hook_pulse",
                            "hook_apply_load", "hook_apply_clear", "hook_apply_pack", "hook_lockplay",
                            "hook_save_ser", "hook_disp", "hook_macro", "hook_fx"],
                  "version": [0, 0, 0, 7, 6, 0], "abi": 3, "heap_floor": "0x2004F3E0",
                  "host_table": {"note": "host_note", "note_port": "host_note_port", "param": "host_param",
                                 "ui": "host_ui", "midi": "host_midi"},
                  "module": "modules/paint.c",
                  "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": "be1b",
                             "note": "build-info block: build number 5706 -> 7102 (ARM dev build, emulation only, "
                                     "never flashed)"}],
                  "description": "devkit-plock (6015) plus the track-8 settings page (Shift + Track 8: notes per press, "
                                 "chord type, In key, scale and root, voicing, octave, MIDI poly, waveform, attack, "
                                 "decay, level; written through the T1 edit path with its BUSY gate; codec "
                                 "synth_settings.h) and SysEx cmd 08 restricted to the DSP meter's X windows (D3)."},
    "devkit-o1": {"src": "devkit_o1.c",
                  "hooks": ["hook_boot", "hook_frame", "hook_sysex", "hook_pad", "hook_button", "hook_pot",
                            "hook_pressure", "hook_encoder", "hook_main", "hook_content", "hook_pulse",
                            "hook_apply_load", "hook_apply_clear", "hook_apply_pack", "hook_mb_reset", "hook_mb_stop",
                            "hook_mb_tail", "hook_rdy_bulk0", "hook_rdy_bulk1", "hook_rdy_enter1", "hook_rdy_leave1",
                            "hook_rdy_tail0", "hook_pack_access", "hook_slot_upload", "hook_slot_job",
                            "hook_sx_upload", "hook_sx_read", "hook_encode", "hook_bulk_chunk", "hook_engine_on", "hook_fs_length", "hook_fs_alloc", "hook_fs_reset",
                            "hook_fs_bulk", "hook_fs_free", "hook_fs_start", "hook_fs_preview", "hook_fs_trim",
                            "hook_fs_stop", "hook_pack_done"],
                  "version": [0, 0, 0, 7, 7, 0], "abi": 3, "heap_floor": "0x2004F440",
                  "host_table": {"note": "host_note", "note_port": "host_note_port", "param": "host_param",
                                 "ui": "host_ui", "midi": "host_midi"},
                  "module": "modules/paint.c",
                  "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": "bf1b",
                             "note": "build-info block: build number 5706 -> 7103 (ARM-only dev build, "
                                     "not for flashing)"}],
                  "description": "devkit-tm (7013) plus O1 bounded DSP waits and fault quarantine: the mailbox "
                                 "poll loops of the DSP filesystem (pack reset 0x37300/0x372FB, capture start/stop "
                                 "0x372F9, trim commit 0x372FE) and the ready-GPIO spins of the bulk paths end after "
                                 "500 ms with a sticky fault (reboot clears); while faulted every filesystem vtable "
                                 "operation, the pack loader, slot uploads and the SysEx sample transfer are refused "
                                 "and fail at their stock failure paths; the Rec button blinks white; SysEx 0D "
                                 "reports."},
    # Arrhythmia 0.1: one root, patches/src/arrhythmia.c, profiles above (OR_PROFILES)
    **{name: or_target(name, **kw) for name, kw in OR_PROFILES.items()},
    "heapdiag": {"src": "heapdiag.c", "hooks": ["hook_sysex"], "version": [0, 0, 0, 7, 3, 5],
                 "description": "Heap diagnostic: SysEx F0 00 20 29 00 60 7E F7 replies with the bump heap "
                                "limit and cursor. Owns no RAM."},
    "flashdump": {"src": "flashdump.c", "hooks": ["hook_sysex"], "version": [0, 0, 0, 7, 3, 8],
                  "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": "0c27",
                             "note": "build-info block: build number 5706 -> 9996 (install marker)"}],
                  "description": "Diagnostics: heap query F0 00 20 29 00 60 7E F7 plus read-only internal-flash "
                                 "dump F0 00 20 29 00 60 7D <addr 5x7bit> F7 (32 bytes per reply). Owns no RAM."},
}
CFLAGS = ["--target=thumbv7em-none-eabi", "-mcpu=cortex-m7", "-mthumb", "-Os", "-ffreestanding", "-fno-builtin",
          "-nostdlib", "-fno-jump-tables", "-fno-pic", "-fno-unwind-tables", "-fno-asynchronous-unwind-tables"]


def bw(at, to):
    """Thumb-2 B.W (encoding T4) from address `at` to `to` (both even)."""
    b = bl(at, to)
    return b[:2] + (int.from_bytes(b[2:], "little") & ~0x4000).to_bytes(2, "little")


def bl(at, to):
    """Thumb-2 BL from address `at` to `to` (both even)."""
    off = to - (at + 4)
    assert -(1 << 24) <= off < (1 << 24) and off % 2 == 0
    s = (off >> 24) & 1
    i1, i2 = (off >> 23) & 1, (off >> 22) & 1
    j1, j2 = (1 - i1) ^ s, (1 - i2) ^ s
    hi = 0xF000 | (s << 10) | ((off >> 12) & 0x3FF)
    lo = 0xD000 | (j1 << 13) | (j2 << 11) | ((off >> 1) & 0x7FF)
    return hi.to_bytes(2, "little") + lo.to_bytes(2, "little")


def main(target):
    cfg = TARGETS[target]
    HOOKS = {h: SITES[h] for h in cfg["hooks"] if h in SITES}
    BWS = {h: BW_SITES[h] for h in cfg["hooks"] if h in BW_SITES}
    VTS = {h: VT_SITES[h] for h in cfg["hooks"] if h in VT_SITES}
    TAILS = {h: TAIL_SITES[h] for h in cfg["hooks"] if h in TAIL_SITES}
    table = cfg.get("host_table")
    extra = [k for k in HOST_EXTRA if table and k in table]
    table_size = HOST_TABLE_SIZE + 4 * len(extra)
    assert not table or set(table) == {"note", *extra}, table
    code_at = CODE_AT + table_size if table else CODE_AT        # with a host table, the code follows it
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    os.chdir(root)
    image = open("build/circuit-rhythm-5706.bin", "rb").read()
    with tempfile.TemporaryDirectory() as tmp:
        obj, text = os.path.join(tmp, "l.o"), os.path.join(tmp, "l.text")
        defs = [f"-D{d}" for d in cfg.get("defines", [])]
        subprocess.run(["clang", *CFLAGS, *defs, "-c", "patches/src/" + cfg["src"], "-o", obj], check=True)
        rel = subprocess.run(["llvm-readelf-21", "-r", obj], capture_output=True, text=True, check=True).stdout
        if ".rel.text" in rel:
            sys.exit("relocations in .text; the code must be self-contained:\n" + rel)
        syms = subprocess.run(["llvm-readelf-21", "-s", obj], capture_output=True, text=True, check=True).stdout
        subprocess.run(["llvm-objcopy-21", "-O", "binary", "-j", ".text", obj, text], check=True)
        code = open(text, "rb").read()
    entry, exports = {}, {}
    for line in syms.splitlines():
        f = line.split()
        if table and len(f) >= 8 and f[7] in table.values() and f[3] == "FUNC":
            exports[f[7]] = int(f[1], 16) & ~1
    for line in syms.splitlines():
        f = line.split()
        if len(f) >= 8 and f[7] in HOOKS and f[3] == "FUNC":
            entry[f[7]] = int(f[1], 16) & ~1
    for line in syms.splitlines():
        f = line.split()
        if len(f) >= 8 and f[7] in BWS and f[3] == "FUNC":
            entry[f[7]] = int(f[1], 16) & ~1
    for line in syms.splitlines():
        f = line.split()
        if len(f) >= 8 and f[7] in VTS | TAILS and f[3] == "FUNC":
            entry[f[7]] = int(f[1], 16) & ~1
    assert set(entry) == set(HOOKS) | set(BWS) | set(VTS) | set(TAILS), entry
    code += b"\xff" * (-len(code) % 4)
    blob = b""
    if table:
        assert set(exports) == set(table.values()), exports
        if cfg.get("module"):
            sys.path.insert(0, os.path.join(root, "tools"))
            import build_module
            out = "build/" + os.path.basename(cfg["module"])[:-2] + ".mod"
            blob = build_module.build(cfg["module"], out)
            build_module.check(blob)
        builtin = code_at + len(code) if blob else 0
        head = struct.pack("<4I", HOST_MAGIC, cfg.get("abi", HOST_ABI), code_at + exports[table["note"]] | 1, builtin)
        head += b"".join(struct.pack("<I", code_at + exports[table[k]] | 1) for k in extra)
        assert len(head) == table_size
        code = head + code + blob
    for at, to in ((SITES[h], STOCK_BL[SITES[h]]) for h in HOOKS):
        assert image[at - BASE:at - BASE + 4] == bl(at, to), f"stock BL at {at:#x} not as expected"
    note = f"hook code, {len(code)} bytes (patches/src/{cfg['src']})"
    if table:
        note = (f"host table ({table_size} bytes), hook code ({len(code) - table_size - len(blob)} bytes, "
                f"patches/src/{cfg['src']})" + (f", built-in module ({len(blob)} bytes, {cfg['module']})" if blob
                                                 else ""))
    assert CODE_AT + len(code) <= FREE_END, f"patch ends at {CODE_AT + len(code):#x}, beyond the free flash"
    patches = [{"addr": f"0x{CODE_AT:08X}", "expect": "ff" * len(code), "new": code.hex(), "note": note}]
    for name, at in HOOKS.items():
        patches.append({"addr": f"0x{at:08X}", "expect": bl(at, STOCK_BL[at]).hex(),
                        "new": bl(at, code_at + entry[name]).hex(), "note": f"BL -> {name}"})
    for name, at in BWS.items():
        assert image[at - BASE:at - BASE + 4] == bw(at, STOCK_BW[at]), f"stock B.W at {at:#x} not as expected"
        patches.append({"addr": f"0x{at:08X}", "expect": bw(at, STOCK_BW[at]).hex(),
                        "new": bw(at, code_at + entry[name]).hex(), "note": f"B.W -> {name}"})
    for name, (at, stock_word) in VTS.items():
        assert image[at - BASE:at - BASE + 4] == struct.pack("<I", stock_word), f"stock vtable word at {at:#x}"
        patches.append({"addr": f"0x{at:08X}", "expect": struct.pack("<I", stock_word).hex(),
                        "new": struct.pack("<I", (code_at + entry[name]) | 1).hex(), "note": f"vtable word -> {name}"})
    for name, (at, stock_hex) in TAILS.items():
        assert image[at - BASE:at - BASE + 4] == bytes.fromhex(stock_hex), f"stock tail at {at:#x} not as expected"
        patches.append({"addr": f"0x{at:08X}", "expect": stock_hex,
                        "new": bw(at, code_at + entry[name]).hex(), "note": f"tail B.W -> {name}"})
    patches += cfg.get("extra", [])
    spec = {
        "description": cfg["description"],
        "base_syx": "firmware/circuit-rhythm-5706.syx",
        "base_sha256": hashlib.sha256(image).hexdigest(),
        "version_nibbles": cfg["version"],
        "code_at": f"0x{code_at:08X}",
        "entries": {k: f"0x{code_at + v:08X}" for k, v in entry.items()},
        "patches": patches,
    }
    if cfg.get("profile"):
        spec["profile"] = cfg["profile"]
    if cfg.get("heap_floor"):
        spec["heap_floor"] = cfg["heap_floor"]        # heap limit after hook_boot (tests read it)
    if table:
        spec["host_table"] = f"0x{CODE_AT:08X}"
        spec["exports"] = {k: f"0x{code_at + v:08X}" for k, v in exports.items()}
        if blob:
            spec["builtin_module"] = {"addr": f"0x{builtin:08X}", "size": len(blob), "src": cfg["module"]}
    json.dump(spec, open(f"patches/{target}.json", "w"), indent=2)
    print(f"code {len(code)} bytes at 0x{CODE_AT:08X}; entries " +
          ", ".join(f"{k}=0x{code_at + v:08X}" for k, v in entry.items()))
    if table:
        print(f"patch area 0x{CODE_AT:08X}-0x{CODE_AT + len(code):08X}: {len(code)} of {FREE_END - CODE_AT} free "
              f"bytes used" + (f"; built-in module at 0x{builtin:08X} ({len(blob)} bytes)" if blob else ""))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "lightshow-glitter")
