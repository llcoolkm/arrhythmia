/* Module files on the microSD card over SysEx (CARD_WRITE=1): write, delete, verify and list files in 1:/MODULES
 * without a card reader. Included by arrhythmia.c after devkit_abi3.c (it uses the loader's helpers). Host side:
 * tools/module.py card write / delete / verify / list.
 *
 * Staging. A file is first sent into the ITCM module area (the same 15 KB that cmd 04 and the picker use), 32 bytes
 * per message with an acknowledgement each, in order. Before anything touches the card, the staged bytes must have
 * the announced size, the announced CRC-32 (zlib) and pass mod_check() (CRM1/CRM2 magic, size field = file size,
 * entry points, header CRC). Starting a session stops a running module and closes a choosing picker; while a
 * session is open no module runs, so MOD_RAM holds the session data (struct cw).
 *
 * Where the card is used. Every file system call of the writer runs in the Main task, from hook_main (the loader
 * step, after the firmware's timer service has returned), one step per Main pass, the same way as the picker's
 * reads: never inside a firmware FatFs call. The firmware's FatFs users are the Main task (pack-switch indexing, the
 * SD automount timer callback, which runs inside the timer service, boot) and the Content task (file jobs). Running
 * in Main serialises the writer with the first group by construction (one task: their calls and ours follow one
 * another, never overlap); the token excludes the second for each step (TOK_MAIN: the Content task waits at the
 * start of its job loop while a step runs; no step while a Content job runs). The token is never kept across
 * passes: the Main task may wait for a Content job later in a pass (a stock request through the Content queue).
 * The scheduler is never suspended across card I/O. A step opens, uses and closes its file within the step (a
 * folder read keeps our own DIR open across steps), so no file object of ours is open while the firmware runs:
 * the shared file object B is borrowed only for a step and only while the firmware has it closed. Steps are small:
 * one directory entry, one start-cluster lookup, one rename, delete or entry removal, or up to 512 bytes of the file
 * in 256-byte pieces (below the 512-byte sector: FatFs copies them with the CPU between ITCM and the FIL buffer in
 * SRAM). Between steps the firmware's Main task may use the card (as any sequence of FatFs calls from one task); a
 * remount there invalidates our DIR, the next call gets the FatFs error and the job fails cleanly. Steps pause
 * while the sequencer runs (the transport object of the last pulse has its run flag set), so the writer's card I/O
 * never delays a playing pattern's timers.
 *
 * Transaction (NAME.MOD, with NAME.TMP and NAME.OLD as its only helpers):
 *   1. recovery (below); 2. NAME.TMP created new (FA_CREATE_NEW: it must not exist after recovery) and closed,
 *   appended to (open, seek to its size, write, close) up to 512 bytes per step, then read back the same way and
 *   compared byte for byte with the staged copy; 3. NAME.MOD renamed to NAME.OLD (if it
 *   exists); 4. NAME.TMP renamed to NAME.MOD (on failure NAME.OLD is renamed back); 5. NAME.OLD deleted.
 *   Any failure of a write ends with recovery again.
 * Recovery runs at the start of every job and after a failed write; it settles every X with an X.TMP or X.OLD in
 * 1:/MODULES. A rename on FAT writes the new directory entry before it removes the old one, and the two may sit in
 * different directory sectors, so after a power cut both entries can exist and share one cluster chain. Recovery
 * therefore compares start clusters (read from the FIL of a read-only open) before it frees anything:
 *   X.TMP: if X.MOD (or X.OLD) exists and starts at the same cluster, only the X.TMP directory entry is removed
 *          (its chain is X.MOD's); otherwise X.TMP is deleted (an unverified or uncommitted copy; X.MOD or X.OLD is the module).
 *   X.OLD: if X.MOD does not exist, X.OLD is renamed back to X.MOD; if both start at the same cluster, only the
 *          X.OLD entry is removed; otherwise X.OLD is deleted (X.MOD is the new file).
 * A folder with more than SCAN_MAX entries cannot be scanned whole: recovery then fails (W_RECOVER) and no job
 * runs (the list job's own cap of 32 shown entries is separate).
 * Removing only a directory entry uses the firmware's own volume, path lookup and entry removal calls (the steps of
 * f_unlink without the chain release) and the volume sync. Any recovery error stops the job (W_RECOVER) before
 * anything is written; every later job tries recovery again first. *.TMP and *.OLD in 1:/MODULES are reserved. At
 * the end of every job, a volume window that is still dirty (our last write failed) is written back with the
 * firmware's sync. A missing MODULES folder is created. Nothing outside 1:/MODULES is written; names are 1..8
 * characters A-Z 0-9 _ -.
 *
 * SysEx cmd 10 (F0 00 20 29 00 60 10 <sub> ... F7):
 *   10 00                        status
 *   10 01 n0..n7 s2 s1 s0 c4..c0 begin a write of NAME.MOD: name (ASCII, 0-padded), size 3x7 (26..15360), CRC-32 5x7
 *   10 02 o2 o1 o0 (hi lo)...    one piece: 1..32 bytes as nibble pairs at offset o (3x7) = bytes received so far
 *   10 03                        commit: checks, then the job runs in Main passes
 *   10 04 n0..n7                 delete NAME.MOD
 *   10 05                        list 1:/MODULES (up to 32 entries; files, not folders, hidden or system entries)
 *   10 06 i                      entry i of the last list: F0 00 20 29 00 60 10 06 i <12 ASCII bytes, 0-padded>
 *                                <size 3x7> F7 (25 bytes; no reply unless a list is done and i < count)
 *   10 07 n0..n7                 verify: read NAME.MOD back; size and CRC-32 in the status
 *   10 7F                        end the session (the module area is free again)
 * Every sub except 06 replies F0 00 20 29 00 60 10 <sub> <answer> <state> <job> <result> <fr> <got 3x7> <size 3x7>
 * <info 5x7> <count> F7 (26 bytes). answer = W_OK if the request was taken, else a W_* code (nothing changed).
 * state 0 no session, 1 staging, 2 job queued or running, 3 job done; job 1 write, 2 delete, 3 list, 4 verify;
 * result = the job's W_* result (15 recovery failed); fr = the FatFs code of a W_FS /
 * W_RECOVER result; got = bytes staged (while a write job runs: bytes written, then verified); size = the announced
 * size (verify: the file's size, capped at 3x7); info = the announced CRC (verify: the file's CRC); count = list
 * entries.
 *
 * Code. All of it lives in the far blob (tools/farblob.py; FAR_PART pass) behind two table functions, far_cw_sysex and
 * far_cw_main; the main blob only forwards cmd 10 and the Main-pass step through far_call (no far blob: cmd 10 gets
 * no reply and nothing runs).
 */

