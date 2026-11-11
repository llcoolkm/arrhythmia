"""Upload an ITCM effect module to a unit running the devkit patch, verify it, and start it; or manage the module
files on the unit's microSD card (images with SysEx cmd 10, CARD_WRITE).

    uv run python -I tools/module.py /dev/snd/midiC3D0 build/stars.mod
    uv run python -I tools/module.py card write  /dev/snd/midiC3D0 build/stars.mod [NAME]
    uv run python -I tools/module.py card delete /dev/snd/midiC3D0 NAME
    uv run python -I tools/module.py card verify /dev/snd/midiC3D0 NAME
    uv run python -I tools/module.py card list   /dev/snd/midiC3D0
    uv run python -I tools/module.py card engine /dev/snd/midiC3D0 <pack 1..31 | default> <NAME | none>
    uv run python -I tools/module.py card engstatus /dev/snd/midiC3D0
    uv run python -I tools/module.py card engine-put /dev/snd/midiC3D0 fm.dse [NAME]
    uv run python -I tools/module.py card engine-put --check fm.dse [NAME]
    uv run python -I tools/module.py card engine-verify /dev/snd/midiC3D0 NAME

Writes the module 32 bytes per SysEx (cmd 04), reads ITCM back through the dump command (7D) and compares, then
sends start (05) and prints the status reply. card write stages the file in ITCM with cmd 10 (one acknowledged
32-byte piece per message), reads the staged bytes back through 7D, commits (the unit writes 1:/modules/name.mod
through name.tmp and checks it; the job runs between the unit's Main-loop passes and waits while the
sequencer plays), then reads the card file back (cmd 10 07: size and CRC-32) and compares.
NAME is 1..8 characters a-z 0-9 _ - (default: the file name, lowercased); the unit adds .mod. Input names are case-insensitive.
A failed step stops
with its result code; nothing is retried. Starting a card command stops a running module on the unit. The read-back covers 0x400 up to at most 0x3FFF, inside what 7D serves
on Arrhythmia 0.1 (ITCM 0x00000100-0x00003FFF only, docs/user/developers.md). Starting is refused by the unit unless
magic, size and CRC check out.
card engine (engine-optional firmware only, cmd 10 0D) sets which engine a card pack plays on track 8 from its next
load. pack is the raw stock selector ID 1..31, distinct from physical pad ordinals: ID p lives in the folder
1:/Rhythm/NN_<name> with NN = p - 1 (pack 1 is 00_, pack 31 is 30_). Selector 0 is not accepted.
Use the unit inventory for the reported pad mapping and folder names. The unit writes that folder's pointer
engine.cfg as one line "name.dse" (an engine file in
1:/engines) or "none" (the stock sample); "default" writes the card default 1:/engines/default.cfg instead (packs
without their own pointer).
Through engine.cft and engine.cfo, recovered as the module files are. The engine file itself is not written here.
card engine-put (engine-optional firmware only, cmd 10 0E / 0F) puts an engine file on the card as
1:/engines/name.dse (NAME 1..8 of a-z 0-9, not default; default: the file name). The file is refused before the unit
is touched unless it is a statically eligible CRE3 file for the generated USB policy, or a legacy CRE2 file
for queue ABI 0x207 and the base contract on older firmware, at most 15,360 bytes. Runtime CRE3 admission
remains the unit's decision. At commit the unit validates the container, writes name.dst, reads it back, renames the old file to
name.dso and name.dst over name.dse, then this tool reads the card file back (size and CRC-32). A refused request
leaves the card unchanged. A cut power or a lost connection during a put leaves name.dse old or new, or for a moment
absent with the old file kept complete as name.dso: a pack pointing to NAME then plays its stock sample (fail-safe)
until the helpers are settled, which happens at the start of the NEXT engine-put or engine-verify of the SAME name
(not at boot). After an interrupted put, resolve any uncertain job outcome before starting another job. Once
resolved, a newly authorised engine-put or engine-verify of the same name performs helper recovery. The verify (cmd
10 0F) is such a job: it runs that recovery first and can rename or delete name.dst / name.dso; it is not a read-only
check. Keep the exported engine validation helpers and USB policy beside this tool.
card engstatus (engine-optional firmware only, cmd 10 0B) prints the engine state, read-only: the unit opens no
session, starts no job and writes nothing. Its first line is the mode: "stock ... normal" only with the stock pair
resident, nothing published, the queue closed and nothing needing attention; it is the ARM's record, not the DSP's
live health. Newer firmware (layout 2) adds the last pack transaction's timing: its span, its own DSP loads, the
watchdog feeds within, the time since the last feed at its start and the largest gap without a feed, in RTOS ticks
(nominally 1 ms). These are raw tick counts, not strict physical bounds: each stamp is quantised to the tick and
lags it by the SysTick service latency, so an interval can read a tick long or short, and the tick stops while
interrupts are masked (pending SysTicks collapse), an unbounded loss on top.
"""
import os
import select
import struct
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_module  # noqa: E402

PRE = bytes.fromhex("F0 00 20 29 00 60")
MOD_BASE = 0x400
PER_MSG = 32
ITCM_END = 0x4000                                    # read-back covers the module area in ITCM only
CHUNK = 32
DUMP_PRE = PRE + b"\x7D"
DUMP_REPLY = len(DUMP_PRE) + 5 + 2 * CHUNK + 1       # 77


def a7(addr):
    return bytes((addr >> (7 * (4 - i))) & 0x7F for i in range(5))


def write_all(fd, data, deadline):
    while data:
        if time.monotonic() > deadline:
            raise TimeoutError("write timed out")
        if not select.select([], [fd], [], 0.1)[1]:
            continue
        try:
            data = data[os.write(fd, data):]
        except BlockingIOError:
            pass


