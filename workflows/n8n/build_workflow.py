"""Generate an importable workflow from the tested storage engine."""
import json
from pathlib import Path
import uuid

HERE = Path(__file__).resolve().parent
engine = (HERE / 'engine.js').read_text()
digest = """
const crypto = require('crypto');
async function describeBinary(field) {
  const bytes = await this.helpers.getBinaryDataBuffer(0, field);
  if (bytes.length > 15 * 1024 * 1024) throw new Error('El archivo excede el límite preparado de 15 MiB.');
  const binary = $input.first().binary[field];
  const filename = binary.fileName || '';
  return {size:bytes.length,sha256:crypto.createHash('sha256').update(bytes).digest('hex'),
    md5:crypto.createHash('md5').update(bytes).digest('base64'),
    extension:filename.split('.').pop().toLowerCase(),mime_type:binary.mimeType};
}
"""
normalize = engine + digest + """
const input = $input.first();
try {
  let meta;
  if (input.json.httpMethod === 'GET' || input.json.query?.key) {
    const q = input.json.query;
    meta = {operation:'download',key:q.key,storage:{provider:q.provider,target:q.target,region:q.region,account:q.account}};
  } else {
    meta = input.json.body || {};
    if (meta.metadata) meta = typeof meta.metadata === 'string' ? JSON.parse(meta.metadata) : meta.metadata;
  }
  const binary = {...input.binary};
  const keys = Object.keys(binary).filter(key => /^file(?:\\d+)?$/.test(key));
  if (keys.length > 1) throw new Error('Solo se admite un archivo por operación.');
  let file = null;
  if (keys.length) {
    file = await describeBinary.call(this, keys[0]);
    binary.file = binary[keys[0]];
    Object.keys(binary).filter(key => key !== 'file').forEach(key => delete binary[key]);
  }
  if (input.json.webhookUrl) {
    const url = new URL(input.json.webhookUrl);
    meta.download_base_url = url.origin + url.pathname.replace(/idpn-documentos$/, 'idpn-documentos-descarga');
  }
  return [{json:{state:IDPN.initialize(meta,file)},binary,pairedItem:{item:0}}];
} catch(error) {
  return [{json:{state:{done:true,status:400,result:{success:false,error:error.message}}},pairedItem:{item:0}}];
}
"""
prepare = engine + """
const input = $input.first();
const state = input.json.state;
const binary = {...input.binary};
let request;
try {request = IDPN.request(state);} catch(error) {IDPN.fail(state,error.message);request=IDPN.request(state);}
if (request?.payload) {
  if (request.payload.json) {
    const bytes=Buffer.from(JSON.stringify(request.payload.json));
    binary.payload={data:bytes.toString('base64'),mimeType:'application/json',fileName:'state.json'};
    request.headers['content-type']='application/json';
    request.headers['content-length']=String(bytes.length);
  } else {
    binary.payload=binary[request.payload.binary];
    if (!binary.payload) {IDPN.fail(state,'No se encontró el binario requerido.');request=IDPN.request(state);}
    else request.headers['content-length']=String(request.payload.binary==='file'?state.file.size:state.record.old[state.index].size);
  }
}
state.request=request;
return [{json:{state},binary,pairedItem:{item:0}}];
"""
transition = engine + digest + """
const input=$input.first();
const previous=$('Solicitud actual').itemMatching(0);
const state=previous.json.state;
const binary={...previous.binary,...input.binary};
let file=null;
try {
  if (input.binary?.received && Number(input.json.statusCode)>=200 && Number(input.json.statusCode)<300) {
    file=await describeBinary.call(this,'received');
    if(state.phase==='source_get')binary.archive=input.binary.received;
    if(state.phase==='download')binary.download=input.binary.received;
  }
  IDPN.transition(state,{statusCode:input.json.statusCode,headers:input.json.headers,body:input.json.body},file);
} catch(error) {IDPN.fail(state,error.message);}
return [{json:{state},binary,pairedItem:{item:0}}];
"""

nodes, connections = [], {}
def node(name, kind, parameters, x, y, version=1, **extra):
    nodes.append({'id':str(uuid.uuid5(uuid.NAMESPACE_URL, 'idpn/'+name)), 'name':name,
                  'type':'n8n-nodes-base.'+kind, 'typeVersion':version, 'position':[x,y], 'parameters':parameters, **extra})
def edge(source, target, branch=0):
    outputs = connections.setdefault(source, {'main':[]})['main']
    while len(outputs) <= branch:
        outputs.append([])
    outputs[branch].append({'node':target,'type':'main','index':0})
def condition(name, expression, x, y):
    node(name,'if',{'conditions':{'options':{'caseSensitive':True,'leftValue':'','typeValidation':'strict'},
         'conditions':[{'id':str(uuid.uuid5(uuid.NAMESPACE_URL,name)), 'leftValue':'={{ '+expression+' }}', 'rightValue':True,
         'operator':{'type':'boolean','operation':'true','singleValue':True}}], 'combinator':'and'},'options':{}},x,y,2)

node('IDPN documentos','webhook',{'httpMethod':'POST','path':'idpn-documentos','authentication':'headerAuth','responseMode':'responseNode',
     'options':{'binaryPropertyName':'file'}},0,0,2,webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL,'idpn/documents')))
