/* Copyright (c) 2026 km. SPDX-License-Identifier: MIT.
 * Read-only inventory, Main task, owned SRAM. No module/marker/CBR2 scratch.
 * Files are reopened for each bounded piece: no shared FIL retained between passes.
 * Never runs recovery, populate, a DSP load, or any FatFs call with a dirty volume.
 * Wire rows are 96 bytes, nibble encoded; see tools/module.py decode_card_row. */
#define EL_ROWS 64u
#define EL_BYTES 96u
#define EL_SEEK ((uint32_t (*)(uint32_t, uint32_t))0x080223D9u)
#define EL_EXISTS ((fn4)0x0802E25Bu)
#define EL_PATH ((fn4)0x0802E44Du)
struct el {
    uint16_t nonce;
    uint8_t state, flags, count, step, obj, current;
    uint8_t ready, target, matches, defstate, defname[8];
    uint16_t seen;
    uint16_t pad;
    uint32_t off, whole, body, last;
    uint32_t dir[16], fi[40];
    char path[64];
    uint8_t row[EL_BYTES];
};
_Static_assert(sizeof(struct el) <= LIST_SIZE, "inventory fits owned SRAM");
#define EL ((struct el *)LIST_AT)
enum { EL_DEFAULT, EL_PACK_OPEN, EL_PACK_READ, EL_ENGINE_OPEN, EL_ENGINE_READ, EL_FILE };

__attribute__((noinline)) static void el_copy(uint8_t *d, const volatile uint8_t *s, uint32_t n)
{
    for (uint32_t i = 0; i < n; i++)
        d[i] = s[i];
}
static void el_word(uint8_t *d, uint32_t v)
{
    *(uint32_t *)d = v;
}
__attribute__((noinline)) static uint32_t el_text(uint8_t *d, const volatile uint8_t *s, uint32_t n)
{
    uint32_t bad = 0, i = 0;
    for (; i < n && s[i]; i++) {
        uint32_t c = s[i];
        if (c < 32 || c >= 127) bad = 1;
        d[i] = c >= 32 && c < 127 ? (uint8_t)c : '?';
    }
    return bad || (i == n && s[i]);
}
static uint32_t el_ptr(const char *path, uint8_t *nm)
{
    volatile uint8_t b[EP_CFG_MAX + 1];
    int32_t n = ep_slurp(path, b, sizeof b);
    if (n == EP_ABSENT) return 0;
    if (n < 0) return 4;
    int32_t r = n > EP_CFG_MAX ? -1 : ep_parse(b, n, (volatile char *)nm);
    return r == 0 ? 1 : r > 0 ? 2 : 3;
}
/* Exact .cfg state stays separate from the effective read-only .cfo fallback. */
__attribute__((noinline)) static void el_pointer(uint8_t *r)
{
    r[4] = el_ptr(EL->path, r + 32);
    r[6] = r[4];
    el_copy(r + 40, r + 32, 8);
    if (!r[6]) {
        uint32_t n = 0;
        while (EL->path[n]) n++;
        EL->path[n - 1] = 'o';
        r[6] = el_ptr(EL->path, r + 40);
        if (r[6]) r[3] |= 8;
    }
    r[5] = r[6] ? 1 : 0;
}
__attribute__((noinline)) static void el_close(void)
{
    if (EL->obj) F_CLOSEDIR((uint32_t)EL->dir);
    EL->obj = 0;
}
__attribute__((noinline)) static void el_end(uint32_t bad)
{
    el_close();
    if (bad) EL->flags |= 3;
    EL->state = bad ? 3 : 2;
}
static uint8_t *el_new(uint32_t type)
{
    if (EL->count >= EL_ROWS) { EL->flags |= 1; el_end(0); return 0; }
    uint8_t *r = EL->row;
    for (uint32_t i = 0; i < EL_BYTES; i++) r[i] = 0;
    r[0] = type;
    r[1] = 127;
    return r;
}
static uint32_t el_root(char *p, uint32_t engines)
{
    uint32_t *w = (uint32_t *)p;
    w[0] = engines ? 0x652F3A31u : 0x522F3A31u;
    w[1] = engines ? 0x6E69676Eu : 0x68747968u;
    w[2] = engines ? 0x00007365u : 0x0000006Du;
    return engines ? 10 : 9;
}
/* Names from this FatFs are OEM bytes, not UTF-8. ASCII is exact; mark non-ASCII
 * lossy and expose the actual short alias, never reinterpret bytes as UTF-8. */