#if CARD_WRITE && !STEP_CHORDS
#error "CARD_WRITE=1 needs the far blob (STEP_CHORDS)"
#endif

#if FAR_PART
#define F_WRITE         ((f_read_fn)0x080220FDu)
#define F_UNLINK        ((f_path_fn)0x080226EDu)
#define F_MKDIR         ((f_path_fn)0x08022789u)
#define F_RENAME        ((f_rename_fn)0x080228C3u)
#define F_LSEEK         ((f_lseek_fn)0x080223D9u)
#define SYNC_FS         ((f_obj_fn)0x080208BFu)     /* write back the volume window (FATFS *), then CTRL_SYNC */
#define FIND_VOLUME     ((find_vol_fn)0x08021A4Du)  /* (&path, &fs, mode): the first call of f_unlink */
#define FOLLOW_PATH     ((f_opendir_fn)0x0802166Du) /* (DIR *, path): the second call of f_unlink */
#define DIR_REMOVE      ((f_obj_fn)0x08021455u)     /* (DIR *): f_unlink's entry removal (no chain released) */
#define FA_WR           0x02u           /* FA_WRITE */
#define FA_WRITE_EXCL   0x06u           /* FA_WRITE | FA_CREATE_NEW */
#define FIL_SCLUST      0x08u           /* FIL: obj.sclust */
#define DIR_NSFLAG      0x2Bu           /* DIR: fn[11], the name status */
#define FR_EXIST        8u
#define CW_REC          4u              /* names with helpers settled per recovery round */
#define CW_ROUNDS       8u              /* recovery rounds per job (more helpers: W_RECOVER; the next job goes on) */
typedef uint32_t (*f_path_fn)(const char *path);
typedef uint32_t (*f_rename_fn)(const char *from, const char *to);
typedef uint32_t (*f_lseek_fn)(uint32_t fil, uint32_t ofs);
typedef uint32_t (*find_vol_fn)(const char **path, uint32_t *fs, uint32_t mode);

#define CW_NONE     0u                  /* state */
#define CW_STAGE    1u
#define CW_JOB      2u
#define CW_DONE     3u
enum { J_NONE, J_WRITE, J_DELETE, J_LIST, J_VERIFY };
enum { W_NONE, W_OK, W_NOCARD, W_BUSY, W_NAME, W_SIZE, W_OFFSET, W_CRC, W_MODULE, W_FS, W_VERIFY, W_NOTFOUND,
       W_NOTASK, W_STATE, W_ITCM, W_RECOVER };      /* W_NOTASK: not used (kept for the numbering) */
enum { S_SCAN, S_SCAN_READ, S_REC, S_REC_T, S_REC_M, S_REC_O, S_REC_ACT_T, S_REC_ACT_O, S_BODY,
       S_W_OPEN, S_W_MKDIR, S_W_DATA, S_V_READ, S_C_OLD, S_C_NEW, S_C_BACK, S_C_DEL,
       S_D_UNLINK, S_Y_OPEN, S_Y_READ, S_Y_DONE, S_L_OPEN, S_L_READ, S_FLUSH, S_END };
enum { IO_WRITE, IO_CMP, IO_CRC };      /* cw_io */
#define OB_NONE     0u                  /* struct cw obj: our DIR is open (OB_DIR) or not */
#define OB_DIR      1u
#define C_ABSENT    0xFFFFFFFFu         /* cluster lookup: no such file */

