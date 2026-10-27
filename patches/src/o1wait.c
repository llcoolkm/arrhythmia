/* Bounded DSP waits and DSP fault quarantine. Compiled after devkit_abi3.c; uses region(), header(), put7(),
 * TX_SYSEX, SRAM_*, LEDS and struct clk of devkit_abi3.c, plus the O1WAIT blocks there (cmd 0D dispatch, o1_frame()
 * from hook_frame). No heap change, no new RAM block.
 *
 * Rule: audio may degrade, the ARM must never hang. A bounded wait that then pretends the DSP answered is not safety;
 * the operation must fail at its boundary, locks must be released, and nothing dependent may be sent to the DSP
 * until a validated recovery. Three layers:
 *
 * 1. Bounded waits. Each wait for a DSP answer (a mailbox word to clear, or a ready pin to go low) is retargeted to a
 *    hook that repeats the original reads and watchdog refreshes in the same order, bounded by the 1 ms tick: O1_MS
 *    (a policy value), with a backstop after O1_FROZEN_* polls at one tick value. On timeout the hook raises the
 *    fault and returns so the enclosing operation exits through its own cleanup. The mailbox word is not touched
 *    after a timeout. Sites:
 *      site 1   hook_mb_reset    sample memory reset, phase 1
 *      site 2   hook_mb_stop     capture stop
 *      site 3   hook_mb_tail     capture start
 *      site 4   hook_mb_tail     trim commit
 *      site 5   hook_mb_tail     sample memory reset, phase 2
 *      site 6   hook_rdy_bulk0   bulk read, core 0
 *      site 7   hook_rdy_bulk1   bulk read, core 1
 *      site 8   hook_rdy_enter1  enter bulk mode, core 1
 *      site 9   hook_rdy_leave1  leave bulk mode, core 1
 *      site 10  hook_rdy_tail0   enter / leave bulk mode, core 0
 *    O1 bounds the waits for the DSP's answer, not the ARM-side transport.
 *
 * 2. Sticky fault. The first timeout sets o1.fault. Nothing in this file clears it: a later answer is counted (late)
 *    and ignored. The only recovery in v0.1 is a reboot. While the fault is set, waits still reached make one poll
 *    and return at once (skipped).
 *
 * 3. Quarantine and visible failure at the operation boundary. While faulted, every DSP sample-memory operation is
 *    refused before it touches the DSP, and the ARM-side operation around it fails the way the firmware already
 *    reports failures: no new sample note-on is published once the pack failed (hook_engine_on; releases and Stop
 *    keep their paths, sounding voices play out); sample length reads 0 for every slot (hook_fs_length); allocation,
 *    reset, bulk read, free, capture start/stop, trim preview/commit are refused (hook_fs_*); a pack load fails
 *    before any DSP traffic (hook_pack_access, hook_pack_done, hook_slot_upload, hook_slot_job); SysEx sample upload
 *    and read-back are refused (hook_sx_upload, hook_sx_read, hook_bulk_chunk). What a refused or timed-out operation
 *    would have done is lost: that is a FAILED operation, reported. Visible on the unit: the Rec button blinks white
 *    (bytes 188..190 of the LED buffer, docs/led-map.md) at about 2 Hz while faulted (hook_encode, o1_frame()).
 *
 * Report (SysEx cmd 0D): F0 00 20 29 00 60 0D <fault> <last site> <timeouts 3x7> <site mask 2x7> <refused 3x7>
 * <late 3x7> <pack failed> F7 (22 bytes). last site bit 6 = ended by the backstop. refused = operations refused and waits skipped
 * while faulted; late = waits that saw the DSP answer while faulted. State: struct o1 (12 bytes) in the spare of
 * the clock block (CLOCK_AT + sizeof(struct clk)), zeroed by hook_boot, touched only while region() is valid.
 * Counters are diagnostic and may race between tasks; the fault byte is written once (set) and never cleared.
 */

#define O1_MS           500u            /* policy: about 750 nominal 0.667 ms blocks; far below the IWDG */
#define O1_FROZEN_MB    0x10000u        /* mailbox polls at one tick value (each a full SHI read) */
#define O1_FROZEN_RDY   0x100000u       /* GPIO polls at one tick value */
#define MB_RESET_REQ    0x372FBu
#define MB_RESET_BUSY   0x37300u
#define MB_TRIM         0x372FEu
#define O1_BACKSTOP     0x40u           /* in last_site */
#define LED_FAULT       188u            /* btn_Rec R, G, B = 188, 189, 190 (docs/led-map.md) */
#define O1_BLINK_SHIFT  8u              /* tick bit 8: 256 ms phases */

