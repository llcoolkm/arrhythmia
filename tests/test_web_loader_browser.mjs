// Browser smoke test using Chrome's DevTools protocol and Node's built-in WebSocket; no npm packages.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
// Process-group inspection below is Linux-specific. Keep unsupported hosts on
// the explicit browser-test skip path before starting children or reading /proc.
const groupSupported=(platform,exists)=>platform==='linux' && exists('/proc/self/stat');
for(const platform of ['darwin','win32']) {
 assert.equal(groupSupported(platform,()=>{throw Error('unsupported host accessed proc');}),false);
}
assert.equal(groupSupported('linux',()=>false),false,'missing proc must skip');
if(!groupSupported(process.platform,fs.existsSync)) {
 console.log('Browser smoke and cleanup regression skipped: Linux process groups and proc are required.');
 process.exit(0);
}
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
// A separate session makes the negative pid ours exclusively. Do not signal
// Chrome processes selected by name: other browser tests may run concurrently.
function ownedLiveGroup(pid) {
 return fs.readdirSync('/proc').filter(name=>/^\d+$/.test(name)).some(name=>{
  try {
   const stat=fs.readFileSync(`/proc/${name}/stat`,'utf8');
   const fields=stat.slice(stat.lastIndexOf(')')+2).split(' ');
   return Number(fields[2])===pid && !['Z','X'].includes(fields[0]);
  } catch(error) {if(['ENOENT','ESRCH'].includes(error.code))return false;throw error;}
 });
}
async function waitOwnedGroup(pid,ms) {
 const end=Date.now()+ms;
 while(ownedLiveGroup(pid)) {if(Date.now()>=end)return false;await pause(25);}
 return true;
}
async function shutdownBrowser(child,closed,requestClose) {
 if(requestClose && child.exitCode===null && child.signalCode===null) {
  // Browser.close flushes profile users; killing only the browser can leave the
  // network service writing TransportSecurity after the browser's exit event.
  await Promise.race([requestClose().catch(()=>{}),pause(1000)]);
 }
 if(!await waitOwnedGroup(child.pid,2000)) {
  for(const signal of ['SIGTERM','SIGKILL']) {
   try {process.kill(-child.pid,signal);}catch(error){if(error.code!=='ESRCH')throw error;}
   if(await waitOwnedGroup(child.pid,1000))break;
  }
 }
 assert.equal(ownedLiveGroup(child.pid),false,'owned browser process group did not stop');
 // close also waits for the wrapper's inherited stderr pipe, unlike exit.
 await Promise.race([closed,pause(1000).then(()=>{throw Error('browser stdio did not close');})]);
}
async function cleanupRegression() {
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'loader-cleanup-'));
 const marker=path.join(folder,'late-write');
 const script=`const {spawn}=require('node:child_process');
 const writer=spawn(process.execPath,['-e',${JSON.stringify("process.on('SIGTERM',()=>{});process.send('ready');setTimeout(()=>{require('node:fs').writeFileSync(process.argv[1],'flushed');process.exit(0);},300);")},process.argv[1]],{stdio:['ignore','ignore','ignore','ipc']});
 writer.on('message',()=>process.exit(0));`;
 const leader=spawn(process.execPath,['-e',script,marker],{detached:true,stdio:'ignore'});
 const closed=new Promise((resolve,reject)=>{leader.once('close',resolve);leader.once('error',reject);});
 await closed;
 assert.equal(ownedLiveGroup(leader.pid),true,'regression needs a surviving profile writer');
 await shutdownBrowser(leader,closed);
 assert.equal(fs.readFileSync(marker,'utf8'),'flushed');
 fs.rmSync(folder,{recursive:true});
 assert.equal(fs.existsSync(folder),false);
 console.log('Browser cleanup: delayed owned descendant is stopped before profile removal.');
}
await cleanupRegression();
if(process.argv.includes('--cleanup-only'))process.exit(0);
const chrome=process.env.CHROME || '/usr/bin/google-chrome';
if(!fs.existsSync(chrome)) {console.log('Chrome unavailable; browser smoke skipped.');process.exit(0);}
const fixturePath=process.env.ENGINE_FIXTURE || new URL('../web/engines/fm.dse',import.meta.url);
const fixture=fs.existsSync(fixturePath)?Array.from(fs.readFileSync(fixturePath)):null;
const fixtureEntry=fixture?{name:'FM',file:'fm.dse',sha256:createHash('sha256').update(Uint8Array.from(fixture)).digest('hex'),size:fixture.length,kind:fixture[24],version:[fixture[30],fixture[29],fixture[28]].join('.'),author:'km',licence:'MIT',status:'official'}:null;
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'loader-browser-'));
const child=spawn(chrome,['--headless','--no-sandbox','--disable-gpu','--remote-debugging-port=0',`--user-data-dir=${tmp}`,'about:blank'],{detached:true,stdio:['ignore','ignore','pipe']});
const closed=new Promise((resolve,reject)=>{child.once('close',resolve);child.once('error',reject);});
let ws,call;
try {
 const endpoint=await new Promise((resolve,reject)=>{let text='';const t=setTimeout(()=>reject(Error('Chrome start timeout')),10000);child.stderr.on('data',b=>{text+=b;const m=/DevTools listening on (ws:\/\/\S+)/.exec(text);if(m){clearTimeout(t);resolve(m[1]);}});child.on('error',reject);});
 ws=new WebSocket(endpoint);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
 let id=0,session;const pending=new Map(),errors=[];
 ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}else if(m.method==='Runtime.exceptionThrown') errors.push(m.params.exceptionDetails);};
 call=(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params,...(session?{sessionId:session}:{})}));});
 const target=await call('Target.createTarget',{url:'about:blank'});session=(await call('Target.attachToTarget',{targetId:target.targetId,flatten:true})).sessionId;
 await call('Page.enable');await call('Runtime.enable');
 const shim=`(() => {
 const p7=(v,n)=>Array.from({length:n},(_,i)=>Math.floor(v/2**(7*(n-1-i)))&127);
 const pre=[240,0,32,41,0,96];window.__sent=[];
 window.__fixture=${JSON.stringify(fixture)};window.__catalogueEntry=${JSON.stringify(fixtureEntry)};window.__catalogueRequests=0;window.__catalogueFailOnce=true;
 const oldFetch=window.fetch.bind(window);
 window.fetch=async(input,options)=>{
  const url=String(input);
  if(url.endsWith('engines/index.json')){window.__catalogueRequests++;if(window.__catalogueFailOnce){window.__catalogueFailOnce=false;return new Response('',{status:503});}return new Response(JSON.stringify({format:'engine-catalogue/1',engines:window.__catalogueEntry?[window.__catalogueEntry]:[]}));}
  if(url.endsWith('engines/fm.dse') && window.__fixture)return new Response(Uint8Array.from(window.__fixture));
  return oldFetch(input,options);
 };
 window.__engineFiles={};let stage=new Uint8Array(),engineName='',phase=0,job=0,got=0,size=0,crc=0;
 const g7=b=>Array.from(b).reduce((v,x)=>v*128+x,0);
 const wireName=m=>String.fromCharCode(...m.slice(8,16).filter(x=>x)).toLowerCase();
 const status=(sub)=>reply([...pre,16,sub,1,phase,job,1,0,...p7(got,3),...p7(size,3),...p7(crc,5),0,247]);
 const engineRow=(name,blob)=>{
  const r=new Uint8Array(96),d=new DataView(r.buffer),b=new DataView(blob.buffer);r[0]=2;r[7]=1;
  new TextEncoder().encodeInto(name+'.dse',r.subarray(48,60));d.setUint32(60,blob.length,true);d.setUint32(64,ML.crc32(blob),true);
  d.setUint32(68,b.getUint32(28,true),true);d.setUint32(72,blob[24],true);d.setUint32(76,b.getUint32(20,true),true);d.setUint32(80,b.getUint32(16,true),true);d.setUint32(84,b.getUint32(12,true),true);
  r[88]=3;r[90]=blob[26];d.setUint32(92,b.getUint32(60,true),true);return r;
 };
 const row=new Uint8Array(96);row.set([1,4,7,1,1,1,1,0]);new TextEncoder().encodeInto('Å <img src=x>',row.subarray(8,32));window.__row=row;
 window.__closed=[];window.__listeners=new Set();
 const input=window.__input={id:'in',name:'Circuit Rhythm',state:'connected',connection:'open',open:async()=>{},close:()=>{window.__closed.push('in');}};
 const reply=data=>input.onmidimessage?.({data:Uint8Array.from(data)});
 const output={id:'out',name:'Circuit Rhythm',state:'connected',connection:'open',open:async()=>{},close:()=>{window.__closed.push('out');},send(m){
  window.__sent.push([...m]);if(m[5]===112){reply([240,0,32,41,0,112,0,0,...p7(4799,3),0,0,...p7(7400,3),247]);return;}
  const sub=m[7];
  if(m[6]===125){const addr=g7(m.slice(7,12)),back=new Uint8Array(32);back.set(stage.subarray(addr-1024,addr-1024+32));reply([...pre,125,...p7(addr,5),...Array.from(back).flatMap(x=>[x>>4,x&15]),247]);return;}
  if(sub===17) {reply([...pre,16,17,0,m[8],m[9],1,0,0,247]);return;}
  if(sub===18){const i=m[10],rows=[row,...Object.entries(window.__engineFiles).map(([n,b])=>engineRow(n,b))],h=[...pre,16,18,i<rows.length?0:3,m[8],m[9],i<rows.length?1:2,0,rows.length,i];reply([...h,...Array.from(rows[i]||new Uint8Array(96)).flatMap(b=>[b>>4,b&15]),247]);return;}
  if(sub===14){engineName=wireName(m);size=g7(m.slice(16,19));crc=g7(m.slice(19,24));stage=new Uint8Array(size);got=0;phase=1;job=1;}
  if(sub===2){const off=g7(m.slice(8,11));for(let i=11;i<m.length-1;i+=2)stage[off+(i-11)/2]=m[i]*16+m[i+1];got=off+(m.length-12)/2;}
  if(sub===3){window.__engineFiles[engineName]=stage.slice();phase=2;job=1;}
  if(sub===15){const file=window.__engineFiles[wireName(m)];phase=2;job=4;size=file.length;crc=ML.crc32(file);}
  if(sub===19){delete window.__engineFiles[wireName(m)];phase=2;job=2;}
  if(sub===13){const name=String.fromCharCode(...m.slice(9,17).filter(x=>x)).toLowerCase();row.fill(0,32,48);new TextEncoder().encodeInto(name,row.subarray(32,40));new TextEncoder().encodeInto(name,row.subarray(40,48));row[4]=name?2:1;row[6]=name?2:1;phase=2;job=1;}
  if(sub===0 && phase===2)phase=3;
  if(sub===127){phase=0;job=0;}
  status(sub);
 }};
 Object.defineProperty(navigator,'requestMIDIAccess',{value:async()=>({inputs:new Map([['in',input]]),outputs:new Map([['out',output]]),addEventListener(t,f){window.__listeners.add(f);},removeEventListener(t,f){window.__listeners.delete(f);}})});
 })();`;
 await call('Page.addScriptToEvaluateOnNewDocument',{source:shim});
 const url=new URL('../web/loader.html',import.meta.url).href;
 await call('Page.navigate',{url});
 const evaluate=async expression=>{const value=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(value.exceptionDetails)throw Error(value.exceptionDetails.exception?.description || value.exceptionDetails.text);return value.result.value;};
 for(let i=0;i<50;i++){if(await evaluate('!!document.getElementById("connect")?.onclick'))break;await new Promise(r=>setTimeout(r,50));}
 assert.equal(await evaluate('document.title'),'Arrhythmia: loader');
 for(let i=0;i<50;i++){if(await evaluate('window.__catalogueRequests===1'))break;await pause(10);}
 assert.equal(await evaluate('document.querySelectorAll("#module-grid button").length'),32);
 await evaluate('document.getElementById("connect").click()');
 for(let i=0;i<100;i++){if(await evaluate('document.querySelectorAll("#packs button:not(:disabled)").length===1'))break;await new Promise(r=>setTimeout(r,50));}
 assert.equal(await evaluate('document.querySelectorAll("#packs button").length'),32);
 assert.equal(await evaluate('document.querySelectorAll("#packs button:not(:disabled)").length'),1);
 assert.equal(await evaluate('window.__catalogueRequests'),2,'next card scan must retry the failed catalogue fetch');
 assert.match(await evaluate('document.querySelectorAll("#packs button")[6].textContent'),/Å <img src=x>/);
 assert.equal(await evaluate('document.querySelectorAll("#packs img").length'),0);
 await evaluate('document.querySelectorAll("#packs button")[6].click()');
 assert.match(await evaluate('document.getElementById("pack-details").textContent'),/Samples/);
 assert.deepEqual(await evaluate('window.__sent.filter(m=>m[6]===16).map(m=>m[7])'),[0,17,18,18]);
 await evaluate('window.__row[3]=5;document.getElementById("scan").click()');
 for(let i=0;i<100;i++){if(await evaluate('document.querySelectorAll("#packs button")[6].textContent.includes("Short alias")'))break;await new Promise(r=>setTimeout(r,20));}
 assert.match(await evaluate('document.querySelectorAll("#packs button")[6].textContent'),/Short alias/);
 await evaluate('document.querySelectorAll("#packs button")[6].click()');
 assert.match(await evaluate('document.getElementById("pack-details").textContent'),/Short alias.*Samples/);
 assert.equal(await evaluate('document.getElementById("set-engine").disabled'),false);
 await evaluate('window.__row[3]=9;window.__row[4]=0;document.getElementById("scan").click()');
 for(let i=0;i<100;i++){if(await evaluate('document.querySelectorAll("#packs button")[6].title.includes("previous engine choice")'))break;await new Promise(r=>setTimeout(r,20));}
 assert.equal(await evaluate('document.querySelectorAll("#packs button")[6].disabled'),false);
 await evaluate('document.querySelectorAll("#packs button")[6].click()');
 assert.match(await evaluate('document.getElementById("pack-details").textContent'),/previous engine choice/);
 await evaluate('window.__row[3]=1;window.__row[4]=4;window.__row[6]=4;document.getElementById("scan").click()');
 for(let i=0;i<100;i++){if(await evaluate('document.querySelectorAll("#packs button")[6].disabled'))break;await new Promise(r=>setTimeout(r,20));}
 assert.equal(await evaluate('document.querySelectorAll("#packs button")[6].disabled'),true);
 // Stage a module choice, then hold an action to exercise the active-action gate.
 await evaluate(`document.getElementById('module-choice').value='';document.getElementById('module-choice').onchange();window.__staged=document.getElementById('module-grid').textContent;window.__originalScan=LL.scan;LL.scan=c=>new Promise(resolve=>{window.__finish=()=>{c.ctx.pending={op:'put',what:'Write test.mod',name:'test.mod',size:26,crc:123};resolve({rows:[],flags:0});};});document.getElementById('scan').click()`);
 for(let i=0;i<50;i++){if(await evaluate('!!window.__finish'))break;await new Promise(r=>setTimeout(r,20));}
 assert.equal(await evaluate('document.getElementById("disconnect").disabled'),true);
 assert.equal(await evaluate('document.getElementById("connect").disabled'),true);
 await evaluate('document.getElementById("disconnect").onclick()');
 assert.deepEqual(await evaluate('window.__closed'),[]);
 await evaluate('window.__finish()');
 for(let i=0;i<100;i++){if(await evaluate('!document.getElementById("disconnect").disabled'))break;await new Promise(r=>setTimeout(r,20));}
 const before=await evaluate('window.__sent.length');
 await evaluate('document.getElementById("disconnect").onclick()');
 assert.equal(await evaluate('window.__sent.length'),before);
 assert.deepEqual(await evaluate('window.__closed'),['in','out']);
 assert.equal(await evaluate('window.__input.onmidimessage===null'),true);
 assert.equal(await evaluate('window.__listeners.size'),0);
 assert.equal(await evaluate('document.getElementById("module-grid").textContent===window.__staged'),true);
 assert.match(await evaluate('document.getElementById("status").textContent'),/Write test.mod.*unknown/);
 assert.equal(await evaluate('document.getElementById("packs").querySelectorAll("button:not(:disabled)").length'),0);
 await evaluate('document.getElementById("connect").onclick()');
 assert.equal(await evaluate('window.__sent.length'),before,'reconnect sends no probes, scans, reconciliation or writes');
 assert.equal(await evaluate('window.__listeners.size'),1);
 assert.equal(await evaluate('document.getElementById("scan").disabled'),true);
 await evaluate('LL.scan=window.__originalScan;document.getElementById("check").onclick()');
 assert.equal(await evaluate('window.__sent.length'),before+2,'explicit unit check probes version and status only');
 assert.match(await evaluate('document.getElementById("unit").textContent'),/Write test.mod.*unknown/);
 assert.equal(await evaluate('document.getElementById("reconcile").disabled'),false);
 assert.equal(await evaluate('document.getElementById("write").disabled'),true);
 assert.equal(await evaluate('document.getElementById("scan").disabled'),true,'UNKNOWN blocks scan and writes across reconnect');
 await evaluate('document.getElementById("reconcile").onclick()');
 assert.equal(await evaluate('window.__sent.length'),before+3,'explicit reconciliation reads status only');
 assert.equal(await evaluate('document.getElementById("scan").disabled'),false);
 assert.equal(await evaluate('document.getElementById("write").disabled'),true,'resolved UNKNOWN still requires a fresh card scan');
 if(fixture) {
  await evaluate(`window.confirm=()=>true;window.__row[3]=1;window.__row[4]=1;window.__row[6]=1;document.getElementById('scan').click()`);
  for(let i=0;i<100;i++){if(await evaluate(String.raw`!!document.querySelector("button[data-engine-file=\"fm.dse\"]:not(:disabled)")`))break;await pause(20);}
  assert.match(await evaluate('document.getElementById("engines").textContent'),/FM.*official/);
  assert.equal(await evaluate(String.raw`document.querySelector("button[data-engine-file=\"fm.dse\"]").disabled`),false,JSON.stringify({ui:await evaluate('JSON.stringify({scan:document.getElementById("scan").disabled,check:document.getElementById("check").disabled,engines:document.getElementById("engines").textContent,local:document.getElementById("engine-local-status").textContent,status:document.getElementById("status").textContent})'),errors}));
  await evaluate(String.raw`document.querySelector("button[data-engine-file=\"fm.dse\"]").click()`);
  for(let i=0;i<300;i++){if(await evaluate('document.getElementById("status").textContent.includes("fm.dse written and verified")'))break;await pause(20);}
  assert.equal(await evaluate('window.__engineFiles.fm?.length'),fixture.length,await evaluate('JSON.stringify({status:document.getElementById("status").textContent,log:document.getElementById("log").textContent,sent:window.__sent.slice(-4)})'));
  assert.match(await evaluate('document.getElementById("status").textContent'),/fm.dse written and verified/);
  assert.equal(await evaluate('window.__sent.filter(m=>m[6]===16 && m[7]===14).length'),1);
  // Repaired header CRCs ensure budget/class failures exercise the shared checker.
  await evaluate(`window.__choose=async(bytes,name)=>{const input=document.getElementById('engine-file'),dt=new DataTransfer();dt.items.add(new File([Uint8Array.from(bytes)],name));input.files=dt.files;input.dispatchEvent(new Event('change'));};window.__fixHeader=b=>{const d=new DataView(b.buffer),H=d.getUint16(4,true);d.setUint32(60,0,true);d.setUint32(60,ML.crc32(b.subarray(0,H)),true);return b;};`);
  for(const [field,value,reason] of [[52,0,'check 13'],[24,128,'check 16']]) {
   const writes=await evaluate('window.__sent.filter(m=>m[6]===16 && m[7]===14).length');
   await evaluate(`{const b=Uint8Array.from(window.__fixture);if(${field}===52)new DataView(b.buffer).setUint32(52,${value},true);else b[${field}]=${value};window.__choose(window.__fixHeader(b),'bad.dse');}`);
   for(let i=0;i<50;i++){if(await evaluate('document.getElementById("engine-local-status").textContent.includes('+JSON.stringify(reason)+')'))break;await pause(10);}
   assert.match(await evaluate('document.getElementById("engine-local-status").textContent'),new RegExp(reason));
   assert.equal(await evaluate('window.__sent.filter(m=>m[6]===16 && m[7]===14).length'),writes);
  }
  const blocked=await evaluate(`(async()=>{let writes=0;try{await LL.put({run(){writes++;}},Uint8Array.from(window.__fixture),7400,{file:'fm.dse',entry:{...window.__catalogueEntry,sha256:'0'.repeat(64)}});}catch(e){return {writes,error:e.message};}return {writes};})()`);
  assert.equal(blocked.writes,0);assert.match(blocked.error,/catalogue identity/);
  await evaluate(`window.__choose(window.__fixture,'custom.dse')`);
  for(let i=0;i<100;i++){if(await evaluate(String.raw`!!document.querySelector("button[data-engine-file=\"custom.dse\"]")`))break;await pause(10);}
  assert.match(await evaluate('document.getElementById("engine-local-status").textContent'),/not reviewed.*DSP code/);
  await evaluate(String.raw`document.querySelector("button[data-engine-file=\"custom.dse\"]").click()`);
  for(let i=0;i<300;i++){if(await evaluate('document.getElementById("status").textContent.includes("custom.dse written and verified")'))break;await pause(20);}
  assert.equal(await evaluate('window.__engineFiles.custom?.length'),fixture.length);
  assert.equal(await evaluate('window.__engineFiles.fm?.length'),fixture.length,'local filename must not overwrite FM');
  await evaluate(String.raw`document.querySelectorAll("#packs button")[6].click();document.querySelector("input[name=instrument][value=\"custom.dse\"]").click();document.getElementById("set-engine").click()`);
  for(let i=0;i<100;i++){if(await evaluate('document.getElementById("status").textContent.includes("Choice saved")'))break;await pause(20);}
  const pointers=await evaluate('window.__sent.filter(m=>m[6]===16 && m[7]===13).map(m=>String.fromCharCode(...m.slice(9,17).filter(x=>x)).toLowerCase())');
  assert.equal(pointers.at(-1),'custom');
  console.log('Catalogue and local file: shared checks, hash before writes, own filenames, per-pack pointer and unreviewed DSP warning passed.');
 } else console.log('Catalogue/local fixture cases skipped: set ENGINE_FIXTURE or stage web/engines/fm.dse.');
 assert.equal(errors.length,0,JSON.stringify(errors));
 console.log('Chrome: full page connects, streams actual pack mapping, renders Unicode/XSS safely, and sends no writes during scan.');
} finally {
 try {await shutdownBrowser(child,closed,call?()=>call('Browser.close'):null);}
 finally {if(ws)ws.close();}
 fs.rmSync(tmp,{recursive:true,force:true});
}
