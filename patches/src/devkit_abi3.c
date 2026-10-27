/* Arrhythmia devkit core: LED control, the input log, ITCM effect modules (module SDK, host table abi 3), the module
 * clock, the microSD module picker and the SysEx command set below. Developer documentation: docs/user/developers.md.
 *
 * Built position-independent with no relocations; firmware functions are called through absolute addresses. State
 * lives in the 512-byte region taken from the top of the heap once at boot (hook_boot). Every mutation of shared state
 * runs with the scheduler suspended; the firmware's encoder and SysEx transmitter are always called outside it.
 *
 * Host table (module ABI, fixed at 0x0806A570 = the start of the patch area; tools/build_lightshow.py writes it):
 *   +0x00 u32 magic 'CRH1' (0x31485243)      check it first: other builds have code or erased flash here
 *   +0x04 u32 abi (3)                        abi >= 2: the host runs 'CRM2' clock entries; abi 3: the calls below
 *   +0x08 host_note(track, note, velocity)   track 0..7, note in engine numbering (60 = unshifted sample, at most 115),
 *                                            velocity 1..127 = note on (larger values are clamped), 0 = note off.
 *                                            Returns 1 if queued, 0 if refused or dropped. Engine only (no MIDI out,
 *                                            no recording). A note on is refused when fewer than Q_OFF_RESERVE + 1
 *                                            slots are free, so note offs keep room; a note off that still does not
 *                                            fit raises a release of the track, ordered after its queued notes.
 *   +0x0C u32 builtin                        address of the built-in module blob in flash (0 = none)
 *   +0x10 host_note_port(t, note, vel, rec)  rec != 0: the front-panel pad path (engine, MIDI out, recorder while Rec
 *                                            is armed and the transport runs); rec == 0: exactly host_note.
 *   +0x14 host_param(t, idx, val)            t 0..7, idx 0..24 (channel parameters), val 0..127; clamped to the
 *                                            parameter's range by the firmware.
 *   +0x18 host_ui()                          track | view << 8 | front << 16 | 1 << 31, 0 = unknown: a snapshot taken
 *                                            every Main pass.
 *   +0x1C host_midi(msg)                     status | d1 << 8 | d2 << 16, channel voice only (0x80..0xEF, data
 *                                            < 0x80, d2 = 0 for 0xCn/0xDn); sent to the MIDI outputs.
 * All calls are made from module callbacks (scheduler suspended) and never call firmware code: they append to one
 * shared ring (top byte a type: 0 note, 1 port note, 2 param, 3 MIDI), so order holds across types. The Main task
 * drains the ring, at most QCALLS firmware calls per pass. Releases are ordered in the stream; one pitch per track
 * (an on while another pitch is held releases the held one first); under overload a track may sound longer than
 * asked, but it always ends released. Every module lifecycle end flushes the ring and releases all 8 tracks. A
 * release is a note off, not silence: one-shot samples play out. Tracks are shared with the pads, the sequencer and
 * MIDI; that is policy. Dropped entries are counted in q_drop / q_bad.
 *
 * Module format (at ITCM MOD_BASE = 0x00000400, up to MOD_MAX bytes; see tools/build_module.py):
 *   u32 magic 'CRM1' or 'CRM2', u32 size (whole module incl. header), u32 crc (CRC-32/zlib over the module with this
 *   field zero), u32 init_off, u32 frame_off (Thumb offsets from MOD_BASE with bit 0 set; init 0 = none, frame
 *   required; each must point inside the code with at least one halfword left), u32 input_off (0 = none), and for
 *   'CRM2' u32 clock_off (0 = none); code from byte 24 ('CRM1') or 28 ('CRM2').
 *   init(leds)                         once on start, with the scheduler suspended
 *   input(leds, kind, index, value)    each input event while the module owns the controls, in the Main task with
 *                                      the scheduler suspended (kind/value as in the 09 log; must not block)
 *   frame(leds, frame_no) -> nonzero   each LED frame in mode 3, with the scheduler suspended; returns 0 to stop
 *   clock(leds, event, pos, bpm)       event 1 START, 2 PULSE, 3 STOP; pos = pulse | step << 8 | beat << 16 with
 *                                      pulse the sequencer's pulse index 0..95 (24 PPQN, wraps every 4 beats), step =
 *                                      pulse / 6, beat = pulse / 24 (STOP: the last pulse delivered); bpm = tempo
 *                                      (internal clock 40..240; 0 if unreadable). Main task, scheduler suspended,
 *                                      must not block; may call host_note. Called only on pulses on which the
 *                                      sequencer steps. START comes right before the first pulse delivered while
 *                                      the module is marked stopped; STOP at most one Main pass after the transport
 *                                      stops.
 * leds is the 224-byte LED buffer (brightness in bits 1..7). Modules have no static data of their own; their RAM is
 * MOD_RAM (0x00000100-0x000003FF, 768 bytes of ITCM, zeroed before init). Every ITCM access first checks ITCMCR
 * (enabled, at least 16 KB). The first 256 bytes of ITCM are never touched.
 *
 * Ownership. Shift + Sample Rec (or SysEx 0A) hands pads, buttons and encoders to the running module and hides them
 * from the firmware's UI; the pots and Shift always stay with the firmware. Routing is decided when a control is
 * pressed and its release (and a pad's pressure) follows the press. When the module stops, restarts or is replaced,
 * its outstanding presses become tombstones whose releases reach nobody. Lights follow ownership: owned, the module's
 * LED buffer is shown; not owned, the normal display, while frame() keeps running unseen. A module without an input
 * entry never owns the controls and its buffer is always shown.
 *
 * Shift + Sample Rec (both edges swallowed):
 *   a module runs           stop it: normal lights and controls are back at once
 *   the picker is open      close it
 *   otherwise               open the picker
 *   ITCM unavailable        the combination keeps its normal meaning
 * Every start (picker or cmd 05) turns ownership on when the module has an input entry.
 *
 * Picker (mode 4). Pad 0 is the built-in module (appended after the hook code by tools/build_lightshow.py, default
 * modules/paint.c; header and CRC checked in flash, copied into ITCM only when needed); pads 1..31 are the files
 * 1:/MODULES/<8.3 name>.MOD, the 31 smallest names in byte order, listed when the picker opens. Available pads are
 * dimly lit (pad 0 cyan, the others white, a failed one red); the pad being worked on blinks. Pressing an available
 * pad loads that module into ITCM, checks it and starts it; a failed load leaves the picker open. No card, no folder
 * or no matching file: pad 0 alone. The card is only ever read: no write, sync, unlink, mkdir, rename or mkfs call is
 * ever made, files are opened read-only, and our file system calls never run concurrently with the firmware's own
 * file jobs (a token makes them exclusive; Main only tries, it never waits). Every opened file and folder is closed.
 *
 * Loaded this boot. A module in ITCM is started only if it was written during this boot (built-in copy, SD load, or
 * a cmd 04 upload). ITCM survives the warm reset after a flash, so an old upload never shadows anything: the gesture
 * never starts ITCM content (it opens the picker), and cmd 05 is refused unless ITCM was loaded this boot.
 *
 * SysEx (USB port): F0 00 20 29 00 60 <cmd> [args] F7
 *   00                off: normal display
 *   01 dens decay     glitter
 *   02 k              static: LED bytes whose index has bit k set (k 0..7, 7F = all)
 *   03 hi lo v...     static: set LED bytes from index hi*128+lo to v << 1
 *   04 a2 a1 a0 n...  module upload: write nibble pairs (hi, lo) as bytes at ITCM MOD_BASE + (a2<<14|a1<<7|a0);
 *                     stops a running module (or closes the picker) first; only inside the module area; marks
 *                     ITCM as loaded this boot; refused while the loader reads the folder or loads a file
 *   05                module start: verify header (magic, size, CRC-32) and call its init; mode 3 runs its frame
 *                     function every LED frame; ownership on for a module with an input entry. Replies with status.
 *                     Refused (nothing changes, status still sent) unless ITCM was loaded this boot and the loader
 *                     is idle or choosing.
 *   06 00             open the picker (as the gesture with no module running)   06 01 p   press pad p in the picker
 *   06 02             cancel the picker                                          06 7F     status only
 *                     each replies F0 00 20 29 00 60 06 <phase> <mode> <fresh> <count> <pick> <result> <bad> F7
 *                     (count..bad are 0 while the loader is idle); phase 0 idle, 1 reading the folder, 2 choosing,
 *                     3 loading; result 0 none, 1 ok, 2 no card, 3 no folder, 4 folder read error, 5 file object
 *                     busy, 6 open error, 7 bad size, 8 read error, 9 bad module, 10 file object taken over
 *   06 03 i           name of pad i + 1: F0 00 20 29 00 60 06 03 i <12 ASCII bytes, 0-padded> F7 (only while choosing)
 *   08 sel a2 a1 a0 n DSP X/Y read (devkit profile only: the DSP meter's X windows; the firmware profile refuses every
 *                     08 with status 0). sel 0 = core 0 X, 1 = core 0 Y, 2 = core 1 X, 3 = core 1 Y; addr =
 *                     a2<<14|a1<<7|a0, n words (1..16). Reply F0 00 20 29 00 60 08 sel a2 a1 a0 n status <6 nibbles
 *                     per word, MSB first> F7; status 1 = read, 0 = refused (nothing touched, no words).
 *   09 p              input log page p (0 or 1): reply F0 00 20 29 00 60 09 p <seq 5x7> <16 entries x 8 nibbles>
 *                     F7. seq counts all events since boot; entry k of page p is ring slot 16p + k, and event
 *                     number i (0-based) lives in slot i % 32. Entry = kind | index << 8 | value << 16 (u32), kind 1
 *                     pad (value = velocity | on << 8), 2 button (value = down), 3 pot (value), 4 pressure (value),
 *                     5 encoder (value = int16 delta). Invalid p: no reply.
 *   0A on             mode: 1 = hand the controls to the running module (refused unless a module runs), 0 = give
 *                     them back; replies with status (byte 8 = 1 | owned << 1)
 *   0B                clock status: F0 00 20 29 00 60 0B <clocked> <running> <pulse> <bpm 2x7> <pulses 5x7>
 *                     <starts 3x7> <stops 3x7> F7
 *   7D a4..a0         dump 32 bytes at addr, reply F0 00 20 29 00 60 7D <addr 5x7> <64 nibbles> F7. Restricted
 *                     (DUMP_ALLOW, every Arrhythmia 0.1 image): only ITCM 0x00000100-0x00003FFF (module RAM and
 *                     code); any other well-formed request, internal flash included, gets the refusal
 *                     F0 00 20 29 00 60 7D <addr 5x7> F7 (13 bytes, no data, nothing read). DUMP_ALLOW=0: internal
 *                     flash 0x08000000-0x0807FFFF or ITCM 0x00000000-0x00003FFF
 *   7E                heap: reply F0 00 20 29 00 60 7E <limit 5x7> <cursor 5x7> F7
 *   7F                status: reply F0 00 20 29 00 60 7F <mode (4 = picker)> <ok> <cursor 5x7> <frames 4x7> <mod:
 *                     0 none, 1 valid, 2 bad, 3 ITCM unavailable> F7
 * Commands 0D and 0E are defined in o1wait.c and scales.c.
 */
#include <stdint.h>