struct cw_ent {
    char n[NAME_LEN - 1];               /* 8.3 short name, 0-padded */
    uint8_t lo, hi;                     /* size, capped at 0xFFFF */
};

/* Session data in ITCM MOD_RAM: valid only while ld_phase is LD_STAGE, LD_JOB or LD_DONE (no module runs then). */
struct cw {
    uint32_t dir[16];                   /* FatFs DIR (0x34 bytes in this build) */
    uint32_t fi[40];                    /* FatFs FILINFO (0x96 bytes in this build) */
    uint32_t size, got, crc, info;
    uint32_t off, ct, cm, co, acc;      /* job: file offset; recovery start clusters (or C_ABSENT); CRC */
    uint16_t seen;                      /* directory entries examined */
    uint8_t job, result, fr, count;
    uint8_t step, obj, had, post, nrec, irec, round, more;
    char name[NAME_LEN];                /* "NAME.MOD" */
    char rec[CW_REC][8];                /* recovery: base names with a .TMP or .OLD, 0-padded */
    union {
        uint8_t buf[PIECE];             /* read-back pieces */
        struct cw_ent ent[PADS];        /* list */
    } u;
};
_Static_assert(sizeof(struct cw) <= MOD_BASE - MOD_RAM, "writer data must fit MOD_RAM");
#define CW          ((volatile struct cw *)MOD_RAM)

static uint32_t cw_state(const struct state *s)
{
    uint32_t p = s->ld_phase;
    return p == LD_STAGE ? CW_STAGE : p == LD_JOB ? CW_JOB : p == LD_DONE ? CW_DONE : CW_NONE;
}

/* "NAME.MOD" from the 8 request bytes: 1..8 of A-Z 0-9 _ -, then zeros only. 0 = invalid. */
static uint32_t cw_name(const uint8_t *m, volatile char *out)
{
    uint32_t n = 0;
    while (n < 8 && m[n])
        n++;
    if (!n)
        return 0;
    for (uint32_t i = 0; i < 8; i++) {
        uint8_t c = m[i];
        if (i < n ? !((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-') : c != 0)
            return 0;
    }
    for (uint32_t i = 0; i < NAME_LEN; i++)
        out[i] = 0;
    for (uint32_t i = 0; i < n; i++)
        out[i] = (char)m[i];
    out[n] = '.'; out[n + 1] = 'M'; out[n + 2] = 'O'; out[n + 3] = 'D';
    return 1;
}

/* "1:/MODULES/<nm>" at d (at most 24 bytes with the NUL). */
static void cw_path(volatile char *d, const volatile char *nm)
{
    uint32_t n = dir_path(d);
    d[n++] = '/';
    for (uint32_t j = 0; j < NAME_LEN - 1 && nm[j]; j++)
        d[n++] = nm[j];
    d[n] = 0;
}

/* "1:/MODULES/<base of nm>.<a b c>" at d. */
static void cw_path_ext(volatile char *d, const volatile char *nm, char a, char b, char c)
{
    volatile char t[NAME_LEN];
    uint32_t n = 0;
    while (n < 8 && nm[n] && nm[n] != '.') {
        t[n] = nm[n];
        n++;
    }
    t[n] = '.'; t[n + 1] = a; t[n + 2] = b; t[n + 3] = c; t[n + 4] = 0;
    cw_path(d, t);
}

static uint32_t crc_add(uint32_t c, const volatile uint8_t *p, uint32_t n)
{
    for (uint32_t i = 0; i < n; i++) {
        c ^= p[i];
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1)));
    }
    return c;
}

/* The status reply (26 bytes), answer = this request's verdict. */
static void cw_reply(struct state *s, uint32_t sub, uint32_t answer)
{
    volatile uint8_t r[26];
    const volatile struct cw *w = CW;
    header(r, 0x10);
    r[7] = (uint8_t)(sub & 0x7F);
    r[8] = (uint8_t)answer;
    SUSPEND_ALL();
    uint32_t st = itcm_ok() ? cw_state(s) : CW_NONE;
    r[9] = (uint8_t)st;
    r[10] = st ? w->job & 0x7F : 0;
    r[11] = st ? w->result & 0x7F : 0;
    r[12] = st ? w->fr & 0x7F : 0;
    put7(r + 13, st ? w->got : 0, 3);
    put7(r + 16, st ? w->size : 0, 3);
    put7(r + 19, st ? w->info : 0, 5);
    r[24] = st ? w->count & 0x7F : 0;
    RESUME_ALL();
    r[25] = 0xF7;
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}

/* cmd 10 06 i: entry i of a finished list. */
static void cw_entry(struct state *s, uint32_t i)
{
    volatile uint8_t r[25];
    const volatile struct cw *w = CW;
    header(r, 0x10);
    r[7] = 0x06;
    r[8] = (uint8_t)i;
    SUSPEND_ALL();
    uint32_t ok = itcm_ok() && s->ld_phase == LD_DONE && w->job == J_LIST && w->result == W_OK && i < w->count;
    for (uint32_t k = 0; k < NAME_LEN - 1; k++)
        r[9 + k] = ok ? w->u.ent[i].n[k] & 0x7F : 0;
    put7(r + 21, ok ? (uint32_t)w->u.ent[i].lo | (uint32_t)w->u.ent[i].hi << 8 : 0, 3);
    RESUME_ALL();
    r[24] = 0xF7;
    if (!ok)
        return;
    uint32_t out[2] = {(uint32_t)r, sizeof r};
    TX_SYSEX(*(volatile uint32_t *)TX_PORT_PTR, 1, out);
}