typedef uint32_t (*tick_fn)(void);
typedef uint32_t (*pin_fn)(uint32_t pin);
typedef uint32_t (*shi_read_fn)(uint32_t core, uint32_t addr);
typedef void (*kick_fn)(void);
typedef uint32_t (*fn4)(uint32_t a, uint32_t b, uint32_t c, uint32_t d);
typedef void (*encode_fn)(void *frame, uint32_t *src);
#define O1_TICK         ((tick_fn)0x08024069u)      /* 1 ms tick */
#define O1_PIN          ((pin_fn)0x08023417u)       /* ready pin read */
#define O1_SHI_READ     ((shi_read_fn)0x080314D7u)  /* shared DSP read */
#define O1_KICK         ((kick_fn)0x0803172Fu)      /* watchdog refresh */
#define STOCK_FS_LENGTH     ((fn4)0x0806438Du)      /* sample length */
#define STOCK_FS_ALLOC      ((fn4)0x080643F5u)      /* alloc */
#define STOCK_FS_RESET      ((fn4)0x08064417u)      /* reset */
#define STOCK_FS_BULK       ((fn4)0x0806444Bu)      /* bulk read */
#define STOCK_FS_FREE       ((fn4)0x08064461u)      /* free */
#define STOCK_FS_START      ((fn4)0x0806446Fu)      /* capture start */
#define STOCK_FS_PREVIEW    ((fn4)0x080644E5u)      /* trim preview */
#define STOCK_FS_TRIM       ((fn4)0x08064513u)      /* trim commit */
#define STOCK_FS_STOP       ((fn4)0x08064481u)      /* capture stop */
#define STOCK_PACK_ACCESS   ((fn4)0x08040E29u)      /* pack access check */
#define STOCK_SLOT_UPLOAD   ((fn4)0x08053E8Du)      /* slot upload, 0 = failed */
#define STOCK_SX_UPLOAD     ((fn4)0x080536D3u)      /* SysEx sample upload */
#define STOCK_SX_READ       ((fn4)0x08053691u)      /* SysEx sample read-back */
#define STOCK_BULK_READ     ((fn4)0x08031431u)      /* bulk read */
#define STOCK_ENCODE        ((encode_fn)0x08029685u)   /* LED frame encoder */
#define STOCK_PUBLISH       ((fn4)0x08040875u)      /* note-on publish */

struct o1 {
    uint8_t fault;                            /* sticky: a DSP wait timed out; only a reboot clears it */
    uint8_t last_site;                        /* site (1..10) of the last timeout, | O1_BACKSTOP */
    uint16_t timeouts;                        /* waits ended by the bound since boot */
    uint16_t sites;                           /* bit n: site n timed out at least once */
    uint16_t refused;                         /* operations refused and waits skipped while faulted */
    uint16_t late;                            /* waits that saw the DSP answer while faulted (ignored) */
    uint8_t phase;                            /* UI task: blink phase last encoded (bit 0), bit 1 = ever shown */
    uint8_t pack_failed;                      /* a slot upload aborted or was refused: the pack's sample data is
                                                 unusable until reboot; sample length reports every slot empty */
};
_Static_assert(sizeof(struct clk) + sizeof(struct o1) <= CLOCK_SIZE, "o1 state must fit the clock block's spare");
#define O1_AT       (CLOCK_AT + sizeof(struct clk))

static volatile struct o1 *o1(void)
{
    return region() ? (volatile struct o1 *)O1_AT : 0;
}

static uint32_t o1_faulted(void)
{
    volatile struct o1 *o = o1();
    return o && o->fault;
}

static uint32_t o1_ok(volatile struct o1 *o)
{
    if (o && o->fault)
        o->late++;                            /* the DSP answered, but nothing is recovered by that */
    return 0;
}

static uint32_t o1_refuse(void)
{
    volatile struct o1 *o = o1();
    if (o)
        o->refused++;
    return 0;
}

static uint32_t o1_fail(volatile struct o1 *o, uint32_t site, uint32_t backstop)
{
    if (o) {
        o->fault = 1;
        o->last_site = (uint8_t)(site | (backstop ? O1_BACKSTOP : 0));
        o->timeouts++;
        o->sites |= (uint16_t)(1u << site);
    }
    return 0;                                 /* the original loop exits; its function unwinds */
}