#define LEDS            224u
#define HEAP_CTL        0x20002B54u
#define HEAP_LIMIT      0x2004F760u
#define REGION_SIZE     0x200u
#define REGION          (HEAP_LIMIT - REGION_SIZE)
#define MAGIC           0x4C534857u     /* "WHSL" */
#define TX_PORT_PTR     0x20006F04u
#define REPLY_BUF       0x20006C68u     /* 19-byte SysEx reply buffer */
#define FLASH_START     0x08000000u
#define FLASH_END       0x08080000u
#define ITCM_END        0x00004000u
#define MOD_BASE        0x00000400u
#define MOD_MAX         (ITCM_END - MOD_BASE)
#define MOD_RAM         0x00000100u
#ifndef DUMP_ALLOW                      /* 1 (arrhythmia.c): cmd 7D reads only DUMP_LO..ITCM_END, see dump() */
#define DUMP_ALLOW      0
#endif
#define DUMP_LO         MOD_RAM
#define MOD_MAGIC       0x314D5243u     /* "CRM1": 24-byte header */
#define MOD_MAGIC2      0x324D5243u     /* "CRM2": 28-byte header, + clock_off */
#define CLOCK_SIZE      0x20u
#define CLOCK_AT        (REGION - CLOCK_SIZE)   /* struct clk; the heap limit becomes CLOCK_AT */
#ifdef TRACKMODES                       /* track modes: patches/src/trackmodes.c */
#define TM_SIZE         0x100u
#define TM_AT           (CLOCK_AT - TM_SIZE)    /* struct tm, below the clock block; the heap limit becomes TM_AT */
#ifdef PLOCK                            /* p-lock fix: struct plx of patches/src/plockfix.c below struct tm */
#define PLX_SIZE        0x40u
#define PLX_AT          (TM_AT - PLX_SIZE)
#ifdef SYNTHPAGE                        /* settings page: patches/src/synthpage.c */
#define PG_SIZE         0x20u
#define PG_AT           (PLX_AT - PG_SIZE)      /* struct pg, below struct plx; the heap limit becomes PG_AT */
#ifdef NOTETAP                          /* note tap: struct a1 of patches/src/notetap.c, fixed, below struct pg */
#define A1_SIZE         0x300u
#define A1_AT           (PG_AT - A1_SIZE)       /* 0x2004F0E0..0x2004F3DF; the heap limit becomes A1_AT */
#if CORE0_SYNTH                         /* core-0 queue feed (prototype): struct q0 of patches/src/core0feed.h, the A1
                                           reservation grown by Q0_SIZE below struct a1 (struct a1 does not move) */
#if FM_PATCH
#if CORE0_RECOVER_TIMING
#define Q0_SIZE         0xD0u                   /* + the optional recovery timing counters (heap limit 16 B lower) */
#else
#define Q0_SIZE         0xC0u                   /* + the FM latch mirrors (core0feed.h; heap limit 32 B lower) */
#endif
#elif CORE0_TONE
#define Q0_SIZE         0xA0u                   /* + the tone state */
#else
#define Q0_SIZE         0x80u
#endif
#define Q0_AT           (A1_AT - Q0_SIZE)       /* 0x2004F080 (0x2004F060 with tone)..0x2004F0DF; heap limit Q0_AT */
#define HEAP_FLOOR      Q0_AT
#else
#define HEAP_FLOOR      A1_AT
#endif
#else
#define HEAP_FLOOR      PG_AT
#endif
#else
#define HEAP_FLOOR      PLX_AT
#endif
#else
#define HEAP_FLOOR      TM_AT
#endif
#else
#define HEAP_FLOOR      CLOCK_AT
#endif
#if SCALES                              /* scales (patches/src/scales.c): struct sc, the lowest block */
#define SC_SIZE         0x20u
#if defined(NOTETAP) && CORE0_SYNTH
#define SC_AT           (Q0_AT - SC_SIZE)
#elif defined(NOTETAP)
#define SC_AT           (A1_AT - SC_SIZE)       /* 0x2004F0C0..0x2004F0DF */
#elif defined(SYNTHPAGE)
#define SC_AT           (PG_AT - SC_SIZE)
#else
#define SC_AT           (PLX_AT - SC_SIZE)      /* 0x2004F3E0..0x2004F3FF */
#endif
#undef HEAP_FLOOR
#define HEAP_FLOOR      SC_AT
#endif
#define TRANSPORT_VT    0x080683E0u     /* transport object check */
#define EV_START        1u
#define EV_PULSE        2u
#define EV_STOP         3u
#define CHUNK           32u
#define ITCMCR          0xE000EF90u
#define DSP_CACHE       0x20003684u     /* cached next DSP write address */
#define DSP_LOCK        0x20003688u     /* byte counting semaphore {count, max} guarding the DSP bus */
#define DSP_MAX_WORDS   16u
#define EV_SLOTS        32u
#define BTN_SHIFT       0x1Bu           /* button index (docs/input-map.json) */
#define BTN_SAMPLE_REC  0x12u
#define EV_PAD          1u
#define EV_BUTTON       2u
#define EV_PRESSURE     4u
#define EV_ENCODER      5u     /* bit 0 EN, bits 6:3 SZ (0b0101 = 16 KB) */
#define HOST_TABLE      0x0806A570u     /* = CODE_AT in tools/build_lightshow.py; the hook code follows the table */
#define HOST_MAGIC      0x31485243u     /* "CRH1" */
#define QLEN            8u              /* note ring entries (power of two) */
#define Q_OFF_RESERVE   2u              /* slots a note on must leave free for note offs */
#define QCALLS          2u              /* router calls per Main-loop pass, in total */
#define SRAM_START      0x20000000u
#define SRAM_END        0x20050000u
#define DISP_PTR        0x20006EECu     /* note router lookup root */
#define ROUTER_VT       0x080694B0u
#define PORT_ENGINE     0x18u           /* router port 0x18 + t: engine only */
#define PORT_PAD        0x00u           /* router port t: front panel (engine, MIDI out, recorder) */
#define T_NOTE          0u              /* ring entry type, bits 24..31 */
#define T_PORT          1u
#define T_PARAM         2u
#define T_MIDI          3u
#define ENGINE_PTR      0x20006EBCu     /* engine object pointer */
#define ENGINE_VT       0x080685E0u
#define GROUP_VT        0x08069084u     /* parameter group check */
#define CH_DESC         0x08066394u     /* channel parameter descriptors, 8 bytes each */
#define CH_PARAMS       25u
#define CH_STORE        0x29u           /* channel parameter store bytes */
#define TX_PORT_VT      0x08068AA8u     /* MIDI output port check */
#define TX_MASK         7u              /* the three MIDI outputs */
#define BTN_CELL        0x20006E90u     /* UI controller lookup root */
#define BTN_LAMBDA      0x080295D1u
#define CTRL_VT         0x080672E4u     /* UI controller check */
#define GRID_VT         0x080678DCu     /* grid page check */
#define PADS            32u
#define MODE_PICK       4u              /* s->mode while the picker is open */
#define SD_OBJ          0x2000033Cu     /* microSD card state */
#define FATFS_1         0x20006328u     /* FatFs volume of drive 1: (microSD) */
#define FILE_B_PTR      0x20003754u     /* shared file object used for module loads */
#define FILE_VT         0x080685B0u     /* file object check */
#define FIL_FSIZE       0x0Cu           /* FIL: obj.objsize */
#define FIL_FLAG        0x10u           /* FIL: flag (mode bits; 0x40 written, 0x80 buffer dirty) */
#define FA_READ         0x01u
#define FR_NO_FILE      4u
#define FR_NO_PATH      5u
#define FI_ATTR         8u              /* FILINFO field offsets (FatFs) */
#define FI_ALT          9u
#define FI_NAME         0x16u
#define AM_SKIP         0x1Eu           /* directory, volume label, system, hidden */
#define PIECE           256u            /* f_read size: below the 512-byte sector, so always copied by the CPU */
#define SCAN_MAX        512u            /* directory entries examined per scan */
#define NAME_LEN        13u             /* 8.3 name + NUL */
#define TOK_MAIN        1u              /* a FatFs call of ours is in flight (Main task) */
#define TOK_CONTENT     2u              /* the Content task is inside its job loop */
#define DIM             0x10u
#define BRIGHT          0x60u

typedef void (*sched_fn)(void);
typedef uint32_t (*resume_fn)(void);
typedef void (*frame_fn)(void *frame, void *src, uint32_t flag);
typedef void (*sysex_fn)(uint32_t a, uint32_t *msg, uint32_t c);
typedef void (*tx_fn)(uint32_t port, uint32_t one, uint32_t *msg);
typedef void (*mod_init_fn)(uint8_t *leds);
typedef uint32_t (*mod_frame_fn)(uint8_t *leds, uint32_t frame_no);
typedef void (*take_fn)(uint32_t sem);
typedef void (*give_fn)(uint32_t *sem_ref);
typedef uint32_t (*dsp_read_fn)(uint32_t core, uint32_t addr);
typedef void (*ui_fn)(uint32_t a, uint32_t b, uint32_t c, uint32_t d);
typedef void (*mod_input_fn)(uint8_t *leds, uint32_t kind, uint32_t index, uint32_t value);
#define ORIG_SCHED  ((sched_fn)0x08026135u)
#define ORIG_FRAME  ((frame_fn)0x0802960Du)
#define ORIG_SYSEX  ((sysex_fn)0x0802CF91u)
#define TX_SYSEX    ((tx_fn)0x080298B9u)
#define SUSPEND_ALL ((sched_fn)0x08024E05u)
#define RESUME_ALL  ((resume_fn)0x08024E15u)
#define SEM_TAKE    ((take_fn)0x08029C8Du)      /* semaphore take */
#define SEM_GIVE    ((give_fn)0x08030EB9u)      /* semaphore give */
#define STOCK_PAD       ((ui_fn)0x08040109u)    /* (ctx, pad, velocity, on) */
#define STOCK_BUTTON    ((ui_fn)0x0804012Bu)    /* (ctx, code, down) */
#define STOCK_POT       ((ui_fn)0x08040141u)    /* (ctx, which, value) */
#define STOCK_PRESSURE  ((ui_fn)0x08040153u)    /* (ctx, pad, value) */
#define STOCK_ENCODER   ((ui_fn)0x0804015Fu)    /* (ctx, knob, int16 delta) */
#define DSP_READ    ((dsp_read_fn)0x08031269u)  /* DSP word read */
#define ORIG_TIMERS ((sched_fn1)0x0802677Bu)    /* Main loop timer service */
typedef void (*sched_fn1)(uint32_t a);
/* note on takes a fifth argument; we pass 0 */
typedef void (*note_on_fn)(uint32_t router, uint32_t note, uint32_t velocity, uint32_t port, uint32_t zero);
typedef void (*note_off_fn)(uint32_t router, uint32_t note, uint32_t port, uint32_t zero);
typedef uint32_t (*content_fn)(uint32_t a, uint32_t b, uint32_t c, uint32_t d);
typedef void (*delay_fn)(uint32_t ms);
typedef uint32_t (*f_open_fn)(uint32_t fil, const char *path, uint32_t mode);
typedef uint32_t (*f_read_fn)(uint32_t fil, uint32_t buf, uint32_t n, uint32_t *br);
typedef uint32_t (*f_obj_fn)(uint32_t obj);
typedef uint32_t (*f_opendir_fn)(uint32_t dir, const char *path);
typedef uint32_t (*f_readdir_fn)(uint32_t dir, uint32_t fno);
#define ORIG_PULSE  ((content_fn)0x080333ABu)  /* pulse hook site */
#define ORIG_CONTENT ((content_fn)0x0802AD41u)  /* Content task hook site */
#define DELAY       ((delay_fn)0x08024151u)     /* delay (ms), yields */
#define F_OPEN      ((f_open_fn)0x08021D91u)
#define F_READ      ((f_read_fn)0x08021F73u)
#define F_CLOSE     ((f_obj_fn)0x08022347u)
#define F_OPENDIR   ((f_opendir_fn)0x08022529u)
#define F_READDIR   ((f_readdir_fn)0x080225B7u)
#define F_CLOSEDIR  ((f_obj_fn)0x08022589u)
typedef uint32_t (*param_fn)(uint32_t group, uint32_t idx, uint32_t val);
typedef uint32_t (*midi_fn)(uint32_t mask, const volatile uint8_t *msg);
#define PARAM_SET   ((param_fn)0x080465DDu)     /* channel parameter set (clamps) */
#define MIDI_OUT    ((midi_fn)0x080468FBu)      /* MIDI message out */

