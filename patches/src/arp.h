/* Arpeggiator state (ARP, issue #31): one fixed block between struct q0 and struct sc (devkit_abi3.c ARP_AT; struct
 * sc stays the lowest block and the heap limit). Zeroed by hook_boot with the other blocks. Written by the Main task
 * only (the tap, the tick, the resets, the page's pad handler and SysEx 11); the UI task reads it for the lights.
 * 0x58 bytes with 32-bit press serials: the block's size moves struct sc and the heap
 * floor, and the main blob's code size follows that address (measured: +56 B over 0x48; larger sizes and a block
 * below struct sc cost more). Code: arp.c. */
#ifndef ARP_H
#define ARP_H
#define ARP_SIZE        0x58u
#define ARP_N           8u              /* held set capacity (two 4-note presses) */
#define AE_REL          0x80u           /* src flag: the press was released while latched (still played) */
#define AE_DEAD         0x40u           /* src flag: taken while the arp was on, now off: never played, its off is
                                           still taken */
#define AE_ECHO         0x20u           /* src flag: the pitch's stock MIDI-out note was sent while this press held it
                                           (its own on, or a normal press of the pitch): the stock final off goes out */
#define AE_LEG          0x10u           /* src flag: the press came on a port with a stock MIDI-out leg (ARP_LEG): only
                                           such presses hold the pitch's MIDI-out note (issue #45) */
#define AE_SRC          0x0Fu
#define AC_DOWN         0x01u           /* cyc: Up/Down is descending */
#define AC_STARTED      0x02u           /* cyc: a note was played since the cycle start */
#define AC_OCT(c)       ((c) >> 2 & 3u) /* cyc: As played, the octave of the next entry */
#define AC_IDX(c)       ((c) >> 4)      /* cyc: As played, the index of the next entry */
#define AX_PEND         0x01u           /* fl: the set became non-empty, the first note is due at the next tick */
#define AX_RUN          0x02u           /* fl: the transport ran at the last tick */
#define AX_MUTED        0x04u           /* fl: track 8 was muted at the last tick */
#define ARP_OFF_BUSY    2u              /* far_arp_off: an off inside our transaction (arp_reent), snap = stage count */
#define ARP_IN_BUSY     1u              /* far_arp_in d: a press inside our transaction (dropped, its stock on sent):
                                           the MIDI-out decision only (issue #45) */
#define AX_OVF          0x08u           /* fl: an off overflowed the stage list; the deferred Stop resets the set */

struct arp {
    uint8_t note[ARP_N];                /* engine note of each held entry (chord() output: octave and fold applied) */
    uint8_t vel[ARP_N];
    uint8_t src[ARP_N];                 /* SRC_* of the press | AE_REL | AE_DEAD */
    uint8_t root[ARP_N];                /* the press's root: its off is matched by source and root, as an a1 instance */
    uint32_t ser[ARP_N];                /* the press's a1 serial (32 bits, compared as a1 compares its instances:
                                           unique per press, wrap order exact against instances and staged-off
                                           snapshots) */
    uint8_t n;                          /* entries in use, arrival order */
    uint8_t last;                       /* last note played in the pitch modes (unfolded: entry note + 12 x octave) */
    uint8_t cyc;                        /* AC_* cycle state */
    uint8_t sounding;                   /* engine note + 1 of the arp note now on, 0 none */
    uint8_t gate_left;                  /* pulses until its off */
    uint8_t emit;                       /* 1 while the arp's own engine router call runs (the tap tags it SRC_ARP) */
    uint8_t fl;                         /* AX_* */
    uint8_t glast;                      /* grid value at the last tick (transport pulse 0..95 or virtual 0..191) */
    uint8_t org;                        /* stopped: the virtual pulse of the key-sync origin */
    uint8_t mo;                         /* MIDI-out note + 1 whose off is still to send (after a reset) */
    uint8_t vp;                         /* stopped: virtual pulse 0..191 (the Main-pass timer) */
    uint8_t c_full;                     /* notes not taken: the set was full (saturating) */
    uint8_t took;                       /* TOOK_* (notetap.c): the press or off now in the tap is the arp's (its stock
                                           MIDI-out leg is skipped), or a press's retrigger off is an arp press's;
                                           written by every take and off decision, cleared each tick */
    uint8_t port;                       /* router port of the press now in the tap (hook_ron): it has a stock MIDI-out
                                           leg only on the pad and sequencer ports (ARP_LEG, issue #45) */
    uint16_t at;                        /* tick (low 16 bits) of the last transport-pulse change (clock-loss watchdog) */
    uint16_t t_prev;                    /* tick of the last tick call (stopped timer) */
    uint16_t acc;                       /* stopped timer: pulse fraction, units of 1/60000 pulse */
    uint16_t rng;                       /* xorshift16 state (Random); 0 = not seeded */
    uint16_t c_steps;                   /* arp notes played (wraps) */
};
_Static_assert(sizeof(struct arp) == ARP_SIZE, "arp block");
#define ARP_P           ((volatile struct arp *)ARP_AT)
#endif