/* Open a session (begin, delete, list, verify): stop a running module, close a choosing picker, clear MOD_RAM, mark
 * the module area as not loaded this boot. Refused while the loader reads the card or a job runs. Caller holds the
 * suspension. Returns W_OK or the refusal. */
static uint32_t cw_open(struct state *s, uint32_t phase, uint32_t job)
{
    if (!itcm_ok())
        return W_ITCM;
    if (ld_busy(s))
        return W_BUSY;
    retire(s);
    if (s->mode == 3 || s->mode == MODE_PICK)
        s->mode = 0;
    s->mod_frame = 0;
    s->ld_fresh = 0;
    for (volatile uint32_t *p = (volatile uint32_t *)MOD_RAM; p < (volatile uint32_t *)MOD_BASE; p++)
        *p = 0;
    CW->job = (uint8_t)job;
    s->ld_phase = (uint8_t)phase;
    return W_OK;
}

static void cw_sysex(struct state *s, const uint8_t *m, uint32_t len)
{
    if (len < 9)
        return;
    uint32_t sub = m[7], answer = W_STATE;
    volatile struct cw *w = CW;
    if (sub == 0x06) {
        if (len == 10)
            cw_entry(s, m[8]);
        return;
    }
    volatile char nm[NAME_LEN];
    SUSPEND_ALL();
    uint32_t st = itcm_ok() ? cw_state(s) : CW_NONE;
    if (sub == 0x00 && len == 9) {
        answer = W_OK;
    } else if (sub == 0x01 && len == 25) {
        uint32_t size = (uint32_t)m[16] << 14 | (uint32_t)m[17] << 7 | m[18];
        uint32_t crc = (uint32_t)m[19] << 28 | (uint32_t)m[20] << 21 | (uint32_t)m[21] << 14 | (uint32_t)m[22] << 7
                       | m[23];
        if (!cw_name(m + 8, nm))
            answer = W_NAME;
        else if (size < HDR1 + 2 || size > MOD_MAX)
            answer = W_SIZE;
        else if ((answer = cw_open(s, LD_STAGE, J_WRITE)) == W_OK) {
            for (uint32_t i = 0; i < NAME_LEN; i++)
                w->name[i] = nm[i];
            w->size = size;
            w->crc = crc;
        }
    } else if (sub == 0x02 && len >= 14 && !((len - 12) & 1) && len <= 12 + 2 * CHUNK) {
        uint32_t off = (uint32_t)m[8] << 14 | (uint32_t)m[9] << 7 | m[10], n = (len - 12) / 2;
        if (st != CW_STAGE)
            answer = W_STATE;
        else if (off != w->got || n > w->size - off)
            answer = W_OFFSET;
        else {
            volatile uint8_t *dst = (volatile uint8_t *)(MOD_BASE + off);
            for (uint32_t i = 0; i < n; i++)
                dst[i] = (uint8_t)(((m[11 + 2 * i] & 0x0F) << 4) | (m[12 + 2 * i] & 0x0F));
            w->got = off + n;
            answer = W_OK;
        }
    } else if (sub == 0x03 && len == 9) {
        const volatile struct mod_hdr *h = (const volatile struct mod_hdr *)MOD_BASE;
        if (st != CW_STAGE)
            answer = W_STATE;
        else if (w->got != w->size)
            answer = W_SIZE;
        else if (~crc_add(0xFFFFFFFFu, (const volatile uint8_t *)MOD_BASE, w->size) != w->crc)
            answer = W_CRC;
        else if (mod_check() != 1 || h->size != w->size)
            answer = W_MODULE;
        else {
            w->result = W_NONE;
            w->got = 0;                     /* from now on: bytes written, then verified */
            s->ld_phase = LD_JOB;           /* the Main passes take it from here (step S_SCAN) */
            answer = W_OK;
        }
    } else if ((sub == 0x04 || sub == 0x07) && len == 17) {
        if (!cw_name(m + 8, nm))
            answer = W_NAME;
        else if ((answer = cw_open(s, LD_JOB, sub == 0x04 ? J_DELETE : J_VERIFY)) == W_OK) {
            for (uint32_t i = 0; i < NAME_LEN; i++)
                w->name[i] = nm[i];
        }
    } else if (sub == 0x05 && len == 9) {
        answer = cw_open(s, LD_JOB, J_LIST);
    } else if (sub == 0x7F && len == 9) {
        if (st == CW_JOB)
            answer = W_BUSY;
        else {
            if (st != CW_NONE)
                s->ld_phase = LD_IDLE;
            answer = W_OK;
        }
    } else {
        RESUME_ALL();
        return;                             /* malformed: no reply */
    }
    RESUME_ALL();
    cw_reply(s, sub, answer);
}

