// Node tests for the JavaScript core of web/export.html (the MIDI export page).
//
//   node tests/test_web_export.mjs
//   node tests/test_web_export.mjs --jobs JOBS.json     (tests/test_web_export.py: export each job to a ZIP file)
//   node tests/test_web_export.mjs --replay JOBS.json   (tests/test_web_export.py: read from recorded transcripts)
//
// The core is the code between EXCORE BEGIN/END in web/export.html, run unchanged. A job is
// {"ncs": path, "source": file name, "pcm": folder or null, "opts": {...}, "out": path}. A replay job is
// {"transcript": path, "what": "list" | "read", "slot", "index", "opts", "out": ZIP path, "result": JSON path}: the
// unit is the transcript (tools/unitread.py --transcript); the result holds the outcome and the frames sent and
// received, for comparison with the Python client. "inject": {"recv_fail": n} makes the n-th receive throw,
// {"send_fail": command byte} makes that send throw after the frame went out (tests/test_unitread.py Inject).

class Inject {
  constructor(link, o) { this.link = link; this.recvFail = o.recv_fail; this.sendFail = o.send_fail; this.n = 0; }
  now() { return this.link.now(); }
  idle(ms) { return this.link.idle(ms); }
  async send(m) {
    await this.link.send(m);
    if (this.sendFail !== undefined && m[6] === 0x03 && m[7] === this.sendFail) throw new Error('adapter: write failed');
  }
  async recv(prefix, ms) {
    if (++this.n === this.recvFail) throw new Error('adapter: read failed');
    return this.link.recv(prefix, ms);
  }
}
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const html = fs.readFileSync(path.join(root, 'web/export.html'), 'utf8');
const core = html.slice(html.indexOf('/* EXCORE BEGIN */'), html.indexOf('/* EXCORE END */'));
const EX = new Function(core + '\nreturn EX;')();

function runJobs(file) {
  for (const j of JSON.parse(fs.readFileSync(file, 'utf8'))) {
    const img = new Uint8Array(fs.readFileSync(j.ncs));
    const names = j.pcm ? fs.readdirSync(j.pcm) : [];
    const p = EX.plan(img, j.source, names, j.opts);
    const samples = new Map(p.needed.map(x => [x.sample, new Uint8Array(fs.readFileSync(path.join(j.pcm, x.sample)))]));
    fs.writeFileSync(j.out, EX.zip(p, samples));
  }
}

async function runReplays(file) {
  const UR = EX.UR;
  for (const j of JSON.parse(fs.readFileSync(file, 'utf8'))) {
    const t = JSON.parse(fs.readFileSync(j.transcript, 'utf8'));
    const replay = new UR.Replay(t.frames), tr = new UR.Transcript();
    const link = j.inject ? new Inject(replay, j.inject) : replay, u = new UR.Unit(link, tr);
    let outcome = 'ok', listing = null;
    try {
      if (j.what === 'list') {
        listing = {};
        for (const [slot, p] of await UR.listProjects(u))
          listing[slot] = { pack: p.pack, sessions: Object.fromEntries(p.sessions) };
      } else {
        const r = await UR.readProject(u, j.slot, j.index, j.opts);
        fs.writeFileSync(j.out, EX.zip(EX.plan(r.image, r.name, r.pcm, j.opts), r.samples));
      }
    } catch (e) {
      outcome = e instanceof UR.Uncertain ? 'uncertain' : e instanceof UR.Refused ? 'refused'
        : e instanceof EX.ProjectError ? 'project' : 'error: ' + e.message;
    }
    fs.writeFileSync(j.result, JSON.stringify({ outcome, frames: tr.frames, consumed: replay.k, listing }));
  }
}

// A link that records what is sent and answers nothing (the tests below stop before any reply is needed).
class Mute {
  constructor(heard, version) { this.heard = heard; this.version = version; this.sent = []; }
  now() { return 0; }
  async idle() { return this.heard; }
  async send(m) { this.sent.push([...m]); }
  async recv(prefix) { return this.version && prefix.length === 6 ? this.version : null; }
}

