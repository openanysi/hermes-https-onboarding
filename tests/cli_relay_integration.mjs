// Exercise an installed, published CLI against real loopback HTTP fixtures only.
// Usage: node tests/cli_relay_integration.mjs /absolute/path/to/node_modules/openany-cli[-dev]
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash,randomUUID} from 'node:crypto';
import test from 'node:test';

if(!process.argv[2])throw new Error('Pass the installed CLI package directory; no CLI source is bundled here.');
const dir=resolve(process.argv[2]);
const {createHermesRelay}=await import(pathToFileURL(join(dir,'src/hermes-delivery.js')));
const {HttpClient,GATEWAY_ORIGIN}=await import(pathToFileURL(join(dir,'src/http.js')));
const isDev=GATEWAY_ORIGIN==='https://api-dev.openany.si';
assert.ok(isDev || GATEWAY_ORIGIN==='https://api.openany.si');
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const reply=(res,status,body)=>{res.writeHead(status,{'content-type':'application/json'});res.end(JSON.stringify(body));};
const read=async req=>{const chunks=[];for await(const chunk of req)chunks.push(chunk);return Buffer.concat(chunks);};
const listen=server=>new Promise((yes,no)=>{server.once('error',no);server.listen(0,'127.0.0.1',yes);});
const stop=server=>new Promise((yes,no)=>{server.closeIdleConnections();server.close(e=>e?no(e):yes());});

async function fixture(t,{missing=false,retry=false}={}){
  const root=await fs.realpath(await fs.mkdtemp(join(tmpdir(),'openany-installed-cli-')));
  const taskRoot=join(root,'tasks'),state=join(root,'state');await fs.mkdir(taskRoot,{mode:0o700});
  const id=randomUUID(),api='fixture-private-hermes-key',seller='fixture-private-seller-key';
  const f={id,root,time:Date.now(),runs:0,uploads:0,stored:null,fail:retry?1:0,bytes:Buffer.from('original report bytes\n'),path:null};
  const native=createServer(async(req,res)=>{
    try{
      assert.equal(req.headers.authorization,'Bearer '+api);
      if(req.url==='/v1/capabilities')return reply(res,200,{object:'hermes.api_server.capabilities',features:{run_submission:true,run_status:true,runs_idempotency:{supported:true,durable:true,retention_seconds:86400}}});
      if(req.url==='/v1/skills')return reply(res,200,{object:'list',data:[{name:'fixture-report'}]});
      if(req.url==='/v1/runs' && req.method==='POST'){
        assert.equal(req.headers['idempotency-key'],id);f.runs++;
        const body=JSON.parse((await read(req)).toString());assert.match(body.instructions,/fixture-report/);
        assert.ok(!JSON.stringify(body).includes(seller));
        f.path=join(taskRoot,id,'report.txt');await fs.writeFile(f.path,f.bytes);
        await fs.writeFile(join(taskRoot,id,'openany-artifacts.json'),JSON.stringify({files:[missing?'missing.txt':'report.txt']}));
        return reply(res,202,{run_id:'run_fixture',status:'queued'});
      }
      if(req.url==='/v1/runs/run_fixture')return reply(res,200,{run_id:'run_fixture',status:'completed',output:'The requested report is complete.'});
      reply(res,404,{error:'not_found'});
    }catch(e){reply(res,500,{error:e.message});}
  });
  const platform=createServer(async(req,res)=>{
    try{
      if(req.method==='GET'){
        if(req.headers.authorization!=='Bearer fixture-buyer-key')return reply(res,401,{error:'unauthorized'});
        assert.equal(req.url,'/download/'+id);assert.ok(f.stored);res.end(f.stored);return;
      }
      assert.equal(req.url,`/v1/agent-api-tasks/${id}/artifacts`); // No result callback or settlement call.
      assert.equal(req.headers.authorization,'Bearer '+seller);f.uploads++;
      const bytes=await read(req);
      const form=await new Request('http://fixture.invalid/upload',{method:'POST',headers:{'content-type':req.headers['content-type']},body:bytes}).formData();
      const file=form.get('files');const data=Buffer.from(await file.arrayBuffer());
      assert.deepEqual(data,f.bytes);assert.equal(JSON.parse(form.get('metadata')).status,'completed');
      if(f.fail-->0)return reply(res,503,{error:{message:'fixture temporary outage'}});
      if(f.stored)assert.deepEqual(f.stored,data);f.stored=data;
      reply(res,200,{task_id:id,status:'stored',artifact:{artifact_id:id,size:data.length,content_type:file.type,sha256:hash(data)}});
    }catch(e){reply(res,500,{error:{message:e.message}});}
  });
  await listen(native);await listen(platform);
  const nativeOrigin='http://127.0.0.1:'+native.address().port,platformOrigin='http://127.0.0.1:'+platform.address().port;
  const env={OPENANY_TASK_ROOT:taskRoot,OPENANY_HERMES_STATE_DIR:state,OPENANY_HERMES_URL:nativeOrigin,OPENANY_HERMES_API_KEY:api,[isDev?'OPENANY_DEV_SELLER_KEY':'OPENANY_SELLER_KEY']:seller};
  const ctx={env,secrets:[],stderr:{write(){}},http:new HttpClient({fetch:(url,options)=>{
    const target=new URL(url);assert.equal(target.origin,GATEWAY_ORIGIN);
    return fetch(platformOrigin+target.pathname,options);
  }})};
  const relay=await createHermesRelay({ctx,now:()=>f.time});await listen(relay.server);
  const origin='http://127.0.0.1:'+relay.server.address().port;
  f.submit=()=>fetch(origin+'/v1/runs',{method:'POST',headers:{authorization:'Bearer '+api,'content-type':'application/json','idempotency-key':id,'x-openany-task-id':id,'x-openany-deadline':new Date(f.deadline).toISOString(),'x-openany-artifact-delivery':'auto_cli'},body:JSON.stringify({input:'Make report',instructions:'Use skill_view to load fixture-report.'})});
  f.deadline=f.time+120000;f.relay=relay;f.origin=origin;f.api=api;f.platformOrigin=platformOrigin;
  t.after(async()=>{await relay.close();await stop(native);await stop(platform);await fs.rm(root,{recursive:true,force:true});});
  return f;
}