static void el_entry(void)
{
    struct el *l = EL;
    uint8_t *fi = (uint8_t *)l->fi;
    const uint8_t *nm = fi + FI_NAME;
    const uint8_t *sn = fi[FI_ALT] ? fi + FI_ALT : nm;
    if (l->step == EL_PACK_READ) {
        if ((fi[FI_ATTR] & AM_SKIP) != 0x10 || nm[0] < '0' || nm[0] > '3'
                || nm[1] < '0' || nm[1] > '9' || nm[2] != '_') return;
        uint32_t p = (nm[0] - '0') * 10 + nm[1] - '0' + 1;
        if (p != l->target) return;
        if (l->matches) { l->row[3] = (l->row[3] & ~1u) | 2; l->row[2] = 0; return; }
        l->matches = 1;
        uint8_t *r = el_new(1);
        if (!r) return;
        r[1] = p;
        if (el_text(r + 8, nm + 3, 24)) {
            r[3] |= 4;
            for (uint32_t i = 8; i < 32; i++) r[i] = 0;
            el_text(r + 8, sn, 12);
        }
        /* Compare the stock's current table with this exact LFN. No populate:
         * a stale/missing/unknown index is explicitly disabled for writes. */
        uint32_t vol = *(volatile uint32_t *)0x20006E20u;
        if (in_sram(vol, 4) && EL_EXISTS(vol, p, 0, 0)) {
            uint32_t path = EL_PATH(vol, p, 0, 0), same = in_sram(path, 64);
            for (uint32_t i = 0; same && i < 33; i++) {
                uint8_t c = *(volatile uint8_t *)(path + 10 + i);
                if (c != nm[i]) same = 0;
                if (!nm[i]) break;
                if (i == 32) same = 0;
            }
            if (same) { r[3] |= 1; r[2] = p + 1; }
        }
        uint32_t k = el_root(l->path, 0);
        l->path[k++] = '/';
        for (uint32_t i = 0; i < 12 && sn[i]; i++) l->path[k++] = sn[i];
        l->path[k++] = '/'; l->path[k++] = 'e'; l->path[k++] = 'n'; l->path[k++] = 'g';
        l->path[k++] = 'i'; l->path[k++] = 'n'; l->path[k++] = 'e'; l->path[k++] = '.';
        l->path[k++] = 'c'; l->path[k++] = 'f'; l->path[k++] = 'g'; l->path[k] = 0;
        el_pointer(r);
        if (!r[6]) {
            r[5] = 2; r[6] = l->defstate & 7; r[3] |= l->defstate & 8;
            el_copy(r + 40, l->defname, 8);
        }
    } else {
        if (fi[FI_ATTR] & AM_SKIP) return;
        uint32_t n = 0;
        while (n < 13 && nm[n]) n++;
        uint8_t name[8] = {0};
        if (ep_parse((volatile uint8_t *)nm, n, (volatile char *)name) != 1) return;
        uint8_t *r = el_new(2);
        if (!r) return;
        for (uint32_t i = 0; i < 8; i++) r[8 + i] = r[32 + i] = r[40 + i] = name[i];
        cd_epath(l->path, (volatile char *)name);
        for (uint32_t i = 0; i < 12 && l->path[11 + i]; i++) r[48 + i] = l->path[11 + i];

        l->off = 0;
        l->whole = l->body = 0xFFFFFFFFu;
        l->step = EL_FILE;
    }
}
static void el_step(void)
{
    struct el *l = EL;
    uint8_t *fi = (uint8_t *)l->fi;
    uint32_t rc;
    if (l->step == EL_DEFAULT) {
        uint8_t *r = el_new(3);
        ep_cfgpath(l->path, 0, 'g');
        el_pointer(r);
        l->defstate = r[6] | (r[3] & 8); el_copy(l->defname, r + 40, 8);
        l->target = 1; l->step = EL_PACK_OPEN; l->ready = 1; l->count = 1;
    } else if (l->step == EL_PACK_OPEN || l->step == EL_ENGINE_OPEN) {
        el_root(l->path, l->step == EL_ENGINE_OPEN);
        rc = F_OPENDIR((uint32_t)l->dir, l->path);
        l->seen = 0; l->matches = 0;
        if (!rc) { l->obj = 1; l->step++; }
        else if (rc == FR_NO_FILE || rc == FR_NO_PATH) {
            if (l->step == EL_PACK_OPEN) l->step = EL_ENGINE_OPEN;
            else el_end(0);
        } else el_end(1);
    } else if (l->step == EL_PACK_READ || l->step == EL_ENGINE_READ) {
        rc = F_READDIR((uint32_t)l->dir, (uint32_t)l->fi);
        if (rc) { el_end(1); return; }
        if (!fi[FI_NAME] || ++l->seen > SCAN_MAX) {
            if (fi[FI_NAME]) l->flags |= 1;
            el_close();
            if (l->step == EL_PACK_READ) {
                l->step = ++l->target <= 31 ? EL_PACK_OPEN : EL_ENGINE_OPEN;
                if (l->matches) { l->ready = 1; l->count++; }
            } else el_end(0);
        } else el_entry();
    } else {
        uint8_t *r = l->row;
        uint32_t fil = file_b(0), got = 0;
        volatile uint32_t bw[PIECE / 4];
        volatile uint8_t *b = (volatile uint8_t *)bw;
        rc = fil ? F_OPEN(fil, l->path, FA_READ) : 1;
        if (rc) { r[7] = rc == FR_NO_FILE || rc == FR_NO_PATH ? 4 : 3; l->step = EL_ENGINE_READ; l->ready = 1; l->count++; return; }
        uint32_t size = link(fil, FIL_FSIZE);
        if (!l->off) el_word(r + 60, size);
        if (size != *(uint32_t *)(r + 60) || (size >> 15) || (l->off && EL_SEEK(fil, l->off))) rc = 1;
        if (!rc) rc = F_READ(fil, (uint32_t)b, PIECE, &got);
        uint32_t closed = F_CLOSE(fil);
        if (rc || closed || (!got && l->off < size)) {
            r[7] = 3; l->flags |= 2; l->step = EL_ENGINE_READ; l->ready = 1; l->count++; return;
        }
        if (!l->off) {
            r[7] = got >= CD_HDR && size == CD_HDR + 3 * bw[1]
                && cd_header(b) && bw[4] == CRBC_ID ? 1 : 2;
            if (got >= CD_HDR) {
                el_word(r + 68, bw[6]); el_word(r + 72, bw[5]);
                el_word(r + 76, bw[3]); el_word(r + 80, bw[4]);
                el_word(r + 84, bw[2]);
            }
        }
        l->whole = crc_add(l->whole, b, got);
        uint32_t skip = l->off ? 0 : got < CD_HDR ? got : CD_HDR;
        l->body = crc_add(l->body, b + skip, got - skip);
        l->off += got;
        if (l->off >= size) {
            el_word(r + 64, ~l->whole);
            if (~l->body != *(uint32_t *)(r + 84)) r[7] = 2;
            l->step = EL_ENGINE_READ; l->ready = 1; l->count++;
        }
    }
}
static void el_main(struct state *s)
{
    if (EL->state != 1) return;
    uint32_t expired = EL->flags & 4 || (uint32_t)(TV() - EL->last) >= 32768u;
    if (EL->ready && !expired) return;
    if (s->ld_phase != LD_IDLE && s->ld_phase != LD_DONE) return;
    struct eo *e = eo();
    if (!e || e->phase != EP_IDLE || !fs_state()) { EL->obj = 0; EL->flags |= 3; EL->state = 3; return; }
    uint32_t tr = CLK->transport;
    if (!expired && tr && link(tr, 0) == TRANSPORT_VT && (link(tr, 0x10) & 255)) return;
    SUSPEND_ALL();
    if (s->tok || fs_state() != 1) { RESUME_ALL(); return; }
    s->tok = TOK_MAIN;
    RESUME_ALL();
    if (expired) el_end(1); else el_step();
    SUSPEND_ALL(); s->tok = 0; RESUME_ALL();
}
static void el_cancel(void)
{
    EL->flags = 4;
}
static void el_cmd(struct state *s, const uint8_t *m, uint32_t len)
{
    uint32_t sub = m[7], answer = 0;
    if (len != (sub == 0x11 ? 11u : 12u)) return;
    if ((m[8] | m[9] | m[len - 2]) & 0x80) return;
    uint32_t nonce = (uint32_t)m[8] << 7 | m[9];
    uint8_t reply[208];
    SUSPEND_ALL();
    if (sub == 0x11 && (EL->state != 1 || EL->nonce != nonce)) {
        if (EL->state == 1 || s->tok || (s->ld_phase != LD_IDLE && s->ld_phase != LD_DONE) || fs_state() != 1)
            answer = 1;
        else {
            volatile uint32_t *p = (volatile uint32_t *)EL;
            for (uint32_t i = 0; i < sizeof(struct el) / 4; i++) p[i] = 0;
            EL->nonce = nonce; EL->state = 1;
        }
    } else if (EL->nonce != nonce) answer = 2;
    if (sub == 0x12 && !answer) {
        uint32_t index = m[10];
        if (EL->ready && index == EL->count - 1) { /* cached row */ }
        else if (EL->ready && index == EL->count && EL->state == 1) {
            EL->ready = 0; answer = 1;
        } else if (EL->state == 1 && index == EL->count) answer = 1;
        else answer = index < EL->count ? 2 : 3;
    }
    if (EL->nonce == nonce) EL->last = TV();
    header(reply, 0x10); reply[7] = sub; reply[8] = answer;
    reply[9] = nonce >> 7; reply[10] = nonce & 127;
    reply[11] = EL->state; reply[12] = EL->flags; reply[13] = EL->count;
    uint32_t n = 14;
    if (sub == 0x12) {
        reply[n++] = m[10];
        for (uint32_t i = 0; i < EL_BYTES; i++) {
            uint32_t b = answer ? 0 : EL->row[i];
            reply[n++] = b >> 4; reply[n++] = b & 15;
        }
    }
    reply[n++] = 0xF7;
    RESUME_ALL();
    uint32_t out[2] = {(uint32_t)reply, n};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}
