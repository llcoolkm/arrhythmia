/* Arpeggiator for track 8 (ARP, issue #31). Included after notetap.c.
 *
 * The arp decides only WHEN the existing one-note press path runs. It never writes to the DSP: its notes are router
 * presses on port 0x18 + 7 (engine only), which re-enter the tap (hook_ron), take the source tag SRC_ARP there and go
 * through a1_press's admission, voice ownership, envelope and tone groups, epoch and recovery checks, exactly like
 * any press. Each arp note is also sent on port 0x20 + 7 (MIDI out only).
 *
 * Main blob (ARM): the plumbing in notetap.c and synthpage.c (FAR_ARP_* calls) and the two stubs at the end of this
 * file. Everything else is in the far blob (FAR_PART): it owns no RAM; its state is struct arp (arp.h, ARP_AT).
 *
 *   collect  FAR_ARP_IN  (a1_press, after chord()): arp on and not the arp's own note: inside a bulk interval or in
 *                         rejection quarantine the press is refused and recorded first, as a1_press refuses; else the
 *                         whole press (1..4 notes) joins the held set with one fresh 32-bit a1 serial, or nothing
 *                         does (no room: it plays normally);
 *                         the press's older rejections are superseded as a1_press would; nothing sounds now
 *   release  FAR_ARP_OFF (a1_release_at, where direct and staged offs meet): the newest press of (source, root) not
 *                         newer than the off's serial, across the arp's entries and a1's instances; the arp takes the
 *                         off only if that press is its own (latched: marked released)
 *   tick     FAR_ARP_TICK (hook_a1_tick, each Main pass while no core-0 transaction is open): the grid (transport
 *                         pulse while the sequencer runs; stopped: a Main-pass timer at the project tempo, started
 *                         at the press), gap resync, the mute guard, the gate, the step; at most one arp note-off and
 *                         one note-on per pass (two router calls each), and one pending MIDI-out off; missed steps
 *                         are dropped, never replayed
 *   reset    FAR_ARP_RESET (release_all, all_off): Stop / mute: the sequencer's entries become dead (the tick
 *                         removes them, and the latch's released entries, at the Stop edge); choke, sample edit:
 *                         none; CC 123, project load, boot: all
 *   page     FAR_ARP_PAD (pg_pad): synth page 3
 *   SysEx    FAR_ARP_SYSEX (cmd 11): 11 00 status, 11 01 flags mode rate octaves gate set
 *
 * MIDI out: the arp's notes go out on port 0x20 + 7; the stock MIDI-out leg of a press or off the arp took is skipped
 * in the router pass-through (notetap.c ARP_OUT_SKIP, flag `took`), so MIDI out carries arp notes only.
 *
 * Settings: the project's 'A' 'R' sub-record at block offset 0x300 (8 bytes: 'A' 'R' 1 mode rate octaves gate
 * flags), all zero = unset = defaults; anything else invalid plays as off and refuses edits. */

#define AR_AT           0x300u          /* block offset of the sub-record (after the chord table, 0x298..0x2F8) */
#define AR_LEN          8u
#define AR_VER          1u
#define AF_ON           1u              /* flags bit 0: arp on */
#define AF_LATCH        2u              /* flags bit 1: latch */
enum { AM_UP, AM_DOWN, AM_UPDOWN, AM_RANDOM, AM_PLAYED, AM_N };
#define AR_RATES        6u              /* 1/4 1/8 1/8T 1/16 1/16T 1/32 */
#define AR_OCTS         3u
#define AR_GATES        4u              /* 25 50 75 100 % */
#define AR_DEF          (3u << 8 | 1u << 16)   /* off, Up, 1/16, 1 octave, 50 %, no latch (cfg packing below) */
#define ARP_SEQ_CELL    0x20006A1Cu     /* sequencer context */
#define ARP_WATCH_MS    500u            /* clock loss: no transport-pulse change for this long while a note
                                           sounds */
#define AP_PORT_ENG     (0x18u + A1_T)  /* router port: engine only (re-enters the tap) */
#define AP_PORT_OUT     (0x20u + A1_T)  /* router port: MIDI out only */
enum { AO_SET, AO_TOGGLE, AO_ALL };     /* edit ops */

/* cfg packing: flags | mode << 4 | rate << 8 | octaves - 1 << 12 | gate << 16 | ok << 20 (ok: writable) */
#define CF_FLAGS(c)     ((c) & 3u)
#define CF_MODE(c)      ((c) >> 4 & 7u)
#define CF_RATE(c)      ((c) >> 8 & 7u)
#define CF_OCT(c)       ((c) >> 12 & 3u)
#define CF_GATE(c)      ((c) >> 16 & 3u)
#define CF_OK           (1u << 20)

#if FAR_PART || PG_LEDS_HERE              /* the settings: the far arp, and the page's lights wherever they are */

static uint32_t ar_valid(const volatile uint8_t *p)
{
    return p[0] == 'A' && p[1] == 'R' && p[2] == AR_VER && p[3] < AM_N && p[4] < AR_RATES && p[5] < AR_OCTS
           && p[6] < AR_GATES && !(p[7] & ~3u);
}

