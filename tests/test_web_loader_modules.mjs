// Offline module plans: frozen identities, full preflight, preview recheck, and stop-on-first-error.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
const html=fs.readFileSync(new URL('../web/loader.html',import.meta.url),'utf8');
const core=html.slice(html.indexOf('/* MLCORE BEGIN */'),html.indexOf('/* MLCORE END */'));
const ML=new Function(core+'\nreturn ML;')();
const manifest=JSON.parse(fs.readFileSync(new URL('../web/module-assets.json',import.meta.url),'utf8'));
assert.deepEqual(ML.MODULE_ASSETS,manifest);
assert.deepEqual(ML.PUBLISHED,['01life.mod','02rain.mod','03balls.mod','04stars.mod','05lights.mod','09paint.mod','10euclid.mod','11pulse.mod','17snake.mod','18mole.mod']);
assert.match(html,/Which module each pad starts, and its colour\./);
assert.equal((html.match(/>Write to card</g)||[]).length,1);
assert.ok(html.indexOf('id="write"')>html.indexOf('id="module-grid"'));
assert.ok(html.indexOf('id="write"')<html.indexOf('id="pad-panel"'));
assert.doesNotMatch(html,/id="module-colours"|Empty pads start nothing\./);
// Current ENGINE_PACK mod_name() normalizes names to lowercase before the firmware's FNV colour rule.
const colourSource=html.slice(html.indexOf('function stagedColours()'),html.indexOf('function showModules()'));
const colours=new Function('assignments',colourSource+'\nreturn stagedColours();')(new Map(manifest.map(a=>[+a.file.slice(0,2),{module:ML.nameOf(a.file).slice(2)}])));
assert.deepEqual([...colours.values()],['#ffe000','#80ff00','#ff6000','#00ff00','#00ff60','#0080ff','#00ffff','#0000ff','#ff0060','#8000ff']);
// Synthetic valid modules let identity tests run without NAS files or a device.
function moduleBlob(seed) {
 const b=new Uint8Array(32),d=new DataView(b.buffer);
 d.setUint32(0,0x314D5243,true);d.setUint32(4,b.length,true);d.setUint32(16,25,true);
 b[28]=seed;d.setUint32(8,ML.crc32(b),true);ML.checkModule(b);return b;
}
const blobs=new Map(manifest.map((a,i)=>[a.file,moduleBlob(i+1)]));
const assets=manifest.map(a=>({...a,size:blobs.get(a.file).length,crc:ML.crc32(blobs.get(a.file))}));
const desired=assets.map(a=>({name:ML.nameOf(a.file),blob:blobs.get(a.file)}));
const legacy=ML.pickerOrder(assets.map(a=>({name:a.legacy.toUpperCase(),size:a.size})));
const identities=new Map(assets.map(a=>[a.legacy,{size:a.size,crc:a.crc}]));
const plan=(files=legacy,ids=identities,want=desired,reset=true)=>ML.modulePlan(files,want,ids,reset,assets);
const jobs=plan();
assert.equal(jobs.filter(j=>j.op==='delete').length,9);
assert.equal(jobs.filter(j=>j.op==='write').length,9);
assert.ok(jobs.filter(j=>j.op==='delete').every(j=>assets.some(a=>a.legacy===j.name+'.mod')));
assert.ok(!jobs.some(j=>j.name==='17snake'),'matching snake remains unchanged');
assert.ok(jobs.findIndex(j=>j.name==='18lights')<jobs.findIndex(j=>j.name==='18mole'));
assert.ok(jobs.findIndex(j=>j.name==='19mole')<jobs.findIndex(j=>j.name==='18mole'));
assert.deepEqual(plan([],new Map()).map(j=>j.op),new Array(10).fill('write'));
const current=ML.pickerOrder(assets.map(a=>({name:a.file,size:a.size})));
const currentIds=new Map(assets.map(a=>[a.file,{size:a.size,crc:a.crc}]));
assert.deepEqual(plan(current,currentIds),[],'reset already defaults is a no-op');
assert.deepEqual(plan(current,currentIds,desired.slice(1),false),[{op:'delete',name:'01life'}],'empty pad removes only an exact known default');
const collision=ML.pickerOrder([...legacy,{name:'01custom.mod',size:32}]);
assert.throws(()=>plan(collision),/Conflict on pad 1/);
const preserved=ML.pickerOrder([...legacy,{name:'06custom.mod',size:32}]);
assert.deepEqual(plan(preserved),jobs,'unknown unused-pad files remain untouched');
for(const name of ['01paint.mod','18lights.mod']) {
 for(const bad of [{size:31,crc:identities.get(name).crc},{size:32,crc:0}]) {
  const ids=new Map(identities);ids.set(name,bad);
  assert.throws(()=>plan(legacy,ids),/Conflict on pad/);
 }
}
for(const name of ['01life.mod','18mole.mod']) {
 const files=ML.pickerOrder([...legacy,{name,size:32}]);
 const ids=new Map(identities);ids.set(name,{size:32,crc:0});
 assert.throws(()=>plan(files,ids),/differs from the staged file/);
}
// The actual published 0.2.2 Euclid identity is recognized under old and relocated numbered names.
const oldEuclid={size:3144,crc:0x78260738};
assert.deepEqual(manifest.find(a=>a.file==='10euclid.mod').previous,[{
 version:'0.2.2',size:3144,crc:0x78260738,sha256:'626cd28ea95d266fab1418a18debaac0b0ee67b8e46db1433f91df0ad0ae52f4',
},{size:5976,crc:0xadb76ea3,sha256:'133f0e31bbc8379b4432be9ddfe184e7a4e64f0b1c532f33d06112b06a1e26e0'}]);
const legacy022=new Map(identities);legacy022.set('02euclid.mod',oldEuclid);
assert.deepEqual(plan(legacy,legacy022),jobs);
for(const name of ['08euclid.mod','10euclid.mod']) {
 const files=ML.pickerOrder([...legacy.filter(e=>e.name.toLowerCase()!=='02euclid.mod'),{name,size:3144}]);
 const ids=new Map(identities);ids.delete('02euclid.mod');ids.set(name,oldEuclid);
 const migrated=plan(files,ids);
 assert.ok(migrated.some(j=>j.op==='write'&&j.name==='10euclid'));
 assert.equal(migrated.some(j=>j.op==='delete'&&j.name===ML.nameOf(name)),name!=='10euclid.mod');
 ids.set(name,{size:3144,crc:0x78260739});
 if(name==='10euclid.mod')assert.throws(()=>plan(files,ids),/Conflict/);
 else assert.ok(!plan(files,ids).some(j=>j.op==='delete'&&j.name===ML.nameOf(name)));
}
const alteredUnused=new Map(identities);alteredUnused.set('19mole.mod',{size:32,crc:0});
assert.ok(!plan(legacy,alteredUnused).some(j=>j.name==='19mole'),'altered unused pad19 is preserved');
const capped=[...legacy];capped.capped=true;assert.throws(()=>plan(capped),/incomplete/);
assert.throws(()=>plan([...legacy,legacy[0]]),/Duplicate card name/);
// Preserve existing manual deletion and its confirmation; planned jobs never use Actions.del's implicit relist.
for(const error of [new ML.CardError('failed','FS failure'),new ML.CardError('unknown','UNKNOWN')]) {
 const sent=[];
 await assert.rejects(ML.executeModulePlan({del:async n=>{sent.push(['delete',n]);throw error;},write:async(_,n)=>sent.push(['write',n])},jobs),e=>e===error);
 assert.deepEqual(sent,[['delete',jobs[0].name]],'first failure stops deletes, writes and retries');
 const writes=[];
 const two=[{op:'write',name:'01life',blob:desired[0].blob},{op:'write',name:'02rain',blob:desired[1].blob}];
 await assert.rejects(ML.executeModulePlan({write:async(_,n)=>{writes.push(n);throw error;}},two),e=>e===error);
 assert.deepEqual(writes,['01life']);
}
// A later failure also stops the batch, including when deletion succeeded before a failed write.
for(const faultAt of [1,jobs.findIndex(j=>j.op==='write')]) {
 const done=[];const fault=new ML.CardError('unknown','UNKNOWN after prior success');
 const record=async(op,n)=>{done.push([op,n]);if(done.length===faultAt+1)throw fault;};
 await assert.rejects(ML.executeModulePlan({del:n=>record('delete',n),write:(_,n)=>record('write',n)},jobs),e=>e===fault);
 assert.deepEqual(done,jobs.slice(0,faultAt+1).map(j=>[j.op,j.name]));
}
const emitted=[];
await ML.executeModulePlan({del:async n=>emitted.push(['delete',n]),write:async(_,n)=>emitted.push(['write',n])},jobs);
assert.deepEqual(emitted,jobs.map(j=>[j.op,j.name]));
// A failure in preflight and any changed card after the preview prevent the first deletion.
let files=legacy.map(e=>({...e})),ids=new Map(identities),events=[];
const card={
 list:async()=>{events.push('list');return {files,capped:false};},
 verify:async n=>{events.push('verify:'+n);return ids.get(n.toLowerCase()+'.mod');},
 del:async n=>events.push('delete:'+n),write:async(_,n)=>events.push('write:'+n),
};
const prepared=await ML.prepareModulePlan(card,desired,true,assets);
assert.deepEqual(prepared.jobs,jobs);
assert.ok(events.every(e=>!e.startsWith('delete:')&&!e.startsWith('write:')));
files.push({name:'01custom.mod',size:32});
await assert.rejects(ML.recheckModulePlan(card,prepared,desired,true,assets),/Conflict on pad 1/);
assert.ok(!events.some(e=>e.startsWith('delete:')));
files=legacy.map(e=>({...e}));files.push({name:'06custom.mod',size:32});
await assert.rejects(ML.recheckModulePlan(card,prepared,desired,true,assets),/changed after the preview/);
files=legacy.map(e=>({...e}));ids.set('18lights.mod',{size:32,crc:0});
await assert.rejects(ML.recheckModulePlan(card,prepared,desired,true,assets),/Conflict on pad/);
const failed=[];
await assert.rejects(ML.prepareModulePlan({list:async()=>({files:legacy,capped:false}),verify:async n=>{failed.push(n);throw new ML.CardError('unknown','UNKNOWN');}},desired,true,assets),/UNKNOWN/);
assert.equal(failed.length,1);
// Assign, reload (new preflight with no browser ownership memory), move, reload, then reset.
const storage=new Map(assets.map(a=>[a.file,blobs.get(a.file)]));
const disk={
 list:async()=>({files:[...storage].map(([name,blob])=>({name,size:blob.length})),capped:false}),
 verify:async n=>{const b=storage.get(n.toLowerCase()+'.mod');return {size:b.length,crc:ML.crc32(b)};},
 del:async n=>{storage.delete(n+'.mod');},write:async(b,n)=>{storage.set(n+'.mod',b);},
};
for(const pad of [6,7]) {
 const moved=desired.map(d=>d.name==='01life'?{...d,name:String(pad).padStart(2,'0')+'life'}:d);
 const next=await ML.prepareModulePlan(disk,moved,false,assets);
 const checked=await ML.recheckModulePlan(disk,next,moved,false,assets);
 await ML.executeModulePlan(disk,checked);
 assert.equal(storage.has('01life.mod'),false);
 assert.equal(storage.has('06life.mod'),pad===6);
 assert.equal(storage.has('07life.mod'),pad===7);
}
const restored=await ML.prepareModulePlan(disk,desired,true,assets);
assert.ok(restored.jobs.some(j=>j.op==='delete'&&j.name==='07life'));
await ML.executeModulePlan(disk,restored.jobs);
assert.deepEqual([...storage.keys()].sort(),assets.map(a=>a.file).sort());
// Optional frozen-file check uses only a caller-supplied offline directory; never accesses MIDI.
if(process.env.MODULE_ASSET_DIR) for(const a of manifest) {
 const b=new Uint8Array(fs.readFileSync(process.env.MODULE_ASSET_DIR+'/'+a.file));ML.checkModule(b);
 assert.equal(b.length,a.size);assert.equal(ML.crc32(b),a.crc);
 assert.equal(crypto.createHash('sha256').update(b).digest('hex'),a.sha256);
}
if(process.env.MODULE_ASSET_DIR && process.env.MODULE_LEGACY_DIR) {
 const realDesired=manifest.map(a=>({name:ML.nameOf(a.file),blob:new Uint8Array(fs.readFileSync(process.env.MODULE_ASSET_DIR+'/'+a.file))}));
 const realFiles=[],realIds=new Map();
 for(const a of manifest) {
  const b=new Uint8Array(fs.readFileSync(process.env.MODULE_LEGACY_DIR+'/'+a.legacy));ML.checkModule(b);
  realFiles.push({name:a.legacy,size:b.length});realIds.set(a.legacy,{size:b.length,crc:ML.crc32(b)});
 }
 const realJobs=ML.modulePlan(ML.pickerOrder(realFiles),realDesired,realIds,true);
 assert.equal(realJobs.filter(j=>j.op==='delete').length,9);assert.equal(realJobs.filter(j=>j.op==='write').length,9);
 assert.ok(realJobs.some(j=>j.name==='02euclid'&&j.op==='delete'));
 assert.ok(realJobs.some(j=>j.name==='10euclid'&&j.op==='write'&&j.blob.length===manifest.find(a=>a.file==='10euclid.mod').size));
}
if(process.env.MODULE_ASSET_DIR && process.env.MODULE_PREVIOUS_DIR) {
 const want=manifest.map(a=>({name:ML.nameOf(a.file),blob:new Uint8Array(fs.readFileSync(process.env.MODULE_ASSET_DIR+'/'+a.file))}));
 const priorFiles=[],priorIds=new Map();
 for(const a of manifest) {
  const b=new Uint8Array(fs.readFileSync(process.env.MODULE_PREVIOUS_DIR+'/'+a.file));ML.checkModule(b);
  priorFiles.push({name:a.file,size:b.length});priorIds.set(a.file,{size:b.length,crc:ML.crc32(b)});
 }
 assert.equal(priorIds.get('10euclid.mod').size,5976);
 assert.equal(priorIds.get('10euclid.mod').crc,0xadb76ea3);
 const upgrade=ML.modulePlan(ML.pickerOrder(priorFiles),want,priorIds,true);
 assert.equal(upgrade.length,1);assert.equal(upgrade[0].op,'write');assert.equal(upgrade[0].name,'10euclid');
 assert.equal(upgrade[0].blob.length,11640);assert.equal(ML.crc32(upgrade[0].blob),0x677cbd2b);
}
console.log('Module staging: frozen manifest, reset migration, preserved user files, conflicts, late recheck and stop-first-failure passed.');