struct state {
    uint32_t magic;
    volatile uint8_t mode, density, decay, was_on;
    uint32_t rng, frames;
    uint32_t fake_src[5];
    uint8_t leds[LEDS];
    uint8_t reply[20];
    uint32_t reply_msg[2];
    uint32_t mod_frame;                       /* Thumb address of the running module's frame function, 0 = none */
    uint32_t ev_seq;                          /* input events logged since boot */
    uint32_t ev[EV_SLOTS];                    /* ring: event i in slot i % EV_SLOTS */
    uint32_t mod_input;                       /* Thumb address of the running module's input function, 0 = none */
    uint32_t pad_mod, btn_mod;                /* press went to the running module: its release/pressure follow */
    uint32_t pad_eat, btn_eat;                /* tombstones: swallow the release, deliver it to nobody */
    uint8_t owned, shift;                     /* mode requested; Shift held (as last seen) */
    /* note ring, touched only with the scheduler suspended: entries [q_head, q_tail) (free-running u8 indices) */
    uint8_t q_head, q_tail, q_kill;           /* q_kill: tracks with a pending release (kill), see kill_at */
    uint32_t q[QLEN];                         /* t | note << 8 | velocity << 16; velocity 0 = off */
    uint16_t q_drop, q_bad;                   /* refused for lack of room; dropped by a failed router check */
    uint8_t sounding[8];                      /* Main task only: pitch the drain holds per track + 1, 0 = none */
    uint8_t kill_at[8];                       /* ring index at which kill(t) applies (q_tail when raised) */
    /* module loader (Main task, except tok); the picker's working data is struct pick in ITCM */
    uint8_t ld_phase;                         /* LD_*; LD_IDLE implies no FatFs object of ours is open */
    uint8_t ld_fresh;                         /* the module area of ITCM was written during this boot */
    volatile uint8_t tok;                     /* TOK_MAIN / TOK_CONTENT, changed only under the suspension */
    /* abi 3 (the prefix above is unchanged) */
    uint8_t sport;                            /* Main task only: bit t = sounding[t] was played on port t */
    volatile uint32_t ui;                     /* host_ui() snapshot, written by the Main task only (one word) */
};
_Static_assert(sizeof(struct state) <= REGION_SIZE, "state must fit the reserved region");

#define LD_IDLE     0u
#define LD_SCAN     1u                        /* reading 1:/MODULES */
#define LD_PICK     2u                        /* choosing; no FatFs object open */
#define LD_LOAD     3u                        /* loading name[pick - 1] into ITCM */
#define OBJ_NONE    0u
#define OBJ_DIR     1u
#define OBJ_FILE    2u
enum { R_NONE, R_OK, R_NOCARD, R_NOFOLDER, R_DIRERR, R_BUSY, R_OPEN, R_SIZE, R_READ, R_BAD, R_LOST };

/* The picker's working data, in ITCM MOD_RAM: used only while the loader is not idle, when no module runs (start()
 * zeroes MOD_RAM and sets the loader idle). Initialised whenever the picker opens from idle. */
struct pick {
    uint32_t dir[16];                         /* FatFs DIR (0x34 bytes in this build) */
    uint32_t fi[40];                          /* FatFs FILINFO (0x96 bytes in this build) */
    uint32_t size, off;                       /* file being loaded: size (<= MOD_MAX), bytes read */
    uint16_t seen;                            /* directory entries examined */
    uint8_t obj, count, pick, result, bad, restart;
    char name[PADS - 1][NAME_LEN];            /* pads 1..count: 8.3 names, ascending */
};
_Static_assert(sizeof(struct pick) <= MOD_BASE - MOD_RAM, "picker data must fit MOD_RAM");
#define PICK        ((volatile struct pick *)MOD_RAM)

struct mod_hdr {
    uint32_t magic, size, crc, init_off, frame_off, input_off;
    uint32_t clock_off;                       /* 'CRM2' only */
};
#define HDR1 24u                              /* 'CRM1' header bytes */
#define HDR2 28u                              /* 'CRM2' header bytes */

/* Header bytes for a module magic, 0 = not a module. */
static uint32_t hdr_len(uint32_t magic)
{
    return magic == MOD_MAGIC ? HDR1 : magic == MOD_MAGIC2 ? HDR2 : 0;
}

/* Clock block (host only; reported by cmd 0B). Written only with the scheduler suspended, except transport, bpm and
 * pulse, which only the Main task writes. */
struct clk {
    uint32_t mod_clock;                       /* Thumb address of the running module's clock entry, 0 = none */
    uint32_t transport;                       /* transport object of the last pulse delivered */
    uint32_t pulses;                          /* pulses delivered since the last START */
    uint16_t starts, stops;                   /* events delivered since boot */
    uint8_t running, pulse, bpm;              /* START delivered and no STOP since; last pulse index; last tempo */
};
_Static_assert(sizeof(struct clk) <= CLOCK_SIZE, "clock block must fit");
#define CLK ((volatile struct clk *)CLOCK_AT)
typedef void (*mod_clock_fn)(uint8_t *leds, uint32_t event, uint32_t pos, uint32_t bpm);

static uint32_t owns(const struct state *s);
static void retire(struct state *s);
static uint32_t run_module(struct state *s);
static void clock_reply(void);
#ifdef TRACKMODES
static void tm_boot(void);
static void tm_sysex(struct state *s, const uint8_t *m, uint32_t len);
#endif
#ifdef PLOCK
static void pl_host(uint32_t t, uint32_t idx);
#endif
#ifdef SYNTHPAGE
#define MODE_PAGE   5u                        /* hook_frame only: s->mode == 0 and the page is open */
static uint32_t pg_live(struct state *s);
static uint32_t pg_button(struct state *s, uint32_t index, uint32_t bit);
static uint32_t pg_pad_press(struct state *s, uint32_t index, uint32_t bit);
static uint32_t pg_encoder(struct state *s, uint32_t knob, uint32_t delta);
static void pg_leds(struct state *s);
static void pg_boot(void);
#endif
#if SCALES
static void sc_boot(void);
static void sc_main(struct state *s);
static void sc_copy(const volatile uint8_t *b);
static void sc_sysex(struct state *s, const uint8_t *m, uint32_t len);
#endif
#if CORE0_BENCH
static void q0_telem(const uint8_t *m, uint32_t len);   /* SysEx 0F, bench core-0 telemetry (patches/src/core0feed.c) */
#endif
#if BENCH_METER_READ
static void q0_meter(const uint8_t *m, uint32_t len);   /* SysEx 08, the restricted meter readout (core0feed.c) */
#endif
#ifdef O1WAIT
static void o1_sysex(const uint8_t *m, uint32_t len);
static void o1_frame(uint32_t *src);      /* stock display shown: blink the DSP-fault LED (patches/src/o1wait.c) */
#endif

static struct state *region(void)
{
    volatile uint32_t *ctl = (volatile uint32_t *)HEAP_CTL;
    struct state *s = (struct state *)REGION;
    if (ctl[0] != HEAP_FLOOR || ctl[1] > HEAP_FLOOR || s->magic != MAGIC)
        return 0;
    return s;
}

void hook_boot(void)
{
    volatile uint32_t *ctl = (volatile uint32_t *)HEAP_CTL;
    if (ctl[0] == HEAP_LIMIT && ctl[1] <= HEAP_FLOOR) {
        struct state *s = (struct state *)REGION;
        uint8_t *p = (uint8_t *)HEAP_FLOOR;   /* (devkit-tm: the track-modes block,) the clock block, then the state */
        for (uint32_t i = 0; i < (CLOCK_AT - HEAP_FLOOR) + CLOCK_SIZE + sizeof *s; i++)
            p[i] = 0;
        s->rng = 0x9E3779B9u ^ ctl[1];
        s->density = 40;
        s->decay = 3;
        s->magic = MAGIC;
#ifdef TRACKMODES
        tm_boot();
#endif
#ifdef SYNTHPAGE
        pg_boot();
#endif
#if SCALES
        sc_boot();
#endif
        ctl[0] = HEAP_FLOOR;
    }
    ORIG_SCHED();
}

static uint32_t rnd(struct state *s)
{
    uint32_t x = s->rng;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    return s->rng = x;
}

static void glitter(struct state *s)
{
    uint32_t d = (uint32_t)s->decay << 1;
    for (uint32_t i = 0; i < LEDS; i++)
        s->leds[i] = s->leds[i] > d ? s->leds[i] - d : 0;
    uint32_t n = s->density >> 4;
    if ((rnd(s) & 15) < (s->density & 15))
        n++;
    while (n--) {
        uint32_t r = rnd(s);
        s->leds[(r >> 8) % LEDS] = 0x80 | (r & 0x7E);
    }
}

static uint32_t crc32(const volatile uint8_t *p, uint32_t n, uint32_t skip_at)
{
    uint32_t c = 0xFFFFFFFFu;
    for (uint32_t i = 0; i < n; i++) {
        uint8_t b = (i >= skip_at && i < skip_at + 4) ? 0 : p[i];
        c ^= b;
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1)));
    }
    return ~c;
}

static int itcm_ok(void)
{
    uint32_t cr = *(volatile uint32_t *)ITCMCR;
    return (cr & 1) && ((cr >> 3) & 0xF) >= 5;
}

/* A Thumb entry: bit 0 set, inside the code after the header (hdr bytes), at least one halfword left. */
static int entry_ok(uint32_t off, uint32_t size, uint32_t hdr)
{
    uint32_t a = off & ~1u;
    return (off & 1) && a >= hdr && size >= 2 && a <= size - 2;   /* no wrap at 0xFFFFFFFF */
}

/* 0 none, 1 valid, 2 present but bad, 3 ITCM unavailable (ITCMCR disabled or smaller than 16 KB). */
static uint32_t mod_check(void)
{
    if (!itcm_ok())
        return 3;
    const volatile struct mod_hdr *h = (const volatile struct mod_hdr *)MOD_BASE;
    uint32_t hdr = hdr_len(h->magic);       /* 'CRM1' or 'CRM2' (clock_off read only for 'CRM2') */
    if (!hdr)
        return 0;
    uint32_t size = h->size;
    if (size < hdr + 2 || size > MOD_MAX || !entry_ok(h->frame_off, size, hdr)
            || (h->init_off && !entry_ok(h->init_off, size, hdr))
            || (h->input_off && !entry_ok(h->input_off, size, hdr))
            || (hdr == HDR2 && h->clock_off && !entry_ok(h->clock_off, size, hdr))
            || crc32((const volatile uint8_t *)MOD_BASE, size, 8) != h->crc)
        return 2;
    return 1;
}

/* One frame of the running module; when it finishes (or vanished) the stock display is back. Caller holds the
 * suspension. Returns 0 if the module stopped. */
static uint32_t run_module(struct state *s)
{
    if (s->mod_frame && ((mod_frame_fn)s->mod_frame)(s->leds, s->frames))
        return 1;
    retire(s);
    s->mod_frame = 0;
    s->mode = 0;
    return 0;
}

/* Copy the built-in module (host table word 3) into ITCM if its header, size and CRC check out in flash. Only when
 * no module runs (never overwrite code that may be running). Caller holds the suspension. */
