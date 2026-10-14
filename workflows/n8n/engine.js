/* IDPN storage protocol. No credentials or network access live in this module. */
const IDPN = (() => {
  const mime = {pdf:'application/pdf', doc:'application/msword', docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document', xls:'application/vnd.ms-excel', xlsx:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', ppt:'application/vnd.ms-powerpoint', pptx:'application/vnd.openxmlformats-officedocument.presentationml.presentation'};
  const encodePath = value => value.split('/').map(encodeURIComponent).join('/');
  const slug = value => value.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  function xmlDecode(value) {
    return value.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (_, entity) => {
      if (entity[0] === '#') return String.fromCodePoint(entity[1].toLowerCase() === 'x' ? parseInt(entity.slice(2), 16) : Number(entity.slice(1)));
      return {amp:'&', lt:'<', gt:'>', quot:'"', apos:"'"}[entity.toLowerCase()];
    });
  }
  const tag = (xml, name) => {const match = xml.match(new RegExp(`<${name}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${name}>`)); return match ? xmlDecode(match[1].replace(/^<!\[CDATA\[([\s\S]*)\]\]>$/, '$1')) : '';};
  function listing(body, provider, destinations = false) {
    if (typeof body !== 'string' || /<!DOCTYPE|<!ENTITY/i.test(body)) throw new Error('Listado XML inválido.');
    const root = destinations ? (provider === 's3' ? 'ListAllMyBucketsResult' : 'EnumerationResults') : (provider === 's3' ? 'ListBucketResult' : 'EnumerationResults');
    if (!new RegExp(`<${root}(?:\\s|>)`).test(body) || !body.includes(`</${root}>`)) throw new Error('Respuesta de inventario incompleta.');
    const itemTag = destinations ? (provider === 's3' ? 'Bucket' : 'Container') : (provider === 's3' ? 'Contents' : 'Blob');
    const entries = [...body.matchAll(new RegExp(`<${itemTag}>([\\s\\S]*?)<\\/${itemTag}>`, 'g'))].map(match => {
      const item = match[1];
      if (destinations) return {name: tag(item, 'Name')};
      const key = tag(item, provider === 's3' ? 'Key' : 'Name');
      const etag = tag(item, provider === 's3' ? 'ETag' : 'Etag');
      const sizeValue = tag(item, provider === 's3' ? 'Size' : 'Content-Length');
      if (!key || !etag || !/^\d+$/.test(sizeValue)) throw new Error('Objeto sin key, etag o tamaño.');
      return {key, etag, size: Number(sizeValue)};
    });
    const next = tag(body, provider === 's3' ? 'NextContinuationToken' : 'NextMarker') || (destinations && provider === 's3' ? tag(body,'ContinuationToken') : '');
    if (provider === 's3' && tag(body, 'IsTruncated') === 'true' && !next) throw new Error('S3 omitió el token de paginación.');
    return {entries, next};
  }
  function initialize(meta, file = null) {
    if (!meta || typeof meta !== 'object' || Array.isArray(meta)) throw new Error('Metadatos inválidos.');
    const storage = meta.storage || {};
    if (!['s3','azure'].includes(storage.provider)) throw new Error('Selecciona S3 o Azure.');
    if (storage.provider === 's3' && !/^[a-z]{2}(?:-[a-z]+)+-\d+$/.test(storage.region || '')) throw new Error('Falta la región de S3.');
    if (storage.provider === 'azure' && !/^[a-z0-9]{3,24}$/.test(storage.account || '')) throw new Error('Falta la cuenta de Azure.');
    const operation = meta.operation || 'document';
    if (!['inventory','document','destinations','download'].includes(operation)) throw new Error('Operación no admitida.');
    if (operation !== 'destinations' && !/^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(storage.target || '')) throw new Error('Bucket/contenedor inválido.');
    if (storage.provider === 'azure' && storage.target && !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(storage.target)) throw new Error('Contenedor Azure inválido.');
    const base = storage.provider === 's3' ? `https://s3.${storage.region}.amazonaws.com` : `https://${storage.account}.blob.core.windows.net`;
    const s = {meta: JSON.parse(JSON.stringify(meta)), operation, provider: storage.provider, base, storage, phase: '', done:false, files:[], pages:0, lockHeld:false, file, archived:[]};
    if (operation === 'destinations') {s.phase = 'list'; return s;}
    if (operation === 'download') {
      if (!validPath(meta.key) || /(?:^|\/)\.idpn/.test(meta.key) || !/\.(?:pdf|docx?|xlsx?|pptx?)$/i.test(meta.key)) throw new Error('Clave de descarga inválida.');
      s.phase = 'download'; return s;
    }
    if (meta.type_id === 'formulario') throw new Error('Los Formularios no se almacenan.');
    const policy=meta.is_politica===true;
    if (policy && (!meta.name || meta.code)) throw new Error('La política requiere nombre, sin código.');
    if ((!policy && !/^[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{1,10}$/i.test(meta.code || '')) || !validPath(meta.directory)) throw new Error('Código o directorio inválido.');
    if (operation === 'inventory') {s.phase = 'list'; return s;}
    if (!['alta','version','baja','finalizar_baja'].includes(meta.action)) throw new Error('Acción inválida.');
    if (!/^[a-zA-Z0-9_-]{16,80}$/.test(meta.operation_id || '')) throw new Error('Falta operation_id estable para reintentos.');
    if ((!['baja','finalizar_baja'].includes(meta.action) || file) && (!file || !mime[file.extension] || !slug(meta.name || ''))) throw new Error('Selecciona un archivo PDF u Office y su nombre.');
    if(meta.action==='finalizar_baja') {
      const ref=meta.reference;
      if(file||!ref||!validPath(ref.key)||!ref.key.startsWith(meta.directory+'/BAJA/')||ref.key.slice((meta.directory+'/BAJA/').length).includes('/')||!/^[a-f0-9]{64}$/.test(ref.sha256)||!Number.isInteger(ref.size)||ref.size<=0)throw new Error('Referencia de baja inválida.');
      const identity=policy?slug(meta.name):meta.code;
      if(!ref.key.split('/').pop().toLowerCase().startsWith(identity.toLowerCase()+(policy?'-v':'-')))throw new Error('La referencia pertenece a otro documento.');
    }
    s.lockKey = `${meta.directory}/.idpn-lock-${policy?'POLICY-'+slug(meta.name):meta.code.toUpperCase()}.json`;
    s.manifestKey = `${meta.directory}/.idpn-operations/${meta.operation_id}.json`;
    s.phase = 'lock';
    return s;
  }
  function validPath(path) {return typeof path === 'string' && path.length > 0 && path.length <= 900 && !path.startsWith('/') && !/[\\\x00-\x1f]/.test(path) && path.split('/').every(part => part && part !== '.' && part !== '..');}
  const objectUrl = (s,key) => `${s.base}/${s.storage.target}/${encodePath(key)}`;
  function request(s) {
    if (s.done) return null;
    const r = {method:'GET', url:s.base, query:{}, headers:{}, binary:false, payload:null};
    const key = value => r.url = objectUrl(s, value);
    const put = (value,payload,headers={}) => {key(value); r.method='PUT'; r.payload=payload; Object.assign(r.headers,headers); if(s.provider==='azure') r.headers['x-ms-blob-type']='BlockBlob';};
    switch(s.phase) {
      case 'list':
        if(s.provider==='s3') {r.url = s.operation==='destinations' ? s.base+'/' : `${s.base}/${s.storage.target}`; if(s.operation!=='destinations') r.query={'list-type':'2', prefix:s.meta.directory+'/'}; if(s.next)r.query['continuation-token']=s.next;}
        else {r.url=s.operation==='destinations' ? s.base+'/' : `${s.base}/${s.storage.target}`; r.query=s.operation==='destinations'?{comp:'list'}:{restype:'container',comp:'list',prefix:s.meta.directory+'/'}; if(s.next)r.query.marker=s.next;}
        break;
      case 'lock': put(s.lockKey,{json:{operation_id:s.meta.operation_id,created_at:new Date().toISOString()}},{'if-none-match':'*'});break;
      case 'manifest_get':key(s.manifestKey);break;
      case 'manifest_put':put(s.manifestKey,{json:s.record},s.manifestEtag?{'if-match':s.manifestEtag}:{'if-none-match':'*'});break;
      case 'upload_head':key(s.record.key);r.method='HEAD';break;
      case 'upload':put(s.record.key,{binary:'file'},uploadHeaders(s,s.file));break;
      case 'reference_get':key(s.meta.reference.key);r.binary=true;break;
      case 'source_get':key(s.record.old[s.index].key);r.binary=true;r.headers['if-match']=s.record.old[s.index].etag;break;
      case 'archive_put':put(s.record.old[s.index].destination,{binary:'archive'},uploadHeaders(s,s.record.old[s.index]));break;
      case 'archive_get':key(s.record.old[s.index].destination);r.binary=true;break;
      case 'source_delete':key(s.record.old[s.index].key);r.method='DELETE';r.headers['if-match']=s.record.old[s.index].etag;break;
      case 'unlock':key(s.lockKey);r.method='DELETE';r.headers['if-match']=s.lockEtag;break;
      case 'download':key(s.meta.key);r.binary=true;break;
      default:throw new Error(`Fase no reconocida: ${s.phase}`);
    }
    return r;
  }
  function uploadHeaders(s,file) {
    const headers={'if-none-match':'*','content-md5':file.md5,'content-type':mime[file.extension]||file.mime_type||'application/octet-stream','content-disposition':'inline'};
    if(s.provider==='azure'){headers['x-ms-blob-content-disposition']='inline';delete headers['content-disposition'];}
    headers[s.provider==='s3'?'x-amz-meta-idpn-sha256':'x-ms-meta-idpn_sha256']=file.sha256;
    headers[s.provider==='s3'?'x-amz-meta-idpn-operation':'x-ms-meta-idpn_operation']=s.meta.operation_id;
    return headers;
  }
  function checkpoint(s,next) {s.afterManifest=next;s.phase='manifest_put';}
  function finish(s,result,status=200) {
    s.result=result;s.status=status;
    if(s.lockHeld&&s.lockEtag)s.phase='unlock';
    else {if(s.lockHeld){s.result={...result,success:false,lock_key:s.lockKey,error:'Bloqueo sin ETag; requiere recuperación manual.'};s.status=502;}s.done=true;}
  }
  function fail(s,error,status=502) {finish(s,{success:false,error:String(error),operation_id:s.meta.operation_id,partial:!!s.record?.uploaded,key:s.record?.key,version:s.record?.version,archived:s.archived,lock_key:s.lockHeld?s.lockKey:undefined},status);}
  function beginArchive(s) {
    const next = s.record.old.findIndex(item=>!item.archived);
    if(next===-1) {s.record.phase='complete';checkpoint(s,'complete');}
    else {s.index=next;s.phase='source_get';}
  }
  function result(s) {
    let {version,filename,key}=s.record;
    let name=s.meta.name||'';
    if(s.meta.action==='baja'&&!s.record.uploaded) {
      const rank=item=>Number(/-v(\d{3,})\.[^.]+$/i.exec(item.key)?.[1]||0);
      const latest=s.record.old.filter(item=>item.archived).sort((a,b)=>rank(b)-rank(a)||b.key.localeCompare(a.key))[0];
      if(!latest)throw new Error('No hay un archivo archivado verificado para publicar la baja.');
      key=latest.destination;filename=key.split('/').pop();
      version=/-v(\d{3,})\.[^.]+$/i.exec(filename)?.[1];
      version=version?'v'+version:null;
      name=name||filename.slice(s.meta.is_politica?0:s.meta.code.length+1).replace(/-v\d{3,}(?=\.)/i,'').replace(/\.[^.]+$/,'').replace(/-/g,' ');
    }
    return {success:true,operation_id:s.meta.operation_id,code:s.meta.code,action:s.meta.action,
      version,filename,key,name,sha256:s.record.sha256,size:s.record.size,storage:s.storage,
      url:key?objectUrl(s,key):null,
      download_url:key&&s.meta.download_base_url ? s.meta.download_base_url+'?'+new URLSearchParams({provider:s.provider,target:s.storage.target,region:s.storage.region||'',account:s.storage.account||'',key}).toString():null,
      archived:s.record.old.filter(item=>item.archived).map(item=>({source_key:item.key,key:item.destination}))};
  }
  function allocate(s) {
    const identity=s.meta.is_politica?slug(s.meta.name):s.meta.code;
    const escaped=identity.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    const versionPattern=new RegExp('^'+escaped+(s.meta.is_politica?'':'-.+')+'-v(\\d{3,})\\.(?:pdf|docx?|xlsx?|pptx?)$','i');
    const legacyPattern=new RegExp('^'+escaped+(s.meta.is_politica?'':'-.+')+'\\.(?:pdf|docx?|xlsx?|pptx?)$','i');
    const files=s.files.filter(item=>{const relative=item.key.slice(s.meta.directory.length+1);return item.key.startsWith(s.meta.directory+'/')&&(!relative.includes('/')||/^BAJA\/[^/]+$/.test(relative))&&(versionPattern.test(relative.split('/').pop())||legacyPattern.test(relative.split('/').pop()));});
    if(s.meta.action==='alta'&&files.length)throw new Error('El documento existe; selecciona Nueva versión.');
    if(s.meta.action==='version'&&!files.length)throw new Error('El documento no existe; selecciona Alta.');
    const old=files.filter(item=>!item.key.slice(s.meta.directory.length+1).startsWith('BAJA/')).map(item=>({...item,destination:s.meta.directory+'/BAJA/'+item.key.split('/').pop(),archived:false}));
    if(s.meta.action==='baja'&&!s.file&&!old.length)throw new Error('No hay versiones activas para dar de baja.');
    const next=Math.max(0,...files.map(item=>Number(versionPattern.exec(item.key.split('/').pop())?.[1]||0)))+1;
    if(s.meta.action==='finalizar_baja') {
      s.record={operation_id:s.meta.operation_id,action:s.meta.action,code:s.meta.code,directory:s.meta.directory,policy_name:s.meta.is_politica?slug(s.meta.name):null,sha256:null,reference:s.meta.reference,version:s.meta.reference.version,filename:s.meta.reference.key.split('/').pop(),key:s.meta.reference.key,phase:'reserved',uploaded:false,old};
      checkpoint(s,'archive');return;
    }
    const upload=s.meta.action!=='baja'||!!s.file;
    const version=!upload?null:'v'+String(next).padStart(3,'0');
    const filename=version?`${s.meta.is_politica?'':s.meta.code+'-'}${slug(s.meta.name)}-${version}.${s.file.extension}`:null;
    s.record={operation_id:s.meta.operation_id,action:s.meta.action,code:s.meta.code,directory:s.meta.directory,
      policy_name:s.meta.is_politica?slug(s.meta.name):null,sha256:s.file?.sha256||null,size:s.file?.size||null,version,filename,key:filename?s.meta.directory+(s.meta.action==='baja'?'/BAJA/':'/')+filename:null,
      phase:'reserved',uploaded:false,old:s.meta.action==='alta'||(s.meta.action==='baja'&&s.meta.defer_archive===true)?[]:old};
    checkpoint(s,upload?'upload_head':'archive');
  }
  function transition(s,response,file=null) {
    const status=Number(response.statusCode||0);
    const headers=Object.fromEntries(Object.entries(response.headers||{}).map(([k,v])=>[k.toLowerCase(),String(v)]));
    const ok=status>=200&&status<300;
    const body=response.body||'';
    try {
      switch(s.phase) {
        case 'lock':
          if(!ok){fail(s,status===412?'El documento está siendo procesado o tiene un bloqueo pendiente.':'No se pudo adquirir el bloqueo.',status===412?409:502);break;}
          s.lockHeld=true;s.lockEtag=headers.etag;if(!s.lockEtag)throw new Error('El bloqueo no devolvió ETag. Requiere recuperación manual.');s.phase='manifest_get';break;
        case 'manifest_get':
          if(status===404){s.phase=s.meta.action==='finalizar_baja'?'reference_get':'list';break;}
          if(!ok)throw new Error('No se pudo consultar la operación anterior.');
          s.record=JSON.parse(body);s.manifestEtag=headers.etag;
          if(!s.manifestEtag||s.record.operation_id!==s.meta.operation_id||s.record.action!==s.meta.action||s.record.code!==s.meta.code||s.record.directory!==s.meta.directory||(s.meta.is_politica&&s.record.policy_name!==slug(s.meta.name))||s.record.sha256!==(s.file?.sha256||null)||(s.meta.action==='finalizar_baja'&&JSON.stringify(s.record.reference)!==JSON.stringify(s.meta.reference)))throw new Error('operation_id pertenece a otra solicitud.');
          if(s.record.phase==='complete')finish(s,result(s));
          else s.phase=s.meta.action==='finalizar_baja'?'reference_get':s.meta.action==='baja'&&!s.file?'archive_resume':'upload_head';
          if(s.phase==='archive_resume')beginArchive(s);break;
        case 'list': {
          if(!ok)throw new Error('No se pudo listar el almacenamiento.');
          const page=listing(body,s.provider,s.operation==='destinations');s.files.push(...page.entries);
          if(++s.pages>1000)throw new Error('Inventario demasiado grande.');
          if(page.next){if(page.next===s.next)throw new Error('Token de paginación repetido.');s.next=page.next;break;}
          if(s.operation==='inventory')finish(s,{success:true,complete:true,files:s.files});
          else if(s.operation==='destinations')finish(s,{success:true,complete:true,targets:s.files});
          else allocate(s);break;
        }
        case 'manifest_put':
          if(!ok||!headers.etag)throw new Error('No se pudo guardar el estado de la operación.');
          s.manifestEtag=headers.etag;
          if(s.afterManifest==='archive')beginArchive(s);
          else if(s.afterManifest==='complete')finish(s,result(s));
          else s.phase=s.afterManifest;break;
        case 'upload_head': {
          if(status===404){s.phase='upload';break;}
          if(!ok)throw new Error('No se pudo verificar el archivo nuevo.');
          const digest=headers[s.provider==='s3'?'x-amz-meta-idpn-sha256':'x-ms-meta-idpn_sha256'];
          if(digest!==s.file.sha256||Number(headers['content-length'])!==s.file.size)throw new Error('La clave de la versión existe con otro contenido.');
          s.record.uploaded=true;checkpoint(s,'archive');break;
        }
        case 'upload':
          if(!ok)throw new Error(status===412?'La versión ya existe; no se sobrescribió.':'Falló la subida.');
          s.phase='upload_head';break;
        case 'reference_get':
          if(!ok||!file||file.sha256!==s.meta.reference.sha256||file.size!==s.meta.reference.size)throw new Error('El archivo testigo falta o cambió; no se archivaron antecedentes.');
          if(s.record)beginArchive(s);else s.phase='list';break;
        case 'source_get': {
          const old=s.record.old[s.index];
          if(status===404){if(!old.sha256)throw new Error('El antecedente desapareció sin una copia verificada.');s.phase='archive_get';break;}
          if(!ok||!file)throw new Error('No se pudo leer el antecedente o cambió su ETag.');
          if(old.sha256&&old.sha256!==file.sha256)throw new Error('El contenido del antecedente cambió.');
          Object.assign(old,file);
          if(s.meta.action==='finalizar_baja'&&file.sha256===s.meta.reference.sha256&&file.size===s.meta.reference.size) {old.destination=s.meta.reference.key;old.duplicate=true;checkpoint(s,'archive_get');}
          else checkpoint(s,'archive_put');break;
        }
        case 'archive_put':
          if(!ok&&status!==412)throw new Error('Falló la copia a BAJA; se conservó el original.');
          s.phase='archive_get';break;
        case 'archive_get': {
          const old=s.record.old[s.index];
          if(!ok||!file||old.sha256!==file.sha256||old.size!==file.size)throw new Error('La copia en BAJA no coincide; se conservó el original.');
          s.phase='source_delete';break;
        }
        case 'source_delete':
          if(!ok&&status!==404)throw new Error('La copia está en BAJA, pero no se pudo retirar el original.');
          s.record.old[s.index].archived=true;s.archived.push(s.record.old[s.index].destination);checkpoint(s,'archive');break;
        case 'unlock':
          if(!ok&&status!==404){s.result={...s.result,success:false,error:'No se pudo liberar el bloqueo. Requiere revisar su ETag antes de retirarlo.',lock_key:s.lockKey,partial:!!s.record?.uploaded};s.status=502;}
          s.lockHeld=false;s.done=true;break;
        case 'download':
          if(!ok||!file)throw new Error('Archivo no disponible.');
          s.download=true;finish(s,{success:true,filename:s.meta.key.split('/').pop(),mime_type:file.mime_type||mime[file.extension]||'application/octet-stream'});break;
        default:throw new Error('Transición desconocida.');
      }
    } catch(error) {fail(s,error.message);}
    return s;
  }
  return {initialize,request,transition,listing,fail,mime};
})();