const tests = {
  async unitAllowList() {
    const UR = EX.UR, P = [0xF0, 0x00, 0x20, 0x29, 0x01, 0x63, 0x03], f = (...b) => Uint8Array.from([...P, ...b, 0xF7]);
    const z8 = new Array(8).fill(0);
    for (const ok of [f(0x40), f(0x41), f(0x0B, 1), f(0x0B, 2), f(0x0B, 3, 31), f(0x0B, 5, 0), f(0x0A, 5, 3, 127),
                      f(0x01, ...z8, 3, 0, 24, 2, ...z8), Uint8Array.from([0xF0, 0, 0x20, 0x29, 0, 0x70, 0xF7]),
                      Uint8Array.from([0xF0, 0, 0x20, 0x29, 0, 0x60, 0x0D, 0xF7])])
      assert.ok(UR.frameAllowed(ok), [...ok]);
    for (const bad of [f(0x01, ...z8, 5, 3, 0, 1, ...z8), f(0x02, ...z8, 5, 3, 0, 0), f(0x03, ...z8, 5, 3, 0, ...z8),
                       f(0x07, 5, 3, 0, 0x30), f(0x08, 2, 3), f(0x08, 5, 3, 0), f(0x0D, 5, 3, 0), f(0x09, 1, 1),
                       f(0x0B, 4, 3), f(0x0A, 7, 3, 0), f(0x01, ...z8, 8, 0, 0, 2, ...z8), f(0x01, ...z8, 3, 32, 0, 2, ...z8),
                       f(0x40, 0), Uint8Array.from([0xF0, 0, 0x20, 0x29, 0, 0x77, 0xF7]),
                       Uint8Array.from([0xF0, 0, 0x20, 0x29, 0, 0x60, 0x08, 0xF7])])
      assert.ok(!UR.frameAllowed(bad), [...bad]);
    assert.equal(UR.wireBytes(0x2000), 29 + 9383 + 28);
    assert.equal(UR.wireBytes(169092), 193740);                  // measured on the unit
    assert.deepEqual(UR.unpack7([0x01, 0x00, 0x7F, 0x05]), [0x80, 0x7F, 0x05]);
    const u = new UR.Unit(new Mute([]));
    await assert.rejects(u.send(f(0x08, 2, 3)), UR.Refused);
    assert.deepEqual(u.link.sent, []);
  },
  async unitRefusesWhilePlaying() {
    const UR = EX.UR, v = Uint8Array.from([0xF0, 0, 0x20, 0x29, 0, 0x70, 0, 3, 0, 0x25, 0x3F, 1, 1, 0, 0x39, 0x2B, 0x40, 2, 0xF7]);
    const link = new Mute([0x90], v);
    await assert.rejects(UR.listProjects(new UR.Unit(link)), UR.Refused);
    assert.equal(link.sent.length, 1);                           // the version query only
    const other = new Mute([], Uint8Array.from([...v.subarray(0, 14), 0x39, 0x07, 0x40, 2, 0xF7]));   // build 7303
    await assert.rejects(UR.listProjects(new UR.Unit(other)), /7303/);
    assert.equal(other.sent.length, 1);
  },
  crc32() {
    assert.equal(EX.crc32(new TextEncoder().encode('123456789')), 0xCBF43926);
    assert.equal(EX.crc32(new Uint8Array(0)), 0);
  },
  vlq() {
    const cases = [[0, [0]], [0x7F, [0x7F]], [0x80, [0x81, 0]], [0x3FFF, [0xFF, 0x7F]], [0x4000, [0x81, 0x80, 0]],
                   [0x0FFFFFFF, [0xFF, 0xFF, 0xFF, 0x7F]]];
    for (const [n, want] of cases) assert.deepEqual(EX.vlq(n), want);
  },
  microstepTable() {
    assert.deepEqual(EX.MS_PULSE, [[0, 0, 0, 1, 1, 1], [0, 0, 1, 1, 2, 2], [0, 0, 1, 2, 2, 3], [0, 1, 2, 3, 4, 5],
                                   [0, 2, 3, 4, 6, 7], [0, 2, 4, 6, 8, 10], [0, 2, 5, 8, 10, 13], [0, 4, 8, 12, 16, 20]]);
  },
  zipStructure() {
    const z = EX.zipStore([['n', new Uint8Array([0x78])]]);
    assert.deepEqual([...z.subarray(0, 14)], [0x50, 0x4B, 3, 4, 20, 0, 0, 8, 0, 0, 0, 0, 0x21, 0]);
    const dv = new DataView(z.buffer, z.byteOffset);
    assert.equal(dv.getUint32(14, true), EX.crc32(new Uint8Array([0x78])));
    assert.equal(dv.getUint32(z.length - 22, true), 0x06054B50);
    assert.equal(dv.getUint16(z.length - 12, true), 1);
  },
  pcmNames() {
    const m = EX.pcmNames(['10_b.wav', '02_a.WAV', '02_z.wav', 'x.wav', '128_no.wav', 'notes.txt', '007_c.wav']);
    assert.deepEqual([...m.entries()].sort((a, b) => a[0] - b[0]), [[2, '02_a.WAV'], [7, '007_c.wav'], [10, '10_b.wav']]);
    assert.equal(EX.stem('05_Kick A.WAV'), 'Kick A');
  },
  findImages() {
    const img = new Uint8Array(EX.SIZE);
    img.set([0x52, 0x44, 0x45, 0x4D]);
    new DataView(img.buffer).setUint32(4, EX.SIZE, true);
    const two = new Uint8Array(3 + 2 * EX.SIZE + 5);
    two.set(img, 3);
    two.set(img, 3 + EX.SIZE);
    assert.deepEqual(EX.findImages(two), [3, 3 + EX.SIZE]);
    assert.deepEqual(EX.findImages(img), [0]);
    assert.deepEqual(EX.findImages(img.subarray(0, EX.SIZE - 1)), []);
  },
  rejects() {
    assert.throws(() => EX.plan(new Uint8Array(100)), EX.ProjectError);
    const img = new Uint8Array(EX.SIZE);
    img.set([0x52, 0x55, 0x53, 0x45]);
    new DataView(img.buffer).setUint32(4, EX.SIZE, true);
    new DataView(img.buffer).setUint32(8, 2, true);
    assert.throws(() => EX.plan(img), /version/);
  },
};

const jobs = process.argv.indexOf('--jobs'), replays = process.argv.indexOf('--replay');
if (jobs >= 0) runJobs(process.argv[jobs + 1]);
else if (replays >= 0) await runReplays(process.argv[replays + 1]);
else for (const [name, fn] of Object.entries(tests)) { await fn(); console.log(`ok ${name}`); }