static uint32_t builtin_copy(void)
{
    const volatile uint32_t *t = (const volatile uint32_t *)HOST_TABLE;
    uint32_t a = t[3];
    if (t[0] != HOST_MAGIC || (a & 3) || a < HOST_TABLE || a > FLASH_END - HDR2)
        return 0;
    const volatile struct mod_hdr *h = (const volatile struct mod_hdr *)a;
    uint32_t size = h->size;
    if (!hdr_len(h->magic) || size < hdr_len(h->magic) + 2 || size > MOD_MAX || size > FLASH_END - a
            || crc32((const volatile uint8_t *)a, size, 8) != h->crc)
        return 0;
    const volatile uint8_t *src = (const volatile uint8_t *)a;
    volatile uint8_t *dst = (volatile uint8_t *)MOD_BASE;
    for (uint32_t i = 0; i < size; i++)
        dst[i] = src[i];
    return 1;
}

/* Stop whatever runs and start the module in ITCM (cmd 05 and the picker): a valid start always stops the old
 * instance, even if the new one fails. Ownership goes on for a module with an input entry. Callers make sure that
 * ITCM was loaded this boot and that no FatFs object of the loader is open (loader idle or choosing): MOD_RAM, which
 * holds the picker's data, is zeroed here and the loader becomes idle. Caller holds the suspension. Returns 1 if the
 * module runs. */
static uint32_t start(struct state *s)
{
    retire(s);
    if (s->mode == 3 || s->mode == MODE_PICK)
        s->mode = 0;
    s->mod_frame = 0;
    s->ld_phase = LD_IDLE;
    if (!s->ld_fresh || mod_check() != 1)
        return 0;
    const volatile struct mod_hdr *h = (const volatile struct mod_hdr *)MOD_BASE;
    for (volatile uint32_t *p = (volatile uint32_t *)MOD_RAM; p < (volatile uint32_t *)MOD_BASE; p++)
        *p = 0;
    __asm__ volatile("dsb\n\tisb" ::: "memory");
    if (h->init_off)
        ((mod_init_fn)(MOD_BASE + h->init_off))(s->leds);
    s->mod_frame = MOD_BASE + h->frame_off;
    s->mod_input = h->input_off ? MOD_BASE + h->input_off : 0;
    CLK->mod_clock = h->magic == MOD_MAGIC2 && h->clock_off ? MOD_BASE + h->clock_off : 0;  /* retire() zeroed the rest */
    s->mode = 3;
    s->owned = s->mod_input != 0;
    return 1;
}

/* R, G, B LED byte of each pad (pad = row * 8 + column; docs/led-map.json), kept in .text: the hook code has no
 * data sections. */
static const uint8_t *pad_leds(void)
{
    const uint8_t *t;
    __asm__("adr %0, 1f\n\tb 2f\n\t.p2align 2\n1:\n\t"
            ".byte 37,38,39, 24,25,26, 100,101,102, 103,88,89, 156,157,158, 159,144,145, 202,203,204, 205,206,207\n\t"
            ".byte 30,31,16, 90,91,92, 93,94,95, 80,81,82, 146,147,148, 149,150,151, 136,137,138, 195,196,197\n\t"
            ".byte 20,21,22, 83,84,85, 86,87,72, 73,74,75, 139,140,141, 142,143,128, 129,130,131, 185,186,187\n\t"
            ".byte 10,11,12, 76,77,78, 79,64,65, 66,67,68, 132,133,134, 135,120,121, 122,123,124, 191,176,177\n\t"
            ".p2align 1\n2:" : "=r"(t));
    return t;
}

static void pad_rgb(struct state *s, const uint8_t *t, uint32_t pad, uint8_t r, uint8_t g, uint8_t b)
{
    s->leds[t[3 * pad]] = r;
    s->leds[t[3 * pad + 1]] = g;
    s->leds[t[3 * pad + 2]] = b;
}

/* The picker's lights (UI task, caller holds the suspension): pad 0 cyan, pads 1..count white, a failed pad red, all
 * dim; the pad being worked on blinks (pad 0 while the folder is read); Sample Rec dim white; everything else dark. */
static void picker_leds(struct state *s)
{
    const volatile struct pick *p = PICK;
    const uint8_t *t = pad_leds();
    uint32_t count = 0, busy = PADS, bad = 0;
    if (itcm_ok()) {
        count = p->count < PADS - 1 ? p->count : PADS - 1;
        bad = p->bad;
        if (s->ld_phase == LD_SCAN)
            busy = 0;
        else if (s->ld_phase == LD_LOAD)
            busy = p->pick;
    }
    for (uint32_t i = 0; i < LEDS; i++)
        s->leds[i] = 0;
    pad_rgb(s, t, 0, 0, DIM, DIM);
    for (uint32_t i = 1; i <= count; i++)
        pad_rgb(s, t, i, DIM, DIM, DIM);
    if (bad && bad <= count + 1)
        pad_rgb(s, t, bad - 1, DIM, 0, 0);
    if (busy < PADS) {
        uint8_t v = (s->frames & 16) ? BRIGHT : 0;
        pad_rgb(s, t, busy, v, v, v);
    }
    s->leds[48] = DIM;                      /* btn_SampleRec R, G, B (docs/led-map.json) */
    s->leds[49] = DIM;
    s->leds[50] = DIM;
}

void hook_frame(void *frame, uint32_t *src, uint32_t flag)
{
    struct state *s = region();
    if (!s) {
        ORIG_FRAME(frame, src, flag);
        return;
    }
    SUSPEND_ALL();
    uint8_t mode = s->mode;
#ifdef SYNTHPAGE
    if (pg_live(s))                         /* also closes the page when anything else is in front */
        mode = MODE_PAGE;
#endif
    if (!mode) {
        uint8_t was_on = s->was_on;
        s->was_on = 0;
        RESUME_ALL();
        if (was_on)
            __atomic_fetch_or(src, 1u, __ATOMIC_SEQ_CST);
#ifdef O1WAIT
        o1_frame(src);
#endif
        ORIG_FRAME(frame, src, flag);
        return;
    }
    uint32_t ready = ((volatile uint8_t *)frame)[2] != 0;
    if (mode == 3 && s->mod_input && !owns(s)) {
        /* lights follow ownership: the module keeps running unseen, the stock display is shown as in mode 0 */
        if (ready) {
            s->frames++;
            run_module(s);
        }
        uint8_t was_on = s->was_on;
        s->was_on = 0;
        RESUME_ALL();
        if (was_on)
            __atomic_fetch_or(src, 1u, __ATOMIC_SEQ_CST);
#ifdef O1WAIT
        o1_frame(src);
#endif
        ORIG_FRAME(frame, src, flag);
        return;
    }
    if (!ready) {
        RESUME_ALL();
        return;
    }
    s->was_on = 1;
    s->frames++;
    if (mode == 1)
        glitter(s);
    else if (mode == 3)
        run_module(s);
    else if (mode == MODE_PICK)
        picker_leds(s);
#ifdef SYNTHPAGE
    else if (mode == MODE_PAGE)
        pg_leds(s);
#endif
    s->fake_src[0] = 1;
    s->fake_src[4] = (uint32_t)s->leds;
    RESUME_ALL();
    ORIG_FRAME(frame, s->fake_src, 0);
}

static void put7(volatile uint8_t *p, uint32_t v, int n)
{
    for (int i = n - 1; i >= 0; i--, v >>= 7)
        p[i] = v & 0x7F;
}

static void header(volatile uint8_t *r, uint8_t cmd)
{
    r[0] = 0xF0; r[1] = 0x00; r[2] = 0x20; r[3] = 0x29; r[4] = 0x00; r[5] = 0x60; r[6] = cmd;
}

static void send_status(struct state *s)
{
    for (int i = 0; i < 20; i++)
        s->reply[i] = 0;
    header(s->reply, 0x7F);
    s->reply[7] = s->mode;
    s->reply[8] = (uint8_t)(1 | owns(s) << 1);
    put7(s->reply + 9, ((volatile uint32_t *)HEAP_CTL)[1], 5);
    put7(s->reply + 14, s->frames, 4);
    s->reply[18] = (uint8_t)mod_check();
    s->reply[19] = 0xF7;
    s->reply_msg[0] = (uint32_t)s->reply;
    s->reply_msg[1] = 20;
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, s->reply_msg);
}

static int dump(const uint8_t *m, uint32_t len)
{
#if DUMP_ALLOW
    /* Arrhythmia 0.1: an explicit allow-list, the module SDK's ITCM (MOD_RAM 0x100 up to ITCM_END: module RAM and
     * code; tools/module.py reads 0x400.. back). Internal flash (bootloader 0x08000000..,
     * stock application 0x08010000.., our patch area and the relocated DSP stream) and everything else is refused:
     * reply F0 00 20 29 00 60 7D <the request's 5 address bytes> F7 (13 bytes, no data), nothing is read. */
    if (len != 13)
        return 0;
    uint32_t addr = 0;
    for (int i = 7; i < 12; i++) {
        if (m[i] > 0x7F)
            return 0;
        addr = (addr << 7) | m[i];
    }
    if (m[7] > 0x0F || addr % CHUNK || addr < DUMP_LO || addr >= ITCM_END || !itcm_ok()) {
        volatile uint8_t n[13];
        header(n, 0x7D);
        for (int i = 7; i < 12; i++)
            n[i] = m[i];
        n[12] = 0xF7;
        uint32_t no[2] = {(uint32_t)n, sizeof n};
        TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, no);
        return 1;
    }
#else
    if (len != 13 || m[7] > 0x0F)
        return 0;
    uint32_t addr = 0;
    for (int i = 7; i < 12; i++) {
        if (m[i] > 0x7F)
            return 0;
        addr = (addr << 7) | m[i];
    }
    if (addr % CHUNK || !((addr >= FLASH_START && addr < FLASH_END) || (addr < ITCM_END && itcm_ok())))
        return 0;
#endif
    volatile uint8_t r[12 + 2 * CHUNK + 1];
    header(r, 0x7D);
    put7(r + 7, addr, 5);
    const volatile uint8_t *src = (const volatile uint8_t *)addr;
    for (uint32_t i = 0; i < CHUNK; i++) {
        uint8_t b = src[i];
        r[12 + 2 * i] = b >> 4;
        r[13 + 2 * i] = b & 0x0F;
    }
    r[12 + 2 * CHUNK] = 0xF7;
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
    return 1;
}

#if !BENCH_METER_READ                   /* BENCH_METER_READ: no general cmd 08 read in the build (q0_meter instead) */
static void dsp_read(const uint8_t *m, uint32_t len)
{
    volatile uint8_t r[14 + 6 * DSP_MAX_WORDS + 1];
    uint32_t sel = 0xFF, addr = 0, n = 0, ok = 0;
    header(r, 0x08);
    for (int i = 7; i < 12; i++)
        r[i] = 0;
    if (len == 13) {                        /* message bytes beyond len are never read */
        sel = m[7];
        n = m[11];
        for (int i = 8; i < 11; i++)
            addr = (addr << 7) | (m[i] & 0x7F);
        for (int i = 7; i < 12; i++)
            r[i] = m[i] & 0x7F;
    }
    /* internal RAM per core/space: core 0 X 0x6000 / Y 0x4000, core 1 X 0x3000 / Y 0x2000
     * words; the DSP prefetches the word after the last one read, so addr + n must stay below the capacity */
    uint32_t cap = (sel & 2) ? ((sel & 1) ? 0x2000u : 0x3000u) : ((sel & 1) ? 0x4000u : 0x6000u);
    if (len == 13 && sel <= 3 && m[8] <= 0x7F && m[9] <= 0x7F && m[10] <= 0x7F && n >= 1 && n <= DSP_MAX_WORDS
            && addr + n < cap
#ifdef CMD08_METER
            /* only the DSP meter's X windows (docs/user/developers.md; tools/dspmeter.py CORES ring .. x_end):
             * core 0 X:$5C00..$5E3F, core 1 X:$2C00..$2E3F. Y and every other X address are refused like a
             * malformed request (status 0, nothing touched). */
            && !(sel & 1) && addr >= ((sel & 2) ? 0x2C00u : 0x5C00u) && addr + n <= ((sel & 2) ? 0x2E40u : 0x5E40u)
#endif
#ifdef NO_CMD08
            /* firmware profile: no DSP reads at all. Every 08 gets the defined refusal (F0 00 20 29 00 60 08
             * <5 echo bytes> 00 F7, status 0, as a malformed request); the read path is compiled out */
            && 0
#endif
            ) {
        uint32_t sem = DSP_LOCK;
        SEM_TAKE(DSP_LOCK);
        for (uint32_t i = 0; i < n; i++) {
            uint32_t w = DSP_READ(sel >> 1, (addr + i) | ((sel & 1) << 15)) >> 8;
            for (int k = 0; k < 6; k++)
                r[13 + 6 * i + k] = (w >> (20 - 4 * k)) & 0x0F;
        }
        *(volatile uint32_t *)DSP_CACHE = 0xFFFFFFFFu;
        SEM_GIVE(&sem);
        ok = 1;
    } else
        n = 0;
    r[12] = (uint8_t)ok;
    r[13 + 6 * n] = 0xF7;
    uint32_t out[2] = {(uint32_t)r, 14 + 6 * n};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}