def read_itcm(fd, start, size, timeout=2.0):
    """Read ITCM back through the dump command (7D), 32 bytes per request; only 0x0000-0x3FFF."""
    assert start % CHUNK == 0 and size % CHUNK == 0 and size > 0 and start + size <= ITCM_END
    end = time.monotonic() + 0.3
    while time.monotonic() < end:                      # drop anything already queued
        if select.select([fd], [], [], 0.05)[0]:
            os.read(fd, 4096)
    data, buf = bytearray(), b""
    for addr in range(start, start + size, CHUNK):
        deadline = time.monotonic() + timeout
        write_all(fd, DUMP_PRE + a7(addr) + b"\xF7", deadline)
        while True:
            i = buf.find(DUMP_PRE)
            j = buf.find(b"\xF7", i) if i >= 0 else -1
            if i >= 0 and j >= 0:
                msg, buf = buf[i:j + 1], buf[j + 1:]
                break
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"no reply for {addr:#06x}")
            if select.select([fd], [], [], left)[0]:
                try:
                    buf += bytes(b for b in os.read(fd, 4096) if b < 0xF8)   # drop realtime bytes
                except BlockingIOError:
                    pass
        got = sum(x << (7 * (4 - k)) for k, x in enumerate(msg[7:12]))
        if len(msg) != DUMP_REPLY or got != addr or any(b > 0x0F for b in msg[12:-1]):
            raise ValueError(f"bad reply for {addr:#06x}: {msg.hex(' ')}")
        data += bytes((msg[12 + 2 * k] << 4) | msg[13 + 2 * k] for k in range(CHUNK))
    return bytes(data)


def upload_msgs(blob):
    for off in range(0, len(blob), PER_MSG):
        part = blob[off:off + PER_MSG]
        yield PRE + bytes([0x04, (off >> 14) & 0x7F, (off >> 7) & 0x7F, off & 0x7F]) + \
            b"".join(bytes((b >> 4, b & 0x0F)) for b in part) + b"\xF7"


def read_reply(fd, cmd, timeout=2.0, prefix=PRE):
    buf, deadline = b"", time.monotonic() + timeout
    while time.monotonic() < deadline:
        if select.select([fd], [], [], 0.05)[0]:
            try:
                buf += bytes(b for b in os.read(fd, 4096) if b < 0xF8)
            except BlockingIOError:
                pass
        i = buf.find(prefix + bytes([cmd]))
        j = buf.find(b"\xF7", i) if i >= 0 else -1
        if i >= 0 and j >= 0:
            return buf[i:j + 1]
    return None


# --- card files (SysEx cmd 10, patches/src/cardwrite.c) ------------------------------------------------------------

CARD = 0x10
STATE = ["none", "staging", "job", "done"]
JOB = ["none", "write", "delete", "list", "verify"]
RESULT = ["none", "ok", "no card", "busy", "bad name", "bad size", "bad offset", "bad CRC", "bad module",
          "file system error", "read-back mismatch", "not found", "no Content task", "not now", "ITCM unavailable",
          "recovery of name.tmp / name.old failed"]
W_OK = 1
CW_DONE = 3


class CardError(Exception):
    pass


ENGINE_BASE_ID = 0xFE80B6B5     # cmd 10 0E: the base contract id the unit admits (crbc.h CRBC_ID)
ENGINE_MAX = ITCM_END - MOD_BASE  # cmd 10 0E: the file is staged in the ITCM module area (15,360 bytes)
CARD_DEFAULT = 0x7F             # cmd 10 0D: the card default instead of a pack
CARD_PACKS = 31                 # accepted card selectors 1..31 (folders 00_..30_)
PACK_ANSWER = ["accept", "busy", "range", "playing", "card job", "module", "fault", "not ready", "malformed"]
PACK_STATE = ["none", "queued", "running", "done", "failed"]
PACK_RESULT = ["none", "ok", "stock refused", "changed", "fault", "busy", "playing", "module", "card job",
               "not ready"]


def pack_number(pack):
    if type(pack) is not int or not 1 <= pack <= CARD_PACKS:
        raise ValueError(f"pack {pack!r}: stock card pack number 1..31")
    return pack


def _pack_fields(seq, state, target, loaded, result):
    if state >= len(PACK_STATE) or target > CARD_PACKS or loaded not in (*range(32), 127) or result >= len(PACK_RESULT):
        raise ValueError("pack load: invalid state, target, loaded pack or result")
    return dict(seq=seq, state=state, target=target, loaded=loaded, result=result)


def decode_pack_load(r):
    """The dedicated 19-byte 10 10 acknowledgement; never a card-writer status."""
    if not r or len(r) != 19 or r[:8] != PRE + bytes([CARD, 0x10]) or r[-1] != 0xF7:
        raise ValueError("bad pack-load reply")
    if any(x > 0x7F for x in r[1:-1]) or r[9] > 0x0F or r[8] >= len(PACK_ANSWER):
        raise ValueError("bad pack-load reply: answer, 7-bit data or canonical u32")
    return dict(answer=r[8], **_pack_fields(g7(r[9:14]), *r[14:18]))


def card_name(name):
    """Case-insensitive name (1..8 of a-z 0-9 _ -); uppercase wire bytes suit earlier card writers."""
    name = name.lower()
    if not 1 <= len(name) <= 8 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in name):
        raise ValueError(f"name {name!r}: 1..8 characters a-z 0-9 _ - (the unit adds .mod)")
    return name.upper().encode().ljust(8, b"\0")


def engine_name(name):
    """Case-insensitive engine name (1..8 of a-z 0-9, not default); uppercase wire bytes for earlier firmware."""
    name = name.lower()
    if not 1 <= len(name) <= 8 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789" for c in name):
        raise ValueError(f"engine name {name!r}: 1..8 characters a-z 0-9 (the unit adds .dse)")
    if name == "default":
        raise ValueError("default is the card default's reserved name")
    return name.upper().encode().ljust(8, b"\0")


