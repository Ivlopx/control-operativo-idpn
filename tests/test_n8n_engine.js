// Offline protocol tests: fake object stores, no SDKs, credentials, or network.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../workflows/n8n/engine.js'),'utf8');
const IDPN = vm.runInNewContext(source+';IDPN',{URLSearchParams,Date});
const fileInfo = (bytes,extension='pdf') => ({size:bytes.length,extension,mime_type:IDPN.mime[extension],
  sha256:crypto.createHash('sha256').update(bytes).digest('hex'),md5:crypto.createHash('md5').update(bytes).digest('base64')});
const escapeXML = value => String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
function store(provider) {
  let serial=0;
  const objects=new Map();
  function seed(key,bytes,headers={}) {objects.set(key,{bytes,headers,etag:'"e'+(++serial)+'"'});}
  function response(r,state,original,archive) {
    const url=new URL(r.url);
    const key=decodeURIComponent(url.pathname.split('/').slice(2).join('/'));
    if(r.query['list-type']==='2'||r.query.comp==='list') {
      const filtered=[...objects.entries()].filter(([key])=>key.startsWith(r.query.prefix||''));
      const offset=Number(r.query['continuation-token']||r.query.marker||0);
      const page=filtered.slice(offset,offset+2),next=offset+2<filtered.length?String(offset+2):'';
      const entries=page.map(([key,obj])=>provider==='s3'?`<Contents><Key>${escapeXML(key)}</Key><ETag>${escapeXML(obj.etag)}</ETag><Size>${obj.bytes.length}</Size></Contents>`:
        `<Blob><Name>${escapeXML(key)}</Name><Properties><Etag>${escapeXML(obj.etag)}</Etag><Content-Length>${obj.bytes.length}</Content-Length></Properties></Blob>`).join('');
      return {statusCode:200,body:provider==='s3'?`<ListBucketResult><IsTruncated>${!!next}</IsTruncated>${entries}${next?`<NextContinuationToken>${next}</NextContinuationToken>`:''}</ListBucketResult>`:
        `<EnumerationResults><Blobs>${entries}</Blobs><NextMarker>${next}</NextMarker></EnumerationResults>`};
    }
    const obj=objects.get(key);
    if(r.method==='PUT') {
      if(r.headers['if-none-match']==='*'&&obj)return {statusCode:412};
      if(r.headers['if-match']&&obj?.etag!==r.headers['if-match'])return {statusCode:412};
      const bytes=r.payload.json?Buffer.from(JSON.stringify(r.payload.json)):r.payload.binary==='file'?original:archive;
      assert.ok(bytes,'upload has bytes');
      if(r.headers['content-md5'])assert.equal(r.headers['content-md5'],fileInfo(bytes).md5);
      seed(key,bytes,{...r.headers});
      return {statusCode:provider==='s3'?200:201,headers:{etag:objects.get(key).etag}};
    }
    if(!obj)return {statusCode:404};
    if(r.headers['if-match']&&obj.etag!==r.headers['if-match'])return {statusCode:412};
    if(r.method==='DELETE'){objects.delete(key);return {statusCode:204};}
    const headers={...obj.headers,etag:obj.etag,'content-length':String(obj.bytes.length)};
    if(r.method==='HEAD')return {statusCode:200,headers};
    return {statusCode:200,headers,body:r.binary?'':obj.bytes.toString(),bytes:r.binary?obj.bytes:null};
  }
  return {objects,seed,response};
}
const baseMeta = provider => ({operation:'document',action:'alta',operation_id:'operation-1234567890',code:'FE-A-RHS-003',type_id:'formato',
  name:'Alta de personal',directory:'zhipos/procedimientos-especificos/administracion/recursos-humanos/formatos',
  storage:{provider,target:'documentos-zhipos',region:'us-east-1',account:'cuentazhipos'},download_base_url:'https://n8n.example.test/webhook/idpn-documentos-descarga'});