#endif

/* A module that can take input is running. Caller holds the scheduler suspension. */
static uint32_t takes_input(const struct state *s)
{
    return s->mode == 3 && s->mod_frame && s->mod_input;
}

/* The mode is in force. */
static uint32_t owns(const struct state *s)
{
    return s->owned && takes_input(s);
}

/* Module lifecycle ends (stop, restart, upload, mode change, frame() returned 0): presses that went to it become
 * tombstones, so their releases reach neither the stock UI nor a later module; ownership is dropped. */
static void retire(struct state *s)
{
    s->pad_eat |= s->pad_mod;
    s->btn_eat |= s->btn_mod;
    s->pad_mod = s->btn_mod = 0;
    s->owned = 0;
    CLK->mod_clock = 0;                     /* no clock entry runs any more; a new start begins stopped */
    CLK->running = 0;
    CLK->pulses = 0;
    s->q_head = s->q_tail;                  /* queued notes of the ended module never play */
    s->q_kill = 0xFF;                       /* release what it left held, before any entry of a new instance */
    for (uint32_t t = 0; t < 8; t++)
        s->kill_at[t] = s->q_tail;
}

/* The loader is reading the folder or loading a file: a FatFs object of ours may be open, MOD_RAM is in use. */
static uint32_t ld_busy(const struct state *s)
{
    return s->ld_phase == LD_SCAN || s->ld_phase == LD_LOAD;
}

/* Open the picker (gesture with nothing running, cmd 06 00): mode 4, folder read requested. From idle the picker
 * data is initialised; otherwise (a cancelled read or load is still being closed, or the picker is open) a restart
 * is requested and the loader closes whatever it holds before reading the folder again. Caller holds the
 * suspension; ITCM checked by the caller. */
static void picker_open(struct state *s)
{
    volatile struct pick *p = PICK;
    retire(s);
    s->mod_frame = 0;
    if (s->ld_phase == LD_IDLE) {
        for (volatile uint32_t *w = (volatile uint32_t *)MOD_RAM; w < (volatile uint32_t *)MOD_BASE; w++)
            *w = 0;                         /* obj = OBJ_NONE: nothing of ours is open while idle */
    } else
        p->restart = 1;
    p->count = 0;
    p->pick = 0;
    p->result = R_NONE;
    p->bad = 0;
    s->ld_phase = LD_SCAN;
    s->mode = MODE_PICK;
}

/* A pad in the picker (pad press, cmd 06 01): pad 0 copies the built-in and starts it at once (flash, no FatFs);
 * pad 1..count asks the loader for that file. Only while choosing. Caller holds the suspension. */
static void picker_pad(struct state *s, uint32_t pad)
{
    volatile struct pick *p = PICK;
    if (s->mode != MODE_PICK || s->ld_phase != LD_PICK || !itcm_ok())
        return;
    if (pad == 0) {
        if (builtin_copy()) {
            s->ld_fresh = 1;
            start(s);
        } else {
            p->result = R_BAD;
            p->bad = 1;                     /* failed pad + 1 */
        }
        return;
    }
    if (pad > p->count)
        return;
    s->ld_fresh = 0;                        /* the module area is about to be overwritten */
    p->pick = (uint8_t)pad;
    p->result = R_NONE;
    p->bad = 0;
    p->off = 0;
    p->size = 0;
    s->ld_phase = LD_LOAD;
}

/* Shift + Sample Rec: stop the running module, close the picker, or open it. Returns 0 when ITCM is unavailable,
 * so the combination keeps its stock meaning. Caller holds the suspension. */
static uint32_t gesture(struct state *s)
{
    if (s->mode == 3 && s->mod_frame) {
        retire(s);
        s->mod_frame = 0;
        s->mode = 0;
        return 1;
    }
    if (s->mode == MODE_PICK) {
        s->mode = 0;                        /* the loader closes what it holds and goes idle */
        return 1;
    }
    if (!itcm_ok())
        return 0;
    picker_open(s);
    return 1;
}

static void deliver(struct state *s, uint32_t kind, uint32_t index, uint32_t value)
{
    if (takes_input(s))                     /* always true while a mod-mask bit is set: retire() clears them */
        ((mod_input_fn)s->mod_input)(s->leds, kind, index, value);
}

/* A release (or pad pressure) follows its press: module, tombstone, or stock. Returns 1 if swallowed. */
static uint32_t follow(struct state *s, uint32_t *mod, uint32_t *eat, uint32_t bit, uint32_t keep,
                       uint32_t kind, uint32_t index, uint32_t value)
{
    if (*mod & bit) {
        if (!keep)
            *mod &= ~bit;
        deliver(s, kind, index, value);
        return 1;
    }
    if (*eat & bit) {
        if (!keep)
            *eat &= ~bit;
        return 1;
    }
    return 0;
}

/* Decide where one event goes; returns 1 if the stock UI must not see it. Caller holds the suspension. */
static uint32_t route(struct state *s, uint32_t kind, uint32_t index, uint32_t value)
{
    uint32_t bit = index < 32 ? 1u << index : 0;
    if (kind == EV_BUTTON) {
        uint32_t down = value != 0;
        if (index == BTN_SHIFT) {
            s->shift = (uint8_t)down;
            return 0;                       /* Shift always reaches the stock UI, so it can never stick there */
        }
        if (!down)
            return follow(s, &s->btn_mod, &s->btn_eat, bit, 0, kind, index, value);
#ifdef SYNTHPAGE
        if (pg_button(s, index, bit))       /* Shift + Track 8, and every button but Play/Rec while the page is open */
            return 1;
#endif
        if (index == BTN_SAMPLE_REC && s->shift && gesture(s)) {
            s->btn_eat |= bit;              /* the gesture is nobody's: its release is swallowed, never delivered */
            return 1;
        }
        if (s->mode == MODE_PICK) {         /* the picker hides the buttons; their releases are swallowed */
            s->btn_eat |= bit;
            return 1;
        }
        if (!owns(s) || !bit)
            return 0;
        s->btn_mod |= bit;
        deliver(s, kind, index, value);
        return 1;
    }
    if (kind == EV_PAD) {
        if (!(value >> 8))
            return follow(s, &s->pad_mod, &s->pad_eat, bit, 0, kind, index, value);
#ifdef SYNTHPAGE
        if (pg_pad_press(s, index, bit))
            return 1;
#endif
        if (s->mode == MODE_PICK) {         /* picker press: its pressure and release are swallowed */
            s->pad_eat |= bit;
            if (bit)
                picker_pad(s, index);
            return 1;
        }
        if (!owns(s) || !bit)
            return 0;
        s->pad_mod |= bit;
        deliver(s, kind, index, value);
        return 1;
    }
    if (kind == EV_PRESSURE)
        return follow(s, &s->pad_mod, &s->pad_eat, bit, 1, kind, index, value);
#ifdef SYNTHPAGE
    if (kind == EV_ENCODER && pg_encoder(s, index, value))
        return 1;
#endif
    if (kind == EV_ENCODER && owns(s)) {
        deliver(s, kind, index, value);
        return 1;
    }
    if (kind == EV_ENCODER && s->mode == MODE_PICK)
        return 1;
    return 0;                               /* pots, and everything while the mode is off */
}

/* Log the event and route it; returns 1 if it is swallowed. */
static uint32_t event(uint32_t kind, uint32_t index, uint32_t value)
{
    struct state *s = region();
    if (!s)
        return 0;
    SUSPEND_ALL();
    s->ev[s->ev_seq % EV_SLOTS] = kind | (index & 0xFF) << 8 | (value & 0xFFFF) << 16;
    s->ev_seq++;
    uint32_t swallow = route(s, kind, index, value);
    RESUME_ALL();
    return swallow;
}

void hook_pad(uint32_t ctx, uint32_t pad, uint32_t velocity, uint32_t on)
{
    if (!event(EV_PAD, pad, (velocity & 0xFF) | (on ? 0x100 : 0)))
        STOCK_PAD(ctx, pad, velocity, on);
}

void hook_button(uint32_t ctx, uint32_t code, uint32_t down, uint32_t d)
{
    if (!event(EV_BUTTON, code, down))
        STOCK_BUTTON(ctx, code, down, d);
}

void hook_pot(uint32_t ctx, uint32_t which, uint32_t value, uint32_t d)
{
    event(3, which, value);
    STOCK_POT(ctx, which, value, d);
}

void hook_pressure(uint32_t ctx, uint32_t pad, uint32_t value, uint32_t d)
{
    if (!event(EV_PRESSURE, pad, value))
        STOCK_PRESSURE(ctx, pad, value, d);
}

void hook_encoder(uint32_t ctx, uint32_t knob, uint32_t delta, uint32_t d)
{
    if (!event(EV_ENCODER, knob, delta))
        STOCK_ENCODER(ctx, knob, delta, d);
}

static void input_reply(struct state *s, uint32_t page)
{
    volatile uint8_t r[13 + 8 * 16 + 1];
    uint32_t ev[16], seq;
    SUSPEND_ALL();
    seq = s->ev_seq;
    for (uint32_t k = 0; k < 16; k++)
        ev[k] = s->ev[16 * page + k];
    RESUME_ALL();
    header(r, 0x09);
    r[7] = (uint8_t)page;
    put7(r + 8, seq, 5);
    for (uint32_t k = 0; k < 16; k++)
        for (int n = 0; n < 8; n++)
            r[13 + 8 * k + n] = (ev[k] >> (28 - 4 * n)) & 0x0F;
    r[13 + 8 * 16] = 0xF7;
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}

static void heap_reply(void)
{
    volatile uint32_t *ctl = (volatile uint32_t *)HEAP_CTL;
    volatile uint8_t *r = (volatile uint8_t *)REPLY_BUF;
    uint32_t limit = ctl[0], cursor = ctl[1];
    header(r, 0x7E);
    put7(r + 7, limit, 5);
    put7(r + 12, cursor, 5);
    r[17] = 0xF7;
    uint32_t out[2] = {REPLY_BUF, 18};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}

/* cmd 06 reply: F0 00 20 29 00 60 06 <phase> <mode> <fresh> <count> <pick> <result> <bad> F7 */
static void loader_reply(struct state *s)
{
    volatile uint8_t r[15];
    header(r, 0x06);
    SUSPEND_ALL();
    uint32_t phase = s->ld_phase, ok = phase != LD_IDLE && itcm_ok();
    const volatile struct pick *p = PICK;
    r[7] = (uint8_t)phase;
    r[8] = s->mode;
    r[9] = s->ld_fresh;
    r[10] = ok ? p->count & 0x7F : 0;
    r[11] = ok ? p->pick & 0x7F : 0;
    r[12] = ok ? p->result & 0x7F : 0;
    r[13] = ok ? p->bad & 0x7F : 0;
    RESUME_ALL();
    r[14] = 0xF7;
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}