def _engine_file():
    """Load engine-file helpers only for commands that need them; other commands can run without the helpers."""
    try:
        import engine_file
    except ImportError as e:
        raise ValueError(f"card engine-put needs tools/engine_file.py and tools/crbc.py beside module.py: {e}") from None
    return engine_file


def engine_check(blob, *, app_build=None):
    """Validate CRE3 USB eligibility, or legacy CRE2 files for older firmware.

    Static eligibility does not grant runtime admission on the connected unit.
    """
    if app_build is not None or blob[:4] == b'CRE3':
        import cre3_check
        policy = cre3_check.policy()
        release = policy['release_version']
        production = app_build is None or app_build >= policy['minimum_app_build']
        if blob[:4] == b'CRE3':
            if not production:
                raise ValueError(f"CRE3 engine files require firmware {release} or newer.")
            return cre3_check.check(blob)
        if blob[:4] == b'CRE2' and production:
            raise ValueError(f"FM 1.0.0 (CRE2) does not run on firmware {release}. Install fm.dse 2.0.0.")
    engine_file = _engine_file()
    h = engine_file.parse(blob)
    engine_file.need(h["magic"] == "CRE2", "not CRE2 (convert a CRE1 file first: tools/engine_file.py convert)")
    engine_file.need(h["abi"] == engine_file.QUEUE_ABI, f"queue ABI {h['abi']:#x}, the firmware speaks "
                     f"{engine_file.QUEUE_ABI:#x}")
    engine_file.need(h["base"] == ENGINE_BASE_ID, f"base id {h['base']:#010x}, not {ENGINE_BASE_ID:#010x} "
                     "(tools/engine_file.py reheader)")
    engine_file.need(1 <= h["kind"] <= 15, f"kind {h['kind']}")
    engine_file.need(len(blob) <= ENGINE_MAX, f"{len(blob)} bytes: the unit stages at most {ENGINE_MAX}")
    return h


def default_name(path):
    return os.path.splitext(os.path.basename(path))[0].lower()


def p7(v, n):
    return bytes((v >> (7 * (n - 1 - i))) & 0x7F for i in range(n))


def g7(b):
    return sum(x << (7 * (len(b) - 1 - i)) for i, x in enumerate(b))


def parse_status(r):
    """The 26-byte cmd 10 status reply as a dict."""
    if not r or len(r) != 26 or r[:7] != PRE + bytes([CARD]):
        raise CardError(f"bad status reply: {r.hex(' ') if r else None}")
    return dict(sub=r[7], answer=r[8], state=r[9], job=r[10], result=r[11], fr=r[12], got=g7(r[13:16]),
                size=g7(r[16:19]), info=g7(r[19:24]), count=r[24])


def describe(st):
    return (f"answer {RESULT[st['answer']] if st['answer'] < len(RESULT) else st['answer']}, state "
            f"{STATE[st['state']] if st['state'] < 4 else st['state']}, job {JOB[st['job']] if st['job'] < 5 else st['job']}"
            f", result {RESULT[st['result']] if st['result'] < len(RESULT) else st['result']}"
            + (f" (FatFs {st['fr']})" if st["fr"] else ""))


def decode_card_row(raw):
    """96-byte read-only inventory row. Text is exact ASCII, a subset of UTF-8;
    lossy/truncated stock OEM names carry flag4 and an actual short alias."""
    if len(raw) != 96 or raw[0] not in (1, 2, 3):
        raise ValueError("invalid inventory row")
    def text(a, b):
        return raw[a:b].split(b"\0", 1)[0].decode("utf-8", "strict")
    values = struct.unpack_from("<7I", raw, 60)
    abi3 = None
    if raw[0] == 2 and raw[88]:
        if raw[88] != 3 or raw[91]:
            raise ValueError("unknown engine inventory extension")
        abi3 = dict(preload_check=raw[89], control_count=raw[90], runtime_check_14="NOT_RUN",
                    header_crc=struct.unpack_from("<I", raw, 92)[0])
    build = values[2]
    version = f"{build >> 16 & 255}.{build >> 8 & 255}.{build & 255}" if build & 0xFF000000 == 0x80000000 else None
    return dict(type=raw[0], selector=raw[1], pad=raw[2], flags=raw[3], own_state=raw[4],
                source=raw[5], effective_state=raw[6], compatibility=raw[7], name=text(8, 32),
                own_name=text(32, 40), effective_name=text(40, 48), filename=text(48, 60),
                version=version if raw[0] == 2 and raw[7] == 1 else None,
                validation_format=("CRE3" if abi3 else "CRE2") if raw[0] == 2 else None,
                engine_format=("CRE3" if abi3 else "CRE2") if raw[0] == 2 and raw[7] == 1 else None, abi3=abi3,
                **dict(zip(("size", "crc", "build", "kind", "abi", "base", "body_crc"), values)))


def decode_inventory(reply, sub, nonce, index=None):
    expected = 15 if sub == 0x11 else 208
    if (not reply or len(reply) != expected or reply[:8] != PRE + bytes((CARD, sub))
            or reply[-1] != 0xF7 or any(b > 127 for b in reply[1:-1])
            or reply[9:11] != bytes((nonce >> 7, nonce & 127))
            or reply[8] > 4 or reply[11] > 3):
        raise ValueError("invalid inventory reply")
    result = dict(answer=reply[8], state=reply[11], flags=reply[12], count=reply[13])
    if sub == 0x12:
        if reply[14] != index or any(b > 15 for b in reply[15:-1]):
            raise ValueError("invalid inventory row framing")
        if not result["answer"]:
            result["row"] = decode_card_row(bytes((reply[15 + i * 2] << 4) | reply[16 + i * 2] for i in range(96)))
    return result