/* ---- jobs (Main task, one step per Main pass from the loader step; see the header) ------------------------------ */

/* Start cluster of 1:/MODULES/<nm> into *c (C_ABSENT if there is no such file): a read-only open of B's FIL and its
 * close (no I/O). Returns the FatFs error, 0 = ok. */
static uint32_t cw_clust(const volatile char *nm, volatile uint32_t *c)
{
    volatile char p[24];
    uint32_t fil = file_b(0);
    if (!fil)
        return 0x7F;                        /* B is open in the firmware */
    cw_path(p, nm);
    uint32_t r = F_OPEN(fil, (const char *)p, FA_READ);
    if (r == FR_NO_FILE) {
        *c = C_ABSENT;
        return 0;
    }
    if (r)
        return r;
    *c = link(fil, FIL_SCLUST);
    F_CLOSE(fil);
    return 0;
}

/* Remove only the directory entry of 1:/MODULES/<nm>, never its cluster chain (it is shared with another entry):
 * the firmware's f_unlink steps without the chain release, then the volume sync. Returns the FatFs error. */
static uint32_t cw_drop(const volatile char *nm)
{
    volatile struct cw *w = CW;
    volatile char p[24];
    cw_path(p, nm);
    const char *path = (const char *)p;
    uint32_t fs = 0;
    uint32_t r = FIND_VOLUME(&path, &fs, 2);
    if (r)
        return r;
    w->dir[0] = fs;                         /* DIR.obj.fs */
    r = FOLLOW_PATH((uint32_t)w->dir, path);
    if (r)
        return r;
    if (((const volatile uint8_t *)w->dir)[DIR_NSFLAG] & 0xA0)
        return 6;                           /* FR_INVALID_NAME: dot or empty name (never ours) */
    r = DIR_REMOVE((uint32_t)w->dir);
    return r ? r : SYNC_FS(fs);
}

/* "X.<a b c>" for recovery name i. */
static void cw_rname(volatile char *out, uint32_t i, char a, char b, char c)
{
    volatile struct cw *w = CW;
    uint32_t n = 0;
    for (; n < 8 && w->rec[i][n]; n++)
        out[n] = w->rec[i][n];
    out[n] = '.'; out[n + 1] = a; out[n + 2] = b; out[n + 3] = c;
    for (n += 4; n < NAME_LEN; n++)
        out[n] = 0;
}

/* Close our DIR if it is open (files never stay open between steps). */
static void cw_close(void)
{
    volatile struct cw *w = CW;
    if (w->obj == OB_DIR)
        F_CLOSEDIR((uint32_t)w->dir);
    w->obj = OB_NONE;
}

/* The job failed: close, record, and (a write, the first time) run recovery again before the end. */
static void cw_fail(uint32_t res, uint32_t fr)
{
    volatile struct cw *w = CW;
    cw_close();
    if (w->result == W_NONE) {
        w->result = (uint8_t)res;
        w->fr = (uint8_t)fr;
    }
    if (w->job == J_WRITE && !w->post) {
        w->post = 1;
        w->round = 0;
        w->step = S_SCAN;
    } else
        w->step = S_FLUSH;
}

/* Recovery could not finish: stop (nothing is written after it). */
static void cw_fail_rec(uint32_t fr)
{
    volatile struct cw *w = CW;
    cw_close();
    if (w->result == W_NONE) {
        w->result = W_RECOVER;
        w->fr = (uint8_t)fr;
    }
    w->step = S_FLUSH;
}