/* cmd 06 03 i: the 8.3 name of pad i + 1, only while choosing. */
static void name_reply(struct state *s, uint32_t i)
{
    volatile uint8_t r[9 + NAME_LEN - 1 + 1];
    const volatile struct pick *p = PICK;
    uint32_t ok;
    header(r, 0x06);
    r[7] = 0x03;
    r[8] = (uint8_t)i;
    SUSPEND_ALL();
    ok = s->ld_phase == LD_PICK && itcm_ok() && i < p->count;
    for (uint32_t k = 0; k < NAME_LEN - 1; k++)
        r[9 + k] = ok ? p->name[i][k] & 0x7F : 0;
    RESUME_ALL();
    r[9 + NAME_LEN - 1] = 0xF7;
    if (!ok)
        return;
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}

/* SysEx cmd 06 (see the header): drives the picker through the same functions as the gesture and the pads. */
static void loader_cmd(struct state *s, const uint8_t *m, uint32_t len)
{
    uint32_t sub = len >= 9 ? m[7] : 0xFF;
    if (sub == 0x03) {
        if (len == 10 && m[8] < PADS - 1)
            name_reply(s, m[8]);
        return;
    }
    if (!((len == 9 && (sub == 0x00 || sub == 0x02 || sub == 0x7F)) || (len == 10 && sub == 0x01 && m[8] < PADS)))
        return;
    SUSPEND_ALL();
    if (sub == 0x00 && !(s->mode == 3 && s->mod_frame) && s->mode != MODE_PICK && itcm_ok())
        picker_open(s);
    else if (sub == 0x01)
        picker_pad(s, m[8]);
    else if (sub == 0x02 && s->mode == MODE_PICK)
        s->mode = 0;
    RESUME_ALL();
    loader_reply(s);
}

void hook_sysex(uint32_t a, uint32_t *msg, uint32_t c)
{
    const uint8_t *m = (const uint8_t *)msg[0];
    uint32_t len = msg[1];
    if (len < 8 || m[0] != 0xF0 || m[1] != 0x00 || m[2] != 0x20 || m[3] != 0x29 || m[4] != 0x00 || m[5] != 0x60
            || m[len - 1] != 0xF7) {
        ORIG_SYSEX(a, msg, c);
        return;
    }
    uint8_t cmd = m[6];
    if (cmd == 0x7D) {
        if (!dump(m, len))
            ORIG_SYSEX(a, msg, c);
        return;
    }
    if (cmd == 0x08) {                      /* the only cmd 08 branch of the dispatch */
#if BENCH_METER_READ
        q0_meter(m, len);
#else
        dsp_read(m, len);
#endif
        return;
    }
    if (cmd == 0x7E && len == 8) {
        heap_reply();
        return;
    }
    struct state *s = region();
    if (!s)
        return;                             /* disabled: our commands are swallowed silently, as in lightshow v4 */
    if (cmd == 0x7F) {
        send_status(s);
        return;
    }
    if (cmd == 0x0A) {
        if (len == 9 && m[7] <= 1) {
            SUSPEND_ALL();
            s->owned = m[7] && takes_input(s);    /* off: held controls still follow their press */
            RESUME_ALL();
            send_status(s);
        }
        return;
    }
    if (cmd == 0x09) {
        if (len == 9 && m[7] <= 1)
            input_reply(s, m[7]);
        return;
    }
    if (cmd == 0x06) {
        loader_cmd(s, m, len);
        return;
    }
    if (cmd == 0x0B) {
        if (len == 8)
            clock_reply();
        return;
    }
#ifdef TRACKMODES
    if (cmd == 0x0C) {
        tm_sysex(s, m, len);
        return;
    }
#endif
#if SCALES
    if (cmd == 0x0E) {
        sc_sysex(s, m, len);
        return;
    }
#endif
#ifdef O1WAIT
    if (cmd == 0x0D) {
        o1_sysex(m, len);
        return;
    }
#endif
#if CORE0_BENCH
    if (cmd == 0x0F) {
        q0_telem(m, len);
        return;
    }
#endif
    if (cmd > 0x05)
        return;
    uint32_t started = 0;
    SUSPEND_ALL();
    /* retire() only in accepted branches, right before the module is really stopped, replaced or restarted:
     * a rejected or malformed command leaves the running module, its ownership and its held presses alone */
    if (cmd == 0x00) {
        retire(s);
        s->mode = 0;
    } else if (cmd == 0x01) {
        retire(s);
        if (len >= 10) {
            s->density = m[7];
            s->decay = m[8] ? m[8] : 1;
        }
        s->mode = 1;
    } else if (cmd == 0x02 && len == 9) {
        uint8_t k = m[7];
        if (k <= 7 || k == 0x7F) {
            retire(s);
            for (uint32_t i = 0; i < LEDS; i++)
                s->leds[i] = (k == 0x7F || ((i >> k) & 1)) ? 0xFE : 0;
            s->mode = 2;
        }
    } else if (cmd == 0x03 && len >= 11) {
        retire(s);
        uint32_t idx = ((uint32_t)(m[7] & 0x7F) << 7) | (m[8] & 0x7F);
        for (uint32_t i = 9; i + 1 < len; i++, idx++)
            if (idx < LEDS)
                s->leds[idx] = (uint8_t)((m[i] & 0x7F) << 1);
        s->mode = 2;
    } else if (cmd == 0x04 && len >= 13 && (len - 11) % 2 == 0) {
        uint32_t off = ((uint32_t)(m[7] & 0x7F) << 14) | ((uint32_t)(m[8] & 0x7F) << 7) | (m[9] & 0x7F);
        uint32_t n = (len - 11) / 2;
        if (off + n <= MOD_MAX && itcm_ok() && !ld_busy(s)) {
            retire(s);
            if (s->mode == 3 || s->mode == MODE_PICK)
                s->mode = 0;                /* never write code that may be running; a choosing picker closes */
            s->mod_frame = 0;
            volatile uint8_t *dst = (volatile uint8_t *)(MOD_BASE + off);
            for (uint32_t i = 0; i < n; i++)
                dst[i] = (uint8_t)(((m[10 + 2 * i] & 0x0F) << 4) | (m[11 + 2 * i] & 0x0F));
            s->ld_fresh = 1;                /* written this boot: cmd 05 may start it */
        }
    } else if (cmd == 0x05 && len == 8) {
        if (s->ld_fresh && !ld_busy(s))
            start(s);                       /* like the picker: ownership on for an input module */
        started = 1;                        /* refused: nothing changed, status still sent */
    }
    RESUME_ALL();
    if (started)
        send_status(s);
}

/* Module ABI (host table word 2): queue a note on (velocity 1..127) or off (0) for track 0..7 at engine note `note`.
 * Called from module callbacks with the scheduler suspended; touches only the ring, never stock code. */
static uint32_t note_q(uint32_t track, uint32_t note, uint32_t velocity, uint32_t type)
{
    struct state *s = region();
    if (!s || track > 7 || note > 127 - 12)
        return 0;
    if (velocity > 127)
        velocity = 127;
    uint32_t used = (uint8_t)(s->q_tail - s->q_head);
    if (used + (velocity ? 1 + Q_OFF_RESERVE : 1) > QLEN) {
        s->q_drop++;
        if (!velocity) {                    /* never lose an off: release the track after its queued notes; a pending
                                               kill on t moves to the tail (lossy latest-tail policy, see header) */
            s->q_kill |= (uint8_t)(1u << track);
            s->kill_at[track] = s->q_tail;
        }
        return 0;
    }
    s->q[s->q_tail % QLEN] = track | note << 8 | velocity << 16 | type << 24;
    s->q_tail++;
    return 1;
}

uint32_t host_note(uint32_t track, uint32_t note, uint32_t velocity)
{
    return note_q(track, note, velocity, T_NOTE);
}

/* Module ABI 3 (host table word 4): a note through router port t (rec != 0, front panel: engine, MIDI out, recorder)
 * or port 0x18 + t (rec == 0, as host_note). Scheduler suspended; touches only the ring. */
uint32_t host_note_port(uint32_t track, uint32_t note, uint32_t velocity, uint32_t rec)
{
    return note_q(track, note, velocity, rec ? T_PORT : T_NOTE);
}

/* Append one non-note entry if `need` slots are free; counts a refusal in q_drop. Caller holds the suspension. */
static uint32_t enqueue(struct state *s, uint32_t e, uint32_t need)
{
    if ((uint8_t)(s->q_tail - s->q_head) + need > QLEN) {
        s->q_drop++;
        return 0;
    }
    s->q[s->q_tail % QLEN] = e;
    s->q_tail++;
    return 1;
}

/* Module ABI 3 (word 5): queue channel parameter idx (0..24) of track t := val (0..127, stored encoding). */
uint32_t host_param(uint32_t track, uint32_t idx, uint32_t val)
{
    struct state *s = region();
    if (!s || track > 7 || idx >= CH_PARAMS || val > 127)
        return 0;
    return enqueue(s, track | idx << 8 | val << 16 | T_PARAM << 24, 1 + Q_OFF_RESERVE);
}

/* Module ABI 3 (word 6): the Main task's snapshot of the selected track and grid view (0 = unknown). */
uint32_t host_ui(void)
{
    struct state *s = region();
    return s ? s->ui : 0;
}

/* Module ABI 3 (word 7): queue a channel voice message status | d1 << 8 | d2 << 16 for the stock MIDI outputs. */
uint32_t host_midi(uint32_t msg)
{
    struct state *s = region();
    uint32_t st = msg & 0xFF, d1 = (msg >> 8) & 0xFF, d2 = (msg >> 16) & 0xFF, hi = st >> 4;
    if (!s || (msg >> 24) || st < 0x80 || st >= 0xF0 || d1 > 0x7F || d2 > 0x7F || ((hi == 0xC || hi == 0xD) && d2))
        return 0;
    uint32_t off = hi == 0x8 || (hi == 0x9 && !d2);
    return enqueue(s, msg | T_MIDI << 24, off ? 1 : 1 + Q_OFF_RESERVE);
}

/* The word at p + off, or 0 (never a valid link) unless p is word aligned and the whole word lies in SRAM. */
static uint32_t link(uint32_t p, uint32_t off)
{
    if ((p & 3) || (off & 3) || p < SRAM_START || p >= SRAM_END || off > SRAM_END - 4 - p)
        return 0;
    return *(volatile uint32_t *)(p + off);
}

/* The note router, or 0 if a link is not readable SRAM or the object check fails. */
static uint32_t router(void)
{
    uint32_t p = link(DISP_PTR, 0);
    p = link(p, 0);
    p = link(p, 0x24);
    p = link(p, 4);
    p = link(p, 4);
    return link(p, 0) == ROUTER_VT ? p : 0;
}

/* kill(t) is due once the head has reached kill_at[t]: no entry queued before it is still in the ring. An index
 * outside (head, tail] (behind the head after a flush or a drop) is due as well. Caller holds the suspension. */
static uint32_t kill_due(const struct state *s, uint32_t t)
{
    uint32_t d = (uint8_t)(s->kill_at[t] - s->q_head);
    return ((s->q_kill >> t) & 1) && (d == 0 || d > (uint8_t)(s->q_tail - s->q_head));
}

/* Router unusable: drop queued note ons, params and MIDI messages, turn queued note offs (either port) into kills,
 * keep existing kills and sounding[]. */