test('published CLI: authenticated run, auto upload, buyer fixture download and exact replay',async t=>{
  const f=await fixture(t);let response=await f.submit();assert.equal(response.status,202);assert.equal((await response.json()).run_id,'run_fixture');
  await f.relay.delivery.step();assert.equal(f.relay.delivery.publicStatus(f.id).state,'stored');assert.equal(f.uploads,1);
  response=await fetch(f.platformOrigin+'/download/'+f.id);assert.equal(response.status,401);
  response=await fetch(f.platformOrigin+'/download/'+f.id,{headers:{authorization:'Bearer fixture-buyer-key'}});assert.deepEqual(Buffer.from(await response.arrayBuffer()),f.bytes);
  response=await f.submit();assert.equal(response.status,202);await f.relay.delivery.step();assert.equal(f.runs,1);assert.equal(f.uploads,1);
  response=await fetch(f.origin+'/v1/skills');assert.equal(response.status,401);
});

test('published CLI: transient upload failure retries identical frozen bytes without rerunning Hermes',async t=>{
  const f=await fixture(t,{retry:true});assert.equal((await f.submit()).status,202);
  await f.relay.delivery.step();assert.equal(f.relay.delivery.publicStatus(f.id).state,'retry');
  await fs.writeFile(f.path,'operator changed original after freeze');f.time+=31000;
  await f.relay.delivery.step();assert.equal(f.relay.delivery.publicStatus(f.id).state,'stored');assert.deepEqual(f.stored,f.bytes);assert.equal(f.runs,1);assert.equal(f.uploads,2);
});

test('published CLI: missing declared file never becomes stored and expires without an upload',async t=>{
  const f=await fixture(t,{missing:true});assert.equal((await f.submit()).status,202);
  await f.relay.delivery.step();assert.notEqual(f.relay.delivery.publicStatus(f.id).state,'stored');assert.equal(f.uploads,0);
  f.time=f.deadline+1;await f.relay.delivery.step();assert.equal(f.relay.delivery.publicStatus(f.id).state,'expired');assert.equal(f.runs,1);assert.equal(f.uploads,0);
});

test('published CLI command starts with seller-only credentials and stops cleanly',async t=>{
  const {spawn}=await import('node:child_process');
  const root=await fs.realpath(await fs.mkdtemp(join(tmpdir(),'openany-cli-command-')));
  const native=createServer((req,res)=>{
    assert.equal(req.headers.authorization,'Bearer fixture-command-hermes-key');
    reply(res,200,{object:'hermes.api_server.capabilities',features:{run_submission:true,run_status:true,runs_idempotency:{supported:true,durable:true,retention_seconds:86400}}});
  });
  const reserve=createServer();await listen(native);await listen(reserve);const port=reserve.address().port;await stop(reserve);
  const env={PATH:process.env.PATH,HOME:root,TMPDIR:root,
    [isDev?'OPENANY_DEV_CONFIG_DIR':'OPENANY_CONFIG_DIR']:join(root,'config'),
    OPENANY_TASK_ROOT:join(root,'tasks'),OPENANY_HERMES_STATE_DIR:join(root,'state'),
    OPENANY_HERMES_URL:'http://127.0.0.1:'+native.address().port,OPENANY_HERMES_API_KEY:'fixture-command-hermes-key',
    OPENANY_HERMES_PORT:String(port),[isDev?'OPENANY_DEV_SELLER_KEY':'OPENANY_SELLER_KEY']:'fixture-command-seller-key'};
  const child=spawn(process.execPath,[join(dir,'bin/openany.js'),'seller','hermes','serve','-j'],{env,stdio:['ignore','pipe','pipe']});
  let output='',errors='';child.stdout.on('data',data=>output+=data.toString());child.stderr.on('data',data=>errors+=data.toString());
  const exited=new Promise(yes=>child.once('exit',(code,signal)=>yes({code,signal})));
  t.after(async()=>{if(child.exitCode===null && child.signalCode===null){child.kill('SIGTERM');await exited;}await stop(native);await fs.rm(root,{recursive:true,force:true});});
  const deadline=Date.now()+8000;
  while(!output.includes('"ready":true') && Date.now()<deadline && child.exitCode===null)await new Promise(yes=>setTimeout(yes,20));
  assert.ok(output.includes('"ready":true'),'CLI failed to become ready: '+errors);
  const response=await fetch('http://127.0.0.1:'+port+'/v1/capabilities',{headers:{authorization:'Bearer fixture-command-hermes-key'}});
  assert.equal(response.status,200);child.kill('SIGTERM');assert.equal((await exited).code,0);
  assert.ok(!output.includes(env.OPENANY_HERMES_API_KEY));assert.ok(!output.includes('fixture-command-seller-key'));
});