/* The settings in force: the sub-record, the defaults while it is unset, off while it is invalid or the block is
 * not readable (CF_OK clear: edits refused). */
static uint32_t arp_cfg(void)
{
    struct tm *t = tm();
    if (!t || !t->store || (t->why != TM_OK && t->why != TM_EMPTY))
        return AR_DEF;
    const volatile uint8_t *p = (const volatile uint8_t *)(t->store + TM_EXT + AR_AT);
    if (all_zero(p, AR_LEN))
        return AR_DEF | CF_OK;
    if (!ar_valid(p))
        return AR_DEF & ~3u;
    return p[7] | (uint32_t)p[3] << 4 | (uint32_t)p[4] << 8 | (uint32_t)p[5] << 12 | (uint32_t)p[6] << 16 | CF_OK;
}
#endif

#if FAR_PART

static uint32_t ar_pulses(uint32_t rate)
{
    static const uint8_t pp[AR_RATES] = {24, 12, 8, 6, 4, 3};
    return pp[rate < AR_RATES ? rate : 3];
}

/* gate length in pulses: 25 / 50 / 75 / 100 % of the step, at least one pulse */
static uint32_t ar_gate(uint32_t rate, uint32_t gate)
{
    uint32_t g = ar_pulses(rate) * (gate + 1u) / 4u;
    return g ? g : 1u;
}

static uint32_t ae_live(volatile struct arp *x, uint32_t i)
{
    return !(x->src[i] & AE_DEAD);
}

static uint32_t ae_live_n(volatile struct arp *x)
{
    uint32_t k = 0;
    for (uint32_t i = 0; i < x->n; i++)
        k += ae_live(x, i);
    return k;
}

/* any press still physically held (not released, not dead) */
static uint32_t ae_held(volatile struct arp *x)
{
    for (uint32_t i = 0; i < x->n; i++)
        if (!(x->src[i] & (AE_REL | AE_DEAD)))
            return 1;
    return 0;
}

/* Remove the entries in mask, keeping the arrival order; As played's next index follows its entry. */
static void ae_compact(volatile struct arp *x, uint32_t mask)
{
    uint32_t k = 0, c = x->cyc, idx = AC_IDX(c);
    for (uint32_t i = 0, n = x->n; i < n; i++) {
        if (mask >> i & 1u) {
            if (i < AC_IDX(c))
                idx--;
            continue;
        }
        x->note[k] = x->note[i];
        x->vel[k] = x->vel[i];
        x->src[k] = x->src[i];
        x->root[k] = x->root[i];
        x->ser[k] = x->ser[i];
        k++;
    }
    x->n = (uint8_t)k;
    x->cyc = (uint8_t)((c & 0x0Fu) | (idx <= k ? idx : 0u) << 4);
}

static void ar_on(uint32_t r, uint32_t note, uint32_t vel, uint32_t port)
{
    ((note_on_fn)((const uint32_t *)ROUTER_VT)[0])(r, note + 12u, vel, port, 0);
}

static void ar_off(uint32_t r, uint32_t note, uint32_t port)
{
    ((note_off_fn)((const uint32_t *)ROUTER_VT)[1])(r, note + 12u, port, 0);
}

/* The sounding arp note's off: engine (through the tap, tagged SRC_ARP) and MIDI out. No router: forgotten, its
 * MIDI-out off goes at a later tick. */
static void ar_note_off(volatile struct arp *x, uint32_t r)
{
    uint32_t n = x->sounding - 1u;
    x->sounding = 0;
    if (!r) {
        x->mo = (uint8_t)(n + 1u);
        return;
    }
    x->emit = 1;
    ar_off(r, n, AP_PORT_ENG);
    x->emit = 0;
    ar_off(r, n, AP_PORT_OUT);
}

/* The next note of the cycle (unfolded), its velocity in *vel; the cycle state advances. Called only with a live
 * entry. Bounded: 8 entries x 3 octaves. */