static void drain_refused(struct state *s)
{
    SUSPEND_ALL();
    while (s->q_head != s->q_tail) {
        uint32_t e = s->q[s->q_head % QLEN];
        s->q_head++;
        if ((e >> 24) <= T_PORT && !((e >> 16) & 0x7F))
            s->q_kill |= (uint8_t)(1u << (e & 7));
        s->q_bad++;
    }
    for (uint32_t t = 0; t < 8; t++) {
        s->kill_at[t] = s->q_tail;          /* the ring is empty: every kill is due */
        if (!s->sounding[t])
            s->q_kill &= (uint8_t)~(1u << t);   /* nothing held there: nothing to release */
    }
    RESUME_ALL();
}

#define ACT_NONE    0u
#define ACT_ON      1u
#define ACT_OFF     2u
#define ACT_PARAM   3u
#define ACT_MIDI    4u

/* The channel parameter group of track t, or 0 unless the engine and the group check out (object checks, a store
 * wholly in SRAM). */
static uint32_t group(uint32_t t)
{
    uint32_t eng = link(ENGINE_PTR, 0);
    if (t > 7 || link(eng, 0) != ENGINE_VT)
        return 0;
    uint32_t g = link(eng, 0x1E4 + 4 * t);
    if (link(g, 0) != GROUP_VT || link(g, 4) != CH_DESC || (link(g, 8) & 0xFF) != 0x10 + t
            || link(g, 0x10) != CH_STORE)
        return 0;
    uint32_t bytes = link(link(g, 0xC), 0);
    if (bytes < SRAM_START || bytes > SRAM_END - CH_STORE)
        return 0;
    return g;
}

/* The MIDI transmitter if it checks out, else 0. */
static uint32_t transmitter(void)
{
    uint32_t tx = link(TX_PORT_PTR, 0);
    uint32_t n = link(tx, 0x18) & 0xFF, ports = link(tx, 0x14);
    if (n < 1 || n > 8)
        return 0;
    for (uint32_t i = 0; i < n; i++)
        if (link(link(ports, 4 * i), 0) != TX_PORT_VT)
            return 0;
    return tx;
}

static void count_bad(struct state *s)
{
    SUSPEND_ALL();
    s->q_bad++;
    RESUME_ALL();
}

/* The router port a held note on t was played on. */
static uint32_t held_port(const struct state *s, uint32_t t)
{
    return ((s->sport >> t) & 1) ? PORT_PAD + t : PORT_ENGINE + t;
}

/* Drain the note ring (Main task, scheduler running). */
static void drain(struct state *s)
{
    if (s->q_head == s->q_tail && !s->q_kill)
        return;                             /* unlocked peek: work missed here is picked up next pass */
    uint32_t r = router();
    if (!r) {
        drain_refused(s);
        return;
    }
    const uint32_t *vt = (const uint32_t *)ROUTER_VT;
    for (uint32_t calls = 0; calls < QCALLS;) {
        uint32_t act = ACT_NONE, t = 8, note = 0, vel = 0, more = 0, port = 0, kind = 0, e = 0;
        SUSPEND_ALL();
        for (uint32_t k = 0; k < 8; k++)
            if (kill_due(s, k)) {
                t = k;
                break;
            }
        if (t < 8) {                        /* a due release first */
            s->q_kill &= (uint8_t)~(1u << t);
            more = 1;
            if (s->sounding[t]) {
                act = ACT_OFF;
                note = s->sounding[t] - 1u;
                port = held_port(s, t);
            }
        } else if (s->q_head != s->q_tail) {
            e = s->q[s->q_head % QLEN];
            more = 1;
            if ((e >> 24) >= T_PARAM) {
                s->q_head++;
                act = (e >> 24) == T_PARAM ? ACT_PARAM : ACT_MIDI;
            } else {
                t = e & 7;
                note = (e >> 8) & 0x7F;
                vel = (e >> 16) & 0x7F;
                kind = (e >> 24) == T_PORT;
                if (vel && s->sounding[t] && (s->sounding[t] != note + 1 || ((s->sport >> t) & 1) != kind)) {
                    act = ACT_OFF;          /* one pitch per track: release the held one first, the on stays queued */
                    note = s->sounding[t] - 1u;
                    vel = 0;
                    port = held_port(s, t);
                } else {
                    s->q_head++;
                    if (vel) {
                        act = ACT_ON;
                        port = kind ? PORT_PAD + t : PORT_ENGINE + t;
                    } else if (s->sounding[t] == note + 1) {
                        act = ACT_OFF;      /* an off releases only the held pitch, on the port it was played on */
                        port = held_port(s, t);
                    }
                }
            }
        }
        RESUME_ALL();
        if (!more)
            break;
        if (act == ACT_NONE)
            continue;                       /* popped without a call: no budget used */
        if (act == ACT_PARAM) {
            uint32_t g = group(e & 7);
            if (!g) {
                count_bad(s);
                continue;
            }
            PARAM_SET(g, (e >> 8) & 0xFF, (e >> 16) & 0x7F);
#ifdef PLOCK
            pl_host(e & 7, (e >> 8) & 0xFF);    /* a held p-lock lane takes the module's value as its base */
#endif
        } else if (act == ACT_MIDI) {
            if (!transmitter()) {
                count_bad(s);
                continue;
            }
            volatile uint8_t m[4] = {(uint8_t)e, (uint8_t)(e >> 8), (uint8_t)(e >> 16), 0};
            MIDI_OUT(TX_MASK, m);           /* the port write copies the three bytes before it returns */
        } else {
            if (act == ACT_ON)
                ((note_on_fn)vt[0])(r, note + 12, vel, port, 0);
            else
                ((note_off_fn)vt[1])(r, note + 12, port, 0);
            s->sounding[t] = act == ACT_ON ? (uint8_t)(note + 1) : 0;
            if (act == ACT_ON && kind)
                s->sport |= (uint8_t)(1u << t);
            else
                s->sport &= (uint8_t)~(1u << t);
        }
        calls++;
    }
}

/* host_ui() snapshot (Main task, top of each pass): the grid page of the UI controller through checked links. */
static void ui_snap(struct state *s)
{
    uint32_t v = 0;
    if (link(BTN_CELL, 0) == BTN_LAMBDA) {
        uint32_t ctrl = link(link(BTN_CELL, 4), 0);
        uint32_t grid = link(ctrl, 0x3C);
        if (link(ctrl, 0) == CTRL_VT && link(grid, 0) == GRID_VT) {
            uint32_t obs = link(grid, 0x25C);
            uint32_t buf = link(obs, 0xC + 4 * (link(obs, 4) & 1));
            uint32_t track = buf >= SRAM_START && buf < SRAM_END ? *(volatile uint8_t *)buf : 0xFF;
            if (track < 8)
                v = track | (link(grid, 0x264) & 0xFF) << 8 | (uint32_t)(link(ctrl, 0x7C) == grid) << 16 | 1u << 31;
        }
    }
    s->ui = v;
}

/* ---- loader (Main task, from hook_main; see the header) ------------------------------------------------------- */

/* Drive 1: for a call: 1 ready (card initialised, volume mounted, window clean), 2 window dirty (wait: no call of ours
 * may make FatFs write a sector back), 0 no card or not mounted. */
static uint32_t fs_state(void)
{
    if (*(volatile uint8_t *)(SD_OBJ + 0x18) != 2)
        return 0;
    uint32_t fs = link(FATFS_1, 0);
    uint32_t w = link(fs, 0);               /* fs_type, pdrv, n_fats, wflag */
    if (!(w & 0xFF))
        return 0;
    return (w >> 24) ? 2 : 1;
}

/* The shared file object's FIL, only while it is closed; with mine, only while the FIL still holds our read-only
 * open (flag exactly FA_READ). 0 = not available. */
static uint32_t file_b(uint32_t mine)
{
    uint32_t b = link(FILE_B_PTR, 0);
    if (link(b, 0) != FILE_VT || (link(b, 0xC) & 0xFF))
        return 0;
    uint32_t fil = link(b, 8);
    if ((fil & 3) || fil < SRAM_START || fil > SRAM_END - 0x1028u)
        return 0;
    if (mine && (link(fil, FIL_FLAG) & 0xFF) != FA_READ)
        return 0;
    return fil;
}

/* "1:/MODULES" at d (11 bytes); returns its length. Plain stores: the hook code has no data sections. */
static uint32_t dir_path(volatile char *d)
{
    d[0] = '1'; d[1] = ':'; d[2] = '/'; d[3] = 'M'; d[4] = 'O'; d[5] = 'D'; d[6] = 'U'; d[7] = 'L'; d[8] = 'E';
    d[9] = 'S'; d[10] = 0;
    return 10;
}

/* The 8.3 name of the entry in fi if it is a module file: not a directory, label, system or hidden entry, size
 * within [header + 2, MOD_MAX], short name ending in ".MOD" made of characters valid in a path segment. The short
 * name is altname, or fname when FatFs left altname empty (no long name: fname is then the short name). */
static uint32_t mod_name(const volatile uint8_t *fi, volatile char *out)
{
    uint32_t size = fi[0] | fi[1] << 8 | fi[2] << 16 | (uint32_t)fi[3] << 24;
    if ((fi[FI_ATTR] & AM_SKIP) || size < HDR1 + 2 || size > MOD_MAX)
        return 0;
    const volatile uint8_t *n = fi[FI_ALT] ? fi + FI_ALT : fi + FI_NAME;
    uint32_t len = 0;
    for (; len < NAME_LEN && n[len]; len++) {
        uint8_t c = n[len];
        if (c <= 0x20 || c >= 0x7F || c == '/' || c == '\\' || c == ':' || c == '*' || c == '?' || c == '"'
                || c == '<' || c == '>' || c == '|')
            return 0;
    }
    if (len < 5 || len >= NAME_LEN || n[len - 4] != '.' || n[len - 3] != 'M' || n[len - 2] != 'O'
            || n[len - 1] != 'D')
        return 0;
    for (uint32_t i = 0; i < NAME_LEN; i++)
        out[i] = i < len ? (char)n[i] : 0;
    return 1;
}

static int name_cmp(const volatile char *a, const volatile char *b)
{
    for (uint32_t i = 0; i < NAME_LEN; i++)
        if (a[i] != b[i] || !a[i])
            return (int)(uint8_t)a[i] - (int)(uint8_t)b[i];
    return 0;
}

/* Keep the 31 smallest names, ascending. */
static void name_insert(volatile struct pick *p, const volatile char *nm)
{
    uint32_t n = p->count, i = n;
    while (i > 0 && name_cmp(nm, p->name[i - 1]) < 0)
        i--;
    if (i >= PADS - 1)
        return;
    uint32_t last = n < PADS - 1 ? n : PADS - 2;
    for (uint32_t k = last; k > i; k--)
        for (uint32_t j = 0; j < NAME_LEN; j++)
            p->name[k][j] = p->name[k - 1][j];
    for (uint32_t j = 0; j < NAME_LEN; j++)
        p->name[i][j] = nm[j];
    if (n < PADS - 1)
        p->count = (uint8_t)(n + 1);
}

static void scan_done(struct state *s, volatile struct pick *p, uint32_t result)
{
    SUSPEND_ALL();
    p->result = (uint8_t)result;
    if (s->ld_phase == LD_SCAN)
        s->ld_phase = LD_PICK;
    RESUME_ALL();
}

