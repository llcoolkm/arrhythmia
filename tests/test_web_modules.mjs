// Node tests for the JavaScript core of web/modules.html (the module loader): allowlist, encoders, module checks and
// a fake unit that follows the cmd 10 protocol of patches/src/cardwrite.c.
//
//   node tests/test_web_modules.mjs
//   node tests/test_web_modules.mjs --dump FILE NAME     (tests/test_web_modules.py: the SysEx of one write, as JSON)
//
// The core is the code between MLCORE BEGIN/END in web/modules.html, run unchanged. No unit, no MIDI device.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const html = fs.readFileSync(path.join(root, 'web/modules.html'), 'utf8');
const core = html.slice(html.indexOf('/* MLCORE BEGIN */'), html.indexOf('/* MLCORE END */'));
const ML = new Function(core + '\nreturn ML;')();

const PRE = [0xF0, 0x00, 0x20, 0x29, 0x00, 0x60];
const p7 = (v, n) => Array.from({ length: n }, (_, i) => Math.floor(v / 2 ** (7 * (n - 1 - i))) & 0x7F);
const g7 = b => Array.from(b).reduce((v, x) => v * 128 + x, 0);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const W = { OK: 1, NOCARD: 2, BUSY: 3, NAME: 4, SIZE: 5, OFFSET: 6, CRC: 7, MODULE: 8, NOTFOUND: 11, STATE: 13 };

// A synthetic module that passes tools/build_module.py check: CRM1 (24-byte header) or CRM2 (28).
function makeModule(size, magic2 = false, seed = 1) {
  const b = new Uint8Array(size), dv = new DataView(b.buffer);
  let x = seed;
  for (let i = 0; i < size; i++) { x = (x * 1103515245 + 12345) >>> 0; b[i] = x >>> 24; }
  const hdr = magic2 ? 28 : 24;
  dv.setUint32(0, magic2 ? 0x324D5243 : 0x314D5243, true);
  dv.setUint32(4, size, true);
  dv.setUint32(12, 0, true); dv.setUint32(16, hdr | 1, true); dv.setUint32(20, 0, true);
  if (magic2) dv.setUint32(24, 0, true);
  dv.setUint32(8, 0, true);
  dv.setUint32(8, ML.crc32(b), true);
  return b;
}