static uint32_t ar_next(volatile struct arp *x, uint32_t mode, uint32_t octs, uint32_t *vel)
{
    uint32_t no = octs + 1u, n = x->n;
    if (mode == AM_PLAYED) {                  /* arrival order, then the same an octave up, ... */
        uint32_t i = AC_IDX(x->cyc), o = AC_OCT(x->cyc);
        if (o >= no)                          /* the octave range shrank (a live edit): rebase to octave 0, the
                                                 arrival order goes on */
            o = 0;
        for (uint32_t k = 0; k < 2u * ARP_N + 1u; k++) {
            if (i >= n) {
                i = 0;
                o = (o + 1u) % no;
            }
            if (ae_live(x, i))
                break;
            i++;
        }
        x->cyc = (uint8_t)((i + 1u) << 4 | o << 2);
        *vel = x->vel[i];
        return x->note[i] + 12u * o;
    }
    if (mode == AM_RANDOM) {                  /* uniform over live entries x octaves (xorshift16) */
        uint32_t s = x->rng ? x->rng : (TICK() & 0xFFFFu) | 1u;
        s ^= s << 7 & 0xFFFFu;
        s ^= s >> 9;
        s ^= s << 8 & 0xFFFFu;
        x->rng = (uint16_t)s;
        uint32_t live = ae_live_n(x), k = s % (live * no), o = k / live, j = k % live;
        for (uint32_t i = 0; i < n; i++)
            if (ae_live(x, i) && !j--) {
                *vel = x->vel[i];
                return x->note[i] + 12u * o;
            }
    }
    /* pitch modes: candidates entry note + 12 x octave; strictly above / below the last, so a pitch held twice
       plays once per pass */
    uint32_t last = x->last, started = x->cyc & AC_STARTED, down = x->cyc & AC_DOWN;
    int32_t above = -1, below = -1, lo = -1, hi = -1;
    uint32_t va = 0, vb = 0, vl = 0, vh = 0;
    for (uint32_t i = 0; i < n; i++) {
        if (!ae_live(x, i))
            continue;
        for (uint32_t o = 0; o < no; o++) {
            int32_t c = (int32_t)(x->note[i] + 12u * o);
            if (lo < 0 || c < lo)
                lo = c, vl = x->vel[i];
            if (hi < 0 || c > hi)
                hi = c, vh = x->vel[i];
            if (c > (int32_t)last && (above < 0 || c < above))
                above = c, va = x->vel[i];
            if (c < (int32_t)last && (below < 0 || c > below))
                below = c, vb = x->vel[i];
        }
    }
    int32_t c;
    uint32_t v;
    if (mode == AM_DOWN) {
        c = started && below >= 0 ? below : hi;
        v = started && below >= 0 ? vb : vh;
    } else if (mode == AM_UPDOWN && started && !down) {
        if (above >= 0)
            c = above, v = va;
        else if (below >= 0)
            c = below, v = vb, down = AC_DOWN;
        else
            c = lo, v = vl;
    } else if (mode == AM_UPDOWN && started) {
        if (below >= 0)
            c = below, v = vb;
        else if (above >= 0)
            c = above, v = va, down = 0;
        else
            c = lo, v = vl;
    } else {                                  /* Up, and the first note of Up/Down */
        c = started && above >= 0 ? above : lo;
        v = started && above >= 0 ? va : vl;
        down = 0;
    }
    x->last = (uint8_t)c;
    x->cyc = (uint8_t)(AC_STARTED | down);
    *vel = v;
    return (uint32_t)c;
}

/* Issue #45. The stock router keeps one MIDI-out note per track 8 pitch for the pad, sequencer and module ports (EMU
 * on the unmodified stock router; MIDI in has no MIDI-out leg and takes no part): a press of a held pitch first sends
 * its off, then the new on; the final off goes when the last holding port releases (one port: at its first release).
 * Both end the pitch's current note, whoever releases. So an arp press of a pitch whose note was sent (a normal press
 * holds it, or an arp press marked AE_ECHO) sends its on too and is marked AE_ECHO, and a normal press of a pitch
 * marks the arp's held entries of it AE_ECHO: an AE_ECHO entry's offs go out as a normal press's do. A retrigger off
 * is skipped only when the pitch's holders are known and none of them was sent (they are all arp presses whose on was
 * skipped). Returns bit 0: sent; bit 1: a holder is known (a1 instances end at Stop, so none known means: the stock
 * decides, nothing is skipped). mark: set AE_ECHO on the arp's held entries of the pitch. */
static uint32_t ar_sent(volatile struct arp *x, struct a1 *a, uint32_t note, uint32_t mark)
{
    uint32_t s = 0, k = 0;
    for (uint32_t i = 0; i < x->n; i++)   /* only presses with a MIDI-out leg hold the pitch's MIDI-out note */
        if ((x->src[i] & (AE_LEG | AE_REL)) == AE_LEG && x->root[i] == note) {   /* still held */
            s |= x->src[i] & AE_ECHO;
            k = 2;
            if (mark)
                x->src[i] |= AE_ECHO;
        }
    for (uint32_t i = 0; i < NINST; i++) {
        struct a1inst *in = &a->in[i];
        if (in->ser && ARP_LEG(in->port) && in->src < SRC_ARP && in->root == note)
            s = 1, k = 2;                     /* engine-only (MIDI in, a module's rec = 0 note): not a sender */
    }
    return (s != 0) | k;
}

/* a normal press of the pitch on a port with a MIDI-out leg: its on is sent */
static uint32_t ar_mark(volatile struct arp *x, struct a1 *a, uint32_t leg, uint32_t note)
{
    return leg ? ar_sent(x, a, note & 0x7Fu, 1) : 0;
}

/* far table FAR_ARP_IN (a1_press after its admission checks: phase, then the bulk / quarantine refusal with its
 * rejection record; and after chord(); Main task, outside the engine lock):
 * 1 if the arp takes the press. sp = src | n << 8 | vel << 16, out = the n engine notes. */