/* Folder read: f_opendir, then one f_readdir per pass, f_closedir at the end (or on any error). */
static void scan_step(struct state *s, volatile struct pick *p)
{
    uint32_t st = fs_state();
    if (st == 2)
        return;
    if (p->obj == OBJ_NONE) {
        if (!st) {
            scan_done(s, p, R_NOCARD);
            return;
        }
        volatile char path[12];
        dir_path(path);
        uint32_t r = F_OPENDIR((uint32_t)p->dir, (const char *)path);
        if (r) {
            scan_done(s, p, r == FR_NO_PATH || r == FR_NO_FILE ? R_NOFOLDER : R_DIRERR);
            return;
        }
        p->obj = OBJ_DIR;
        p->seen = 0;
        return;
    }
    const volatile uint8_t *fi = (const volatile uint8_t *)p->fi;
    uint32_t r = st ? F_READDIR((uint32_t)p->dir, (uint32_t)p->fi) : 1;
    if (r || !fi[FI_NAME] || ++p->seen > SCAN_MAX) {
        F_CLOSEDIR((uint32_t)p->dir);
        p->obj = OBJ_NONE;
        scan_done(s, p, !r ? R_OK : st ? R_DIRERR : R_NOCARD);
        return;
    }
    volatile char nm[NAME_LEN];
    if (mod_name(fi, nm))
        name_insert(p, nm);
}

static void load_fail(struct state *s, volatile struct pick *p, uint32_t result)
{
    SUSPEND_ALL();
    p->result = (uint8_t)result;
    p->bad = (uint8_t)(p->pick + 1);
    if (s->ld_phase == LD_LOAD)
        s->ld_phase = LD_PICK;
    RESUME_ALL();
}

/* File load: f_open (read-only) into B's FIL, then one 256-byte f_read per pass straight into ITCM, f_close at the
 * end or on any error; the header is checked after the first piece, mod_check() before the start. */
static void load_step(struct state *s, volatile struct pick *p)
{
    uint32_t st = fs_state();
    if (st == 2)
        return;
    if (p->obj == OBJ_NONE) {
        uint32_t fil = file_b(0), k = p->pick;
        if (!st || !fil || k < 1 || k > p->count) {
            load_fail(s, p, !st ? R_NOCARD : !fil ? R_BUSY : R_OPEN);
            return;
        }
        volatile char path[24];
        uint32_t n = dir_path(path);
        path[n++] = '/';
        for (uint32_t j = 0; j < NAME_LEN; j++)
            path[n + j] = p->name[k - 1][j];
        path[n + NAME_LEN - 1] = 0;
        if (F_OPEN(fil, (const char *)path, FA_READ)) {
            load_fail(s, p, R_OPEN);
            return;
        }
        p->obj = OBJ_FILE;
        uint32_t size = link(fil, FIL_FSIZE);
        if (size < HDR1 + 2 || size > MOD_MAX) {
            F_CLOSE(fil);
            p->obj = OBJ_NONE;
            load_fail(s, p, R_SIZE);
            return;
        }
        p->size = size;
        p->off = 0;
        return;
    }
    uint32_t fil = file_b(1);
    if (!fil) {
        p->obj = OBJ_NONE;                  /* a firmware job took the object over: never touched again */
        load_fail(s, p, R_LOST);
        return;
    }
    uint32_t size = p->size, off = p->off, br = 0;
    uint32_t n = size - off < PIECE ? size - off : PIECE;
    uint32_t result = !st ? R_NOCARD : (size > MOD_MAX || off >= size) ? R_SIZE : R_NONE;
    if (result == R_NONE && (F_READ(fil, MOD_BASE + off, n, &br) || br != n))
        result = R_READ;
    const volatile struct mod_hdr *h = (const volatile struct mod_hdr *)MOD_BASE;
    if (result == R_NONE && !off && (!hdr_len(h->magic) || h->size != size))
        result = R_BAD;                     /* the first piece holds the whole header (size >= 26) */
    if (result == R_NONE) {
        p->off = off + n;
        if (off + n < size)
            return;
    }
    F_CLOSE(fil);
    p->obj = OBJ_NONE;
    if (result != R_NONE) {
        load_fail(s, p, result);
        return;
    }
    SUSPEND_ALL();
    if (s->mode == MODE_PICK && s->ld_phase == LD_LOAD) {
        if (mod_check() == 1) {
            s->ld_fresh = 1;
            start(s);                       /* mode 3, ownership for an input module, loader idle */
        } else {
            p->result = R_BAD;
            p->bad = (uint8_t)(p->pick + 1);
            s->ld_phase = LD_PICK;
        }
    }
    RESUME_ALL();
}

/* Close what the loader holds: the folder, or the file if the object still holds our read-only open (otherwise a
 * firmware job has taken it over). Neither close does I/O: f_closedir only validates, and f_close's f_sync writes
 * nothing for a FIL that was never written. */
static void close_obj(volatile struct pick *p)
{
    if (p->obj == OBJ_DIR)
        F_CLOSEDIR((uint32_t)p->dir);
    else {
        uint32_t fil = file_b(1);
        if (fil)
            F_CLOSE(fil);
    }
    p->obj = OBJ_NONE;
}

/* One loader step per Main pass: at most one FatFs call that may read the card, plus the close that ends a folder or
 * file, never while the Content task is in its job loop (it then holds the token: skip this pass). Close first: an
 * open object that the current request does not want (picker cancelled or reopened) is closed before anything else;
 * the loader goes idle only with nothing open. */
static void loader(struct state *s)
{
    uint32_t phase = s->ld_phase;           /* unlocked peek: Main task is the only writer of ld_phase */
    if (phase == LD_IDLE || (phase == LD_PICK && s->mode == MODE_PICK))
        return;
    if (!itcm_ok()) {
        SUSPEND_ALL();
        s->ld_phase = LD_IDLE;              /* cannot happen (ITCMCR is set once at boot); the data is out of reach */
        if (s->mode == MODE_PICK)
            s->mode = 0;
        RESUME_ALL();
        return;
    }
    volatile struct pick *p = PICK;
    SUSPEND_ALL();
    if (s->tok & TOK_CONTENT) {
        RESUME_ALL();
        return;
    }
    s->tok |= TOK_MAIN;
    uint32_t want = s->mode == MODE_PICK, restart = p->restart;
    RESUME_ALL();
    uint32_t keep = !want || restart ? OBJ_NONE : phase == LD_SCAN ? OBJ_DIR : phase == LD_LOAD ? OBJ_FILE : OBJ_NONE;
    if (p->obj != OBJ_NONE && p->obj != keep)
        close_obj(p);
    else if (!want) {
        SUSPEND_ALL();
        s->ld_phase = LD_IDLE;
        RESUME_ALL();
    } else if (restart)
        p->restart = 0;
    else if (phase == LD_SCAN)
        scan_step(s, p);
    else if (phase == LD_LOAD)
        load_step(s, p);
    SUSPEND_ALL();
    s->tok &= (uint8_t)~TOK_MAIN;
    RESUME_ALL();
}

/* ---- clock (Main task; see the header) ------------------------------------------------------------------------ */

/* Tempo in BPM (internal clock 40..240), 0 if unreadable. */
static uint32_t tempo(uint32_t seq)
{
    uint32_t w = link(seq, 0x84), ext = link(seq, 0x88) & 0xFF;
    uint32_t bpm = ext == 1 ? (uint32_t)(((uint64_t)w * 0x9C4u) >> 32) : (w & 0xFF);
    return bpm > 255 ? 255 : bpm;
}

/* Call the clock entry. Caller holds the suspension and has checked mod_clock. */
static void clock_call(struct state *s, uint32_t event, uint32_t pulse, uint32_t bpm)
{
    uint32_t pos = pulse | (pulse / 6) << 8 | (pulse / 24) << 16;
    ((mod_clock_fn)CLK->mod_clock)(s->leds, event, pos, bpm);
}

/* Pulse hook (each 24-PPQN pulse on which the sequencer steps): the original call first, then START (if marked
 * stopped) and PULSE to a running clocked module. Returns at once when none runs. */
uint32_t hook_pulse(uint32_t obj, uint32_t b, uint32_t c, uint32_t d)
{
    uint32_t r = ORIG_PULSE(obj, b, c, d);
    struct state *s = region();
    if (!s || !CLK->mod_clock)
        return r;
    uint32_t seq = link(obj, 0) == TRANSPORT_VT ? link(obj, 8) : 0;
    uint32_t pulse = link(seq, 0x20) & 0xFF, bpm = tempo(seq);
    if (!seq || pulse >= 96)
        return r;                           /* not the object we know: deliver nothing */
    SUSPEND_ALL();
    if (CLK->mod_clock && s->mode == 3 && s->mod_frame) {
        CLK->transport = obj;
        CLK->pulse = (uint8_t)pulse;
        CLK->bpm = (uint8_t)bpm;
        if (!CLK->running) {
            CLK->running = 1;
            CLK->pulses = 0;
            CLK->starts++;
            clock_call(s, EV_START, pulse, bpm);
        }
        if (CLK->mod_clock) {               /* START may not stop the module, but stay safe */
            CLK->pulses++;
            clock_call(s, EV_PULSE, pulse, bpm);
        }
    }
    RESUME_ALL();
    return r;
}

/* After the timer service: STOP when the transport last seen has stopped. */
static void clock_poll(struct state *s)
{
    if (!CLK->mod_clock || !CLK->running)
        return;                             /* unlocked peek: only Main and suspended code change these */
    uint32_t obj = CLK->transport;
    if (link(obj, 0) == TRANSPORT_VT && (link(obj, 0x10) & 0xFF))
        return;
    SUSPEND_ALL();
    if (CLK->mod_clock && CLK->running && s->mode == 3 && s->mod_frame) {
        CLK->running = 0;
        CLK->stops++;
        clock_call(s, EV_STOP, CLK->pulse, CLK->bpm);
    }
    RESUME_ALL();
}

/* cmd 0B: F0 00 20 29 00 60 0B <clocked> <running> <pulse> <bpm 2x7> <pulses 5x7> <starts 3x7> <stops 3x7> F7 */
static void clock_reply(void)
{
    volatile uint8_t r[7 + 3 + 2 + 5 + 3 + 3 + 1];
    header(r, 0x0B);
    SUSPEND_ALL();
    uint32_t on = CLK->mod_clock != 0, run = CLK->running, pulse = CLK->pulse, bpm = CLK->bpm;
    uint32_t pulses = CLK->pulses, starts = CLK->starts, stops = CLK->stops;
    RESUME_ALL();
    r[7] = (uint8_t)on;
    r[8] = (uint8_t)run;
    r[9] = (uint8_t)(pulse & 0x7F);
    put7(r + 10, bpm, 2);
    put7(r + 12, pulses, 5);
    put7(r + 17, starts, 3);
    put7(r + 20, stops, 3);
    r[23] = 0xF7;
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}

/* Main loop, first call of each pass: run the original timer service (pulses reach hook_pulse inside it), take the
 * host_ui snapshot, deliver a STOP, drain the note ring, then one loader step. Main task, scheduler running. */
void hook_main(uint32_t timers)
{
    ORIG_TIMERS(timers);
    struct state *s = region();
    if (!s)
        return;
    ui_snap(s);
    clock_poll(s);
    drain(s);
    loader(s);
#if SCALES
    sc_main(s);
#endif
}

/* Content task loop: the original job loop runs only while no FatFs call of ours is in flight and holds the token
 * meanwhile, so a loader call never overlaps a firmware file job. */
uint32_t hook_content(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    struct state *s = region();
    if (!s)
        return ORIG_CONTENT(a, b, c, d);
    for (;;) {
        SUSPEND_ALL();
        if (!(s->tok & TOK_MAIN)) {
            s->tok |= TOK_CONTENT;
            RESUME_ALL();
            break;
        }
        RESUME_ALL();
        DELAY(1);                           /* a Main call is in flight (Main is waiting for the card): let it end */
    }
    uint32_t r = ORIG_CONTENT(a, b, c, d);
    SUSPEND_ALL();
    s->tok &= (uint8_t)~TOK_CONTENT;
    RESUME_ALL();
    return r;
}
