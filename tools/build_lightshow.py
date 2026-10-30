"""Compile a hook patch (C in patches/src/) and write its patch set JSON.

    uv run python -I tools/build_lightshow.py [lightshow-glitter|heapdiag|flashdump|devkit|devkit-dsp|devkit-input|devkit-mode|devkit-play|devkit-load|devkit-clock|devkit-abi3|devkit-tm|devkit-plock|devkit-a2|devkit-o1|arrhythmia-devkit-arm|arrhythmia-firmware-arm|...]

--spec-dir DIR writes the patch set to DIR/<target>.json instead of patches/ (tests use a folder under build/).

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
# The far blob (targets with "far", tools/farblob.py): in the old core-0 window, from the core-1 cap (Codex, #9) to
# the end of the old stock core-0 stream. Only joined images (core 0 moved, pointer 0x08031658 rewritten) may run it.
FAR_AT, FAR_END = 0x0801B000, 0x08020805
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
         "hook_dsp_load0": 0x080278CC, "hook_dsp_load1": 0x080278A8,
         # FM_BANKVIEW: the stock store reset inside the project operations that create a project (fresh creation):
         # an empty slot selected, a loaded file that is not a project, clear, a pack switch without a file (notetap.c)
         "hook_rst_absent": 0x08037C8A, "hook_rst_tail": 0x08037E74, "hook_rst_clear": 0x0803868A,
         "hook_rst_pack": 0x08038742,
         # STEP_CHORDS (0.2.1): the Note view's step entry (a key pressed with a step held)
         "hook_se": 0x0805D838,
         # STEP_CHORDS (0.2.1): the recorder's note-off step write (0x08048E70), its three call sites
         "hook_rec_off0": 0x08048CB2, "hook_rec_off1": 0x08048D52, "hook_rec_off2": 0x08048E52}
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
            0x080278CC: 0x08031110, 0x080278A8: 0x08031110,
            0x08037C8A: 0x08032DB2, 0x08037E74: 0x08032DB2, 0x0803868A: 0x08032DB2, 0x08038742: 0x08032DB2,
            0x0805D838: 0x0805DF1A, 0x08048CB2: 0x08048E70, 0x08048D52: 0x08048E70, 0x08048E52: 0x08048E70}
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
            # FM_BANKVIEW (7304): the Sample view's release and observer entry (track 8's family pages)
            "hook_sv_rel": (0x08068298, 0x0805C2BF), "hook_sv_obs": (0x080682C0, 0x0805C405),
            # O1 quarantine: operation boundaries
            "hook_fs_length": (0x08066E5C, 0x0806438D),
            "hook_fs_alloc": (0x08066E64, 0x080643F5), "hook_fs_reset": (0x08066E68, 0x08064417),
            "hook_fs_bulk": (0x08066E6C, 0x0806444B), "hook_fs_free": (0x08066E78, 0x08064461),
            "hook_fs_start": (0x08066E80, 0x0806446F), "hook_fs_preview": (0x08066E84, 0x080644E5),
            "hook_fs_trim": (0x08066E88, 0x08064513), "hook_fs_stop": (0x08066E94, 0x08064481),
            # scales
            "hook_kb_obs": (0x080696F4, 0x08055F69),
            # STEP_CHORDS (0.2.1): the step player's microstep entry (vtable 0x08068D24, shared by the 8 players)
            "hook_seqstep": (0x08068D2C, 0x0804AFBD),
            # STEP_CHORDS (0.2.1): the recorder's note-on (vtable 0x08067864, shared by the 8 recorders)
            "hook_rec_on": (0x08067870, 0x08048B79),
            # STEP_CHORDS: the recorder's note-off (same vtable) and the Note view's pad press (vtable 0x08067248)
            "hook_rec_rel": (0x08067874, 0x08048C59), "hook_np": (0x08067248, 0x0805D7A1),
            # STEP_CHORDS: the Note view's observer, both vtable words (the view; its observer interface at + 8)
            "hook_nv_obs": (0x08067268, 0x0805E08F), "hook_nv_obs8": (0x08067278, 0x0805E08D),
            # BOOT_PULSE: the pack-load progress object's vtable (0x08068E70) +8 bar draw and +0x10 end
            "hook_pb_draw": (0x08068E78, 0x08054147), "hook_pb_end": (0x08068E80, 0x080541AB)}
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
OR_BANKVIEW_HOOKS = ["hook_sv_rel", "hook_sv_obs", "hook_rst_absent", "hook_rst_tail", "hook_rst_clear", "hook_rst_pack"]
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
                                    dsp="Codex firmware-profile streams layout with the "
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
    # 0.2 PROTOTYPE (emulator only, never an image, never flashed): the AD profile with the track-8 synth fed to the DSP's
    # provisional core-0 queue (CORE0_SYNTH=1, patches/src/core0feed.c); ARM-only label 7280
    "arrhythmia-core0-proto": dict(synth=1, cmd08=0, scales=1, ad=1, core0=1, label=7280, version=[0, 0, 0, 7, 9, 0]),
    # the same plus the live tone producer (CORE0_TONE=1: the proposed A08000 packet, header 000204); a
    # separate profile so that the 000202 feed stays reviewable on its own; ARM-only label 7281
    "arrhythmia-core0-tone": dict(synth=1, cmd08=0, scales=1, ad=1, core0=2, label=7281, version=[0, 0, 0, 7, 9, 0]),
    # the tone profile admitting the key-scaled instrument header instead (VERSION 000205; the
    # same A08000 packet, no ARM change but the accepted header); ARM-only label 7282
    "arrhythmia-core0-keyscale": dict(synth=1, cmd08=0, scales=1, ad=1, core0=3, label=7282,
                                      version=[0, 0, 0, 7, 9, 0]),
    # PRIVATE bench ARM base (never public): the 7282 feed plus CORE0_BULK_EXCLUDE (any bulk interval closes the synth
    # path until a DSP load); the private bench build adds the DEV_NOPLAY gate; intermediate label 7283, image
    # label 7284
    "arrhythmia-core0-bench-arm": dict(synth=1, cmd08=0, scales=1, ad=1, core0=4, label=7283,
                                       version=[0, 0, 0, 7, 9, 0]),
    # 0.2, core 0 the only synth (master 2026-10-21; the core-1 retirement plan): the 7283 feed plus
    # CORE0_ONLY (the core-1 legacy voice retired on the ARM: no ENABLE X:2E70 write, no core-1 frame from our code);
    # emulator only, never flashed from here; the private bench build adds the DEV_NOPLAY gate; label 7286
    "arrhythmia-core0-only-arm": dict(synth=1, cmd08=0, scales=1, ad=1, core0=5, label=7286,
                                      version=[0, 0, 0, 7, 9, 0]),
    # the same profile plus PAD_POLY=1 (pads poly with POLY on, reviewed) under a new label; every
    # other profile keeps pads on the lane, so 7286 stays bound to the bytes of image 7287; the private bench build adds the gate;
    # image label 7289
    "arrhythmia-core0-padchord-arm": dict(synth=1, cmd08=0, scales=1, ad=1, core0=5, label=7288, pad_poly=1,
                                          version=[0, 0, 0, 7, 9, 0]),
    # the 7288 configuration plus FM_PATCH=1 (master 2026-10-21: per-note FM settings, algorithm / ratio bank /
    # feedback / modulator AD-to-floor, latched ahead of the ONSET in the envelope group) and
    # the flash trims C1-C3 (TRIM_RING, TRIM_HIDE, TRIM_LEDS), all only here; header VERSION 000206 (proposed,
    # not frozen); ARM-only label 7302 (7290-7299 belong to the timing branch, 7300 / 7301 are synthetic flash-test
    # builds); EMULATION ONLY, no DSP stream for 000206 exists, never an image
    "arrhythmia-core0-fmpatch-arm": dict(synth=1, cmd08=0, scales=1, ad=1, core0=5, label=7302, pad_poly=1, fm_patch=1,
                                         version=[0, 0, 0, 7, 9, 0]),
    # the 7302 configuration plus FM_BANKVIEW=1 (master 2026-10-21, option A, release 0.2,
    # with master's ruling): the FM ratio family and algorithm are track
    # 8's sample slot (Sample view pages 0 serial4 and 1 dual_stack), codec 2 version 2, new projects slot 0 + FM depth
    # 96, no FM view (macros 1..4 on the single synth page view); the single define implies PAD_POLY, FM_PATCH and trims
    # C1-C3 (arrhythmia.c), so those stay on 7288 / 7302
    # in the tables; ARM-only label 7304; EMULATION ONLY, never an image from here
    # FM_207 (fm207=1): header VERSION 000207 and the 207 R127 release reference, on 7304 and 7306's ARM only
    "arrhythmia-core0-bankview-arm": dict(synth=1, cmd08=0, scales=1, ad=1, core0=5, label=7304, bankview=1, fm207=1,
                                          version=[0, 0, 0, 7, 9, 0]),
    # Public 0.2 firmware ARM profile, paired with the VERSION 207 DSP source in tools/dspvoice207/.
    # The ARM base label stays 7304; the full-image builder selects the release label.
    # Reproduce the earlier VERSION 206 public image with its original Clear + macro 6 behavior.
    "arrhythmia-firmware-arm-fm206": dict(synth=1, cmd08=0, scales=1, ad=1, core0=5, label=7304, bankview=1,
                                          legacy_fm206=1, version=[0, 0, 0, 7, 9, 0],
                                          image=("arrhythmia-firmware", 7306)),
    "arrhythmia-firmware-arm-fm": dict(synth=1, cmd08=0, scales=1, ad=1, core0=5, label=7304, bankview=1, fm207=1,
                                       version=[0, 0, 0, 7, 9, 0], image=("arrhythmia-firmware", 7308)),
    # the timing bench's ARM: 7304 plus BENCH_METER_READ=1 (meter_read=1): cmd 08 is the restricted core-0 meter
    # readout; BENCH ONLY, ARM-only label 7305, never a public or release build; EMULATION ONLY, never an image from
    # here. FM_207 as 7304 (fm207=1), so 7305 stays exactly 7304 + the readout.
    "arrhythmia-core0-bankview-meter-arm": dict(synth=1, cmd08=0, scales=1, ad=1, core0=5, label=7305, bankview=1,
                                                fm207=1, meter_read=1, version=[0, 0, 0, 7, 9, 0]),
    # 0.2.1 candidate (not released): the two FM_207 profiles above plus CORE0_RECOVER=1 (recover=1; runtime
    # content-transfer recovery, patches/src/core0feed.c). New profiles, so that
    # 7304 and arrhythmia-firmware-arm-fm keep rebuilding the pinned 0.2 bytes; the same ARM-only base label 7304 and
    # version nibbles; no image and no (label, version) entry (that is added only when an image is pinned);
    # EMULATION ONLY, never an image from here. The public source builder joins the recovery ARM with the supported DSP stream.
    "arrhythmia-core0-bankview-arm-recover": dict(synth=1, cmd08=0, scales=1, ad=1, core0=5, label=7304, bankview=1,
                                                  fm207=1, recover=1, version=[0, 0, 0, 7, 9, 0]),
    "arrhythmia-firmware-arm-fm-recover": dict(synth=1, cmd08=0, scales=1, ad=1, core0=5, label=7304, bankview=1,
                                               fm207=1, recover=1, version=[0, 0, 0, 7, 9, 0],
                                               image=("arrhythmia-firmware", 7316)),
    # Earlier public recovery profile naming image arrhythmia-firmware 7319, kept so that its pin still rebuilds;
    # the ARM bytes are the same. EMULATION ONLY, never an image from here.
    "arrhythmia-firmware-arm-fm-recover-m7": dict(synth=1, cmd08=0, scales=1, ad=1, core0=5, label=7304, bankview=1,
                                                  fm207=1, recover=1, version=[0, 0, 0, 7, 9, 0],
                                                  image=("arrhythmia-firmware", 7319)),
    # Release 0.2 public ARM profile: the same recovery defines, naming image arrhythmia-firmware 7323. Its own
    # profile so that the earlier profiles keep their pins; the ARM bytes are the same. EMULATION ONLY, never an
    # image from here.
    "arrhythmia-firmware-arm-fm-recover-x4": dict(synth=1, cmd08=0, scales=1, ad=1, core0=5, label=7304, bankview=1,
                                                  fm207=1, recover=1, version=[0, 0, 0, 7, 9, 0],
                                                  image=("arrhythmia-firmware", 7323)),
    # 0.2.1 development line (not released): the 7323 / 7324 ARM profiles with BUILTIN_MODULE=0 (builtin=0, #4) and
    # FB_MACRO1=1 (fb_macro1=1, #2: main macro 1 = feedback, synth page macro 1 = Tune): no
    # built-in module in flash (host table word 3 is 0); paint ships as a card file (build/paint.mod from
    # tools/build_module.py) and the picker shows card files from pad 0. New profiles, so that the 0.2 profiles keep
    # their pins; ARM-only base label 7304. The public profile names image arrhythmia-firmware 7329; the gated twin
    # (7330) gets the DEV_NOPLAY gate from the private bench profile tool (--dev021). EMULATION ONLY, never an image
    # from here.
    "arrhythmia-firmware-arm-021": dict(synth=1, cmd08=0, scales=1, ad=1, core0=5, label=7304, bankview=1, fm207=1,
                                        recover=1, builtin=0, fb_macro1=1, step_chords=1, boot_pulse=1,
                                        version=[0, 0, 0, 7, 9, 0], image=("arrhythmia-firmware", 7329)),
    "arrhythmia-core0-bankview-arm-021": dict(synth=1, cmd08=0, scales=1, ad=1, core0=5, label=7304, bankview=1,
                                              fm207=1, recover=1, builtin=0, fb_macro1=1, step_chords=1,
                                              boot_pulse=1, version=[0, 0, 0, 7, 9, 0]),
    # OPTIONAL (separate commit, may be dropped): the recover profile plus CORE0_RECOVER_TIMING=1 (timing=1): ACK
    # latency and reinit duration counters, c_init_late (struct q0 block 0xD0, 0F reply 185 bytes); the rig
    # only; excluded from the public candidate source build.
    "arrhythmia-core0-bankview-arm-recover-timing": dict(synth=1, cmd08=0, scales=1, ad=1, core0=5, label=7304,
                                                         bankview=1, fm207=1, recover=1, timing=1,
                                                         version=[0, 0, 0, 7, 9, 0]),
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
                                             dsp="Codex firmware-profile streams "
                                                 "(tools/voicestack.py): core 1 voice decoder, "
                                                 "ENABLE 0 at boot, core 0 stock relocated to 0x0807A020, no meter"),
}


# BOOT_PULSE (0.2.1, #5): the pack-load progress object's (0x20000A8C) bar draw (+8) and end (+0x10), vtable 0x08068E70
BP_HOOKS = ["hook_pb_draw", "hook_pb_end"]
FAR_HOOKS = ["hook_se", "hook_seqstep", "hook_rec_on", "hook_rec_off0", "hook_rec_off1", "hook_rec_off2",
             "hook_rec_rel", "hook_np", "hook_nv_obs", "hook_nv_obs8"]


def or_target(name, synth, cmd08, label, version, image=None, a1_patch=0, dsp="none", scales=0, ad_blocks=None,
              dump_open=0, ad=0, core0=0, pad_poly=0, fm_patch=0, bankview=0, meter_read=0, fm207=0, legacy_fm206=0,
              recover=0, timing=0, builtin=1, fb_macro1=0, step_chords=0, boot_pulse=0):
    q0 = {0: 0, 1: 0x80, 2: 0xA0, 3: 0xA0, 4: 0xA0, 5: 0xA0}[core0]   # struct q0 block (devkit_abi3.c Q0_SIZE)
    fm = fm_patch or bankview                                          # FM_BANKVIEW implies FM_PATCH (arrhythmia.c)
    assert not legacy_fm206 or (bankview and not fm207), name
    assert not fm207 or bankview, name                                 # FM_207 builds on FM_BANKVIEW (arrhythmia.c)
    assert not recover or fm207, name                                  # CORE0_RECOVER builds on FM_207 (arrhythmia.c)
    assert not timing or recover, name                                 # CORE0_RECOVER_TIMING needs CORE0_RECOVER
    if fm:
        q0 = 0xC0                                                     # + the FM latch mirrors (core0feed.h)
    if timing:
        q0 = 0xD0                                                     # + the optional timing counters (core0feed.h)
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
    if core0:
        parts.append("PROTOTYPE, emulator only: track 8's lifecycle (ONSET with the A/D/S/R envelope group and the M1 Tune "
                     "snapshot, KILL, generation-matched RELEASE, ALL OFF) goes to the provisional core-0 queue X:4D00..4D4F "
                     f"after the ready header (VERSION {'000207' if fm207 else '000206' if fm else ({2: '000204', 3: '000205', 4: '000205', 5: '000205'}).get(core0, '000202')}) and an "
                     "epoch handshake, "
                     "batched under the bus semaphore; "
                     + ("live tone (M6 FM depth, M7 cutoff, M8 resonance: Codex's proposed A08000 packet), "
                        if core0 >= 2 else "")
                     + f"no class-A word on the stock ring; struct q0 {q0:#x} B below struct a1 (heap limit {q0:#x} B "
                       "lower)"
                     + ("; core 0 is the only synth: the core-1 legacy voice is retired (no ENABLE X:2E70 write, no "
                        "core-1 frame)" if core0 == 5 else ""))
    if fm_patch:
        parts.append("FM patch (FM_PATCH, emulation only): algorithm, ratio bank, feedback and the modulator AD-to-floor "
                     "envelope from patch slot 7 codec 2, latched ahead of the ONSET in the envelope group (PATCH_FM "
                     "A09000, MOD_AD A0A000, sent only when changed; a barrier sends all), the synth page's FM view "
                     "(Up / Down); flash trims C1-C3")
    if bankview:
        parts.append("FM patch (FM_PATCH, emulation only) as 7302 with the ratio family and algorithm read from track 8's "
                     "sample slot (channel parameter 0, slots 0..31: family slot & 15, algorithm slot >> 4) at the "
                     "press: track 8's Sample view selects them (page 0 serial4, page 1 dual_stack; pages 2..7 dark and "
                     "inert, the release of an inert press swallowed; the view opens on the page of the selected base), "
                     "codec 2 version 2 (bytes +2 and +3 reserved; version 1 converted once at load or boot), new "
                     "projects slot 0 and FM depth 96, no FM view (macros 1..4 on the single synth page view) "
                     "(FM_BANKVIEW, which implies PAD_POLY, FM_PATCH and the flash trims C1-C3)")
    if recover:
        parts.append("runtime content-transfer recovery (CORE0_RECOVER, 0.2.1 candidate, emulation only): a runtime bulk "
                     "whose pre-transfer epoch is ACKed with FAULT 0 is followed by the own DSP reinit through READY "
                     "(READY=0 once, INIT_COUNT + 1 confirmed on two consecutive reads), a new epoch and the barrier; "
                     "every uncertain outcome excludes until a DSP load; transfers from another task, a transfer after "
                     "READY=0 and allocator pages outside 0x300000..0xD86000 exclude; 0F reply "
                     + ("185 bytes with the OPTIONAL timing counters t_ack_max, t_init_max, t_init_last (ticks, "
                        "0xFFFE saturated, 0xFFFF incomplete) and c_init_late (CORE0_RECOVER_TIMING; struct q0 block "
                        "0xD0)" if timing else "173 bytes"))
    if not builtin:
        parts.append("no built-in module (BUILTIN_MODULE=0, 0.2.1): host table word 3 is 0; paint ships as a card "
                     "file; numbered picker pads (NNname.MOD on pad NN, 01..32; the rest on the free pads), name-hash pad "
                     "colours, Play always to the transport, Play LED blink while a clocked module waits (PICK_V2)")
    if step_chords:
        parts.append("track 8 step chords (STEP_CHORDS, 0.2.1): byte 6 key into the project's 31-shape table at T1 "
                     "block + 0x298; Note view step entry adds / removes notes; one shared gate")
    assert not boot_pulse or step_chords, name                        # the pulse line lives in the far blob
    if boot_pulse:
        parts.append("startup pulse line (BOOT_PULSE, 0.2.1, #5): during the boot pack load the pads show the ECG "
                     "pulse line over the load progress (loaded pads dim green); stock otherwise")
    if fb_macro1:
        parts.append("track 8 main macro 1 is FM feedback at note start (FB_MACRO1, 0.2.1: channel parameter 6, "
                     "stored 4..124, read as 0..127); the synth page's macro 1 is Tune (patch slot +4, codec 2 "
                     "version 3, default 64); older slots convert once; new projects and Clear + macro 1: feedback 0")
    if meter_read:
        parts.append("BENCH ONLY: cmd 08 is the restricted core-0 meter readout (X:$5C00..$5E5F, 1..16 words, a 14-bit "
                     "nonce, the 0F idle rule and a deadline before every DSP word, no write; the general cmd 08 read is "
                     "compiled out; BENCH_METER_READ)")
    if not dump_open:
        parts.append("cmd 7D reads only ITCM 0x00000100-0x00003FFF (module RAM and code); every other address, "
                     "internal flash included, gets the refusal (13 bytes, no data)")
    floor = (0x2004F0E0 if synth else 0x2004F400) - (0x20 if scales else 0) - q0
    return {"src": "arrhythmia.c", "hooks": OR_HOOKS + (OR_A1_HOOKS if synth else []) + (OR_SCALES_HOOKS if scales else [])
                      + (OR_AD_HOOKS if ad else []) + (OR_BANKVIEW_HOOKS if bankview else [])
                      + (FAR_HOOKS if step_chords else []) + (BP_HOOKS if boot_pulse else []),
            "far": dict(roots=FAR_HOOKS + (BP_HOOKS if boot_pulse else []),
                        table=["far_chord", "far_col"] + (["far_pulse"] if boot_pulse else [])) if step_chords else None,
            "defines": [f"SYNTH={synth}", f"CMD08={cmd08}"] + (["A1_PATCH=1"] if a1_patch else [])
                       + (["SCALES=1"] if scales else []) + ([f"AD_BLOCKS={ad_blocks}u"] if ad_blocks else [])
                       + (["DUMP_ALLOW=0"] if dump_open else []) + (["AD_LIVE=1"] if ad else [])
                       + (["CORE0_SYNTH=1"] if core0 else []) + (["CORE0_TONE=1"] if core0 >= 2 else [])
                       + (["CORE0_KEYSCALE=1"] if core0 >= 3 else []) + (["CORE0_BULK_EXCLUDE=1", "CORE0_BENCH=1"] if core0 >= 4 else [])
                       + (["CORE0_ONLY=1"] if core0 == 5 else []) + (["PAD_POLY=1"] if pad_poly else [])
                       + (["FM_PATCH=1", "TRIM_RING=1", "TRIM_HIDE=1", "TRIM_LEDS=1"] if fm_patch else [])
                       + (["FM_BANKVIEW=1"] if bankview else []) + (["BENCH_METER_READ=1"] if meter_read else [])
                       + (["FM_207=1"] if fm207 else []) + (["CORE0_RECOVER=1"] if recover else [])
                       + (["CORE0_RECOVER_TIMING=1"] if timing else [])
                       + (["LEGACY_FM206_ARM=1"] if legacy_fm206 else [])
                       + ([] if builtin else ["BUILTIN_MODULE=0"])
                       + (["FB_MACRO1=1"] if fb_macro1 else [])
                       + (["STEP_CHORDS=1"] if step_chords else []) + (["BOOT_PULSE=1"] if boot_pulse else []),
            "profile": dict({"synth": synth, "cmd08": "meter windows" if cmd08 else "refused", "a1_patch": a1_patch,
                             "image": f"{image[0]} {image[1]}" if image else "none (emulation only)",
                             "dsp_layer": dsp}, **({"scales": 1} if scales else {}),
                            **({} if dump_open else {"cmd7d": "ITCM 0x00000100-0x00003FFF only"}),
                            **({"ad": "PATCH_AD"} if ad else {}),
                            **({"core0": "core-0 queue feed (prototype)"} if core0 else {}),
                            **({"core0_bulk": "recovered: own reinit and a new epoch after a silent runtime bulk, "
                                              "otherwise excluded until a DSP load (CORE0_RECOVER)" if recover else
                                              "excluded: closed after any bulk interval until a DSP load",
                                "core0_bench": "pacing 4 entries / 1 hold per Main pass; silence before bulk; "
                                               "startup bulk before OPEN restarts admission; SysEx 0F telemetry"}
                               if core0 >= 4 else {}),
                            **({"core0_only": "core 0 only: core-1 legacy voice retired (no ENABLE, no core-1 frame)"}
                               if core0 == 5 else {}),
                            **({"core0_tone": "A08000 live tone (proposed), VERSION "
                                              + ("000207 (FM patch latches, frozen; FM_207)" if fm207 else
                                                 "000206 (FM patch latches, proposed, not frozen)" if fm else
                                                 "000205 (key-scaled instrument)" if core0 >= 3 else "000204")}
                               if core0 >= 2 else {}),
                            **({"fm_patch": "PATCH_FM A09000 + MOD_AD A0A000 latched in the envelope group, patch slot "
                                            "7 codec 2, page FM view; trims C1-C3 (TRIM_RING, TRIM_HIDE, TRIM_LEDS)"}
                               if fm_patch else {}),
                            **({"fm_patch": "PATCH_FM A09000 + MOD_AD A0A000 latched in the envelope group, patch slot "
                                            "7 codec 2 version 2, macros 1..4 on the single synth page view (no FM "
                                            "view); trims C1-C3 (implied by FM_BANKVIEW)",
                                "bankview": "family and algorithm = track 8's sample slot 0..31 (channel parameter 0), "
                                            "Sample view pages 0 (serial4) and 1 (dual_stack) select them; codec 2 "
                                            "version 2; label 7304"}
                               if bankview else {}),
                            **({"core0_timing": "OPTIONAL: t_ack_max, t_init_max, t_init_last (ticks; 0xFFFE "
                                                "saturated, 0xFFFF incomplete) and c_init_late in the 0F tail"}
                               if timing else {}),
                            **({"meter_read": "bench only: cmd 08 = restricted core-0 meter readout X:$5C00..$5E5F"} if meter_read else {}),
                            **({} if builtin else {"modules": "card only: no built-in module (host table word 3 = 0); "
                                                              "numbered picker pads: NNname.MOD on pad NN - 1 (01..32), "
                                                              "the other files on the free pads"}),
                            **({"macro1": "track 8 main macro 1 = FM feedback (parameter 6, 4..124 -> 0..127); "
                                          "synth page macro 1 = Tune (patch slot +4, codec 2 version 3)"}
                               if fb_macro1 else {})),
            "version": version, "abi": 3, "heap_floor": f"0x{floor:08X}",
            "host_table": {"note": "host_note", "note_port": "host_note_port", "param": "host_param",
                           "ui": "host_ui", "midi": "host_midi"},
            **({"module": "modules/paint.c"} if builtin else {}),
            **({"probes": ["vc_tune_pitch"]} if core0 else {}),
            "extra": [{"addr": "0x080101CA", "expect": "4a16", "new": struct.pack("<H", label).hex(),
                       "note": f"build-info block: build number 5706 -> {label} (Arrhythmia 0.1 ARM-only base"
                               + (f" of {image[0]} {image[1]}" if image else "") + "; emulation only, never flashed)"}]
                     # FB_MACRO1 (unit session 021): track 8's main macros 1 (feedback) and 4 (sustain) are levels,
                     # unipolar. The stock knob light shows colours 0x121 (track 8 knob 1) and 0x124 (knob 4)
                     # centred; both compares are pointed at track 7's centred knobs 0x119 / 0x11C, which the
                     # compares just before already test, so only track 8's knobs 1 and 4 change (Tune stays centred
                     # on the synth page, our own frame)
                     + ([{"addr": "0x0803E9F4", "expect": "21", "new": "19",
                          "note": "knob light: track 8 knob 1 (colour 0x121) unipolar (movw r2, #0x121 -> #0x119)"},
                         {"addr": "0x0803E9FC", "expect": "92", "new": "8e",
                          "note": "knob light: track 8 knob 4 (colour 0x124) unipolar (cmp.w r6, #0x124 -> #0x11c)"}]
                        if fb_macro1 else []),
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


def main(target, spec_dir="patches"):
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
    far, far_abs = cfg.get("far"), {}
    with tempfile.TemporaryDirectory() as tmp:
        obj, text = os.path.join(tmp, "l.o"), os.path.join(tmp, "l.text")
        defs = [f"-D{d}" for d in cfg.get("defines", [])]
        if far:                               # pass 1: the far blob; the main blob is compiled with its key
            sys.path.insert(0, os.path.join(root, "tools"))
            import farblob
            fobj = os.path.join(tmp, "far.o")
            subprocess.run(["clang", *CFLAGS, *defs, "-DFAR_PART=1", "-ffunction-sections", "-fdata-sections", "-c",
                            "patches/src/" + cfg["src"], "-o", fobj], check=True)
            far_blob, far_abs, far_key = farblob.link(open(fobj, "rb").read(), far["roots"], far["table"], FAR_AT)
            assert FAR_AT + len(far_blob) <= FAR_END, f"far blob ends {FAR_AT + len(far_blob):#x}, past {FAR_END:#x}"
            defs.append(f"-DFAR_KEY=0x{far_key:08X}u")
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
    for k in far["roots"] if far else ():      # far entries, as offsets from code_at like the others
        assert k not in entry, f"{k} is in both blobs"
        entry[k] = far_abs[k] - code_at
    assert set(entry) == set(HOOKS) | set(BWS) | set(VTS) | set(TAILS), entry
    probes = {}                               # emulator test entry points (internal functions; only profiles that ask)
    for line in syms.splitlines():
        f = line.split()
        if len(f) >= 8 and f[7] in cfg.get("probes", ()) and f[3] == "FUNC":
            probes[f[7]] = int(f[1], 16) & ~1
    assert set(probes) == set(cfg.get("probes", ())), probes
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
    if far:
        patches.append({"addr": f"0x{FAR_AT:08X}", "expect": image[FAR_AT - BASE:FAR_AT - BASE + len(far_blob)].hex(),
                        "new": far_blob.hex(), "note": f"far blob, {len(far_blob)} bytes (old core-0 window; key "
                        f"0x{far_key:08X}; joined images only)"})
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
        spec["heap_floor"] = cfg["heap_floor"]
    if far:
        spec["far"] = {"addr": f"0x{FAR_AT:08X}", "end": f"0x{FAR_AT + len(far_blob):08X}", "limit": f"0x{FAR_END:08X}",
                       "key": f"0x{far_key:08X}", "sha256": hashlib.sha256(far_blob).hexdigest(),
                       "main_sha256": hashlib.sha256(code).hexdigest(),
                       "entries": {k: f"0x{v:08X}" for k, v in far_abs.items()}}
    if probes:
        spec["probes"] = {k: f"0x{code_at + v:08X}" for k, v in probes.items()}        # heap limit after hook_boot (tests read it)
    if table:
        spec["host_table"] = f"0x{CODE_AT:08X}"
        spec["exports"] = {k: f"0x{code_at + v:08X}" for k, v in exports.items()}
        if blob:
            spec["builtin_module"] = {"addr": f"0x{builtin:08X}", "size": len(blob), "src": cfg["module"]}
    json.dump(spec, open(os.path.join(spec_dir, f"{target}.json"), "w"), indent=2)   # --spec-dir: tests write under build/
    print(f"code {len(code)} bytes at 0x{CODE_AT:08X}; entries " +
          ", ".join(f"{k}=0x{code_at + v:08X}" for k, v in entry.items()))
    if table:
        print(f"patch area 0x{CODE_AT:08X}-0x{CODE_AT + len(code):08X}: {len(code)} of {FREE_END - CODE_AT} free "
              f"bytes used" + (f"; built-in module at 0x{builtin:08X} ({len(blob)} bytes)" if blob else ""))
    if far:
        print(f"far blob 0x{FAR_AT:08X}-0x{FAR_AT + len(far_blob):08X}: {len(far_blob)} of {FAR_END - FAR_AT} bytes")


if __name__ == "__main__":
    av = sys.argv[1:]
    sd = av[av.index("--spec-dir") + 1] if "--spec-dir" in av else "patches"   # where the spec goes (default: patches/)
    av = [a for i, a in enumerate(av) if a != "--spec-dir" and (i == 0 or av[i - 1] != "--spec-dir")]
    os.makedirs(sd, exist_ok=True)
    main(av[0] if av else "lightshow-glitter", sd)