uint32_t far_arp_in(uint32_t sp, uint32_t note, uint32_t outp, uint32_t d)
{
    volatile struct arp *x = ARP_P;
    struct a1 *a = (struct a1 *)A1_AT;
    uint32_t src = sp & 0xFFu, n = sp >> 8 & 0xFFu, vel = sp >> 16 & 0x7Fu, leg = ARP_LEG(x->port);
    const uint8_t *out = (const uint8_t *)outp;
    x->took = 0;                              /* this press's MIDI-out leg: sent unless taken below */
    if (x->emit || src >= SRC_ARP || !n || n > 4u)
        return 0;
    uint32_t h = leg ? ar_sent(x, a, note & 0x7Fu, 0) : 1u, sent = h & 1u;
    if (h == 2u)
        x->took = TOOK_RETRIG;                /* held only by arp presses whose on was skipped: no retrigger off */
    if (d == ARP_IN_BUSY)                     /* a press while busy: dropped, its stock on is sent; the arp's holders
                                                 of the pitch then own a sent note */
        return ar_mark(x, a, leg, note), 0;
    uint32_t cfg = arp_cfg();
    if (!(CF_FLAGS(cfg) & AF_ON))
        return ar_mark(x, a, leg, note), 0;   /* a normal press: its on is sent */
    uint32_t sem = eng_lock(), no = Q0->bulk || Q0->rej_quar;
    if (no) {                                 /* inside a bulk SHI interval, or in rejection quarantine: refused and
                                                 recorded as a1_press refuses, before the set changes (latch included);
                                                 its off is then swallowed */
        if (Q0->bulk)
            Q0->c_bulk_drop++;
        q0_rej_add(src, (int32_t)note);
    }
    eng_unlock(sem);
    if (no)
        return ar_mark(x, a, leg, note), 1;   /* refused: its stock on is sent */
    if ((CF_FLAGS(cfg) & AF_LATCH) && !ae_held(x)) {   /* latch: the first press after all keys are up replaces */
        uint32_t m = 0;
        for (uint32_t i = 0; i < x->n; i++)
            if (ae_live(x, i))
                m |= 1u << i;
        ae_compact(x, m);
    }
    if (n > ARP_N - x->n) {                   /* the whole press or nothing: not taken, it plays as a normal press and
                                                 its off goes the normal way */
        x->c_full = (uint8_t)(x->c_full + n < 0xFFu ? x->c_full + n : 0xFFu);
        return ar_mark(x, a, leg, note), 0;   /* not taken: a normal press, its on is sent */
    }
    if (!ae_live_n(x)) {
        x->cyc = 0;
        x->fl |= AX_PEND;
    }
    uint32_t ser = ++a->serial;
    if (!ser)
        ser = ++a->serial;
    for (uint32_t v = 0; v < n; v++) {
        uint32_t i = x->n;
        x->note[i] = out[v];
        x->vel[i] = (uint8_t)(vel ? vel : 1u);
        x->src[i] = (uint8_t)(src | (leg ? AE_LEG | (sent ? AE_ECHO : 0u) : 0u));
        x->root[i] = (uint8_t)(note & 0x7Fu);
        x->ser[i] = ser;
        x->n = (uint8_t)(i + 1u);
    }
    sem = eng_lock();                         /* accepted, as a1_press accepts: older rejections of the pitch go */
    q0_rej_accept(src, (int32_t)note);
    eng_unlock(sem);
    if (!sent)
        x->took |= TOOK_ON;                   /* a sent pitch: this on goes out too (AE_ECHO), so the note's offs do */
    return 1;
}

/* far table FAR_ARP_OFF (a1_release_at): 1 if the off belongs to a press the arp holds: the newest press of (src,
 * root) not newer than the off's serial snap. query = 1 (an off staged while our transaction is busy, at its
 * arrival): only the decision, so its stock MIDI-out leg, which runs now, follows the owner the replay will find;
 * the set is not changed (the replay does that); query = ARP_OFF_BUSY (arp_reent: snap is the stage count before
 * the off, the snapshot is the serial now) is that query when the off was staged. When the stage list was full
 * (the off is not staged and a Stop is deferred) the off is applied now (the set is the arp's own RAM, and a re-entry runs in the Main task, never inside
 * a set walk), and the deferred Stop resets the whole set (AX_OVF). */