// ------------------------------------------------------------------ fake unit (cardwrite.c, simplified)
class FakeUnit {
  constructor(o = {}) {
    Object.assign(this, { app: 7331, writer: true, files: new Map(), playing: false, busy: false, card: true,
                          silentAfter: Infinity, phase: 0, job: 0, result: 0, fr: 0, got: 0, size: 0, crc: 0,
                          info: 0, count: 0, name: '', staged: null, ents: [], sent: [], instant: true,
                          lose: new Set(), delay: {}, jobFail: null, verifyCrc: null, countOverride: null,
                          noEntry: new Set(), corrupt: null }, o);
    this.link = null;
  }
  reply(m, ms = 0) { setTimeout(() => this.link.feed(m), ms); }
  status(sub, answer) {
    if (this.lose.has(sub)) { this.lose.delete(sub); return; }      // the unit acted; the reply is lost
    let r = [...PRE, 0x10, sub, answer, this.phase, this.job, this.result, this.fr,
             ...p7(this.got, 3), ...p7(this.size, 3), ...p7(this.info, 5), this.count, 0xF7];
    if (this.corrupt && this.corrupt(sub, this)) r = r.slice(0, 20).concat([0x7F, 0xF7]);   // malformed reply
    this.reply(Uint8Array.from(r), this.delay[sub] || 0);
  }
  name8(m) {
    const b = Array.from(m.subarray(8, 16)), n = b.indexOf(0) < 0 ? 8 : b.indexOf(0);
    const s = String.fromCharCode(...b.slice(0, n));
    return n && /^[A-Z0-9_-]+$/.test(s) && b.slice(n).every(c => c === 0) ? s + '.MOD' : null;
  }
  open(job, phase) {
    if (this.phase === 2 && !this.playing && this.instant) this.runJob();   // time passes: a waiting job finishes
    if (this.busy || this.phase === 2) return W.BUSY;
    this.job = job; this.phase = phase; this.result = 0; this.got = 0; this.fr = 0; this.count = 0;
    return W.OK;
  }
  runJob() {
    if (this.jobFail && this.jobFail.job === this.job) {
      Object.assign(this, { result: this.jobFail.result, fr: this.jobFail.fr || 0, phase: 3 });
      this.jobFail = null;
      return;
    }
    if (!this.card) this.result = W.NOCARD;
    else if (this.job === 1) { this.files.set(this.name, this.staged.slice(0, this.size)); this.result = W.OK; }
    else if (this.job === 2) this.result = this.files.delete(this.name) ? W.OK : W.NOTFOUND;
    else if (this.job === 3) { this.ents = [...this.files]; this.count = this.ents.length; this.result = W.OK; }
    else if (this.job === 4) {
      const f = this.files.get(this.name);
      if (f) { this.size = f.length; this.info = this.verifyCrc ?? ML.crc32(f); this.result = W.OK; }
      else this.result = W.NOTFOUND;
    }
    if (this.job === 3 && this.countOverride !== null) this.count = this.countOverride;
    this.phase = 3;
  }
  receive(m) {
    assert.ok(ML.allowed(m), `the page sent a message outside its allowlist: ${ML.toHex(m, ' ')}`);
    this.sent.push(m);
    if (this.sent.length > this.silentAfter) return;
    if (ML.toHex(m) === ML.toHex(ML.VERSION_QUERY)) {
      this.reply(Uint8Array.from([0xF0, 0, 0x20, 0x29, 0, 0x70, 0, 0, ...p7(4799, 3), 0, 0, ...p7(this.app, 3), 0x40, 2, 0xF7]));
      return;
    }
    if (!this.writer) return;
    const sub = m[7], len = m.length;
    if (sub === 0x06) {
      const i = m[8];
      if (this.phase === 3 && this.job === 3 && i < this.count && !this.noEntry.has(i)) {
        const [n, f] = this.ents[i] || [`F${i}.MOD`, new Uint8Array(30)], nm = Array.from({ length: 12 }, (_, k) => k < n.length ? n.charCodeAt(k) : 0);
        this.reply(Uint8Array.from([...PRE, 0x10, 0x06, i, ...nm, ...p7(f.length, 3), 0xF7]));
      }
      return;
    }
    let answer = W.STATE;
    if (sub === 0x00) {
      answer = W.OK;
      if (this.phase === 2 && !this.playing && this.instant) this.runJob();
    } else if (sub === 0x01 && len === 25) {
      const name = this.name8(m), size = g7(m.subarray(16, 19)), crc = g7(m.subarray(19, 24)) >>> 0;
      if (!name) answer = W.NAME;
      else if (size < 26 || size > 15360) answer = W.SIZE;
      else if ((answer = this.open(1, 1)) === W.OK) {
        Object.assign(this, { name, size, crc, staged: new Uint8Array(size), info: 0 });   // info: verify only
      }
    } else if (sub === 0x02) {
      const off = g7(m.subarray(8, 11)), n = (len - 12) / 2;
      if (this.phase !== 1) answer = W.STATE;
      else if (off !== this.got || n > this.size - off) answer = W.OFFSET;
      else {
        for (let i = 0; i < n; i++) this.staged[off + i] = (m[11 + 2 * i] << 4) | m[12 + 2 * i];
        this.got = off + n; answer = W.OK;
      }
    } else if (sub === 0x03) {
      if (this.phase !== 1) answer = W.STATE;
      else if (this.got !== this.size) answer = W.SIZE;
      else if (ML.crc32(this.staged) !== this.crc) answer = W.CRC;
      else { this.got = 0; this.phase = 2; answer = W.OK; }
    } else if ((sub === 0x04 || sub === 0x07) && len === 17) {
      const name = this.name8(m);
      if (!name) answer = W.NAME;
      else if ((answer = this.open(sub === 0x04 ? 2 : 4, 2)) === W.OK) this.name = name;
    } else if (sub === 0x05) answer = this.open(3, 2);
    else if (sub === 0x7F) {
      if (this.phase === 2) answer = W.BUSY; else { this.phase = 0; answer = W.OK; }
    } else return;
    this.status(sub, answer);
  }
  subs() { return this.sent.filter(m => m[5] === 0x60).map(m => m[7]); }
}
function rig(o = {}, opt = {}) {
  const unit = new FakeUnit(o);
  const link = new ML.Link(m => unit.receive(Uint8Array.from(m)));
  unit.link = link;
  const card = new ML.Card(link, { replyMs: 200, probeMs: 200, drainMs: 0, pollMs: 1, hintMs: 20, jobMs: 300, ...opt });
  return { unit, link, card };
}

// ------------------------------------------------------------------ --dump: one write, every SysEx sent, as JSON
const argv = process.argv.slice(2);
if (argv[0] === '--dump') {
  const blob = new Uint8Array(fs.readFileSync(argv[1]));
  const { unit, card } = rig({}, { pollMs: 0 });
  await card.write(blob, argv[2]);
  const out = Buffer.from(JSON.stringify(unit.sent.map(m => ML.toHex(m))));
  for (let o = 0; o < out.length;) o += fs.writeSync(1, out, o);   // synchronous: exit must not cut a pipe write
  process.exit(0);
}

let passed = 0;
const results = [];
async function test(name, fn) {
  try { await fn(); passed++; results.push(`ok   ${name}`); }
  catch (e) { results.push(`FAIL ${name}: ${e.stack}`); process.exitCode = 1; }
}
async function rejects(p, kind, re) {
  let err = null;
  try { await p; } catch (e) { err = e; }
  assert.ok(err, 'expected an error');
  assert.ok(err instanceof ML.CardError, `not a CardError: ${err && err.stack}`);
  assert.equal(err.kind, kind, err.message);
  if (re) assert.match(err.message, re);
  return err;
}