/* Recovery scan: is the entry in fi a helper (X.TMP or X.OLD, X 1..8 of A-Z 0-9 _ -)? Then note X once. */
static void cw_note(void)
{
    volatile struct cw *w = CW;
    const volatile uint8_t *fi = (const volatile uint8_t *)w->fi;
    if (fi[FI_ATTR] & AM_SKIP)
        return;
    const volatile uint8_t *n = fi[FI_ALT] ? fi + FI_ALT : fi + FI_NAME;
    uint32_t len = 0;
    while (len < NAME_LEN - 1 && n[len])
        len++;
    if (len < 5 || n[len - 4] != '.')
        return;
    uint32_t tmp = n[len - 3] == 'T' && n[len - 2] == 'M' && n[len - 1] == 'P';
    uint32_t old = n[len - 3] == 'O' && n[len - 2] == 'L' && n[len - 1] == 'D';
    if (!tmp && !old)
        return;
    for (uint32_t j = 0; j < len - 4; j++) {
        uint8_t c = n[j];
        if (!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-'))
            return;
    }
    for (uint32_t i = 0; i < w->nrec; i++) {
        uint32_t same = 1;
        for (uint32_t j = 0; j < 8; j++)
            if (w->rec[i][j] != (j < len - 4 ? (char)n[j] : 0))
                same = 0;
        if (same)
            return;
    }
    if (w->nrec >= CW_REC) {
        w->more = 1;
        return;
    }
    for (uint32_t j = 0; j < 8; j++)
        w->rec[w->nrec][j] = j < len - 4 ? (char)n[j] : 0;
    w->nrec++;
}

/* One file step on B's FIL, opened and closed within the step: kind IO_WRITE appends up to 2 pieces of the staged
 * copy to NAME.TMP (whose size must be the bytes written so far); IO_CMP reads up to 2 pieces of NAME.TMP (size must
 * be the staged size) and compares; IO_CRC reads up to 2 pieces of NAME.MOD into the CRC. Returns W_OK or a W_* code
 * (w->fr set). */
static uint32_t cw_io(uint32_t kind)
{
    volatile struct cw *w = CW;
    volatile char p[24];
    uint32_t fil = file_b(0);
    if (!fil)
        return W_BUSY;
    if (kind == IO_CRC)
        cw_path(p, w->name);
    else
        cw_path_ext(p, w->name, 'T', 'M', 'P');
    uint32_t r = F_OPEN(fil, (const char *)p, kind == IO_WRITE ? FA_WR : FA_READ), res = W_OK;
    if (r) {
        w->fr = (uint8_t)r;
        return W_FS;
    }
    if (link(fil, FIL_FSIZE) != (kind == IO_WRITE ? w->off : w->size)) {
        r = 0;
        res = W_VERIFY;                     /* the file is not as this job left it */
    } else if ((r = F_LSEEK(fil, w->off)) != 0)
        res = W_FS;
    for (uint32_t i = 0; res == W_OK && i < 2 && w->off < w->size; i++) {
        uint32_t n = w->size - w->off < PIECE ? w->size - w->off : PIECE, k = 0;
        if (kind == IO_WRITE)
            r = F_WRITE(fil, MOD_BASE + w->off, n, &k);
        else
            r = F_READ(fil, (uint32_t)w->u.buf, n, &k);
        if (r || k != n) {
            res = r || kind == IO_WRITE ? W_FS : W_VERIFY;
            if (!r && kind == IO_WRITE)
                r = 7;                      /* bw < n: the volume is full (reported as FR_DENIED) */
            break;
        }
        if (kind == IO_CMP) {
            const volatile uint8_t *src = (const volatile uint8_t *)(MOD_BASE + w->off);
            for (uint32_t j = 0; j < n; j++)
                if (src[j] != w->u.buf[j])
                    res = W_VERIFY;
        } else if (kind == IO_CRC)
            w->acc = crc_add(w->acc, w->u.buf, n);
        w->off += n;
    }
    uint32_t c = F_CLOSE(fil);
    if (res == W_OK && c) {
        res = W_FS;
        r = c;
    }
    w->fr = (uint8_t)r;
    w->got = w->off;
    return res;
}

/* One step of the job. Main task, token held for the step. */
static void cw_step(void)
{
    volatile struct cw *w = CW;
    const volatile uint8_t *fi = (const volatile uint8_t *)w->fi;
    volatile char a[NAME_LEN], b[NAME_LEN], pa[24], pb[24];
    uint32_t r, fil, k;
    if (w->step != S_FLUSH && !fs_state()) {
        w->obj = OB_NONE;                   /* the card or its volume is gone: abandon the folder object */
        if (w->result == W_NONE)
            w->result = W_NOCARD;
        w->step = S_END;
        return;
    }
    switch (w->step) {
    case S_SCAN:                            /* recovery round: list the helpers */
        w->nrec = 0;
        w->more = 0;
        w->seen = 0;
        dir_path(pa);
        r = F_OPENDIR((uint32_t)w->dir, (const char *)pa);
        if (r == FR_NO_PATH || r == FR_NO_FILE)
            w->step = w->post ? S_FLUSH : S_BODY;     /* no folder: nothing to recover */
        else if (r)
            cw_fail_rec(r);
        else {
            w->obj = OB_DIR;
            w->step = S_SCAN_READ;
        }
        break;
    case S_SCAN_READ:
        r = F_READDIR((uint32_t)w->dir, (uint32_t)w->fi);
        if (!r && fi[FI_NAME] && ++w->seen <= SCAN_MAX) {
            cw_note();
            break;
        }
        cw_close();
        if (r || fi[FI_NAME])
            cw_fail_rec(r);                 /* an error, or more entries than one scan covers: fail closed */
        else {
            w->irec = 0;
            w->step = S_REC;
        }
        break;
    case S_REC:
        if (w->irec < w->nrec)
            w->step = S_REC_T;
        else if (w->more && ++w->round < CW_ROUNDS)
            w->step = S_SCAN;
        else if (w->more)
            cw_fail_rec(0);
        else
            w->step = w->post ? S_FLUSH : S_BODY;
        break;
    case S_REC_T:                           /* start clusters of X.TMP, X.MOD, X.OLD (C_ABSENT: no such file) */
    case S_REC_M:
    case S_REC_O:
        k = w->step;
        cw_rname(a, w->irec, k == S_REC_T ? 'T' : k == S_REC_M ? 'M' : 'O',
                 k == S_REC_T ? 'M' : k == S_REC_M ? 'O' : 'L', k == S_REC_T ? 'P' : 'D');
        r = cw_clust(a, k == S_REC_T ? &w->ct : k == S_REC_M ? &w->cm : &w->co);
        if (r)
            cw_fail_rec(r);
        else
            w->step = k + 1;
        break;
    case S_REC_ACT_T:
        r = 0;
        if (w->ct != C_ABSENT) {
            cw_rname(a, w->irec, 'T', 'M', 'P');
            if (w->ct && w->ct == w->cm)
                r = cw_drop(a);             /* an interrupted X.TMP -> X.MOD: the chain is X.MOD's */
            else if (w->ct && w->ct == w->co)
                r = cw_drop(a);             /* never seen, but never free X.OLD's chain either */
            else {
                cw_path(pa, a);
                r = F_UNLINK((const char *)pa);
            }
        }
        if (r)
            cw_fail_rec(r);
        else
            w->step = S_REC_ACT_O;
        break;
    case S_REC_ACT_O:
        r = 0;
        if (w->co != C_ABSENT) {
            cw_rname(a, w->irec, 'O', 'L', 'D');
            cw_rname(b, w->irec, 'M', 'O', 'D');
            cw_path(pa, a);
            cw_path(pb, b);
            if (w->cm == C_ABSENT)
                r = F_RENAME((const char *)pa, (const char *)pb);     /* the old file back */
            else if (w->co && w->co == w->cm)
                r = cw_drop(a);             /* an interrupted rename: one chain, two entries */
            else
                r = F_UNLINK((const char *)pa);                       /* the new file is in place */
        }
        if (r)
            cw_fail_rec(r);
        else {
            w->irec++;
            w->step = S_REC;
        }
        break;
    case S_BODY:
        w->step = w->job == J_WRITE ? S_W_OPEN : w->job == J_DELETE ? S_D_UNLINK : w->job == J_VERIFY ? S_Y_OPEN
                  : S_L_OPEN;
        break;
    case S_W_OPEN:                          /* write: NAME.TMP, created new and empty */
        fil = file_b(0);
        if (!fil) {
            cw_fail(W_BUSY, 0);
            break;
        }
        cw_path_ext(pa, w->name, 'T', 'M', 'P');
        r = F_OPEN(fil, (const char *)pa, FA_WRITE_EXCL);
        if (r == FR_NO_PATH)
            w->step = S_W_MKDIR;
        else if (r)
            cw_fail(r == FR_EXIST ? W_RECOVER : W_FS, r);
        else if ((r = F_CLOSE(fil)) != 0)
            cw_fail(W_FS, r);
        else {
            w->off = 0;
            w->got = 0;
            w->step = S_W_DATA;
        }
        break;
    case S_W_MKDIR:
        dir_path(pa);
        r = F_MKDIR((const char *)pa);
        if (r)
            cw_fail(W_FS, r);
        else
            w->step = S_W_OPEN;
        break;
    case S_W_DATA:                          /* up to 512 bytes per step, then the read-back from offset 0 */
    case S_V_READ:
    case S_Y_READ:
        k = w->step;
        r = cw_io(k == S_W_DATA ? IO_WRITE : k == S_V_READ ? IO_CMP : IO_CRC);
        if (r != W_OK)
            cw_fail(r, w->fr);
        else if (w->off >= w->size) {
            w->off = 0;
            w->step = k == S_W_DATA ? S_V_READ : k == S_V_READ ? S_C_OLD : S_Y_DONE;
        }
        break;
    case S_C_OLD:                           /* commit: NAME.MOD -> NAME.OLD */
        cw_path(pa, w->name);
        cw_path_ext(pb, w->name, 'O', 'L', 'D');
        r = F_RENAME((const char *)pa, (const char *)pb);
        if (r && r != FR_NO_FILE)
            cw_fail(W_FS, r);
        else {
            w->had = !r;
            w->step = S_C_NEW;
        }
        break;
    case S_C_NEW:                           /* NAME.TMP -> NAME.MOD */
        cw_path_ext(pa, w->name, 'T', 'M', 'P');
        cw_path(pb, w->name);
        r = F_RENAME((const char *)pa, (const char *)pb);
        if (r) {
            w->result = W_FS;
            w->fr = (uint8_t)r;
            if (w->had)
                w->step = S_C_BACK;
            else
                cw_fail(W_FS, r);
        } else if (w->had)
            w->step = S_C_DEL;
        else {
            w->result = W_OK;
            w->step = S_FLUSH;
        }
        break;
    case S_C_BACK:                          /* the second rename failed: the old file back */
        cw_path_ext(pa, w->name, 'O', 'L', 'D');
        cw_path(pb, w->name);
        F_RENAME((const char *)pa, (const char *)pb);
        cw_fail(W_FS, w->fr);               /* recovery then settles whatever is left */
        break;
    case S_C_DEL:                           /* NAME.OLD: if this fails, the next job's recovery deletes it */
        cw_path_ext(pa, w->name, 'O', 'L', 'D');
        F_UNLINK((const char *)pa);
        w->result = W_OK;
        w->step = S_FLUSH;
        break;
    case S_D_UNLINK:
        cw_path(pa, w->name);
        r = F_UNLINK((const char *)pa);
        w->fr = (uint8_t)r;
        w->result = !r ? W_OK : r == FR_NO_FILE || r == FR_NO_PATH ? W_NOTFOUND : W_FS;
        w->step = S_FLUSH;
        break;
    case S_Y_OPEN:                          /* verify: NAME.MOD size, then its CRC-32 in read steps */
        fil = file_b(0);
        if (!fil) {
            cw_fail(W_BUSY, 0);
            break;
        }
        cw_path(pa, w->name);
        r = F_OPEN(fil, (const char *)pa, FA_READ);
        if (r) {
            cw_fail(r == FR_NO_FILE || r == FR_NO_PATH ? W_NOTFOUND : W_FS, r);
            break;
        }
        k = link(fil, FIL_FSIZE);
        F_CLOSE(fil);
        w->size = k < (1u << 21) ? k : (1u << 21) - 1;
        w->off = 0;
        w->got = 0;
        w->acc = 0xFFFFFFFFu;
        if (k > MOD_MAX)
            cw_fail(W_SIZE, 0);
        else
            w->step = k ? S_Y_READ : S_Y_DONE;
        break;
    case S_Y_DONE:
        w->info = ~w->acc;
        w->result = W_OK;
        w->step = S_FLUSH;
        break;
    case S_L_OPEN:                          /* list */
        dir_path(pa);
        r = F_OPENDIR((uint32_t)w->dir, (const char *)pa);
        if (r) {
            cw_fail(r == FR_NO_PATH || r == FR_NO_FILE ? W_NOTFOUND : W_FS, r);
            break;
        }
        w->obj = OB_DIR;
        w->seen = 0;
        w->step = S_L_READ;
        break;
    case S_L_READ:
        r = F_READDIR((uint32_t)w->dir, (uint32_t)w->fi);
        if (r || !fi[FI_NAME] || ++w->seen > SCAN_MAX) {
            cw_close();
            if (r)
                cw_fail(W_FS, r);
            else {
                w->result = W_OK;
                w->step = S_FLUSH;
            }
            break;
        }
        if (!(fi[FI_ATTR] & AM_SKIP) && w->count < PADS) {
            const volatile uint8_t *nm = fi[FI_ALT] ? fi + FI_ALT : fi + FI_NAME;
            volatile struct cw_ent *e = &w->u.ent[w->count];
            for (uint32_t j = 0, end = 0; j < NAME_LEN - 1; j++) {
                end |= !nm[j];
                e->n[j] = end ? 0 : (char)nm[j];
            }
            uint32_t size = fi[0] | fi[1] << 8 | fi[2] << 16 | (uint32_t)fi[3] << 24;
            size = size > 0xFFFF ? 0xFFFF : size;
            e->lo = (uint8_t)size;
            e->hi = (uint8_t)(size >> 8);
            w->count++;
        }
        break;
    case S_FLUSH:                           /* a window left dirty by a failed write of ours: write it back */
        if (fs_state() == 2)
            SYNC_FS(link(FATFS_1, 0));
        w->step = S_END;
        break;
    default:
        w->step = S_END;
        break;
    }
}

/* Main pass (loader step, LD_JOB): one step, under the token, outside the suspension; none while the sequencer runs
 * or a firmware Content job runs. The token is held for the step only: the Main task may itself wait for a Content
 * job later in the same pass (a stock request through the Content queue), so it never keeps the Content task out
 * across passes. */
static void cw_main(struct state *s)
{
    volatile struct cw *w = CW;
    if (s->ld_phase != LD_JOB || !itcm_ok())
        return;
    uint32_t t = CLK->transport;
    if (t && link(t, 0) == TRANSPORT_VT && (link(t, 0x10) & 0xFF))
        return;                             /* the sequencer runs: no card I/O of ours until it stops */
    SUSPEND_ALL();
    if (s->tok & TOK_CONTENT) {
        RESUME_ALL();
        return;                             /* a firmware Content job runs: next pass */
    }
    s->tok |= TOK_MAIN;
    RESUME_ALL();
    if (w->step != S_END)
        cw_step();
    SUSPEND_ALL();
    s->tok &= (uint8_t)~TOK_MAIN;
    if (w->step == S_END) {
        if (w->result == W_NONE)
            w->result = W_STATE;
        s->ld_phase = LD_DONE;
    }
    RESUME_ALL();
}

uint32_t far_cw_sysex(uint32_t s, uint32_t m, uint32_t len, uint32_t d)
{
    (void)d;
    cw_sysex((struct state *)s, (const uint8_t *)m, len);
    return 1;
}

uint32_t far_cw_main(uint32_t s, uint32_t b, uint32_t c, uint32_t d)
{
    (void)b; (void)c; (void)d;
    cw_main((struct state *)s);
    return 1;
}
#else
static void cw_sysex(struct state *s, const uint8_t *m, uint32_t len)
{
    far_call(FAR_CW_SYSEX, (uint32_t)s, (uint32_t)m, len, 0);
}

static void cw_main(struct state *s)
{
    far_call(FAR_CW_MAIN, (uint32_t)s, 0, 0, 0);
}
#endif