class Card:
    """cmd 10 over a transport: xfer(msg, cmd) sends one SysEx and returns the reply starting with PRE + cmd (or
    None). The fd transport below talks to a unit; the emulator tests pass their own.
    Inventory reads use their own streaming nonce and bounded row polls."""
    def __init__(self, xfer, poll=lambda: None, polls=400):
        self.xfer, self.poll, self.polls = xfer, poll, polls
        self._inventory_nonce = 0

    def req(self, body):
        st = parse_status(self.xfer(PRE + bytes([CARD]) + body + b"\xF7", CARD))
        if st["sub"] != body[0]:
            raise CardError(f"reply for sub {st['sub']:#04x}, expected {body[0]:#04x}")
        return st

    def must(self, body, what):
        st = self.req(body)
        if st["answer"] != W_OK:
            raise CardError(f"{what} refused: {describe(st)}")
        return st

    def wait(self, what):
        """Poll the status until the job is done; its result must be ok."""
        for _ in range(self.polls):
            st = self.req(b"\x00")
            if st["state"] == CW_DONE:
                if st["result"] != W_OK:
                    raise CardError(f"{what} failed: {describe(st)}")
                return st
            if st["state"] != 2:
                raise CardError(f"{what}: no job any more: {describe(st)}")
            self.poll()
        raise CardError(f"{what}: no result after {self.polls} polls (the unit's job waits while the sequencer "
                        "plays: stop playback and the job finishes on its own)")

    def stage(self, blob, name, sub=b"\x01"):
        crc = zlib.crc32(blob)
        self.must(sub + (card_name(name) if sub == b"\x01" else engine_name(name)) + p7(len(blob), 3) + p7(crc, 5),
                  "begin")
        for off in range(0, len(blob), PER_MSG):
            part = blob[off:off + PER_MSG]
            st = self.must(b"\x02" + p7(off, 3) + b"".join(bytes((b >> 4, b & 0x0F)) for b in part),
                           f"piece at {off}")
            if st["got"] != off + len(part):
                raise CardError(f"piece at {off}: unit has {st['got']} bytes")

    def readback(self, n):
        """The staged bytes, through 7D (32 bytes per request)."""
        data = b""
        for addr in range(MOD_BASE, MOD_BASE + (n + CHUNK - 1) // CHUNK * CHUNK, CHUNK):
            r = self.xfer(DUMP_PRE + a7(addr) + b"\xF7", 0x7D)
            if not r or len(r) != DUMP_REPLY or g7(r[7:12]) != addr:
                raise CardError(f"no read-back for {addr:#06x}: {r.hex(' ') if r else None}")
            data += bytes((r[12 + 2 * k] << 4) | r[13 + 2 * k] for k in range(CHUNK))
        return data[:n]

    def verify(self, name):
        self.must(b"\x07" + card_name(name), "verify")
        st = self.wait("verify")
        return st["size"], st["info"]

    def write(self, blob, name):
        build_module.check(blob)
        self.stage(blob, name)
        back = self.readback(len(blob))
        if back != blob:
            bad = next(i for i in range(len(blob)) if back[i] != blob[i])
            self.req(b"\x7F")
            raise CardError(f"staged copy differs at offset {bad}; nothing was written to the card")
        self.must(b"\x03", "commit")
        self.wait("write")
        size, crc = self.verify(name)
        if (size, crc) != (len(blob), zlib.crc32(blob)):
            raise CardError(f"card file {name.lower()}.mod: {size} bytes CRC {crc:08x}, expected {len(blob)} bytes CRC "
                            f"{zlib.crc32(blob):08x}")
        self.req(b"\x7F")
        return size, crc

    def engine_put(self, blob, name):
        """cmd 10 0E: the engine file 1:/engines/name.dse = blob (staged and read back as card write; the unit checks
        the whole-file CRC-32 and container header, writes name.dst, reads it back, renames over name.dse with the old
        one as name.dso), then read back from the card (10 0F). Returns (size, CRC)."""
        engine_check(blob)
        self.stage(blob, name, b"\x0E")
        back = self.readback(len(blob))
        if back != blob:
            self.req(b"\x7F")
            raise CardError("staged copy differs; nothing was written to the card")
        self.must(b"\x03", "commit")
        self.wait("engine file")
        size, crc = self.engine_verify(name)
        if (size, crc) != (len(blob), zlib.crc32(blob)):
            raise CardError(f"card file {name.lower()}.dse: {size} bytes CRC {crc:08x}, expected {len(blob)} bytes CRC "
                            f"{zlib.crc32(blob):08x}")
        self.req(b"\x7F")
        return size, crc

    def inventory(self, nonce=None):
        """Read-only streaming scan. Retrying row polls never starts recovery or writes."""
        if nonce is None:
            self._inventory_nonce = (self._inventory_nonce + 1) & 16383
            nonce = self._inventory_nonce
        if type(nonce) is not int or not 0 <= nonce < 16384:
            raise ValueError("inventory nonce must fit 14 bits")
        def req(sub, index=None):
            data = bytes((CARD, sub, nonce >> 7, nonce & 127))
            if index is not None:
                data += bytes((index,))
            return decode_inventory(self.xfer(PRE + data + b"\xF7", CARD), sub, nonce, index)
        start = req(0x11)
        if start["answer"]:
            raise CardError("inventory start refused")
        rows = []
        for index in range(65):
            for _ in range(self.polls):
                result = req(0x12, index)
                if result["state"] == 3:
                    raise CardError("inventory read failed; discard previous rows")
                if result["answer"] == 0:
                    rows.append(result["row"])
                    break
                if result["state"] == 2 and result["answer"] == 3:
                    return dict(rows=rows, flags=result["flags"], nonce=nonce)
                if result["answer"] != 1:
                    raise CardError("inventory generation/index lost")
                self.poll()
            else:
                raise CardError("inventory timed out")
        raise CardError("inventory row bound exceeded")

    def engine_delete(self, name):
        """Delete only NAME.DSE after typed NAME.DST/DSO recovery; pointers remain."""
        self.must(b"\x13" + engine_name(name), "engine delete")
        self.wait("engine delete")
        self.req(b"\x7F")

    def engine_verify(self, name):
        self.must(b"\x0F" + engine_name(name), "engine verify")
        st = self.wait("engine verify")
        return st["size"], st["info"]

    def delete(self, name):
        self.must(b"\x04" + card_name(name), "delete")
        self.wait("delete")
        self.req(b"\x7F")

    def engine(self, pack, name):
        """Stock pack pack's engine pointer (1..31: folder pack - 1; None: the card default) = name.dse, or none (name
        None)."""
        if pack is not None and not 1 <= pack <= CARD_PACKS:
            raise ValueError(f"pack {pack}: stock card selector 1..31; selector 0 is unavailable")
        if name is not None and name.lower() == "default":
            raise ValueError("default is the card default's reserved name")
        nm = bytes(8) if name is None else card_name(name)
        self.must(b"\x0D" + bytes([CARD_DEFAULT if pack is None else pack]) + nm, "engine pointer")
        self.wait("engine pointer")
        self.req(b"\x7F")

    def list(self):
        self.must(b"\x05", "list")
        st = self.wait("list")
        out = []
        for i in range(st["count"]):
            r = self.xfer(PRE + bytes([CARD, 0x06, i]) + b"\xF7", CARD)
            if not r or len(r) != 25 or r[7] != 0x06 or r[8] != i:
                raise CardError(f"no list entry {i}: {r.hex(' ') if r else None}")
            out.append((r[9:21].rstrip(b"\0").decode("ascii", "replace"), g7(r[21:24])))
        self.req(b"\x7F")
        return out

    def engstatus(self):
        """cmd 10 0B: the engine status (read-only; no session)."""
        r = self.xfer(PRE + bytes([CARD, 0x0B]) + b"\xF7", CARD)
        try:
            return decode_engstatus(r)
        except ValueError as e:
            raise CardError(str(e))


EP_PHASE = ["idle", "select", "samples"]
EP_C0 = ["none", "candidate", "stock (no data)", "stock (restart)", "terminal", "stock", "loader wait"]
EP_VERDICT = ["none", "ok", "same", "no pointer", "stock pointer", "invalid", "no file", "bad header", "pass 1 refused",
              "BAD", "session BAD", "busy", "nested", "O1 fault", "refused", "changed", "not ready", "terminal",
              "loader wait", "I/O error"]
EP_PTR = ["none", "pack", "pack (.CFO)", "default", "default (.CFO)"]
EP_SEL = ["stock", "engine armed", "core-1 suppression armed"]
EP_FL = ["own load", "retry", "abort", "pushed", "was engine", "refreshed", "slot error"]
EP_QPH = ["closed", "header", "request", "open", "recover", "quarantine", "init", "init wait"]
EP_KIND = ["stock", "FM", "reserved"]
EP_EO = ("<I4B4BI2BHIHH4BI4B8s", "magic phase tf sel c0out r_pair r_kind r_fault judged r_crc c_pack c_kind att c_crc "
         "rgen c_words n_trav n_ok n_fail fl bad_crc verdict ptr busy loads c_name")
EP_EO_MAGIC, EP_TRY, EP_BAD, EP_CBR2 = 0x31504F45, 0x59525443, 0x44414243, 0x32524243
EP_WD = ("<4I4HH2B2H", "last t0 t1 c1at dsp0 dsp1 age gap feeds open c1 wmax n")   # struct wd (layout 2), ticks (ms)
EP_LEN = {1: 163, 2: 203, 3: 223, 4: 248, 5: 253}                                           # 10 0B layout version -> length
M32 = 0xFFFFFFFF


def decode_engstatus(r):
    """The engine status reply (cmd 10 0B, 'E' layouts 1..4) as a dict. Layout 2 adds watchdog
    timing; layout 3 adds the pack-load record; layout 4 adds cached ABI3 health. Absent records are None. ValueError for foreign, noncanonical or
    incorrectly sized replies."""
    if not r or r[:8] != PRE + bytes([CARD, 0x0B]):
        raise ValueError("no engine status reply")
    if len(r) < 11 or r[8] != ord("E") or r[-1] != 0xF7:
        raise ValueError(f"not the engine-optional status layout ({len(r)} bytes)")
    if r[9] not in EP_LEN:
        raise ValueError(f"engine status layout version {r[9]} not known")
    if len(r) != EP_LEN[r[9]]:
        raise ValueError(f"engine status layout {r[9]} is {EP_LEN[r[9]]} bytes, not {len(r)}")
    if any(x > 0x7F for x in r[1:-1]):
        raise ValueError("engine status: a data byte is not 7-bit")
    starts = [11 + 5 * i for i in range(28)] + [157] + ([162 + 5 * i for i in range(8)] if r[9] >= 2 else [])
    pack_starts = [202 + 5 * i for i in range(4)] if r[9] >= 3 else []
    health_starts = [222 + 5 * i for i in range(6 if r[9] >= 5 else 5)] if r[9] >= 4 else []
    if any(r[k] > 0x0F for k in starts + pack_starts + health_starts): # 5 x 7 bits hold 35: a canonical u32 has a top digit <= 0x0F
        raise ValueError("engine status: a word is not a canonical 32-bit value")
    if r[10] > 0x1F:
        raise ValueError(f"engine status: unknown flags {r[10]:#x}")
    words = [g7(r[k:k + 5]) for k in starts[:28]]
    eo = dict(zip(EP_EO[1].split(), struct.unpack(EP_EO[0], struct.pack("<12I", *words[:12]))))
    eo["c_name"] = eo["c_name"].rstrip(b"\0").decode("ascii", "replace")
    f = r[10]
    wdw = [g7(r[k:k + 5]) for k in starts[29:]] or None
    wd = dict(zip(EP_WD[1].split(), struct.unpack(EP_WD[0], struct.pack("<8I", *wdw)))) if wdw else None
    pack_load = None
    if pack_starts:
        seq, started, finished, packed = (g7(r[k:k + 5]) for k in pack_starts)
        pack_load = dict(started=started, finished=finished,
                         **_pack_fields(seq, *struct.pack("<I", packed)))
    abi3_health = None
    if health_starts:
        mismatch, overrun, poll_at, flags, last_check = (g7(r[k:k + 5]) for k in health_starts[:5])
        armed, reason, started, reserved = struct.pack("<I", flags)
        if mismatch > 0xFFFFFF or overrun > 0xFFFFFF or armed > 1 or started > 1 or reason > 4 or reserved or last_check > 255:
            raise ValueError("engine status: noncanonical ABI3 health record")
        abi3_health = dict(mismatch=mismatch, overrun=overrun, poll_at=poll_at,
                           armed=bool(armed), reason=reason, started=bool(started), last_check=last_check)
        if r[9] >= 5:
            normal_max = g7(r[health_starts[5]:health_starts[5]+5])
            if normal_max > 0xFFFFFF: raise ValueError("engine status: noncanonical normal-span maximum")
            abi3_health['normal_max'] = normal_max
            abi3_health['normal_max_scope'] = 'Main cached since-load normal-block span maximum; F included; poll_at is ARM poll time'
    return dict(version=r[9], flags=f, engine_pack=bool(f & 1), engine_flash=bool(f & 2), itcm=bool(f & 4),
                eo_ok=bool(f & 8), a1_ok=bool(f & 16), eo=eo, eo_words=words[:12], itcm_words=words[12:],
                marker=words[12:20], cbr2=words[20:28], pub=r[151], tok=r[152], ld_phase=r[153], q_ph=r[154],
                q_busy=r[155], q_excl=r[156], tick=g7(r[157:162]), wd_words=wdw, wd=wd, pack_load=pack_load,
                abi3_health=abi3_health)


def _name(table, i):
    return table[i] if 0 <= i < len(table) else f"#{i}"


def engstatus_mode(d):
    """(mode, attention items) from decode_engstatus's dict. Only a complete record (struct eo and a1 present) is
    classified; residency (eo's resident pair) is not publication (pub). "stock" is normal only when the stock pair is
    resident, nothing is published, the queue is closed and nothing needs attention. The record is the ARM's own
    bookkeeping, not a reading of the DSP's live health."""
    e = d["eo"]
    if not d["eo_ok"] or not d["a1_ok"] or e["magic"] != EP_EO_MAGIC:
        return "unknown", ["engine state missing (struct eo or a1 absent): not classified"]
    att = []
    if e["r_fault"]:
        att.append("resident fault")
    if d["q_excl"]:
        att.append("queue excluded")
    if d["q_busy"]:
        att.append("queue busy")
    if e["phase"]:
        att.append(f"transaction in phase {_name(EP_PHASE, e['phase'])}")
    if d["tok"] & 1:
        att.append("a Main call holds the token")
    if d["ld_phase"] == 5:
        att.append("a card job runs")
    health = d.get("abi3_health")
    if e["r_pair"] == 1 and health and (not health["armed"] or health["reason"]):
        att.append("ABI3 runtime health unready")
    if e["r_pair"] == 0 and d["pub"] == 0:
        mode = "stock"
        if d["q_ph"] != 0:
            att.append(f"queue {_name(EP_QPH, d['q_ph'])} with the stock pair resident")
    elif e["r_pair"] == 1 and d["pub"] == 0:
        mode = f"engine resident ({_name(EP_KIND, e['r_kind'])}), not published"
    elif e["r_pair"] == 1 and d["pub"] == e["r_kind"]:
        mode = f"engine published ({_name(EP_KIND, d['pub'])})"
    else:
        mode = (f"inconsistent: published {_name(EP_KIND, d['pub'])}, resident "
                f"{'engine ' + _name(EP_KIND, e['r_kind']) if e['r_pair'] else 'stock pair'}")
    return mode, att


def describe_engstatus(d):
    """Readable lines for decode_engstatus's dict; the first is the mode (engstatus_mode)."""
    mode, att = engstatus_mode(d)
    normal = mode == "stock" and not att and d["q_ph"] == 0
    head = [f"mode: {mode}" + (" (stock pair resident, nothing published, queue closed): normal" if normal else "")
            + (f"; attention: {', '.join(att)}" if att else "")]
    e, out = d["eo"], head
    p = d.get("pack_load")
    if p is not None:
        loaded = "unknown" if p["loaded"] == 127 else str(p["loaded"])
        out.append(f"pack load request #{p['seq']}: {PACK_STATE[p['state']]}, target {p['target']}, "
                   f"result {PACK_RESULT[p['result']]}; recorded loaded pack {loaded}; "
                   f"started {p['started']}, finished {p['finished']} ticks")
    if not d["eo_ok"] or e["magic"] != EP_EO_MAGIC:
        out.append("transaction state: absent (no struct eo: the devkit region is not set up)")
    else:
        fl = [n for k, n in enumerate(EP_FL) if e["fl"] >> k & 1]
        out.append(f"transaction: phase {_name(EP_PHASE, e['phase'])}, verdict {_name(EP_VERDICT, e['verdict'])}, "
                   f"pointer {_name(EP_PTR, e['ptr'])}, attempt {e['att']}, DSP loads {e['loads']}, "
                   f"busy refusals {e['busy']}, trial failed {e['tf']}, flags {', '.join(fl) or 'none'}")
        out.append(f"candidate: pack {e['c_pack']} kind {_name(EP_KIND, e['c_kind'])} {e['c_name'].lower() or '-'}.dse "
                   f"CRC {e['c_crc']:08x} {e['c_words']} words; core-0 send {_name(EP_C0, e['c0out'])}, "
                   f"armed {_name(EP_SEL, e['sel'])}")
        out.append(f"resident: {'engine' if e['r_pair'] else 'stock pair'} kind {_name(EP_KIND, e['r_kind'])} "
                   f"CRC {e['r_crc']:08x}, fault {e['r_fault']}, rgen {e['rgen']}, marker judged {e['judged']}, "
                   f"session BAD {e['bad_crc']:08x}")
        out.append(f"slots (last samples phase): {e['n_trav']} traversed, {e['n_ok']} ok, {e['n_fail']} failed, "
                   f"{(e['n_trav'] - e['n_ok'] - e['n_fail']) & 0xFF} empty")
    out.append(f"published: {_name(EP_KIND, d['pub'])}" + ("" if d["a1_ok"] else " (no a1 record)"))
    health = d.get("abi3_health")
    if health:
        reasons = ("none", "read/write unconfirmed", "stack mismatch", "trial overrun",
                   "declared cycle budget exceeds the platform meter range (24-bit)")
        out.append(f"ABI3 cached health: armed {health['armed']}, reason {reasons[health['reason']]}, "
                   f"mismatch {health['mismatch']}, overrun {health['overrun']}, last poll {health['poll_at']} ticks; "
                   f"last pre-load check {health['last_check']} (runtime check 14 is separate)")
        if 'normal_max' in health:
            out.append(f"ABI3 cached normal-span maximum: {health['normal_max']} cycles since load, F included; "
                       f"last ARM poll {health['poll_at']} ticks")
    out.append(f"core-0 queue: {_name(EP_QPH, d['q_ph'])}, busy {d['q_busy']}, excluded {d['q_excl']}")
    tok = [n for k, n in ((1, "Main call"), (2, "Content job")) if d["tok"] & k]
    out.append(f"token: {', '.join(tok) or 'free'}; card writer: {'a job runs' if d['ld_phase'] == 5 else 'idle'} "
               f"(phase {d['ld_phase']})")
    m, c = d["marker"], d["cbr2"]
    mk = {EP_TRY: "TRY", EP_BAD: "BAD", 0: "none"}.get(m[0], f"{m[0]:08x}")
    cm = "ok" if m[1] == (~m[0] & 0xFFFFFFFF) else "bad"
    out.append(f"ITCM: {'on' if d['itcm'] else 'off'}; marker {mk} (complement {cm}) CRC {m[2]:08x} state {m[3]:08x}")
    if c[0] == EP_CBR2:
        out.append(f"boot record CBR2 #{c[1]}: pack {c[2] & 0xFF} kind {c[2] >> 8 & 0xFF} pointer {c[2] >> 16 & 0xFF} "
                   f"verdict {_name(EP_VERDICT, c[2] >> 24)}, CRC {c[3]:08x}, rgen {c[4] & 0xFFFF} attempt "
                   f"{c[4] >> 16}, loads {c[5] & 0xFF} c0out {_name(EP_C0, c[5] >> 8 & 0xFF)}, slots "
                   f"{c[6] & 0xFF}/{c[6] >> 8 & 0xFF}/{c[6] >> 16 & 0xFF}/{c[6] >> 24} (traversed/ok/failed/empty), "
                   f"tick {c[7]}")
    else:
        out.append("boot record CBR2: none yet")
    w = d.get("wd")
    if w is None:
        out.append("watchdog and load timing: not in this firmware's readout (layout 1)")
    elif not w["n"]:
        out.append(f"watchdog and load timing: no transaction timed yet; last feed {(d['tick'] - w['last']) & M32} "
                   "ticks before the readout")
    else:
        def q(v):                             # 16-bit fields saturate: 0xFFFF is a lower bound
            return f">={v}" if v == 0xFFFF else f"{v}"
        span = (w["t1"] - w["t0"]) & M32
        out.append(f"last transaction (#{q(w['n'])}{', still open' if w['open'] else ''}): "
                   + (f"{span} ticks (tick {w['t0']}..{w['t1']})" if not w["open"] else f"from tick {w['t0']}")
                   + f", own DSP loads core 0 {q(w['dsp0'])} + core 1 {q(w['dsp1'])} ticks; watchdog: "
                   f"{q(w['feeds'])} feeds, {q(w['age'])} ticks since the last feed at the start, largest gap "
                   f"{q(w['gap'])} ticks")
        out.append(f"watchdog: largest gap of the timed transactions this boot {q(w['wmax'])} ticks (nothing outside "
                   f"them is covered); last feed {(d['tick'] - w['last']) & M32} ticks before the readout")
    if w is not None:
        out.append("timing unit: raw RTOS-tick counts (nominally 1 ms), not strict physical bounds: tick quantisation "
                   "and SysTick service latency (an interval can read a tick long or short), plus an unbounded loss "
                   "while interrupts are masked; >=65535 marks a saturated field")
    out.append(f"engine-optional firmware: per-pack engines {'on' if d['engine_pack'] else 'off'}, flash engine "
               f"{'yes' if d['engine_flash'] else 'no'}; tick {d['tick']} ms (read with the rest, in one scheduler "
               "suspension; the ARM's record, not the DSP's live health)")
    return out


def fd_xfer(fd):
    def xfer(msg, cmd):
        write_all(fd, msg, time.monotonic() + 2.0)
        return read_reply(fd, cmd)
    return xfer


def app_build(fd):
    """Read the application build before choosing an engine file format."""
    prefix = PRE[:5]
    write_all(fd, prefix + b"\x70\xf7", time.monotonic() + 2.0)
    reply = read_reply(fd, 0x70, prefix=prefix)
    if not reply or len(reply) < 17 or any(value >= 128 for value in reply[1:-1]):
        raise CardError("no valid firmware version reply")
    return g7(reply[13:16])


def card_main(args):
    if len(args) < 2 or args[0] not in ("write", "delete", "verify", "list", "engine", "engstatus", "engine-put",
                                        "engine-verify"):
        sys.exit(__doc__)
    op, dev, rest = args[0], args[1], args[2:]
    if op == "write":
        if not 1 <= len(rest) <= 2:
            sys.exit(__doc__)
        blob = open(rest[0], "rb").read()
        name = rest[1].lower() if len(rest) == 2 else default_name(rest[0])
        try:
            build_module.check(blob)
            card_name(name)
        except ValueError as e:
            sys.exit(f"{rest[0]}: {e}")
    elif op == "engine-put":
        check_only = dev == "--check"
        if not 1 <= len(rest) <= 2:
            sys.exit(__doc__)
        with open(rest[0], "rb") as stream:
            blob = stream.read()
        name = rest[1].lower() if len(rest) == 2 else default_name(rest[0])
        try:
            engine_name(name)
            if check_only:
                import cre3_check
                info = engine_check(blob, app_build=cre3_check.policy()["minimum_app_build"])
                print(f"{name}.dse: {info['engine_format']}, {len(blob)} bytes, static checks passed")
                return
            engine_check(blob)
        except ValueError as e:
            sys.exit(f"{rest[0]}: {e}")
    elif op == "engine-verify":
        if len(rest) != 1:
            sys.exit(__doc__)
        name = rest[0].lower()
        try:
            engine_name(name)
        except ValueError as e:
            sys.exit(str(e))
    elif op in ("delete", "verify"):
        if len(rest) != 1:
            sys.exit(__doc__)
        name = rest[0].lower()
        try:
            card_name(name)
        except ValueError as e:
            sys.exit(str(e))
    elif op == "engine":
        if len(rest) != 2:
            sys.exit(__doc__)
        pack = None if rest[0].lower() == "default" else int(rest[0]) if rest[0].isdigit() else -1
        name = None if rest[1].lower() == "none" else rest[1].lower()
        try:
            if pack == -1 or (pack is not None and not 1 <= pack <= CARD_PACKS):
                raise ValueError(f"pack {rest[0]}: stock pack number 1..31, or default")
            if name is not None:
                card_name(name)
                if name == "default":
                    raise ValueError("default is the card default's reserved name")
        except ValueError as e:
            sys.exit(str(e))
    elif rest:
        sys.exit(__doc__)
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    try:
        card = Card(fd_xfer(fd), poll=lambda: time.sleep(0.05), polls=600)
        if op == "write":
            size, crc = card.write(blob, name)
            print(f"wrote 1:/modules/{name.lower()}.mod: {size} bytes, CRC {crc:08x}, read back from the card and matched")
        elif op == "delete":
            card.delete(name)
            print(f"deleted 1:/modules/{name}.mod")
        elif op == "verify":
            size, crc = card.verify(name)
            card.req(b"\x7F")
            print(f"1:/modules/{name.lower()}.mod: {size} bytes, CRC {crc:08x}")
        elif op == "engine":
            card.engine(pack, name)
            where = "1:/engines/default.cfg" if pack is None else f"pack {pack}'s engine.cfg"
            print(f"wrote {where}: {'none' if name is None else name + '.dse'} (from the pack's next load)")
        elif op == "engstatus":
            print("\n".join(describe_engstatus(card.engstatus())))
        elif op == "engine-verify":
            size, crc = card.engine_verify(name)
            card.req(b"\x7F")
            print(f"1:/engines/{name}.dse: {size} bytes, CRC {crc:08x} (the job settled {name}.dst / {name}.dso first)")
        elif op == "engine-put":
            engine_check(blob, app_build=app_build(fd))
            size, crc = card.engine_put(blob, name)
            print(f"wrote 1:/engines/{name}.dse: {size} bytes, CRC {crc:08x}, read back from the card and matched "
                  "(a pack pointing to it loads it from its next load)")
        else:
            for n, size in card.list():
                print(f"{n.lower():12s} {size:6d}")
    except (CardError, OSError, ValueError) as e:
        sys.exit(f"card {op}: {e}")
    finally:
        os.close(fd)


def main(dev, path):
    blob = open(path, "rb").read()
    try:
        build_module.check(blob)                   # refuse before touching the unit
    except ValueError as e:
        sys.exit(f"{path}: {e}")
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    try:
        for msg in upload_msgs(blob):
            write_all(fd, msg, time.monotonic() + 2.0)
            time.sleep(0.005)
        size = (len(blob) + 31) // 32 * 32
        back = read_itcm(fd, MOD_BASE, size)[:len(blob)]
        if back != blob:
            bad = next(i for i in range(len(blob)) if back[i] != blob[i])
            sys.exit(f"read-back mismatch at offset {bad}: wrote {blob[bad]:#04x}, read {back[bad]:#04x}")
        print(f"uploaded and verified {len(blob)} bytes at ITCM {MOD_BASE:#x}")
        write_all(fd, PRE + b"\x05\xF7", time.monotonic() + 2.0)
        st = read_reply(fd, 0x7F)
        if not st or len(st) != 20:
            sys.exit(f"no status reply after start: {st.hex(' ') if st else None}")
        print(f"status: mode {st[7]}, module {['none', 'valid', 'BAD', 'ITCM unavailable'][st[18]] if st[18] < 4 else st[18]}")
        if st[7] != 3:
            sys.exit("module did not start")
    finally:
        os.close(fd)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "card":
        card_main(sys.argv[2:])
        sys.exit(0)
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
