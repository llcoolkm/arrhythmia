// Offline combined-loader protocol/safety tests. No MIDI device or network.
import fs from 'node:fs';
import assert from 'node:assert/strict';
const html=fs.readFileSync(new URL('../web/loader.html',import.meta.url),'utf8');
const part=n=>html.slice(html.indexOf(`/* ${n} BEGIN */`),html.indexOf(`/* ${n} END */`));
const {ML,LL,CRE3USB}=new Function(part('MLCORE')+'\n'+part('LLCORE')+'\nreturn {ML,LL,CRE3USB};')();
const releasePolicy=JSON.parse(fs.readFileSync(new URL('../tools/abi3_usb_policy.json',import.meta.url),'utf8'));
assert.equal(CRE3USB.policy.minimum_app_build,releasePolicy.minimum_app_build);
assert.equal(CRE3USB.policy.release_version,releasePolicy.release_version);
const legacyBuild=releasePolicy.minimum_app_build-1;
const asset={size:14906,crc:0x2EB146FE,build:0x80010000,version:'1.0.0',kind:1,abi:0x207,base:0xFE80B6B5};
const engine={type:2,compatible:1,file:'fm.dse',...asset};
function packet(sub,n,index=0,b=new Uint8Array(96),o={}) {
 const h=[...ML.PRE,0x10,sub,o.answer??0,...ML.p7(n,2),o.state??1,o.flags??0,o.count??1];
 return Uint8Array.from(sub===0x11?[...h,0xF7]:[...h,index,...Array.from(b).flatMap(x=>[x>>4,x&15]),0xF7]);
}
function binary(o={}) {
 const b=new Uint8Array(96);b.set([1,1,2,1,1,1,1,0]);
 new TextEncoder().encodeInto(o.name??'01_Ångström',b.subarray(8,32));
 for(const [k,v] of Object.entries(o)) if(/^\d+$/.test(k)) b[+k]=v;
 return b;
}
function rig(rows,flags=0) {
 const sent=[];let busy=true;
 const link=new ML.Link(m=>{
  sent.push([...m]); const n=ML.g7(m.subarray(8,10)),i=m[10];
  if(m[7]===0x11) link.feed(packet(0x11,n,0,undefined,{count:0}));
  else if(m[7]===0x12) {
   if(busy) {busy=false;link.feed(packet(0x12,n,i,undefined,{answer:1,count:i,flags}));}
   else if(i>=rows.length) link.feed(packet(0x12,n,i,undefined,{answer:3,state:2,count:rows.length,flags}));
   else {busy=true;link.feed(packet(0x12,n,i,rows[i],{count:i+1,flags}));}
  }
 });
 return {card:new ML.Card(link,{drainMs:1,pollMs:1,replyMs:20,jobMs:1000}),sent};
}
assert.equal(LL.versionText(0x80010000),'1.0.0');
assert.equal(LL.versionText(0x8001FF80),'1.255.128');
assert.equal(LL.version(0x300),null);assert.equal(LL.version(0x81010000),null);
assert.equal(LL.engineState(null,asset).text,'Not on the card');
assert.equal(LL.engineState(engine,asset).action,'Remove from card');
assert.match(LL.engineState({...engine,compatible:2},asset).text,/incompatible/);
assert.match(LL.engineState({...engine,compatible:3},asset).text,/unknown version/);
assert.match(LL.engineState({...engine,crc:0},asset).text,/different file/);
assert.match(LL.engineState({...engine,crc:0,build:0x80020000},asset).text,/newer/);
assert.doesNotMatch(LL.engineState({...engine,crc:0,build:0x300},asset).text,/older|0.3.0/);
assert.match(LL.engineState({...engine,crc:0,build:0x80000100},asset).text,/older/);
const b=binary(), decoded=LL.decodeRow(packet(0x12,123,0,b),123,0);
assert.equal(decoded.row.name,'01_Ångström');assert.equal(decoded.row.selector,1);assert.equal(decoded.row.pad,2);
assert.equal(LL.decodeRow(packet(0x12,123,0,b),124,0),null);
assert.equal(LL.decodeRow(packet(0x12,123,0,b),123,1),null);
assert.equal(LL.decodeRow(packet(0x12,123,0,b).slice(0,-1),123,0),null);
const nib=packet(0x12,123,0,b);nib[15]=16;assert.equal(LL.decodeRow(nib,123,0),null);
assert.equal(LL.decodeRow(packet(0x12,123,0,binary({1:32})),123,0),null);
assert.equal(LL.decodeRow(packet(0x12,123,0,binary({2:33})),123,0),null);
assert.equal(LL.decodeRow(packet(0x12,123,0,binary({88:1})),123,0),null);
const utf=binary();utf[8]=0xFF;assert.throws(()=>LL.decodeRow(packet(0x12,123,0,utf),123,0),/Unreadable card name/);
const xss=LL.decodeRow(packet(0x12,123,0,binary({name:'<img src=x onerror=x>'})),123,0).row;
assert.equal(xss.name,'<img src=x onerror=x>');
assert.match(html,/b\.appendChild\(el\('div',r && r\.name\?r\.name:'Unknown'\)\)/);
assert.doesNotMatch(html,/innerHTML\s*=\s*[^;'"\s]/); // names always go through textContent
assert.throws(()=>LL.validateSnapshot([decoded.row,decoded.row]),/ambiguous/);
assert.throws(()=>LL.validateSnapshot([engine,engine]),/duplicate/);
const snap=LL.validateSnapshot([decoded.row]);
assert.equal(LL.writable(decoded.row,snap),true);
assert.equal(LL.writable({...decoded.row,selector:0},snap),false);
assert.equal(LL.writable({...decoded.row,flags:2},snap),false);
assert.equal(LL.writable({...decoded.row,flags:5},snap),true);
assert.equal(LL.writable({...decoded.row,flags:9},snap),true);
assert.equal(LL.writable({...decoded.row,flags:13},snap),true);
assert.equal(LL.writable({...decoded.row,flags:3},snap),false);
assert.equal(LL.writable({...decoded.row,effective:3},snap),false);
assert.equal(LL.writable({...decoded.row,effective:4},snap),false);
assert.equal(LL.writable({type:3,selector:127,flags:8,own:0,effective:2,source:1},snap),true);
assert.equal(LL.writable({type:3,selector:127,flags:8,own:0,effective:3,source:1},snap),false);
assert.equal(LL.writable({...decoded.row,own:3},snap),false);
assert.equal(LL.writable(decoded.row,{...snap,flags:1}),false);
assert.equal(LL.writable({type:3,selector:127,flags:0,own:1,effective:1,source:1},snap),true);
assert.equal(LL.frame(packet(0x11,1,0,undefined,{flags:8}),0x11,1),null);
for(const flags of [4,7]) {
 assert.equal(LL.frame(packet(0x11,1,0,undefined,{flags,state:3}),0x11,1).flags,flags);
 assert.equal(LL.decodeRow(packet(0x12,1,0,undefined,{answer:4,state:3,flags}),1,0).flags,flags);
 const link=new ML.Link(m=>link.feed(packet(0x11,2,0,undefined,{state:3,flags})));
 await assert.rejects(LL.scan(new ML.Card(link,{drainMs:1}),2),/unavailable/);
}
assert.equal(LL.frame(packet(0x11,1,0,undefined,{count:65}),0x11,1),null);
const mismatch=rig([b]);const send=mismatch.card.link._send;
mismatch.card.link._send=m=>{
 if(m[7]===0x12 && m[10]===1) mismatch.card.link.feed(packet(0x12,3,1,undefined,{answer:3,state:2,count:0}));
 else send(m);
};
await assert.rejects(LL.scan(mismatch.card,3),/changed/);
const r=rig([b]);const s=await LL.scan(r.card,17);assert.equal(s.rows.length,1);
assert.ok(r.sent.every(m=>[0x11,0x12].includes(m[7])),'scan never verifies, recovers, ends or writes');
const incomplete=rig([b],1);assert.equal((await LL.scan(incomplete.card,18)).flags,1);
assert.ok(ML.allowed(LL.enc.pointer(1,true)));assert.equal(LL.enc.pointer(1,true)[7],0x0D);
assert.deepEqual([...LL.enc.pointer(1,false).slice(9,17)],new Array(8).fill(0));
assert.equal(LL.enc.engineDelete()[7],0x13);assert.ok(ML.allowed(LL.enc.engineDelete()));
assert.equal(ML.allowed(Uint8Array.from([...ML.PRE,0x10,0x10,0xF7])),false);
assert.ok(!Object.keys(LL.enc).some(k=>/load/i.test(k)));
assert.doesNotMatch(html,/Load now|Track 8 plays|0x10, 0x10/);
assert.match(html,/<title>Circus Arrhythmia FM: loader<\/title>/);
assert.match(html,/<p class="sub">Track 8 instrument<\/p>/);
assert.doesNotMatch(html,/loaded with pack|Empty pads start nothing/);
assert.throws(()=>LL.checkEngine(new Uint8Array(35)),/header/);
const fake=new Uint8Array(35),d=new DataView(fake.buffer);d.setUint32(0,0x32455243,true);d.setUint32(4,1,true);
assert.throws(()=>LL.checkEngine(fake, legacyBuild),/incompatible/);
d.setUint32(12,0x207,true);d.setUint32(16,0xFE80B6B5,true);d.setUint32(20,1,true);
assert.throws(()=>LL.checkEngine(fake, legacyBuild),/body CRC/);
const upgrade = `FM 1.0.0 (CRE2) does not run on firmware ${releasePolicy.release_version}. Install fm.dse 2.0.0.`;
assert.throws(()=>LL.checkEngine(fake), {message: upgrade});
await assert.rejects(LL.put({run(){throw Error('must validate first');}},fake), {message: upgrade});
// Check status reads only cmd 10 00, even when the previous write outcome was unknown.
const sent=[],link=new ML.Link(m=>{sent.push([...m]);link.feed(Uint8Array.from([...ML.PRE,0x10,0,1,3,1,1,0,...ML.p7(0,3),...ML.p7(0,3),...ML.p7(0,5),0,0xF7]));});
const card=new ML.Card(link,{ctx:{pending:{op:'write',what:'Write'}},settleMs:1});
await card.reconcile();assert.deepEqual(sent.map(m=>m[7]),[0]);assert.equal(card.ctx.needsScan,true);
await assert.rejects(card.write(new Uint8Array(35),'x'));
const badCard={run(){throw Error('must validate first');}};await assert.rejects(LL.put(badCard,new Uint8Array(35)),/header/);
if(process.argv[2]) {
 const fm=new Uint8Array(fs.readFileSync(process.argv[2])), expected=process.argv[3] || '1.0.0';
 const target = expected === '1.0.0' ? legacyBuild : undefined;
 assert.equal(LL.checkEngine(fm,target).version,expected);
 if(expected === '1.0.0')assert.throws(()=>LL.checkEngine(fm),{message:upgrade});
 else assert.throws(()=>LL.checkEngine(fm,legacyBuild), {message: `CRE3 engine files require firmware ${releasePolicy.release_version} or newer.`});
 function writerRig(corrupt=false) {
  let phase=0,job=0,got=0,size=0,crc=0;const sent=[],stage=new Uint8Array(fm.length);
  const link=new ML.Link(m=>{
   sent.push([...m]);const sub=m[7];
   if(m[6]===0x7D) {
    const addr=ML.g7(m.subarray(7,12)),off=addr-0x400,back=new Uint8Array(32);back.set(stage.subarray(off,off+32));if(corrupt)back[0]^=1;
    link.feed(Uint8Array.from([...ML.PRE,0x7D,...ML.p7(addr,5),...Array.from(back).flatMap(x=>[x>>4,x&15]),0xF7]));return;
   }
   if(sub===0x0E){phase=1;job=1;size=ML.g7(m.subarray(16,19));crc=ML.g7(m.subarray(19,24));}
   if(sub===2){const off=ML.g7(m.subarray(8,11));for(let i=11;i<m.length-1;i+=2)stage[off+(i-11)/2]=m[i]*16+m[i+1];got=off+(m.length-12)/2;}
   if(sub===3 || sub===0x0D){phase=2;job=1;}
   if(sub===0x0F){phase=2;job=4;size=fm.length;crc=ML.crc32(fm);}
   if(sub===0x13){phase=2;job=2;}
   if(sub===0 && phase===2)phase=3;
   if(sub===0x7F)phase=0;
   link.feed(Uint8Array.from([...ML.PRE,0x10,sub,1,phase,job,1,0,...ML.p7(got,3),...ML.p7(size,3),...ML.p7(crc,5),0,0xF7]));
  });
  return {card:new ML.Card(link,{drainMs:1,pollMs:1,replyMs:20}),sent,stage};
 }
 const good=writerRig();await LL.put(good.card,fm,target);assert.deepEqual(good.stage,fm);
 const subs=good.sent.filter(m=>m[6]===0x10).map(m=>m[7]);
 assert.deepEqual(subs.slice(-5),[3,0,15,0,127]);
 assert.equal(good.sent.filter(m=>m[6]===0x7D).length,Math.ceil(fm.length/32));
 const bad=writerRig(true);await assert.rejects(LL.put(bad.card,fm,target),/read back correctly/);
 assert.ok(!bad.sent.some(m=>m[6]===0x10 && [3,127].includes(m[7])),'bad staged readback stops before commit and END');
 assert.ok(bad.card.ctx.pending);
 const del=writerRig();await LL.remove(del.card);assert.deepEqual(del.sent.map(m=>m[7]),[19,0,127]);
 const ptr=writerRig();await LL.pointer(ptr.card,decoded.row,true,snap);assert.deepEqual(ptr.sent.map(m=>m[7]),[13,0,127]);
 assert.ok(!ptr.sent.some(m=>m[7]===16),'saving a choice never loads a pack');
}
console.log('Combined loader: protocol, streamed scans, malformed/Unicode records, versions, pointers and read-only reconciliation passed.');

// Releasing MIDI aborts local waiters without commands, touches only owned ports,
// detaches the access listener, and creates a new link on the next open.
{
 const sent=[], closed=[], listeners=new Set();
 const port=id=>({id,name:'Circuit Rhythm',state:'connected',connection:'open',
  open:async()=>{},close:async()=>{closed.push(id);},send:m=>sent.push(m)});
 const input=port('in'),output=port('out'),other=port('other');
 const access={inputs:new Map([['in',input],['other',other]]),outputs:new Map([['out',output]]),
  addEventListener:(type,fn)=>listeners.add(fn),removeEventListener:(type,fn)=>listeners.delete(fn)};
 const session=new ML.Session(access);
 const old=await session.open({input,output});
 const waiter=assert.rejects(old.next(10000),e=>e.kind==='cancelled');
 await session.dispose();await waiter;
 assert.deepEqual(closed,['in','out']);assert.equal(sent.length,0);
 assert.equal(input.onmidimessage,null);assert.equal(listeners.size,0);
 const fresh=new ML.Session(access);const link=await fresh.open({input,output});
 assert.notEqual(link,old);assert.equal(link.err,null);assert.equal(sent.length,0);
 // One failed close must not prevent the other owned port from closing.
 input.close=()=>{throw Error('gone');};await fresh.dispose();
 assert.deepEqual(closed,['in','out','out']);
}
console.log('MIDI disconnect: owned ports, local abort, listener disposal and fresh silent reconnect passed.');