/* ---- 1. bounded waits --------------------------------------------------------------------------------------------- */

/* Like the original loop: first read of addr, then while nonzero { read poll; refresh IWDG }, bounded. */
static uint32_t o1_mailbox(uint32_t core, uint32_t addr, uint32_t site)
{
    volatile struct o1 *o = o1();
    uint32_t poll = addr == MB_RESET_REQ ? MB_RESET_BUSY : addr;
    uint32_t t0 = O1_TICK(), seen = t0, same = 0;
    if (!O1_SHI_READ(core, addr))
        return o1_ok(o);
    if (o && o->fault)
        return o1_refuse();                   /* quarantined: no further waiting on this DSP */
    for (;;) {
        uint32_t v = O1_SHI_READ(core, poll);
        O1_KICK();
        if (!v)
            return o1_ok(o);
        uint32_t now = O1_TICK();
        if (now - t0 >= O1_MS)
            return o1_fail(o, site, 0);
        if (now != seen) {
            seen = now;
            same = 0;
        } else if (++same >= O1_FROZEN_MB) {
            return o1_fail(o, site, 1);
        }
    }
}

/* Like the original spin: until the ready pin reads low, bounded. */
static uint32_t o1_ready(uint32_t pin, uint32_t site)
{
    volatile struct o1 *o = o1();
    uint32_t t0 = O1_TICK(), seen = t0, same = 0;
    if (!O1_PIN(pin))
        return o1_ok(o);
    if (o && o->fault)
        return o1_refuse();
    for (;;) {
        if (!O1_PIN(pin))
            return o1_ok(o);
        uint32_t now = O1_TICK();
        if (now - t0 >= O1_MS)
            return o1_fail(o, site, 0);
        if (now != seen) {
            seen = now;
            same = 0;
        } else if (++same >= O1_FROZEN_RDY) {
            return o1_fail(o, site, 1);
        }
    }
}

/* ---- 1b. abort at the enclosing operation (first fault, or an operation in flight when faulted) ------------------
 * The waits that are followed by further DSP traffic do not return into it when the fault is set after the wait;
 * the naked hooks below unwind to the enclosing operation's own cleanup/exit (site 1: return to the loader, no
 * phase 2; sites 6/7: the bulk read's cleanup tail with result 0; sites 8/9/10: by the enclosing operation, a slot
 * upload (abandoned, pack_failed) or a SysEx sample upload (its cleanup tail, 0 bytes sent), otherwise a plain
 * return). The end-bulk frame sent on cleanup is one best-effort transport shutdown attempt after a fault, not a
 * flush, cancellation or recovered mode. Every unwind lands on the enclosing function's own stack pointer.
 */

#define PCM_EPILOGUE_SP     0x2Cu           /* slot upload: epilogue stack adjust */
#define BULK_TAIL           0x080314B1u     /* bulk read cleanup tail */
#define SX_TAIL             0x08053775u     /* SysEx upload cleanup tail */
typedef void (*cs_fn)(uint32_t core);
#define CS_RELEASE      ((cs_fn)0x08031627u)        /* chip select release */
#define END_BULK        ((cs_fn)0x0803157Fu)        /* end bulk mode (its ready wait is site 9/10) */

static uint32_t __attribute__((used, noinline)) o1_mb_reset_c(uint32_t core, uint32_t addr)
{
    o1_mailbox(core, addr, 1);
    return o1_faulted();
}

static uint32_t __attribute__((used, noinline)) o1_rdy_c(uint32_t pin, uint32_t site)
{
    o1_ready(pin, site);
    return o1_faulted();
}

/* The enclosing operation of an enter/leave ready wait, from its saved return address. */
static uint32_t __attribute__((used, noinline)) o1_abort_kind(uint32_t lr)
{
    lr &= ~1u;                                /* BL saves the return address with the Thumb bit */
    if (lr == 0x08053F26u || lr == 0x08053F36u || lr == 0x08053F7Cu || lr == 0x08054054u)
        return 1;                             /* slot upload, before or between transfers */
    if (lr == 0x080536EEu || lr == 0x080536FEu)
        return 2;                             /* SysEx sample upload, before its transfers */
    return 0;
}

/* The slot upload's abort: local CS release, one best-effort end-bulk frame as a transport shutdown attempt (not a
 * flush, a cancellation or a recovered DSP mode), then NO further DSP command or data: the slot's descriptor words
 * are not written. The pack is marked unusable instead (pack_failed; sample length reports every slot empty). The
 * end-bulk's own ready wait is reached with this function's return address, which o1_abort_kind does not classify:
 * no retry. */