await test('allowlist: version query and cmd 10 subs only', () => {
  const m = a => Uint8Array.from(a);
  assert.ok(ML.allowed(ML.VERSION_QUERY));
  for (const s of [0, 1, 2, 3, 4, 5, 6, 7, 0x7F]) assert.ok(ML.allowed(m([...PRE, 0x10, s, 0xF7])), `sub ${s}`);
  for (const bad of [[0xF0, 0, 0x20, 0x29, 0, 0x77, 0xF7], [...PRE, 0x08, 0, 0, 0, 0, 1, 0xF7], [...PRE, 0x00, 0xF7],
                     [...PRE, 0x04, 0, 0, 0, 1, 2, 0xF7], [...PRE, 0x05, 0xF7], [...PRE, 0x10, 0x08, 0xF7],
                     [...PRE, 0x10, 0x01, 0x80, 0xF7], [0xF0, 0, 0x20, 0x29, 0, 0x70, 1, 0xF7], [...PRE, 0x10, 0x00],
                     [0xF0, 0, 0x20, 0x29, 0, 0x71, 0xF7], [0xF0, 0, 0x20, 0x29, 0, 0x60, 0x0F, 0xF7]])
    assert.ok(!ML.allowed(m(bad)), ML.toHex(bad, ' '));
  const link = new ML.Link(() => { throw new Error('must not be sent'); });
  assert.throws(() => link.send(m([0xF0, 0, 0x20, 0x29, 0, 0x77, 0xF7])), /does not send/);
  assert.throws(() => link.send(m([...PRE, 0x08, 0, 0, 0, 0, 1, 0xF7])), /does not send/);
});

await test('crc32 is zlib CRC-32', () => {
  assert.equal(ML.crc32(new TextEncoder().encode('123456789')), 0xCBF43926);
  assert.equal(ML.crc32(new Uint8Array(0)), 0);
});

await test('encoders: the cardwrite.c layout', () => {
  assert.equal(ML.toHex(ML.enc.begin('01PAINT', 1000, 0xCBF43926), ' '),
               'f0 00 20 29 00 60 10 01 30 31 50 41 49 4e 54 00 00 07 68 0c 5f 50 72 26 f7');
  assert.equal(ML.enc.begin('A', 26, 0).length, 25);
  assert.equal(ML.toHex(ML.enc.piece(32, [0xAB, 0x01]), ' '), 'f0 00 20 29 00 60 10 02 00 00 20 0a 0b 00 01 f7');
  assert.equal(ML.enc.piece(0, new Uint8Array(32)).length, 12 + 64);
  assert.equal(ML.enc.del('X').length, 17);
  assert.equal(ML.enc.verify('X').length, 17);
  assert.equal(ML.toHex(ML.enc.entry(5), ' '), 'f0 00 20 29 00 60 10 06 05 f7');
  for (const k of ['status', 'commit', 'list', 'end']) assert.equal(ML.enc[k]().length, 9);
});

await test('names: 1..8 of A-Z 0-9 _ -', () => {
  for (const ok of ['A', '01PAINT', 'MY_MOD-1', 'ABCDEFGH']) ML.nameBytes(ok);
  for (const bad of ['', 'abc', 'ABCDEFGHI', 'A.B', 'Ä', 'A B', '../X'])
    assert.throws(() => ML.nameBytes(bad), ML.CardError, bad);
  assert.equal(ML.nameOf('C:\\x\\01paint.mod'), '01PAINT');
  assert.equal(ML.nameOf('stars.MOD'), 'STARS');
});

await test('module check (tools/build_module.py check)', () => {
  for (const [n, m2] of [[26, false], [28 + 2, true], [1000, false], [15360, true]]) ML.checkModule(makeModule(n, m2));
  assert.throws(() => ML.checkModule(new Uint8Array(25)), /shorter/);
  assert.throws(() => ML.checkModule(makeModule(15361)), /maximum/);
  const bad = makeModule(100); bad[0] ^= 1; assert.throws(() => ML.checkModule(bad), /magic/);
  const crc = makeModule(100); crc[50] ^= 1; assert.throws(() => ML.checkModule(crc), /CRC/);
  const sz = makeModule(100).slice(0, 99); assert.throws(() => ML.checkModule(sz), /size field/);
  const ent = makeModule(100); new DataView(ent.buffer).setUint32(16, 24, true);
  assert.throws(() => ML.checkModule(ent), /frame entry/);
});

await test('check unit: stock firmware gets no cmd 10', async () => {
  const { unit, card } = rig({ app: 5706 });
  const r = await ML.checkUnit(card);
  assert.equal(r.ok, false); assert.match(r.text, /stock firmware/);
  assert.deepEqual(unit.subs(), []);
});

await test('check unit: firmware without the card writer', async () => {
  const { unit, card } = rig({ writer: false, app: 7329 });
  const r = await ML.checkUnit(card);
  assert.equal(r.ok, false); assert.match(r.text, /no card writer/);
  assert.deepEqual(unit.subs(), [0x00], 'one status request, no retry');
});

await test('check unit: card writer present', async () => {
  const { card } = rig();
  const r = await ML.checkUnit(card);
  assert.equal(r.ok, true);
});