uint32_t far_arp_off(uint32_t src, uint32_t note, uint32_t snap, uint32_t query)
{
    volatile struct arp *x = ARP_P;
    if (src >> 16)                            /* arp_reent: a press while busy (src | vel << 16) */
        return far_arp_in((src & 0xFFu) | 1u << 8 | (src & 0x7F0000u), note, 0, ARP_IN_BUSY);
    x->took = 0;                              /* this off's MIDI-out leg: sent unless taken below */
    if (query == ARP_OFF_BUSY) {
        query = Q0->noff != snap;             /* staged: decision only; not staged: applied now, the set reset */
        if (!query)
            x->fl |= AX_OVF;
        snap = ((struct a1 *)A1_AT)->serial;
    }
    if (x->emit || src >= SRC_ARP)
        return 0;
    uint32_t found = 0, echo = 0;
    uint32_t best = 0;
    for (uint32_t i = 0; i < x->n; i++)
        if ((x->src[i] & (AE_SRC | AE_REL)) == src && x->root[i] == (note & 0x7Fu)
                && ser_cmp(x->ser[i], snap) <= 0 && (!found || ser_cmp(x->ser[i], best) > 0))
            best = x->ser[i], found = 1, echo = x->src[i] & AE_ECHO;
    if (!found)
        return 0;
    struct a1 *a = (struct a1 *)A1_AT;        /* the newest press across both owners takes the off */
    for (uint32_t i = 0; i < NINST; i++) {
        struct a1inst *in = &a->in[i];
        if (in->ser && in->src == src && in->root == (uint8_t)(note & 0x7Fu) && ser_cmp(in->ser, snap) <= 0
                && ser_cmp(in->ser, best) > 0)
            return 0;
    }
    if (!echo)
        x->took = TOOK_OFF;                   /* an AE_ECHO press's on was sent (#45): its off goes out */
    if (query)
        return 1;
    uint32_t latch = (CF_FLAGS(arp_cfg()) & (AF_ON | AF_LATCH)) == (AF_ON | AF_LATCH), m = 0;
    for (uint32_t i = 0; i < x->n; i++) {
        if (x->ser[i] != best || (x->src[i] & (AE_SRC | AE_REL)) != src)
            continue;
        if (latch && ae_live(x, i))
            x->src[i] |= AE_REL;              /* latched: still played, its press is up */
        else
            m |= 1u << i;
    }
    ae_compact(x, m);
    return 1;
}

/* far table FAR_ARP_RESET (release_all: why; all_off: why | 0x100). The caller releases or cuts the voices; the arp
 * forgets its note (its MIDI-out off goes at the next tick). Stop / mute: the sequencer's entries become dead (never
 * played again; the stock offs that follow in the same pass are still recognised, so their MIDI-out legs stay
 * suppressed) and the tick removes them at the Stop or mute edge; choke and sample edit: the set stays (the next
 * step plays); CC 123, project load, boot: all go. The Stop deferred for a stage-list overflow (AX_OVF): offs were
 * lost, so every entry goes out of play: latched released ones are removed, the others become dead and are removed
 * by their own offs, which stay recognised (no MIDI-out off, no engine release). */
uint32_t far_arp_reset(uint32_t why, uint32_t b, uint32_t c, uint32_t d)
{
    (void)b; (void)c; (void)d;
    volatile struct arp *x = ARP_P;
    uint32_t w = why & 0xFFu, m = 0, ovf = w == WHY_STOP && (x->fl & AX_OVF);
    x->fl &= (uint8_t)~AX_OVF;
    for (uint32_t i = 0; i < x->n; i++) {
        if (w == WHY_STOP) {
            if (ovf && (x->src[i] & AE_REL))
                m |= 1u << i;                 /* overflow: a latched, released press goes (no off will come) */
            else if (ovf || (x->src[i] & AE_SRC) == SRC_SEQ)
                x->src[i] |= AE_DEAD;
        } else if (w != WHY_CHOKE && w != WHY_EDIT) {
            m |= 1u << i;
        }
    }
    ae_compact(x, m);
    if (x->sounding) {
        if (!x->mo)
            x->mo = x->sounding;
        x->sounding = 0;
    }
    x->fl &= (uint8_t)~AX_PEND;
    return 1;
}

/* The grid. Running: the transport pulse 0..95 (swung, bar-locked), *per = 96. Stopped: a virtual pulse 0..191 at
 * the project tempo, *per = 192, counted by this Main-pass timer (pulse = 60000 / (24 x bpm) ms, the fraction
 * carried; a pass later than 100 ms counts as 100 ms: missed time is dropped, never replayed). The timer restarts
 * at the press that starts the set (key sync). 0xFFFF: the sequencer is not readable. */
static uint32_t ar_grid(volatile struct arp *x, uint32_t now, uint32_t *per, uint32_t *run, uint32_t *gap)
{
    uint32_t seq = link(ARP_SEQ_CELL, 0), tr = CLK->transport;
    if (!seq)
        return 0xFFFFu;
    uint32_t w20 = link(seq, 0x20) & 0xFFu;
    if (w20 >= 96u)
        return 0xFFFFu;
    uint32_t r = tr && link(tr, 0) == TRANSPORT_VT && (link(tr, 0x10) & 0xFFu) && link(tr, 8) == seq;
    uint16_t t16 = (uint16_t)now;
    uint32_t bpm = tempo(seq), dt = (uint16_t)(t16 - x->t_prev);
    if (bpm < 20u)
        bpm = 20u;
    *gap = r && dt > 120000u / bpm;           /* running: more than half a wrap (48 pulses) since the last pass */
    if (!r) {
        uint32_t acc = x->acc + (dt < 100u ? dt : 100u) * bpm * 24u, k = acc / 60000u;
        x->acc = (uint16_t)(acc - k * 60000u);
        x->vp = (uint8_t)((x->vp + k) % 192u);
    }
    x->t_prev = t16;
    *run = r;
    *per = r ? 96u : 192u;
    return r ? w20 : x->vp;
}