static void __attribute__((used, noinline)) o1_abort_pcm(uint32_t slot)
{
    (void)slot;
    volatile struct o1 *o = o1();
    if (o)
        o->pack_failed = 1;
    CS_RELEASE(0);
    END_BULK(0);
}

__attribute__((naked)) uint32_t hook_mb_reset(uint32_t core, uint32_t addr)
{
    __asm__ volatile(
        "push {r4, lr}\n\t"
        "bl o1_mb_reset_c\n\t"
        "cbnz r0, 1f\n\t"
        "pop {r4, pc}\n"                    /* r0 = 0: the original loop exits normally */
        "1: pop {r4, lr}\n\t"
        "pop {r0, r4, r5, pc}");             /* the enclosing frame: return to the loader, no phase 2 */
}

__attribute__((naked)) uint32_t hook_rdy_bulk0(uint32_t pin)
{
    __asm__ volatile(
        "push {r4, lr}\n\t"
        "movs r1, #6\n\t"
        "bl o1_rdy_c\n\t"
        "cbnz r0, 1f\n\t"
        "pop {r4, pc}\n"
        "1: pop {r4, lr}\n\t"
        "movs r6, #0\n\t"                   /* the bulk read returns r6 */
        "movw r0, #0x14B1\n\t"
        "movt r0, #0x0803\n\t"
        "bx r0");                             /* its cleanup tail: end-bulk, cache, semaphore, pop */
}

__attribute__((naked)) uint32_t hook_rdy_bulk1(uint32_t pin)
{
    __asm__ volatile(
        "push {r4, lr}\n\t"
        "movs r1, #7\n\t"
        "bl o1_rdy_c\n\t"
        "cbnz r0, 1f\n\t"
        "pop {r4, pc}\n"
        "1: pop {r4, lr}\n\t"
        "movs r6, #0\n\t"
        "movw r0, #0x14B1\n\t"
        "movt r0, #0x0803\n\t"
        "bx r0");
}

/* Sites 8, 9, 10: wait, then unwind by the enclosing operation (one body per hook: no local branch symbol). */
#define O1_RDY_UNWIND(site)                                                                                       \
    __asm__ volatile(                                                                                             \
        "push {r4, lr}\n\t"                                                                                       \
        "movs r1, #" #site "\n\t"                                                                                 \
        "bl o1_rdy_c\n\t"                                                                                         \
        "cbnz r0, 1f\n\t"                                                                                         \
        "pop {r4, pc}\n"                                                                                           \
        "1: ldr r0, [sp, #20]\n\t"          /* the enclosing frame's saved return address */                      \
        "bl o1_abort_kind\n\t"                                                                                    \
        "cmp r0, #1\n\t"                                                                                          \
        "beq 2f\n\t"                                                                                              \
        "cmp r0, #2\n\t"                                                                                          \
        "beq 3f\n\t"                                                                                              \
        "movs r0, #0\n\t"                                                                                         \
        "pop {r4, pc}\n"                    /* a cleanup tail: let it finish */                                   \
        "2: pop {r4, lr}\n\t"                                                                                     \
        "pop {r3, r4, r5, lr}\n\t"          /* drop the enter/leave frame: slot upload's body sp */               \
        "mov r0, r7\n\t"                    /* slot */                                                            \
        "bl o1_abort_pcm\n\t"                                                                                     \
        "movs r0, #0\n\t"                                                                                         \
        "add sp, #0x2c\n\t"                                                                                       \
        "pop.w {r4, r5, r6, r7, r8, r9, r10, r11, pc}\n"                                                           \
        "3: pop {r4, lr}\n\t"                                                                                     \
        "pop {r3, r4, r5, lr}\n\t"          /* SysEx upload's body sp */                                          \
        "movs r7, #0\n\t"                                                                                         \
        "movw r0, #0x3775\n\t"                                                                                    \
        "movt r0, #0x0805\n\t"                                                                                    \
        "bx r0")                              /* its tail: end-bulk, give the semaphore, return r7 */

__attribute__((naked)) uint32_t hook_rdy_enter1(uint32_t pin)
{
    O1_RDY_UNWIND(8);
}

__attribute__((naked)) uint32_t hook_rdy_leave1(uint32_t pin)
{
    O1_RDY_UNWIND(9);
}