await test('write, list, verify, delete', async () => {
  const { unit, card } = rig();
  const blob = makeModule(1000);
  const r = await card.write(blob, '01PAINT');
  assert.deepEqual([r.size, r.crc], [1000, ML.crc32(blob)]);
  assert.deepEqual(unit.files.get('01PAINT.MOD'), blob);
  assert.equal(unit.phase, 0, 'session ended');
  assert.equal(unit.sent.filter(m => m[7] === 0x02).length, Math.ceil(1000 / 32));
  assert.deepEqual(await card.list(), { files: [{ name: '01PAINT.MOD', size: 1000 }], capped: false });
  assert.deepEqual(await card.verify('01PAINT'), { size: 1000, crc: ML.crc32(blob) });
  await card.del('01PAINT');
  assert.equal(unit.files.size, 0);
  assert.deepEqual((await card.list()).files, []);
  await rejects(card.verify('01PAINT'), 'failed', /not found/);
  assert.equal(unit.phase, 0, 'a failed job ends the session once');
  assert.equal(card.ctx.pending, null);
});

await test('CRC above 2^31 compares unsigned', async () => {
  let blob, seed = 1;
  do blob = makeModule(200, false, seed++); while (ML.crc32(blob) < 0x80000000);
  const { card } = rig();
  const r = await card.write(blob, 'HI');
  assert.equal(r.crc, ML.crc32(blob));
  assert.ok(r.crc >= 0x80000000);
});

await test('write status info is not the announced CRC (verify only)', async () => {
  const { unit, card } = rig();
  const blob = makeModule(300), seen = [];
  const orig = unit.status.bind(unit);
  unit.status = (sub, a) => { seen.push([unit.job, unit.phase, unit.info]); orig(sub, a); };
  const r = await card.write(blob, 'X');
  const write = seen.filter(([job]) => job === 1);
  assert.ok(write.length > 3 && write.every(([, , info]) => info === 0), 'info stays 0 through the write job');
  assert.ok(seen.some(([job, phase, info]) => job === 4 && phase === 3 && info === ML.crc32(blob)));
  assert.equal(r.crc, ML.crc32(blob), 'the CRC comes from the verify job only');
});

await test('busy: one refused request, no retry, no END, work in progress not cancelled', async () => {
  const { unit, card } = rig({ busy: true });
  await rejects(card.write(makeModule(200), 'X'), 'busy', /Stop playback and try again/);
  assert.deepEqual(unit.subs(), [0x01]);
  assert.equal(card.ctx.pending, null, 'a refusal is a known outcome');
});

await test('BUSY with an unrelated job still running', async () => {
  const { unit, card } = rig({ phase: 2, job: 3, playing: true });     // a list job of another client waits
  await rejects(card.write(makeModule(200), 'X'), 'busy', /not cancelled/);
  await rejects(card.del('X'), 'busy');
  await rejects(card.list(), 'busy');
  assert.deepEqual(unit.subs(), [0x01, 0x04, 0x05], 'one request each, no END');
  assert.equal(unit.phase, 2);
  assert.equal(unit.job, 3, 'the running job is untouched');
});

await test('refused END during a job', async () => {
  const { unit, card } = rig({ phase: 2, job: 1, playing: true });
  const st = await card.req(ML.enc.end(), 'End');
  assert.equal(st.answer, ML.W_BUSY);
  assert.equal(unit.phase, 2, 'END does not cancel the job');
});

await test('transport running pauses the job beyond the client timeout', async () => {
  const events = [];
  const { unit, card } = rig({ playing: true }, { onEvent: k => events.push(k) });
  const t0 = Date.now();
  const e = await rejects(card.write(makeModule(200), 'X'), 'running', /stop playback, then select Check status/);
  assert.doesNotMatch(e.message, /fail|power/i);
  assert.ok(Date.now() - t0 < 2000);
  assert.ok(events.includes('slow'));
  assert.equal(unit.subs().filter(s => s === 0x01).length, 1, 'one begin only');
  assert.equal(unit.subs().filter(s => s === 0x03).length, 1, 'one commit only');
  assert.ok(!unit.subs().includes(0x7F), 'no END while the job runs');
  assert.equal(card.ctx.pending.op, 'write');
  await rejects(card.list(), 'pending', /Check status/);              // nothing more until checked
  const n = unit.sent.length;
  let r = await card.reconcile();
  assert.equal(r.running, true);
  assert.ok(card.ctx.pending, 'still pending while the job runs');
  assert.deepEqual(unit.subs().slice(-1), [0x00]);
  assert.equal(unit.sent.length, n + 1, 'reconcile while running: one status request only');
  unit.playing = false;                                                // playback stops: the job finishes
  r = await card.reconcile();
  assert.equal(r.ok, true); assert.match(r.text, /write is complete/);
  assert.equal(card.ctx.pending, null);
  assert.equal(unit.subs().filter(s => s === 0x01).length, 1, 'never re-BEGIN');
});

await test('lost begin ACK: unknown, nothing more, no END; reconcile finds no file', async () => {
  const { unit, card } = rig({ lose: new Set([0x01]) });
  await rejects(card.write(makeModule(200), 'X'), 'unknown', /outcome is unknown/);
  assert.deepEqual(unit.subs(), [0x01]);
  assert.equal(unit.phase, 1, 'the unit took it: a staging session is open');
  const r = await card.reconcile();
  assert.equal(r.ok, false); assert.match(r.text, /not on the card.*did not complete/);
  assert.equal(unit.subs().filter(s => s === 0x01 || s === 0x02 || s === 0x03).length, 1, 'no resend');
});