node('Descarga pública','webhook',{'httpMethod':'GET','path':'idpn-documentos-descarga','responseMode':'responseNode','options':{}},0,220,2,
     webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL,'idpn/download')))
node('Validar entrada','code',{'jsCode':normalize},240,0,2)
node('Solicitud actual','code',{'jsCode':prepare},480,0,2)
condition('Terminado','$json.state.done === true',720,0)
condition('Respuesta binaria','$json.state.download === true && $json.state.result.success === true',940,-200)
node('Responder JSON','respondToWebhook',{'respondWith':'json','responseBody':'={{ $json.state.result }}','options':{'responseCode':'={{ $json.state.status || 200 }}'}},1180,-140,1.4)
node('Descargar archivo','respondToWebhook',{'respondWith':'binary','responseDataSource':'set','inputFieldName':'download',
     'options':{'responseCode':200,'responseHeaders':{'entries':[
         {'name':'Content-Type','value':'={{ $json.state.result.mime_type }}'},
         {'name':'Content-Disposition','value':'={{ "attachment; filename*=UTF-8\'\'" + encodeURIComponent($json.state.result.filename) }}'},
         {'name':'Cache-Control','value':'no-store'}]}}},1180,-320,1.4)
condition('Proveedor S3',"$json.state.provider === 's3'",940,180)
condition('Lectura pública',"$json.state.operation === 'download'",850,380)
condition('S3 archivo','$json.state.request.binary === true',1180,100)
condition('Azure archivo','$json.state.request.binary === true',1180,420)
for provider, y, credential in [('S3',0,'aws'),('Azure',320,'azureStorageSharedKeyApi')]:
    for binary, delta in [(False,0),(True,160)]:
        name=provider+(' leer archivo' if binary else ' operación')
        params={'method':'={{ $json.state.request.method }}','url':'={{ $json.state.request.url }}',
          'authentication':'predefinedCredentialType','nodeCredentialType':credential,
          'sendQuery':True,'specifyQuery':'json','jsonQuery':'={{ JSON.stringify($json.state.request.query) }}',
          'sendHeaders':True,'specifyHeaders':'json','jsonHeaders':'={{ JSON.stringify($json.state.request.headers) }}',
          'sendBody':'={{ $json.state.request.payload !== null }}','contentType':'binaryData','inputDataFieldName':'payload',
          'options':{'timeout':30000,'redirect':{'redirect':{'followRedirects':False}},
                     'response':{'response':{'fullResponse':True,'neverError':True,'responseFormat':'file' if binary else 'text','outputPropertyName':'received' if binary else 'body'}}}}
        node(name,'httpRequest',params,1430,y+delta,4.2,continueOnFail=True)
        edge(provider+' archivo',name,0 if binary else 1)
        edge(name,'Procesar resultado')
public_params = json.loads(json.dumps(params))
public_params['authentication'] = 'none'
del public_params['nodeCredentialType']
node('Leer enlace público','httpRequest',public_params,1430,660,4.2,continueOnFail=True)
edge('Leer enlace público','Procesar resultado')
node('Procesar resultado','code',{'jsCode':transition},1690,260,2)
node('Instrucciones','stickyNote',{'content':'## IDPN: S3 + Azure\n1. Credencial Header Auth en IDPN documentos: X-IDPN-Token.\n2. Seleccionar AWS IAM en los dos nodos S3.\n3. Seleccionar Azure Storage Shared Key en los dos nodos Azure.\n4. En self-hosted habilitar NODE_FUNCTION_ALLOW_BUILTIN=crypto en el runner.\n5. Los buckets/contenedores deben existir y permitir lectura pública de los documentos.\n\nLa aplicación selecciona región, cuenta, destino y directorio. El workflow calcula versiones, verifica copias y conserva antecedentes en BAJA.\n\nImportación/ex ejecución real pendiente de validar en tu versión de n8n. Consultar README.md de esta carpeta.','height':500,'width':450},-20,-540)
edge('IDPN documentos','Validar entrada');edge('Descarga pública','Validar entrada');edge('Validar entrada','Solicitud actual')
edge('Solicitud actual','Terminado');edge('Terminado','Respuesta binaria',0);edge('Terminado','Lectura pública',1)
edge('Lectura pública','Leer enlace público',0);edge('Lectura pública','Proveedor S3',1)
edge('Respuesta binaria','Descargar archivo',0);edge('Respuesta binaria','Responder JSON',1)
edge('Proveedor S3','S3 archivo',0);edge('Proveedor S3','Azure archivo',1);edge('Procesar resultado','Solicitud actual')
workflow={'name':'IDPN · Documentos S3 y Azure', 'nodes':nodes, 'connections':connections,
          'active':False,'settings':{'executionOrder':'v1','executionTimeout':900,'saveDataSuccessExecution':'none','saveDataErrorExecution':'all'},
          'pinData':{},'tags':[]}
(HERE / 'idpn-documentos.json').write_text(json.dumps(workflow,ensure_ascii=False,indent=2)+'\n')
print(f'Workflow generado: {len(nodes)} nodos, sin credenciales ni activación.')