__attribute__((naked)) uint32_t hook_rdy_tail0(uint32_t pin)
{
    O1_RDY_UNWIND(10);
}

uint32_t hook_mb_stop(uint32_t core, uint32_t addr)
{
    return o1_mailbox(core, addr, 2);         /* after the wait: the operation's own cleanup */
}

uint32_t hook_mb_tail(uint32_t core, uint32_t addr)
{
    return o1_mailbox(core, addr, addr == MB_RESET_REQ ? 5 : addr == MB_TRIM ? 4 : 3);   /* then: return */
}

/* ---- 3. quarantine: DSP sample-memory operations -------------------------------------------------------------------- */

/* Engine admission (every sample note-on, whatever its source): while the pack failed no new sample note-on is
 * published. Release, forced off and Stop are not touched. Voices already sounding when the fault hit play out;
 * nothing here silences the DSP. */
uint32_t hook_engine_on(uint32_t g, uint32_t note, uint32_t vel, uint32_t d)
{
    volatile struct o1 *o = o1();
    if (o && o->pack_failed)
        return 0;
    return STOCK_PUBLISH(g, note, vel, d);
}

#if CORE0_SYNTH
static uint32_t q0_bulk_call(fn4 f, uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    q0_bulk_enter();
    uint32_t r = f(a, b, c, d);
    q0_bulk_leave();
    return r;
}
#define BULK_CALL(f, a, b, c, d) q0_bulk_call(f, a, b, c, d)
#else
#define BULK_CALL(f, a, b, c, d) f(a, b, c, d)
#endif

/* Sample length: 0 = empty for every slot once the pack
 * failed, so stale descriptors left by an aborted or refused upload are not offered by the UI either. */
uint32_t hook_fs_length(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    volatile struct o1 *o = o1();
    return o && o->pack_failed ? 0 : STOCK_FS_LENGTH(a, b, c, d);
}

uint32_t hook_fs_alloc(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
#if CORE0_RECOVER                             /* allocator validation: a page outside the pool is refused (0 = exhausted) */
    if (o1_faulted())
        return o1_refuse();
    uint32_t r = STOCK_FS_ALLOC(a, b, c, d);
    return r && q0_alloc_bad(r) ? 0 : r;
#else
    return o1_faulted() ? o1_refuse() : STOCK_FS_ALLOC(a, b, c, d);        /* 0 = exhausted: the upload fails */
#endif
}

uint32_t hook_fs_reset(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    return o1_faulted() ? o1_refuse() : STOCK_FS_RESET(a, b, c, d);
}

/* The filesystem bulk read (vtable path): the whole stock call, its cleanup and error returns included, inside the
 * bulk interval. */
uint32_t hook_fs_bulk(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    return o1_faulted() ? o1_refuse() : BULK_CALL(STOCK_FS_BULK, a, b, c, d);
}

uint32_t hook_fs_free(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    return o1_faulted() ? o1_refuse() : STOCK_FS_FREE(a, b, c, d);
}

uint32_t hook_fs_preview(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    return o1_faulted() ? o1_refuse() : STOCK_FS_PREVIEW(a, b, c, d);
}

uint32_t hook_fs_trim(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    return o1_faulted() ? o1_refuse() : STOCK_FS_TRIM(a, b, c, d);
}

uint32_t hook_fs_start(uint32_t obj, uint32_t slot, uint32_t c, uint32_t d)
{
    if (o1_faulted())
        return o1_refuse();                   /* obj+4 (recording) stays 0 */
    uint32_t r = STOCK_FS_START(obj, slot, c, d);
    if (o1_faulted())
        *(volatile uint8_t *)(obj + 4) = 0;   /* the start timed out: not recording */
    return r;
}

uint32_t hook_fs_stop(uint32_t obj, uint32_t b, uint32_t c, uint32_t d)
{
    if (o1_faulted()) {
        *(volatile uint8_t *)(obj + 4) = 0;   /* the original post-condition, without the mailbox */
        return o1_refuse();
    }
    return STOCK_FS_STOP(obj, b, c, d);
}

/* ---- 3. quarantine: pack and SysEx sample transfer call sites ------------------------------------------------------- */

uint32_t hook_pack_access(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    if (o1_faulted()) {
        o1_refuse();
        return 1;                             /* the pack load fails before any DSP traffic */
    }
    uint32_t r = STOCK_PACK_ACCESS(a, b, c, d);
#if CORE0_RECOVER                             /* the whole pack load is ONE bulk interval, closed in o1_pack_result
                                                 (the pack loader returns early only when this is 1) */
    if (r != 1)
        q0_bulk_enter();
#endif
    return r;
}