/* Track 8 muted (issue #49): the arp checks both playback mute gates before emitting an engine note.
 * Unreadable or invalid mute data counts as not muted. One bounded loop checks both gates. */
static uint32_t ar_muted(uint32_t r)
{
    uint32_t d[2] = {link(link(link(link(DISP_PTR, 0), 0), 0x38), 0) - 4u + 0x8Cu, r + 0x1A48u};
    for (uint32_t i = 0; i < 2; i++) {
        uint32_t stride = link(d[i], 0), base = link(d[i], 8);
        if (stride - 1u > 0xFFu || link(d[i], 4) != 8u || base - 0x20000000u > 0x50000u)
            continue;
        uint32_t obs = link(base + stride * A1_T, 0);
        if (obs - 0x20000000u < 0x50000u && *(volatile uint8_t *)obs_el(obs, 0))
            return 1;
    }
    return 0;
}

/* far table FAR_ARP_TICK (hook_a1_tick, Main task, no core-0 transaction open). Bounded: at most one off and one
 * on (two router calls each) and one pending MIDI-out off per pass; no loop waits; missed steps are dropped. */
uint32_t far_arp_tick(uint32_t ap, uint32_t b, uint32_t c, uint32_t d)
{
    (void)b; (void)c; (void)d;
#if PWRITE
    pw_service();                             /* write to pattern: at most one request per Main pass (pwrite.c) */
#endif
    struct a1 *a = (struct a1 *)ap;
    volatile struct arp *x = ARP_P;
    uint32_t now = TICK(), cfg = arp_cfg(), r = router();
    uint16_t t16 = (uint16_t)now;
    x->took = 0;                              /* a take's MIDI-out leg is in its own router call: never later */
    if (x->mo && r) {                         /* a reset forgot a note: its MIDI-out off */
        ar_off(r, x->mo - 1u, AP_PORT_OUT);
        x->mo = 0;
    }
#if ENGINE_OPT
    if (b)                                    /* no track-8 engine (pub 0): the write service and a forgotten note's
                                                 MIDI-out off only, no arp step */
        return 0;
#endif
    uint32_t per = 0, run = 0, gap = 0, g = ar_grid(x, now, &per, &run, &gap);
    if (g > 0xFFu)
        return 1;
    if (run != !!(x->fl & AX_RUN)) {          /* Play: the cycle restarts on the bar; Stop: key-synced free grid */
        x->fl ^= AX_RUN;
        x->at = t16;
        if (run) {
            x->cyc = 0;
            x->glast = (uint8_t)((g + per - 1u) % per);
        } else {                              /* Stop: the sequencer's entries and the latch's released ones go */
            uint32_t m = 0;
            for (uint32_t i = 0; i < x->n; i++)
                if ((x->src[i] & AE_SRC) == SRC_SEQ || (x->src[i] & AE_REL))
                    m |= 1u << i;
            ae_compact(x, m);
            x->org = (uint8_t)g;
            x->glast = (uint8_t)g;
            x->acc = 0;
        }
    }
    uint32_t np = gap ? 0u : (g + per - x->glast) % per;   /* a gap: resync, no step in this pass */
    x->glast = (uint8_t)g;
    if (np && run)
        x->at = t16;
    if (gap && x->sounding)
        ar_note_off(x, r);
    if (a->phase != PH_OPEN) {                /* admission closed (recovery, DSP load): the epoch reset clears the
                                                 voices; nothing is emitted until it opens */
        if (x->sounding) {
            x->mo = x->sounding;
            x->sounding = 0;
        }
        return 1;
    }
    if (!(CF_FLAGS(cfg) & AF_ON)) {           /* off: the note goes; released entries go, held ones are marked dead */
        if (x->sounding)
            ar_note_off(x, r);
        uint32_t m = 0;
        for (uint32_t i = 0; i < x->n; i++) {
            if (x->src[i] & AE_REL)
                m |= 1u << i;
            else
                x->src[i] |= AE_DEAD;
        }
        ae_compact(x, m);
        x->fl &= (uint8_t)~AX_PEND;
        return 1;
    }
    if (!(CF_FLAGS(cfg) & AF_LATCH)) {        /* latch off: the released entries go */
        uint32_t m = 0;
        for (uint32_t i = 0; i < x->n; i++)
            if (x->src[i] & AE_REL)
                m |= 1u << i;
        ae_compact(x, m);
    }
    uint32_t live = ae_live_n(x), rate = ar_pulses(CF_RATE(cfg)), off = 0, due = 0;
    if (x->sounding && np) {
        if (np >= x->gate_left)
            off = 1;
        else
            x->gate_left = (uint8_t)(x->gate_left - np);
    }
    if (x->sounding && run && (uint16_t)(t16 - x->at) > ARP_WATCH_MS)
        off = 1;                              /* the clock stopped under a running transport (a chosen timeout) */
    if (live && np && ((g + per - (run ? 0u : x->org)) % per) % rate < np)
        due = 1;                              /* a step boundary lies in the pulses since the last pass: one step */
    if (x->fl & AX_PEND) {                    /* stopped: the first note at the press, the timer from it; running: the
                                                 next step boundary (quantised start) */
        x->fl &= (uint8_t)~AX_PEND;
        if (live && !run) {
            due = 1;
            x->org = (uint8_t)g;
            x->acc = 0;
        }
    }
    if (ar_muted(r)) {                        /* track 8 muted: nothing is emitted; at the mute edge the sequencer's
                                                 entries become dead (their stock offs, which may come later, are still
                                                 recognised; Stop removes what is left) */
        if (!(x->fl & AX_MUTED)) {
            x->fl |= AX_MUTED;
            for (uint32_t i = 0; i < x->n; i++)
                if ((x->src[i] & AE_SRC) == SRC_SEQ)
                    x->src[i] |= AE_DEAD;
            live = ae_live_n(x);
        }
        due = 0;
        off |= x->sounding != 0;
    } else {
        x->fl &= (uint8_t)~AX_MUTED;
    }
    if (!live && x->sounding)
        off = 1;                              /* the set emptied (not latched): the note ends */
    if (due && x->sounding)
        off = 1;                              /* one note at a time: the previous one goes first */
    if (off && x->sounding)
        ar_note_off(x, r);
    if (due && r) {
        uint32_t vel = 100, note = fold((int32_t)ar_next(x, CF_MODE(cfg), CF_OCT(cfg), &vel));
        x->emit = 1;
        ar_on(r, note, vel, AP_PORT_ENG);
        x->emit = 0;
        ar_on(r, note, vel, AP_PORT_OUT);
        x->sounding = (uint8_t)(note + 1u);
        x->gate_left = (uint8_t)ar_gate(CF_RATE(cfg), CF_GATE(cfg));
        x->c_steps++;
    }
    return 1;
}