function run(meta,storage,bytes,hook=()=>{}) {
  const state=IDPN.initialize(meta,bytes?fileInfo(bytes):null);
  let archive;
  for(let steps=0;!state.done&&steps<1000;steps++){
    const request=IDPN.request(state);
    hook(state,request);
    const response=storage.response(request,state,bytes,archive);
    const file=response.bytes?fileInfo(response.bytes):null;
    if(state.phase==='source_get'&&response.bytes)archive=response.bytes;
    IDPN.transition(state,response,file);
  }
  assert.ok(state.done,'workflow terminates');
  return state;
}
let count=0;
function test(name,fn){fn();count++;process.stdout.write('ok '+name+'\n');}
for(const provider of ['s3','azure']) {
  test(provider+': alta, new version, BAJA, retry',()=>{
    const storage=store(provider),meta=baseMeta(provider);
    const first=run(meta,storage,Buffer.from('version one'));
    assert.equal(first.result.success,true);assert.equal(first.result.version,'v001');
    assert.ok(first.result.url.includes('/zhipos/procedimientos-especificos/'));
    const inventory=run({...meta,operation:'inventory'},storage);
    assert.equal(inventory.result.complete,true);
    assert.ok(inventory.result.files.some(file=>file.key===first.result.key));
    const newer={...meta,action:'version',operation_id:'operation-2234567890'};
    const second=run(newer,storage,Buffer.from('version two'));
    assert.equal(second.result.success,true);assert.equal(second.result.version,'v002');
    assert.equal(storage.objects.has(first.result.key),false);
    assert.ok(storage.objects.has(meta.directory+'/BAJA/'+first.result.filename));
    const retry=run(newer,storage,Buffer.from('version two'));
    assert.equal(retry.result.version,'v002');assert.equal(retry.result.success,true);
    const third=run({...meta,action:'baja',operation_id:'operation-3234567890'},storage);
    assert.equal(third.result.success,true);
    assert.equal(third.result.version,'v002');
    assert.equal(third.result.filename,second.result.filename);
    assert.equal(third.result.key,meta.directory+'/BAJA/'+second.result.filename);
    assert.ok(third.result.url.includes('/BAJA/'));
    assert.ok(third.result.download_url);
    const retryBaja=run({...meta,action:'baja',operation_id:'operation-3234567890'},storage);
    assert.equal(retryBaja.result.key,third.result.key);
    assert.equal(storage.objects.has(second.result.key),false);
    assert.ok(storage.objects.has(meta.directory+'/BAJA/'+second.result.filename));
    const renewed=run({...meta,action:'version',operation_id:'operation-4234567890'},storage,Buffer.from('renewed'));
    assert.equal(renewed.result.version,'v003');
  });
  test(provider+': different archive content never deletes active original',()=>{
    const storage=store(provider),meta=baseMeta(provider);
    const first=run(meta,storage,Buffer.from('version one'));
    storage.seed(meta.directory+'/BAJA/'+first.result.filename,Buffer.from('conflicting content'));
    const newer=run({...meta,action:'version',operation_id:'operation-2234567890'},storage,Buffer.from('version two'));
    assert.equal(newer.result.success,false);
    assert.ok(storage.objects.has(first.result.key));
    assert.ok(storage.objects.has(newer.record.key));
    assert.ok(!storage.objects.has(newer.lockKey));
  });
  test(provider+': occupied lock prevents writes',()=>{
    const storage=store(provider),meta=baseMeta(provider);
    storage.seed(meta.directory+'/.idpn-lock-'+meta.code+'.json',Buffer.from('another execution'));
    const state=run(meta,storage,Buffer.from('new'));
    assert.equal(state.result.success,false);assert.equal(state.status,409);
    assert.equal(storage.objects.size,1);
  });
  test(provider+': interrupted operation resumes same reserved version',()=>{
    const storage=store(provider),meta=baseMeta(provider),bytes=Buffer.from('file');
    const state=IDPN.initialize(meta,fileInfo(bytes));
    for(let i=0;i<20&&state.phase!=='upload';i++) {
      const r=IDPN.request(state),res=storage.response(r,state,bytes);
      IDPN.transition(state,res);
    }
    assert.equal(state.phase,'upload');
    storage.response(IDPN.request(state),state,bytes); // Provider committed, client response was lost.
    IDPN.transition(state,{statusCode:0});
    storage.response(IDPN.request(state),state,bytes); // Release own lock after reporting failure.
    const retry=run(meta,storage,bytes);
    assert.equal(retry.result.success,true);assert.equal(retry.result.version,'v001');
  });
}
test('malformed inventory never means empty storage',()=>{
  assert.throws(()=>IDPN.listing('<ListBucketResult>','s3'));
  assert.throws(()=>IDPN.listing('<ListBucketResult><IsTruncated>true</IsTruncated></ListBucketResult>','s3'));
});
test('formulario and path traversal are rejected',()=>{
  assert.throws(()=>IDPN.initialize({...baseMeta('s3'),type_id:'formulario'},fileInfo(Buffer.from('x'))));
  assert.throws(()=>IDPN.initialize({...baseMeta('s3'),directory:'../../escape'},fileInfo(Buffer.from('x'))));
});
test('download cannot read internal operation manifests',()=>{
  assert.throws(()=>IDPN.initialize({...baseMeta('s3'),operation:'download',key:'folder/.idpn-operations/operation.json'}));
});
test('encoded keys and XML entities are decoded without dropping inventory',()=>{
  const page=IDPN.listing('<ListBucketResult><IsTruncated>false</IsTruncated><Contents><Key>folder/file &amp; name.pdf</Key><ETag>&quot;e1&quot;</ETag><Size>12</Size></Contents></ListBucketResult>','s3');
  assert.equal(page.entries[0].key,'folder/file & name.pdf');
  assert.equal(page.entries[0].etag,'"e1"');
});
for (const provider of ['s3','azure']) {
  test(provider+': baja archives all active versions and returns highest for publication',()=>{
    const storage=store(provider),meta={...baseMeta(provider),action:'baja',name:''};
    for (const version of ['v003','v001','v002']) storage.seed(meta.directory+'/'+meta.code+'-control-'+version+'.pdf',Buffer.from(version));
    const completed=run(meta,storage);
    assert.equal(completed.result.success,true);
    assert.equal(completed.result.archived.length,3);
    assert.equal(completed.result.version,'v003');
    assert.equal(completed.result.name,'control');
    assert.ok(completed.result.key.endsWith('/BAJA/'+meta.code+'-control-v003.pdf'));
  });
}
for (const provider of ['s3','azure']) {
  test(provider+': policies without code have independent versions and BAJA',()=>{
    const storage=store(provider),meta={...baseMeta(provider),is_politica:true,type_id:'politica',code:'',name:'Política de calidad',directory:'zhipos/procedimientos-especificos/ingenieria/calidad/politicas'};
    const first=run(meta,storage,Buffer.from('policy one'));
    assert.equal(first.result.success,true);
    assert.equal(first.result.filename,'politica-de-calidad-v001.pdf');
    assert.equal(first.result.code,'');
    const other=run({...meta,name:'Política ambiental',operation_id:'operation-policy-other'},storage,Buffer.from('environment'));
    assert.equal(other.result.version,'v001');
    const second=run({...meta,action:'version',operation_id:'operation-policy-second'},storage,Buffer.from('policy two'));
    assert.equal(second.result.version,'v002');
    assert.ok(storage.objects.has(other.result.key));
    assert.ok(storage.objects.has(meta.directory+'/BAJA/'+first.result.filename));
    const baja=run({...meta,action:'baja',operation_id:'operation-policy-archive'},storage);
    assert.equal(baja.result.success,true);
    assert.equal(baja.result.filename,'politica-de-calidad-v002.pdf');
    assert.ok(baja.result.key.includes('/BAJA/'));
  });
}
for (const provider of ['s3','azure']) {
  test(provider+': baja uploads reference directly to BAJA without existing object',()=>{
    const storage=store(provider),meta={...baseMeta(provider),action:'baja'};
    const completed=run(meta,storage,Buffer.from('historical reference'));
    assert.equal(completed.result.success,true);
    assert.equal(completed.result.version,'v001');
    assert.ok(completed.result.key.includes('/BAJA/'));
    assert.equal(storage.objects.get(completed.result.key).bytes.toString(),'historical reference');
    assert.equal(completed.result.archived.length,0);
    assert.equal(run(meta,storage,Buffer.from('historical reference')).result.key,completed.result.key);
  });
  test(provider+': baja verifies reference upload before archiving active document',()=>{
    const storage=store(provider),meta=baseMeta(provider);
    const active=run(meta,storage,Buffer.from('active'));
    const completed=run({...meta,action:'baja',operation_id:'operation-baja-reference'},storage,Buffer.from('reference'),state=>{
      if (state.phase==='source_get') assert.ok(storage.objects.has(state.record.key));
    });
    assert.equal(completed.result.version,'v002');
    assert.ok(completed.result.key.includes('/BAJA/'));
    assert.ok(storage.objects.has(meta.directory+'/BAJA/'+active.result.filename));
    assert.equal(storage.objects.has(active.result.key),false);
  });
}
for (const provider of ['s3','azure']) {
  test(provider+': failed baja reference upload preserves active original',()=>{
    const storage=store(provider),meta=baseMeta(provider);
    const active=run(meta,storage,Buffer.from('active'));
    const originalResponse=storage.response;
    storage.response=(request,...args)=>request.method==='PUT'&&request.payload.binary==='file'?{statusCode:503}:originalResponse(request,...args);
    const failed=run({...meta,action:'baja',operation_id:'operation-failed-reference'},storage,Buffer.from('reference'));
    assert.equal(failed.result.success,false);
    assert.ok(storage.objects.has(active.result.key));
    assert.equal(storage.objects.has(meta.directory+'/BAJA/'+active.result.filename),false);
  });
}
for (const provider of ['s3','azure']) {
  test(provider+': two-step baja leaves originals until folio finalization and deduplicates by content',()=>{
    const storage=store(provider),meta=baseMeta(provider);
    const first=run(meta,storage,Buffer.from('older version'));
    const current=run({...meta,action:'version',operation_id:'operation-current-test'},storage,Buffer.from('current version'));
    storage.seed(first.result.key,Buffer.from('older version'));
    const witness=run({...meta,action:'baja',defer_archive:true,operation_id:'operation-witness-test'},storage,Buffer.from('current version'));
    assert.ok(storage.objects.has(current.result.key));
    assert.equal(witness.result.archived.length,0);
    const finalize={...meta,action:'finalizar_baja',operation_id:'operation-finalize-test',reference:{key:witness.result.key,sha256:witness.result.sha256,size:witness.result.size,version:witness.result.version}};
    const completed=run(finalize,storage);
    assert.equal(completed.result.success,true);
    assert.equal(storage.objects.has(current.result.key),false);
    assert.ok(storage.objects.has(witness.result.key));
    assert.equal(storage.objects.has(meta.directory+'/BAJA/'+current.result.filename),false);
    assert.ok(storage.objects.has(meta.directory+'/BAJA/'+first.result.filename));
    assert.equal(run(finalize,storage).result.success,true);
  });
  test(provider+': changed witness blocks finalization without touching originals',()=>{
    const storage=store(provider),meta=baseMeta(provider);
    const active=run(meta,storage,Buffer.from('active'));
    const witness=run({...meta,action:'baja',defer_archive:true,operation_id:'operation-witness-test'},storage,Buffer.from('reference'));
    storage.seed(witness.result.key,Buffer.from('changed'));
    const completed=run({...meta,action:'finalizar_baja',operation_id:'operation-finalize-test',reference:{key:witness.result.key,sha256:witness.result.sha256,size:witness.result.size,version:witness.result.version}},storage);
    assert.equal(completed.result.success,false);
    assert.ok(storage.objects.has(active.result.key));
  });
}
test('upload failure never archives the current version',()=>{
  const storage=store('s3'),meta=baseMeta('s3');
  const first=run(meta,storage,Buffer.from('version one'));
  const original=storage.response;
  storage.response=(request,state,bytes,archive)=>state.phase==='upload'?{statusCode:500}:original(request,state,bytes,archive);
  const failed=run({...meta,action:'version',operation_id:'operation-2234567890'},storage,Buffer.from('version two'));
  assert.equal(failed.result.success,false);
  assert.ok(storage.objects.has(first.result.key));
  assert.ok(!storage.objects.has(meta.directory+'/BAJA/'+first.result.filename));
});
test('public download is routed to an unauthenticated HTTP node',()=>{
  const workflow=JSON.parse(fs.readFileSync(path.join(__dirname,'../workflows/n8n/idpn-documentos.json'),'utf8'));
  assert.equal(workflow.nodes.find(node=>node.name==='Leer enlace público').parameters.authentication,'none');
  assert.equal(workflow.active,false);
  for(const outputs of Object.values(workflow.connections))for(const links of outputs.main)for(const link of links)assert.ok(workflow.nodes.some(node=>node.name===link.node));
  const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
  for(const node of workflow.nodes.filter(node=>node.type==='n8n-nodes-base.code'))new AsyncFunction(node.parameters.jsCode);
});
process.stdout.write(`${count} pruebas completadas.\n`);