await test('lost piece ACK: unknown, no further piece, no END', async () => {
  const { unit, card } = rig();
  unit.corrupt = null;
  const orig = unit.status.bind(unit);
  let pieces = 0;
  unit.status = (sub, a) => { if (sub === 0x02 && ++pieces === 3) return; orig(sub, a); };
  await rejects(card.write(makeModule(1000), 'X'), 'unknown');
  assert.equal(unit.subs().filter(s => s === 0x02).length, 3);
  assert.ok(!unit.subs().includes(0x7F));
  assert.equal(unit.sent.length, 1 + 3);
});

await test('lost commit ACK before disk completion', async () => {
  const { unit, card } = rig({ lose: new Set([0x03]), playing: true });
  const blob = makeModule(500);
  await rejects(card.write(blob, 'X'), 'unknown');
  assert.ok(!unit.subs().includes(0x7F), 'no END after a lost commit ACK');
  assert.equal(unit.subs().filter(s => s === 0x03).length, 1, 'no second commit');
  assert.equal((await card.reconcile()).running, true);
  unit.playing = false;
  const r = await card.reconcile();
  assert.equal(r.ok, true);
  assert.deepEqual(unit.files.get('X.MOD'), blob);
});

await test('lost commit ACK after disk completion', async () => {
  const { unit, card } = rig({ lose: new Set([0x03]) });
  const blob = makeModule(500);
  await rejects(card.write(blob, 'X'), 'unknown');
  unit.runJob();                                                       // the write finished meanwhile
  const r = await card.reconcile();
  assert.equal(r.ok, true); assert.match(r.text, /intended file/);
  assert.equal(unit.subs().filter(s => s === 0x01).length, 1);
});

await test('lost delete ACK: reconcile by verify, no resend', async () => {
  const { unit, card } = rig();
  await card.write(makeModule(100), 'X');
  unit.lose.add(0x04);
  await rejects(card.del('X'), 'unknown');
  assert.ok(!unit.subs().slice(-1).includes(0x7F));
  const r = await card.reconcile();
  assert.equal(r.ok, true); assert.match(r.text, /delete is complete/);
  assert.equal(unit.subs().filter(s => s === 0x04).length, 1);
});

await test('late same-sub reply after a timeout is not taken as an answer', async () => {
  const { unit, link, card } = rig({ delay: { 0x05: 400 } });
  await card.write(makeModule(100), 'X');
  await rejects(card.list(), 'unknown');                               // the list reply comes after 400 ms
  await sleep(450);                                                    // ... and is now queued in the link
  assert.ok(link.q.length >= 1);
  await rejects(card.write(makeModule(100), 'Y'), 'pending');
  unit.delay = {};
  const r = await card.reconcile();                                    // drops the late reply first
  assert.equal(r.done, true);
  assert.equal(card.ctx.pending, null);
  assert.deepEqual((await card.list()).files.map(f => f.name), ['X.MOD']);
});

await test('job done with W_FS and W_RECOVER: FatFs code shown, no retry, the card can have changed', async () => {
  const a = rig({ jobFail: { job: 1, result: 9, fr: 7 } });
  let e = await rejects(a.card.write(makeModule(100), 'X'), 'failed', /File system error.*FatFs code 7.*can have changed/);
  assert.equal(a.unit.subs().filter(s => s === 0x01).length, 1);
  assert.deepEqual(a.unit.subs().slice(-1), [0x7F], 'the session is ended once (job done)');
  const b = rig({ jobFail: { job: 3, result: 15, fr: 1 } });
  e = await rejects(b.card.list(), 'failed', /helper files.*Do not delete them by hand/);
  assert.match(e.message, /FatFs code 1/);
  assert.equal(b.unit.subs().filter(s => s === 0x05).length, 1, 'no loop on W_RECOVER');
});

await test('bad verify CRC or size: mismatch', async () => {
  const { unit, card } = rig({ verifyCrc: 0x12345678 });
  await rejects(card.write(makeModule(100), 'X'), 'mismatch', /CRC 12345678/);
  assert.equal(card.ctx.pending, null);
  assert.deepEqual(unit.subs().slice(-1), [0x7F]);
});

await test('malformed status reply is ignored; unknown outcome', async () => {
  const { unit, card } = rig({ corrupt: (sub, u) => sub === 0x00 && u.job === 4 && u.phase === 3 });
  await rejects(card.write(makeModule(100), 'X'), 'unknown');
  assert.ok(card.ctx.pending);
  assert.ok(!unit.subs().slice(-1).includes(0x7F));
  for (const bad of [[...PRE, 0x10, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x10, 0, 0, 0, 0, 0, 0xF7].concat([]),
                     [...PRE, 0x10, 0, 1, 0, 0, 0, 0, 0x80, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xF7]])
    assert.equal(ML.parseStatus(Uint8Array.from(bad)), null);
  assert.equal(ML.parseEntry(Uint8Array.from([...PRE, 0x10, 0x06, 2, ...new Array(12).fill(0x41), 0, 0, 30, 0xF7]), 1), null,
               'an entry reply for another index');
});

