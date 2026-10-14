"""Native cloud transfer. The browser never chooses storage keys or versions."""
import hashlib
from pathlib import Path
import re
from urllib.parse import urlparse

from document_matrix import slug



def transfer_document(plan, operation_id, temporary_file=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', operation_id):
        raise ValueError('Identificador de operación inválido.')
    metadata = {'operation':'document', 'operation_id':operation_id, 'site':plan['site'],
                'code':plan['code'], 'type_id':plan['type_id'], 'action':plan['action'],
                'defer_archive':plan.get('defer_archive',False), 'is_politica':plan.get('is_politica',False), 'name':plan.get('name',''), 'directory':plan['classification_directory'], 'storage':plan['storage']}
    from storage_engine import execute_storage
    content = Path(temporary_file).read_bytes() if temporary_file else None
    result = execute_storage(metadata, temporary_file)
    if not isinstance(result,dict) or result.get('success') is not True:
        raise ValueError(str(result.get('error','El almacenamiento no confirmó la operación.')) if isinstance(result,dict) else 'Respuesta inválida del almacenamiento.')
    if result.get('operation_id') != operation_id or result.get('code') != plan['code'] or result.get('action') != plan['action']:
        raise ValueError('La respuesta del almacenamiento no corresponde a este documento.')
    if plan['action'] != 'baja' or temporary_file:
        version = result.get('version','')
        if not re.fullmatch(r'v[0-9]{3,}', version):
            raise ValueError('El almacenamiento no devolvió una versión válida.')
        extension = Path(temporary_file).suffix.lower()
        filename = f'{(plan["code"]+"-") if plan["code"] else ""}{slug(plan["name"])}-{version}{extension}'
        if result.get('filename') != filename or result.get('key') != plan['classification_directory']+('/BAJA/' if plan['action']=='baja' else '/')+filename:
            raise ValueError('El almacenamiento devolvió un nombre o directorio diferente al calculado por la matriz.')
    else:
        identity = re.escape(slug(plan['name'])) if plan.get('is_politica') else re.escape(plan['code'])+r'-.+'
        pattern = re.compile(r'^'+identity+(r'(?:-v[0-9]{3,})?' if plan.get('is_politica') else '')+r'\.(?:pdf|docx?|xlsx?|pptx?)$', re.I)
        archived = result.get('archived')
        if not isinstance(archived, list) or not archived:
            raise ValueError('El almacenamiento no devolvió los archivos archivados de la baja.')
        for item in archived:
            if not isinstance(item, dict):
                raise ValueError('Inventario de baja inválido.')
            filename = str(item.get('key', '')).split('/')[-1]
            if not pattern.fullmatch(filename) or item.get('key') != plan['classification_directory']+'/BAJA/'+filename or item.get('source_key') != plan['classification_directory']+'/'+filename:
                raise ValueError('La baja devolvió un archivo fuera del directorio del documento.')
        def rank(item):
            match = re.search(r'-v([0-9]{3,})\.[^.]+$', item['key'], re.I)
            return (int(match.group(1)) if match else 0, item['source_key'])
        latest = max(archived, key=rank)
        filename = latest['key'].split('/')[-1]
        if result.get('key') != latest['key'] or result.get('filename') != filename:
            raise ValueError('El almacenamiento no devolvió la última versión archivada para publicar la baja.')
    for field in ['url','download_url']:
        parsed = urlparse(result.get(field,'') or '')
        if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username:
            raise ValueError('El almacenamiento no devolvió los enlaces de visualización y descarga esperados.')
    if plan.get('defer_archive'):
        if not re.fullmatch(r'[a-f0-9]{64}', str(result.get('sha256',''))) or not isinstance(result.get('size'),int) or result['size']<=0:
            raise ValueError('El almacenamiento no confirmó la integridad del archivo testigo.')
        if temporary_file and (result['sha256'] != hashlib.sha256(content).hexdigest() or result['size'] != len(content)):
            raise ValueError('La integridad confirmada no corresponde al archivo testigo subido.')
    return result