/* ---- settings edits: synth page 3, SysEx 11 ---------------------------------------------------------- */

/* tm_edit callback: arg = op | field << 8 | value << 16 (AO_SET, AO_TOGGLE: value = flag bits); AO_ALL: arg >> 8 =
 * the cfg packing. Values are checked by the callers. */
static uint32_t arp_edit_cb(volatile uint8_t *b, uint32_t arg)
{
    volatile uint8_t *p = b + AR_AT;
    if (!ar_valid(p)) {
        if (!all_zero(p, AR_LEN))
            return TS_READONLY;               /* invalid content (plays as off): never overwritten */
        p[0] = 'A';
        p[1] = 'R';
        p[2] = AR_VER;
        p[3] = AM_UP;
        p[4] = 3;
        p[5] = 0;
        p[6] = 1;
        p[7] = 0;
    }
    uint32_t op = arg & 0xFFu, f = arg >> 8 & 0xFFu, v = arg >> 16 & 0xFFu;
    if (op == AO_ALL) {
        uint32_t c = arg >> 8;
        p[3] = (uint8_t)CF_MODE(c);
        p[4] = (uint8_t)CF_RATE(c);
        p[5] = (uint8_t)CF_OCT(c);
        p[6] = (uint8_t)CF_GATE(c);
        p[7] = (uint8_t)CF_FLAGS(c);
    } else if (op == AO_TOGGLE) {
        p[7] ^= (uint8_t)v;
    } else {
        p[f] = (uint8_t)v;
    }
    return TS_OK;
}

/* the one write path (tm_edit is inlined where it is called: one copy here) */
static __attribute__((noinline)) uint32_t ar_write(struct state *s, uint32_t arg)
{
    return tm_edit(s, arp_edit_cb, arg);
}

static void arp_edit(struct state *s, uint32_t arg)
{
    volatile struct pg *g = PG;
    uint32_t r = ar_write(s, arg);
    g->res = (uint8_t)r;
    if (r == TS_OK) {
        g->edits++;
    } else {
        g->refused++;
        g->fb = PG_FB_FRAMES;
    }
}

/* far table FAR_ARP_PAD: the arp page's pad (pg_pad, Main task). */
uint32_t far_arp_pad(uint32_t sp, uint32_t pv, uint32_t c, uint32_t d)
{
    (void)c; (void)d;
    struct state *s = (struct state *)sp;
    uint32_t pad = pv & 0xFFu;
    if (pad == 0)
        arp_edit(s, AO_TOGGLE | AF_ON << 16);
    else if (pad <= AM_N)
        arp_edit(s, AO_SET | 3u << 8 | (pad - 1u) << 16);
    else if (pad == 7)
        arp_edit(s, AO_TOGGLE | AF_LATCH << 16);
    else if (pad >= 8 && pad < 8u + AR_RATES)
        arp_edit(s, AO_SET | 4u << 8 | (pad - 8u) << 16);
    else if (pad >= 16 && pad < 16u + AR_OCTS)
        arp_edit(s, AO_SET | 5u << 8 | (pad - 16u) << 16);
    else if (pad >= 24 && pad < 24u + AR_GATES)
        arp_edit(s, AO_SET | 6u << 8 | (pad - 24u) << 16);
    return 1;
}