await test('list cap and index refusal', async () => {
  const a = rig({ countOverride: 32 });
  const r = await a.card.list();
  assert.equal(r.files.length, 32); assert.equal(r.capped, true);
  const b = rig({ countOverride: 3, noEntry: new Set([1]) });
  await rejects(b.card.list(), 'unknown', /entry 1/);
  assert.deepEqual(b.unit.subs().filter(s => s === 0x06).length, 2, 'stops at the silent index');
  const c = rig({ countOverride: 33 });
  await rejects(c.card.list(), 'failed', /at most 32/);
});

await test('disconnect, then explicit reconciliation without resending the change', async () => {
  const { unit, link, card } = rig({ playing: true });
  const blob = makeModule(400);
  const p = card.write(blob, 'X');
  await sleep(60);                                                    // in the job wait
  link.abort(new ML.CardError('disconnected', 'The unit was disconnected.'));
  await rejects(p, 'unknown', /disconnected.*outcome is unknown/);
  const ctx = card.ctx;
  assert.equal(ctx.pending.name, 'X');
  // reconnect: a new link, the same ctx
  const link2 = new ML.Link(m => unit.receive(Uint8Array.from(m)));
  unit.link = link2;
  const c2 = new ML.Card(link2, { ...card.o, ctx });
  await rejects(c2.write(blob, 'X'), 'pending');
  unit.playing = false;
  const r = await c2.reconcile();
  assert.equal(r.ok, true);
  assert.equal(unit.subs().filter(s => s === 0x01).length, 1);
  assert.equal(unit.subs().filter(s => s === 0x03).length, 1);
});

await test('refused: no card, invalid file, bad name', async () => {
  const a = rig({ card: false });
  await rejects(a.card.list(), 'failed', /No microSD card/);
  const b = rig();
  await rejects(b.card.write(new Uint8Array(100), 'X'), 'invalid', /magic/);
  assert.deepEqual(b.unit.sent, [], 'an invalid file is refused before anything is sent');
  await rejects(b.card.write(makeModule(100), 'bad'), 'invalid', /Name/);
  assert.deepEqual(b.unit.sent, []);
});

await test('stale replies are drained before a request', async () => {
  const { unit, link } = rig();
  link.feed(Uint8Array.from([...PRE, 0x10, 0x05, 1, 3, 3, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 9, 0xF7]));
  const c2 = new ML.Card(link, { replyMs: 200, drainMs: 20, pollMs: 1, jobMs: 300 });
  assert.deepEqual((await c2.list()).files, []);
  assert.ok(unit.sent.length > 0);
});

// ---------------------------------------------------------------- page actions (ML.Actions, the page's flow)
function pageRig(o = {}, answers = []) {
  const r = rig(o);
  const ui = { statuses: [], logs: [], asked: [], lists: 0 };
  const a = new ML.Actions({
    card: async () => r.card, status: (t, c) => ui.statuses.push([t, c]), log: (t, c) => ui.logs.push([t, c]),
    confirm: q => { ui.asked.push(q); return answers.length ? answers.shift() : true; },
    busy: () => {}, listed: () => { ui.lists++; }, lost: () => {},
  });
  return { ...r, a, ui, last: () => ui.statuses[ui.statuses.length - 1] };
}
const count = (unit, sub) => unit.subs().filter(s => s === sub).length;

await test('page: failed delete (W_RECOVER, W_FS) sends no LIST; the error stays', async () => {
  for (const [result, re] of [[15, /helper files/], [9, /File system error/]]) {
    const p = pageRig();
    await p.card.write(makeModule(100), 'X');
    p.unit.jobFail = { job: 2, result, fr: 3 };
    const n = count(p.unit, 0x05);
    const r = await p.a.del('X');
    assert.equal(r.ok, false);
    assert.equal(count(p.unit, 0x05), n, 'no LIST after a failed delete');
    assert.match(p.last()[0], re); assert.equal(p.last()[1], 'bad');
    assert.equal(p.ui.lists, 0);
  }
});

await test('page: BUSY delete sends no LIST; the warning stays', async () => {
  const p = pageRig({ busy: true });
  const r = await p.a.del('X');
  assert.equal(r.ok, false);
  assert.deepEqual(p.unit.subs(), [0x04]);
  assert.match(p.last()[0], /Stop playback and try again/); assert.equal(p.last()[1], 'warn');
});

await test('page: lost delete ACK sends no LIST', async () => {
  const p = pageRig();
  await p.card.write(makeModule(100), 'X');
  p.unit.lose.add(0x04);
  const n = p.unit.sent.length;
  await p.a.del('X');
  assert.equal(p.unit.sent.length, n + 1, 'the delete only');
  assert.match(p.last()[0], /outcome is unknown/);
});

await test('page: successful delete sends exactly one LIST', async () => {
  const p = pageRig();
  await p.card.write(makeModule(100), 'X');
  const n = count(p.unit, 0x05);
  const r = await p.a.del('X');
  assert.equal(r.ok, true);
  assert.equal(count(p.unit, 0x05), n + 1);
  assert.match(p.last()[0], /^Deleted X\.MOD\. 0 files/); assert.equal(p.ui.lists, 1);
});