static uint32_t slot_upload(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    volatile struct o1 *o = o1();
    if (o && o->fault) {
        o->pack_failed = 1;                   /* a slot not uploaded after a reset: the pack is unusable */
        return o1_refuse();
    }
#if CORE0_SYNTH                               /* the PCM upload runs the SHI in 16-bit bulk mode: no core-0 queue access */
    q0_bulk_enter();
    uint32_t r = STOCK_SLOT_UPLOAD(a, b, c, d);
    q0_bulk_leave();
    return r;
#else
    return STOCK_SLOT_UPLOAD(a, b, c, d);
#endif
}

uint32_t hook_slot_upload(uint32_t a, uint32_t b, uint32_t c, uint32_t d)   /* pack loader loop */
{
    return slot_upload(a, b, c, d);
}

uint32_t hook_slot_job(uint32_t a, uint32_t b, uint32_t c, uint32_t d)      /* single-slot job */
{
    return slot_upload(a, b, c, d);
}

uint32_t hook_sx_upload(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    return o1_faulted() ? o1_refuse() : BULK_CALL(STOCK_SX_UPLOAD, a, b, c, d);
}

uint32_t hook_sx_read(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    return o1_faulted() ? o1_refuse() : BULK_CALL(STOCK_SX_READ, a, b, c, d);
}

/* SysEx sample read-back: one bulk read chunk per iteration, the result ignored, so the
 * loop runs on after the first chunk aborted; each later chunk is refused here (no frame, no semaphore). */
uint32_t hook_bulk_chunk(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    return o1_faulted() ? o1_refuse() : BULK_CALL(STOCK_BULK_READ, a, b, c, d);
}

/* The pack loader's result (its success tail is replaced by a B.W here): r0 = 0 (failed) when a fault is set, as
 * the loader's own early-exit path reports; the frame is the loader's. */
static uint32_t __attribute__((used, noinline)) o1_pack_result(void)
{
#if CORE0_RECOVER
    q0_bulk_leave();                          /* the pack interval opened in hook_pack_access */
#endif
    return o1_faulted() ? 0 : 1;
}

__attribute__((naked)) void hook_pack_done(void)
{
    __asm__ volatile("bl o1_pack_result\n\tpop {r4, r5, r6, pc}");
}

/* ---- 3. visible fault: Rec button blinks white ---------------------------------------------------------------------- */

/* After the view rendered into src[4], before the bit-plane encoding. UI task. */
void hook_encode(void *frame, uint32_t *src)
{
    volatile struct o1 *o = o1();
    if (o && o->fault && src[4] >= SRAM_START && src[4] <= SRAM_END - LEDS) {
        volatile uint8_t *leds = (volatile uint8_t *)src[4];
        uint8_t v = (o->phase & 1) ? 0xFE : 0;
        leds[LED_FAULT] = v;
        leds[LED_FAULT + 1] = v;
        leds[LED_FAULT + 2] = v;
    }
    STOCK_ENCODE(frame, src);
}

/* hook_frame, normal-display branches, before ORIG_FRAME: a changed blink phase marks the source dirty, so the frame
 * encoder renders and re-encodes it (hook_encode draws the LED). UI task only. */
static void o1_frame(uint32_t *src)
{
    volatile struct o1 *o = o1();
    if (!o || !o->fault)
        return;
    uint8_t phase = (uint8_t)(((O1_TICK() >> O1_BLINK_SHIFT) & 1) | 2);
    if (phase != o->phase) {
        o->phase = phase;
        __atomic_fetch_or(src, 1u, __ATOMIC_SEQ_CST);
    }
}

/* cmd 0D (see the header): F0 00 20 29 00 60 0D F7. No reply while the region is invalid (as every devkit command). */
static void o1_sysex(const uint8_t *m, uint32_t len)
{
    (void)m;
    volatile struct o1 *o = o1();
    if (len != 8 || !o)
        return;
    volatile uint8_t r[22];
    header(r, 0x0D);
    r[7] = o->fault;
    r[8] = o->last_site & 0x7F;
    put7(r + 9, o->timeouts, 3);
    put7(r + 12, o->sites, 2);
    put7(r + 14, o->refused, 3);
    put7(r + 17, o->late, 3);
    r[20] = o->pack_failed;
    r[21] = 0xF7;
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}