#endif /* FAR_PART */

#if PG_LEDS_HERE                        /* in the far blob, or the main blob with PG_LEDS_MAIN (synthpage.c) */
/* The arp page's lights (pg_nav_leds, UI task). Dim = option, bright = in force; pad 1 white while an arp note
 * sounds. */
static void arp_leds(struct state *s, const uint8_t *t)
{
    uint32_t cfg = arp_cfg(), on = CF_FLAGS(cfg) & AF_ON;
    uint8_t v = on ? BRIGHT : DIM;
    if (on && ARP_P->sounding)
        pad_rgb(s, t, 0, BRIGHT, BRIGHT, BRIGHT);
    else
        pad_rgb(s, t, 0, 0, v, 0);
    for (uint32_t i = 0; i < AM_N; i++) {
        v = i == CF_MODE(cfg) ? BRIGHT : DIM;
        pad_rgb(s, t, 1 + i, v, v >> 2, 0);
    }
    v = CF_FLAGS(cfg) & AF_LATCH ? BRIGHT : DIM;
    pad_rgb(s, t, 7, v, 0, 0);
    for (uint32_t i = 0; i < AR_RATES; i++) {
        v = i == CF_RATE(cfg) ? BRIGHT : DIM;
        pad_rgb(s, t, 8 + i, 0, v, v);
    }
    for (uint32_t i = 0; i < AR_OCTS; i++) {
        v = i == CF_OCT(cfg) ? BRIGHT : DIM;
        pad_rgb(s, t, 16 + i, 0, 0, v);
    }
    for (uint32_t i = 0; i < AR_GATES; i++) {
        v = i == CF_GATE(cfg) ? BRIGHT : DIM;
        pad_rgb(s, t, 24 + i, v, v, v);
    }
}
#endif

#if FAR_PART
/* far table FAR_ARP_SYSEX (cmd 11, Main task):
 *   11 00                              status
 *   11 01 flags mode rate octaves gate set all (flags bit 0 on, bit 1 latch; octaves 0..2 = 1..3; gate 0..3)
 * Reply (21 bytes): F0 00 20 29 00 60 11 <sub> <status> <why> <rec> <flags> <mode> <rate> <octaves> <gate> <held>
 * <steps, 2 x 7 bits> <full> F7; rec 0 unset, 1 valid, 2 invalid or not writable; a set out of range: status
 * REFUSED, nothing written. Malformed: no reply. */
uint32_t far_arp_sysex(uint32_t sp, uint32_t mp, uint32_t len, uint32_t d)
{
    (void)d;
    struct state *s = (struct state *)sp;
    const uint8_t *m = (const uint8_t *)mp;
    uint32_t sub = len >= 9 ? m[7] : 0xFFu, status = TS_REFUSED;
    if (len == 9 && sub == 0x00) {
        status = tm_attach(s) && tm()->store ? TS_OK : TS_REFUSED;
    } else if (len == 14 && sub == 0x01) {
        if (m[8] <= 3u && m[9] < AM_N && m[10] < AR_RATES && m[11] < AR_OCTS && m[12] < AR_GATES)
            status = ar_write(s, AO_ALL | ((uint32_t)m[8] | (uint32_t)m[9] << 4 | (uint32_t)m[10] << 8
                                                       | (uint32_t)m[11] << 12 | (uint32_t)m[12] << 16) << 8);
    } else {
        return 1;
    }
    struct tm *t = tm();
    uint32_t cfg = arp_cfg();
    const volatile uint8_t *p = t && t->store ? (const volatile uint8_t *)(t->store + TM_EXT + AR_AT) : 0;
    volatile uint8_t r[21];
    header(r, 0x11);
    r[7] = (uint8_t)sub;
    r[8] = (uint8_t)status;
    r[9] = t ? t->why : TM_NOSTORE;
    r[10] = (uint8_t)(!p || all_zero(p, AR_LEN) ? 0 : (cfg & CF_OK) ? 1 : 2);
    r[11] = (uint8_t)CF_FLAGS(cfg);
    r[12] = (uint8_t)CF_MODE(cfg);
    r[13] = (uint8_t)CF_RATE(cfg);
    r[14] = (uint8_t)CF_OCT(cfg);
    r[15] = (uint8_t)CF_GATE(cfg);
    r[16] = ARP_P->n;
    put7(r + 17, ARP_P->c_steps & 0x3FFFu, 2);
    r[19] = (uint8_t)(ARP_P->c_full > 127u ? 127u : ARP_P->c_full);
    r[20] = 0xF7;
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
    return 1;
}
#endif /* FAR_PART */

/* Main blob: the two calls into the far blob that devkit_abi3.c and synthpage.c make. */
static void arp_pad(struct state *s, uint32_t pv)
{
    far_call(FAR_ARP_PAD, (uint32_t)s, pv, 0, 0);
}

static void arp_sysex(struct state *s, const uint8_t *m, uint32_t len)
{
    far_call(FAR_ARP_SYSEX, (uint32_t)s, (uint32_t)m, len, 0);
}