await test('page: delete not confirmed sends nothing', async () => {
  const p = pageRig({}, [false]);
  await p.a.del('X');
  assert.deepEqual(p.unit.sent, []);
});

await test('page: failed or refused write sends no LIST; a good write exactly one', async () => {
  const a = pageRig({ jobFail: { job: 1, result: 9, fr: 1 } });
  await a.a.write(makeModule(100), 'X');
  assert.equal(count(a.unit, 0x05), 0); assert.match(a.last()[0], /File system error/);
  const b = pageRig({ busy: true });
  await b.a.write(makeModule(100), 'X');
  assert.deepEqual(b.unit.subs(), [0x07]); assert.match(b.last()[0], /Stop playback/);
  const c = pageRig({ verifyCrc: 1 });
  await c.a.write(makeModule(100), 'X');
  assert.equal(count(c.unit, 0x05), 0); assert.match(c.last()[0], /expected/);
  const d = pageRig();
  const r = await d.a.write(makeModule(100), 'X');
  assert.equal(r.ok, true); assert.equal(count(d.unit, 0x05), 1);
  assert.match(d.last()[0], /^Wrote X\.MOD: 100 bytes.*size and CRC-32 match\. 1 file in the folder/);
});

await test('page: verify and check status start no follow-up job', async () => {
  const p = pageRig();
  await p.card.write(makeModule(100), 'X');
  await p.a.verify('Y');                                              // not found: failed
  assert.equal(count(p.unit, 0x05), 0);
  assert.match(p.last()[0], /not found/);
  p.unit.lose.add(0x03);
  await p.a.write(makeModule(120, false, 7), 'Z');
  p.unit.runJob();
  await p.a.reconcile();
  assert.equal(count(p.unit, 0x05), 0, 'no list after a failed verify, a lost write or check status');
  assert.match(p.last()[0], /write is complete/);
});

// Two review cases: (1) a delete that fails with
// W_RECOVER starts no LIST (above: 'page: failed delete ...'); (2) a capped 32-entry list that does not show the
// target does not let a write replace an existing file without the question.
await test('probe 2: capped list hides an existing file: fresh verify before BEGIN, then the question', async () => {
  const p = pageRig({}, [true]);
  const old = makeModule(60, false, 9);
  p.unit.files.set('HIDDEN.MOD', old);
  p.a.entries = Object.assign(Array.from({ length: 32 }, (_, i) => ({ name: `OTHER${i}.MOD`, size: 30 })), { capped: true });
  const r = await p.a.write(makeModule(100), 'HIDDEN');
  assert.equal(r.ok, true);
  assert.equal(p.ui.asked.length, 1);
  assert.match(p.ui.asked[0], new RegExp(`HIDDEN\\.MOD is on the card now \\(60 bytes, CRC ${ML.hex8(ML.crc32(old))}\\)`));
  const subs = p.unit.subs();
  assert.deepEqual(subs.slice(0, 4), [0x07, 0x00, 0x7F, 0x01], 'verify of the name, its end, then BEGIN');
  assert.equal(subs.filter(x => x === 0x07).length, 2, 'the fresh check and the final verify');
});

await test('probe 2: a stale list that shows nothing still asks when the file exists', async () => {
  const p = pageRig({}, [false]);
  p.unit.files.set('X.MOD', makeModule(40));
  p.a.entries = Object.assign([], { capped: false });                 // stale: the card was changed since
  const r = await p.a.write(makeModule(100), 'X');
  assert.equal(r.ok, false);
  assert.equal(p.ui.asked.length, 1);
  assert.ok(!p.unit.subs().includes(0x01), 'declined: no BEGIN');
  assert.equal(p.unit.files.get('X.MOD').length, 40);
  assert.match(p.last()[0], /Nothing written/);
});

await test('fresh check: not found writes without a question', async () => {
  const p = pageRig();
  p.a.entries = Object.assign([{ name: 'X.MOD', size: 40 }], { capped: false });   // stale: lists a deleted file
  const r = await p.a.write(makeModule(100), 'X');
  assert.equal(r.ok, true);
  assert.equal(p.ui.asked.length, 0);
});

await test('fresh check: any other result stops; no BEGIN, the error stays', async () => {
  for (const [o, re] of [[{ busy: true }, /Stop playback/], [{ jobFail: { job: 4, result: 15, fr: 2 } }, /helper files/],
                         [{ card: false }, /No microSD card/], [{ lose: new Set([0x07]) }, /outcome is unknown/]]) {
    const p = pageRig(o);
    const r = await p.a.write(makeModule(100), 'X');
    assert.equal(r.ok, false);
    assert.ok(!p.unit.subs().includes(0x01), `no BEGIN (${re})`);
    assert.match(p.last()[0], re);
    assert.equal(p.ui.asked.length, 0);
  }
  const q = pageRig();
  await q.a.write(new Uint8Array(100), 'X');                            // an invalid file: nothing is sent
  assert.deepEqual(q.unit.sent, []);
});

// ---------------------------------------------------------------- list order and pads (PICK_V2 picker)
const E = (name, size = 100) => ({ name, size });
const order = (es, capped) => ML.pickerOrder(es, capped).map(e => `${e.pad}:${e.name}`);

