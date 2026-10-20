// Node tests for the JavaScript core of web/index.html: patcher, hash refusals, and a fake-MIDI flash simulation.
//
//   node tests/test_web_installer.mjs [--stock firmware/circuit-rhythm-5706.syx --release release/x.json --out out.syx]
//
// The core is the code between CRCORE BEGIN/END in web/index.html, run unchanged. Without --stock, the firmware
// cases are skipped; the protocol cases use a synthetic image and need no Novation bytes.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const core = html.slice(html.indexOf('/* CRCORE BEGIN */'), html.indexOf('/* CRCORE END */'));
const CR = new Function(core + '\nreturn CR;')();

const args = Object.fromEntries(process.argv.slice(2).reduce((a, x, i, all) =>
  (x.startsWith('--') ? [...a, [x.slice(2), all[i + 1]]] : a), []));
let passed = 0;
const results = [];
async function test(name, fn) {
  try { await fn(); passed++; results.push(`ok   ${name}`); }
  catch (e) { results.push(`FAIL ${name}: ${e.stack}`); process.exitCode = 1; }
}
const T = { initMs: 20, confirmMs: 300, writeMs: 200, finishMs: 300, drainMs: 10, probeMs: 200, stopSettleMs: 10,
            resetMs: 400, reconnectMs: 1500, settleMs: 20 };
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ------------------------------------------------------------------ fake Web MIDI
class FakePort {
  constructor(id, name, type, access) { Object.assign(this, { id, name, type, access, state: 'connected',
    connection: 'closed', onmidimessage: null, busy: false, unit: null }); }
  async open() {
    if (this.busy) { const e = new Error('Port is not available'); e.name = 'InvalidAccessError'; throw e; }
    if (this.state !== 'connected') { const e = new Error('disconnected'); e.name = 'InvalidStateError'; throw e; }
    this.connection = 'open';
  }
  close() { this.connection = 'closed'; }
  send(d) {
    if (this.state !== 'connected' || this.connection !== 'open') throw new Error('port not open');
    this.unit.receive(Uint8Array.from(d));
  }
}
class FakeAccess {
  constructor(name = 'Circuit Rhythm') {
    this.inputs = new Map(); this.outputs = new Map(); this.listeners = [];
    this.input = new FakePort('in-1', name, 'input', this); this.output = new FakePort('out-1', name, 'output', this);
    this.inputs.set('in-1', this.input); this.outputs.set('out-1', this.output);
    const other = new FakePort('in-9', 'Some Synth', 'input', this); this.inputs.set('in-9', other);
  }
  addEventListener(t, f) { if (t === 'statechange') this.listeners.push(f); }
  setState(s) {
    for (const p of [this.input, this.output]) {
      p.state = s; if (s === 'disconnected') p.connection = 'closed';
      for (const f of this.listeners) f({ port: p });
    }
  }
}
const ack = off => Uint8Array.of(0xF0, 0, 0x20, 0x29, 0, 0x72, (off >> 5) & 0x7F, (off >> 12) & 0x7F, (off >> 19) & 0x7F, 0xF7);