await test('list order: the picker order, not the directory order', () => {
  const dir = [E('19MOLE.MOD'), E('STARS.MOD'), E('03LIFE.MOD'), E('ABC.TMP'), E('01PAINT.MOD'), E('2X.MOD'),
               E('10STARS.MOD'), E('_U.MOD'), E('README.TXT'), E('A.MOD')];
  assert.deepEqual(order(dir), ['1:01PAINT.MOD', '3:03LIFE.MOD', '10:10STARS.MOD', '19:19MOLE.MOD',
    '2:2X.MOD', '4:A.MOD', '5:STARS.MOD', '6:_U.MOD', '0:ABC.TMP', '0:README.TXT']);
});

await test('list order: the ten release modules in any directory order', () => {
  const names = ML.PUBLISHED.map(f => ML.nameOf(f) + '.MOD');
  const want = names.map(n => `${+n.slice(0, 2)}:${n}`);
  for (let k = 0; k < 5; k++) {
    const shuffled = names.slice().sort(() => Math.sin(k * 7 + names.length) - 0.5 + Math.random() - 0.5);
    assert.deepEqual(order(shuffled.map(n => E(n))), want);
  }
});

await test('pads: 00 and 33..99 are unnumbered; a duplicate number takes the next free pad', () => {
  assert.deepEqual(order([E('00ZERO.MOD'), E('33HIGH.MOD'), E('32LAST.MOD'), E('05B.MOD'), E('05A.MOD'), E('B.MOD')]),
    ['5:05A.MOD', '1:05B.MOD', '32:32LAST.MOD', '2:00ZERO.MOD', '3:33HIGH.MOD', '4:B.MOD']);
  // listing: numbered (05A, 05B, 32LAST) first; 05B loses pad 5 to 05A and takes the lowest free pad, then the rest
});

await test('pads: byte order (digits before _ before letters; shorter first)', () => {
  // '.' (2E) < '9' (39) < 'B' (42) < '_' (5F)
  assert.deepEqual(order([E('AB.MOD'), E('A_.MOD'), E('A9.MOD'), E('A.MOD')]),
                   ['1:A.MOD', '2:A9.MOD', '3:AB.MOD', '4:A_.MOD']);
});

await test('pads: files the picker skips (size, name) have no pad and follow', () => {
  assert.deepEqual(order([E('B.MOD'), E('TINY.MOD', 25), E('BIG.MOD', 15361), E('A.MOD', 26), E('C.MOD', 15360),
                          E('X.OLD')]),
    ['1:A.MOD', '2:B.MOD', '3:C.MOD', '0:BIG.MOD', '0:TINY.MOD', '0:X.OLD']);
});

await test('pads: up to 32 files; the 33rd and later get no pad', () => {
  const es = Array.from({ length: 34 }, (_, i) => E(`M${String(i).padStart(2, '0')}.MOD`));
  const o = ML.pickerOrder(es.reverse());
  assert.deepEqual(o.slice(0, 32).map(e => e.pad), Array.from({ length: 32 }, (_, i) => i + 1));
  assert.deepEqual(o.slice(32).map(e => [e.name, e.pad]), [['M32.MOD', 0], ['M33.MOD', 0]]);
});

await test('pads: a capped list shows the order but no pads', () => {
  assert.deepEqual(order([E('B.MOD'), E('01A.MOD')], true), ['0:01A.MOD', '0:B.MOD']);
});

// The two layouts that the emulated firmware picker test checks (pads there are 0-based).
await test('pads: the firmware picker cases', () => {
  const names = ['PAINT.MOD', '02BBB.MOD', '33Y.MOD', '02AAA.MOD', '1Z.MOD', '00X.MOD', '05.MOD'];
  const want = { 1: '02AAA.MOD', 4: '05.MOD', 0: '02BBB.MOD', 2: '00X.MOD', 3: '1Z.MOD', 5: '33Y.MOD', 6: 'PAINT.MOD' };
  const got = Object.fromEntries(ML.pickerOrder(names.map(n => E(n))).map(e => [e.pad - 1, e.name]));
  assert.deepEqual(got, want);
  const many = [...Array.from({ length: 40 }, (_, i) => `M${String(i).padStart(2, '0')}.MOD`), '20LATE.MOD'];
  const o = ML.pickerOrder(many.reverse().map(n => E(n)));
  const pad = Object.fromEntries(o.filter(e => e.pad).map(e => [e.pad - 1, e.name]));
  assert.equal(Object.keys(pad).length, 32);
  assert.equal(pad[19], '20LATE.MOD'); assert.equal(pad[31], 'M30.MOD');
});

await test('page: List shows the files in the picker order', async () => {
  const p = pageRig();
  for (const n of ['STARS', '19MOLE', '01PAINT']) p.unit.files.set(`${n}.MOD`, makeModule(100));
  await p.a.list();
  assert.deepEqual(p.a.entries.map(e => [e.pad, e.name]), [[1, '01PAINT.MOD'], [19, '19MOLE.MOD'], [2, 'STARS.MOD']]);
  assert.equal(p.a.entries.capped, false);
});

console.log(results.join('\n'));
console.log(`${passed}/${results.length} passed`);