class FakeUnit {
  // Follows the application's update handler like tests/test_flash_mock.py, plus version probe, modules, Play and USB.
  constructor(access, o = {}) {
    this.o = { boot: 4799, app: 5706, module: false, play: 'press', crcBad: false, finishSilent: false,
               dropAckAt: null, unplugAt: null, installs: true, stayOnUsb: false, ...o };
    this.access = access; access.output.unit = this;
    this.app = this.o.app; this.module = this.o.module;
    this.sent = []; this.stage = null; this.writes = 0; this.size = 0; this.crc = 0; this.offset = 0;
    this.headerSeen = false;
  }
  reply(m) {
    setImmediate(() => { const f = this.access.input.onmidimessage; if (f && this.access.input.state === 'connected') f({ data: m }); });
  }
  receive(m) {
    this.sent.push(m);
    const cmd = m[5];
    if (!CR.allowed(m)) throw new Error(`unit got a message outside the allowlist: ${CR.toHex(m, ' ')}`);
    if (cmd === 0x70) {
      const b = n => [(n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F];
      this.reply(Uint8Array.of(0xF0, 0, 0x20, 0x29, 0, 0x70, 0, 3, ...b(this.o.boot), 1, 1, ...b(this.app), 0x40, 2, 0xF7));
    } else if (cmd === 0x60) {
      this.module = false;
    } else if (cmd === 0x71) {
      this.reply(Uint8Array.of(0xF0, 0, 0x20, 0x29, 1, 0x63, 4, 2, 0, 0, 0xF7));   // unrelated chatter
    } else if (cmd === 0x7C) {
      this.headerSeen = true;
      const hb = m.subarray(6, -1);
      const nib = b => b.reduce((v, n) => v * 16 + n, 0);
      this.size = nib(hb.subarray(7, 15)); this.crc = nib(hb.subarray(15, 23));
      this.stage = new Uint8Array(this.size + 32).fill(0xFF);
      this.offset = 0x20;
      if (this.module) return;                       // a running module hides the Play prompt
      if (this.o.play === 'never') return;
      setTimeout(() => this.reply(ack(0x20)), 5);    // master presses Play
    } else if (cmd === 0x72) {
      this.writes++;
      this.stage.set(CR.unpack7(m.subarray(6, -1)), this.offset);
      this.offset += 0x20;
      if (this.o.unplugAt === this.writes) { setImmediate(() => this.access.setState('disconnected')); return; }
      if (this.o.dropAckAt !== this.writes) this.reply(ack(this.offset >= this.size ? 0 : this.offset));
    } else if (cmd === 0x73) {
      this.stage.set(CR.unpack7(m.subarray(6, -1)), 0);
      if (this.o.finishSilent) return;
      const img = this.stage.subarray(0, this.size);
      const ok = CR.crc32mpeg2(img) === this.crc && !this.o.crcBad;
      this.reply(Uint8Array.of(0xF0, 0, 0x20, 0x29, 0, 0x73, ok ? 0x7F : 0x7E, 0x7F, 0x7F, 0xF7));
      if (!ok || this.o.stayOnUsb) return;
      const label = CR.labelOf(img);
      setTimeout(() => {
        this.access.setState('disconnected');        // reset; bootloader installs if the build-info differs
        setTimeout(() => { if (label !== this.app && this.o.installs) this.app = label; this.access.setState('connected'); }, 100);
      }, 20);
    }
  }
  cmds() { return this.sent.map(m => m[5]); }
}

async function session(unitOpts = {}, accessName) {
  const access = new FakeAccess(accessName);
  const unit = new FakeUnit(access, unitOpts);
  const s = new CR.Session(access);
  const pairs = s.pairs();
  assert.equal(pairs.length, 1);
  await s.open(pairs[0]);
  return { access, unit, s };
}

// Synthetic application image: no Novation bytes.
function synthetic(label, nbytes = 4096) {
  const img = new Uint8Array(nbytes);
  for (let i = 0; i < nbytes; i++) img[i] = (i * 7 + 3) & 0xFF;
  img[0x1CA] = label & 0xFF; img[0x1CB] = label >> 8; img[0x1CC] = 1; img[0x1CD] = 0x63;
  const init = Uint8Array.of(1, 0x63, 0, 0, 0, 7, 3, 9), hp = Uint8Array.of(0, 0, 0, 0, 7, 3, 9);
  const syx = CR.build(img, init, hp);
  return { syx, image: img, label };
}

async function expectFlashError(p, kind) {
  try { await p; } catch (e) { assert.ok(e instanceof CR.FlashError, e.stack); assert.equal(e.kind, kind, e.message); return e; }
  assert.fail(`expected FlashError ${kind}`);
}

// ------------------------------------------------------------------ primitives
await test('sha256 and crc32/mpeg2 known answers', () => {
  const enc = s => new TextEncoder().encode(s);
  assert.equal(CR.sha256(enc('')), 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
  assert.equal(CR.sha256(enc('abc')), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  assert.equal(CR.sha256(new Uint8Array(1000).fill(0x61)),
    '41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3');
  assert.equal(CR.crc32mpeg2(enc('123456789')), 0x0376E6E7);
});
await test('pack7/unpack7 round trip and synthetic build/parse', () => {
  const b = Uint8Array.from({ length: 32 }, (_, i) => (i * 37) & 0xFF);
  const p = CR.pack7(b); assert.equal(p.length, 37); assert.deepEqual(CR.unpack7(p), b);
  const s = synthetic(6100);
  const back = CR.parse(s.syx); assert.ok(back.crcOk); assert.deepEqual(back.image, s.image);
});
await test('default timeouts: Play 120 s, WRITE 5 s, FINISH 30 s, INIT 2 s', () => {
  assert.equal(CR.DEFAULTS.confirmMs, 120000); assert.equal(CR.DEFAULTS.writeMs, 5000);
  assert.equal(CR.DEFAULTS.finishMs, 30000); assert.equal(CR.DEFAULTS.initMs, 2000);
});
await test('allowlist: never 0x77, only the exact 0x70 query and 0x60 00 stop', () => {
  const m = c => Uint8Array.of(0xF0, 0, 0x20, 0x29, 0, c, 0, 0xF7);
  assert.equal(CR.allowed(m(0x77)), false);
  assert.equal(CR.allowed(CR.VERSION_QUERY), true);
  assert.equal(CR.allowed(CR.STOP_MODULE), true);
  assert.equal(CR.allowed(Uint8Array.of(0xF0, 0, 0x20, 0x29, 0, 0x60, 0x04, 0, 0, 0, 0xF7)), false);
  assert.equal(CR.allowed(m(0x70)), false);
  for (const c of [0x71, 0x7C, 0x72, 0x73]) assert.equal(CR.allowed(m(c)), true);
  let sent = 0;
  const link = new CR.Link(() => sent++);
  assert.throws(() => link.send(m(0x77)), e => e.kind === 'refused');
  assert.equal(sent, 0);
});
await test('Link reassembles split SysEx and skips real-time bytes', async () => {
  const link = new CR.Link(() => {});
  link.feed(Uint8Array.of(0xF8, 0xF0, 0, 0x20)); link.feed(Uint8Array.of(0xFE, 0x29, 0, 0x72, 1, 0, 0, 0xF7));
  const r = await link.next(10);
  assert.equal(CR.ackOffset(r), 0x20);
  assert.equal(await link.next(5), null);
});

// ------------------------------------------------------------------ flash simulation (synthetic image)
await test('install from stock: probe, INIT, HEADER, Play, blocks, 73 7F, reconnect, label', async () => {
  const { unit, s } = await session();
  const pay = synthetic(6100);
  const ev = [];
  const r = await CR.install(s, pay, { ...T, onEvent: (k) => ev.push(k) });
  assert.equal(r.label, 6100);
  assert.deepEqual(unit.stage.subarray(0, unit.size), pay.image);
  assert.ok(!unit.cmds().includes(0x60), 'no stop-module on stock firmware');
  assert.ok(!unit.cmds().includes(0x77));
  assert.deepEqual(unit.cmds().slice(0, 3), [0x70, 0x71, 0x7C]);
  assert.equal(unit.cmds().at(-1), 0x70);
  for (const k of ['prompt', 'ready', 'progress', 'finish', 'installing']) assert.ok(ev.includes(k), k);
});
await test('running module: stop-module sent before INIT, then the prompt appears', async () => {
  const { unit, s } = await session({ app: 6013, module: true });
  const r = await CR.install(s, synthetic(6100), T);
  assert.equal(r.label, 6100);
  const c = unit.cmds();
  assert.ok(c.indexOf(0x60) > c.indexOf(0x70) && c.indexOf(0x60) < c.indexOf(0x71));
});
await test('running module not stopped hides the prompt: HEADER times out, nothing written', async () => {
  const { unit, s } = await session({ app: 6013, module: true });
  const e = await expectFlashError(CR.flashStream(s.link, synthetic(6100).syx, T), 'noplay');
  assert.ok(e.untouched); assert.equal(unit.writes, 0); assert.ok(!unit.cmds().includes(0x73));
});
await test('no Play pressed: HEADER ack timeout aborts before any WRITE', async () => {
  const { unit, s } = await session({ play: 'never' });
  const e = await expectFlashError(CR.install(s, synthetic(6100), T), 'noplay');
  assert.ok(e.untouched && /untouched/.test(e.message));
  assert.equal(unit.writes, 0);
});
await test('CRC mismatch: 73 7E reported, nothing installed', async () => {
  const { unit, s } = await session({ crcBad: true });
  const e = await expectFlashError(CR.install(s, synthetic(6100), T), 'mismatch');
  assert.ok(e.untouched); assert.equal(unit.app, 5706);
});
await test('no reply after FINISH: unknown outcome', async () => {
  const { s } = await session({ finishSilent: true });
  const e = await expectFlashError(CR.install(s, synthetic(6100), T), 'unknown');
  assert.equal(e.untouched, false);
});
await test('missing WRITE ack: timeout aborts before FINISH', async () => {
  const { unit, s } = await session({ dropAckAt: 7 });
  const e = await expectFlashError(CR.install(s, synthetic(6100), T), 'ack');
  assert.ok(/WRITE 7/.test(e.message) && e.untouched);
  assert.ok(!unit.cmds().includes(0x73));
});
await test('unplug during transfer: aborted, FINISH never sent', async () => {
  const { unit, s } = await session({ unplugAt: 9 });
  const e = await expectFlashError(CR.install(s, synthetic(6100), T), 'disconnected');
  assert.ok(e.untouched); assert.ok(!unit.cmds().includes(0x73));
});
await test('cancel while waiting for Play: aborted, nothing written', async () => {
  const { unit, s } = await session({ play: 'never' });
  const p = CR.install(s, synthetic(6100), { ...T, confirmMs: 5000,
    onEvent: k => { if (k === 'prompt') setTimeout(() => s.link.abort(new CR.FlashError('cancelled', 'cancelled', true)), 20); } });
  const e = await expectFlashError(p, 'cancelled');
  assert.ok(e.untouched); assert.equal(unit.writes, 0);
});
await test('busy port: open refused with a clear message', async () => {
  const access = new FakeAccess(); new FakeUnit(access);
  access.input.busy = true;
  const s = new CR.Session(access);
  const e = await expectFlashError(s.open(s.pairs()[0]), 'busy');
  assert.ok(/Components/.test(e.message));
});
await test('port names: Linux style name pairs, unrelated ports ignored', async () => {
  const { s } = await session({}, 'Circuit Rhythm MIDI 1');
  assert.equal(s.pair.name, 'Circuit Rhythm MIDI 1');
});
await test('wrong bootloader or no answer: refused before INIT', async () => {
  let { unit, s } = await session({ boot: 4486 });
  await expectFlashError(CR.install(s, synthetic(6100), T), 'refused');
  assert.ok(!unit.cmds().includes(0x71));
  ({ unit, s } = await session({ app: 3000 }));
  await expectFlashError(CR.install(s, synthetic(6100), T), 'refused');
});
await test('same label already installed: refused (bootloader would skip it)', async () => {
  const { unit, s } = await session({ app: 6100 });
  await expectFlashError(CR.install(s, synthetic(6100), T), 'refused');
  assert.ok(!unit.cmds().includes(0x71));
});
await test('unit never leaves USB after 73 7F: reported, label checked on the same port', async () => {
  const { s } = await session({ stayOnUsb: true });
  const e = await expectFlashError(CR.install(s, synthetic(6100), T), 'unknown');
  assert.ok(/never left USB/.test(e.message), e.message);
});
await test('malformed streams refused before anything is sent (0x77 inserted, extra HEADER)', async () => {
  const { unit, s } = await session();
  const msgs = CR.split(synthetic(6100).syx);
  const bad77 = new Uint8Array([...msgs.slice(0, 5).flatMap(m => [...m]), 0xF0, 0, 0x20, 0x29, 0, 0x77, 0, 0xF7,
                                ...msgs.slice(5).flatMap(m => [...m])]);
  await expectFlashError(CR.flashStream(s.link, bad77, T), 'refused');
  const h2 = Uint8Array.from(msgs[1]); h2[6] = 2;
  const extra = new Uint8Array([...msgs[0], ...msgs[1], ...h2, ...msgs.slice(2).flatMap(m => [...m])]);
  await expectFlashError(CR.flashStream(s.link, extra, T), 'refused');
  await expectFlashError(CR.flashStream(s.link, synthetic(6100).syx, { ...T, expectSha256: '00'.repeat(32) }), 'refused');
  assert.equal(unit.sent.length, 0);
});

// ------------------------------------------------------------------ firmware cases
// A build's pin and patches, for a release in either shape.
const B = (r, name) => CR.selectBuild(r, name);
if (args.stock && args.release) {
  const stock = new Uint8Array(fs.readFileSync(args.stock));
  const relText = fs.readFileSync(args.release, 'utf8');
  const rel = () => JSON.parse(relText);
  let out;
  await test('JS patch output equals the pinned output hash (default build)', () => {
    out = CR.applyRelease(rel(), stock);
    assert.equal(CR.sha256(out.syx), B(rel()).output.syx_sha256);
    if (args.out) fs.writeFileSync(args.out, out.syx);
  });
  await test('every build in the release reproduces its own pin', () => {
    for (const b of CR.builds(rel())) {
      const o = CR.applyRelease(rel(), stock, b.name);
      assert.equal(CR.sha256(o.syx), b.output.syx_sha256, b.name);
      assert.equal(o.label, b.output.label);
      if (args.outdir) fs.writeFileSync(path.join(args.outdir, `${b.name}.syx`), o.syx);
    }
    assert.throws(() => CR.applyRelease(rel(), stock, 'no-such-build'), CR.Refused);
  });
  await test('embedded release equals the release file', () => {
    const i = html.indexOf('id="release">') + 'id="release">'.length;
    const emb = JSON.parse(html.slice(i, html.indexOf('</script>', i)));
    const key = r => CR.builds(r).map(b => `${b.name}:${b.output.label}`).join(',');
    if (key(emb) === key(rel())) assert.deepEqual(emb, rel());   // every release is named Arrhythmia; key on builds
  });
  await test('legacy single-build shape reads as one build; default build is "firmware" when present', () => {
    const b = B(rel());
    const legacy = { format: 'crpatch/1', name: 'legacy', description: b.description, input: rel().input,
                     output: b.output, patches: b.patches };
    assert.equal(CR.builds(legacy).length, 1);
    assert.equal(CR.sha256(CR.applyRelease(legacy, stock).syx), b.output.syx_sha256);
    const names = CR.builds(rel()).map(x => x.name);
    assert.equal(b.name, names.includes('firmware') ? 'firmware' : names[0]);
  });
  const refuse = (what, mutate, re) => test(`refuses: ${what}`, () => {
    const r = rel(); let s = Uint8Array.from(stock);
    const x = mutate(r, s); if (x) s = x;
    assert.throws(() => CR.applyRelease(r, s), e => e instanceof CR.Refused && re.test(e.message));
  });
  await refuse('a changed stock file', (r, s) => { s[5000] ^= 1; }, /input \.syx SHA-256/);
  await refuse('a truncated stock file', (r, s) => s.slice(0, s.length - 44), /input \.syx SHA-256/);
  await refuse('a changed published byte (output pin)', r => {
    const pc = B(r).patches[0].pieces.find(p => 'hex' in p);
    pc.hex = (parseInt(pc.hex.slice(0, 2), 16) ^ 1).toString(16).padStart(2, '0') + pc.hex.slice(2);
  }, /output syx_sha256/);
  await refuse('a wrong stock-span hash', r => {
    const p = B(r).patches.find(p => p.expect_sha256); p.expect_sha256 = '0'.repeat(64);
  }, /expected hash/);
  await refuse('a wrong copy-source hash', r => {
    const pc = B(r).patches.flatMap(p => p.pieces).find(p => 'copy' in p); pc.sha256 = '0'.repeat(64);
  }, /copy source/);
  await refuse('a wrong output pin', r => { B(r).output.syx_sha256 = '1'.repeat(64); }, /output syx_sha256/);
  await refuse('a vector-table patch', r => { B(r).patches[0].addr = '0x08010010'; }, /vector table|expected hash|straddles/);
  await refuse('overlapping patches', r => { const ps = B(r).patches; ps.push({ ...ps[1] }); }, /overlaps/);
  await refuse('duplicate build names', r => { r.builds = [...CR.builds(r), { ...B(r) }]; }, /unique/);
  await test('back to stock: the pinned file passes unchanged, a changed one is refused', () => {
    const p = CR.checkStock(rel(), stock);
    assert.equal(p.syx, stock); assert.equal(p.label, 5706);
    const bad = Uint8Array.from(stock); bad[100] ^= 1;
    assert.throws(() => CR.checkStock(rel(), bad), CR.Refused);
  });
  await test('full simulated install of the release from stock, then back to stock', async () => {
    const { access, unit, s } = await session({ app: 5706 });
    const r = await CR.install(s, out, T);
    assert.equal(r.label, B(rel()).output.label);
    assert.equal(CR.sha256(unit.stage.subarray(0, unit.size)), B(rel()).output.image_sha256);
    unit.module = true;                               // the new firmware may be running a module
    const back = await CR.install(s, CR.checkStock(rel(), stock), T);
    assert.equal(back.label, 5706);
    assert.equal(CR.sha256(unit.stage.subarray(0, unit.size)), rel().input.image_sha256);
    assert.ok(unit.cmds().includes(0x60));
    assert.ok(unit.sent.every(CR.allowed));
    void access;
  });
  await test('two builds: same-label refusal applies per image; the other image installs over the first', async () => {
    const bl = CR.builds(rel());
    if (bl.length < 2) return;
    const [a, b] = bl.map(x => CR.applyRelease(rel(), stock, x.name));
    const { unit, s } = await session({ app: a.label });
    await expectFlashError(CR.install(s, a, T), 'refused');
    assert.ok(!unit.cmds().includes(0x71));
    const r = await CR.install(s, b, T);
    assert.equal(r.label, b.label);
    assert.equal(CR.sha256(unit.stage.subarray(0, unit.size)), bl[1].output.image_sha256);
    assert.ok(unit.sent.every(CR.allowed));
  });
  await test('install over a different build label: each image installs over a unit running another label (module running)', async () => {
    for (const x of CR.builds(rel())) {
      const p = CR.applyRelease(rel(), stock, x.name);
      for (const prev of [7300, 7301]) {
        if (prev === p.label) continue;
        const { unit, s } = await session({ app: prev, module: true });
        const r = await CR.install(s, p, T);
        assert.equal(r.label, p.label);
        assert.equal(CR.sha256(unit.stage.subarray(0, unit.size)), x.output.image_sha256);
        assert.ok(unit.cmds().includes(0x60));
        assert.ok(unit.sent.every(CR.allowed));
      }
    }
  });
} else results.push('skip firmware cases (no --stock/--release)');

console.log(results.join('\n'));
console.log(`${passed} passed${process.exitCode ? ', FAILURES' : ''}`);
