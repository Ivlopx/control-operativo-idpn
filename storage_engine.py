"""Native document lifecycle, with cloud locks and resumable operation manifests."""
import hashlib
import json
import mimetypes
import re
from pathlib import Path
from document_matrix import _storage, slug
from document_stamp import STAMP_SUFFIX, next_stamp
from storage_providers import LIMIT, StorageError, make_storage

EXTENSIONS = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'}


def valid_path(key):
    return isinstance(key, str) and 0 < len(key) <= 900 and not key.startswith('/') and not re.search(r'[\\\x00-\x1f]', key) and all(p not in ('', '.', '..') for p in key.split('/'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_matches(meta, key):
    directory = meta['directory'] + '/'
    if not key.startswith(directory):
        return False
    relative = key[len(directory):]
    if '/' in relative and not re.fullmatch(r'baja/[^/]+', relative):
        return False
    identity = re.escape(slug(meta['name']) if meta.get('is_politica') else meta['code'])
    pattern = identity + (STAMP_SUFFIX if meta.get('is_politica') else r'-.+') + r'\.(?:pdf|docx?|xlsx?|pptx?)'
    return bool(re.fullmatch(pattern, relative.split('/')[-1], re.I))


def execute_storage(meta, temporary_file=None, provider=None):
    meta = dict(meta)
    storage = _storage(meta.get('storage', {}))
    if not storage['provider'] or (storage['provider'] == 's3' and not storage['region']) or (storage['provider'] == 'azure' and not storage['account']):
        raise ValueError('Configura el proveedor y su región o cuenta.')
    if meta.get('operation') != 'destinations' and not storage['target']:
        raise ValueError('Selecciona un bucket o contenedor existente.')
    cloud = provider or make_storage(storage)
    if meta.get('operation') == 'destinations':
        return {'success': True, 'complete': True, 'targets': cloud.targets()}
    if not valid_path(meta.get('directory')):
        raise ValueError('Directorio inválido.')
    if meta.get('type_id') == 'formulario':
        raise ValueError('Los formularios no usan almacenamiento.')
    policy = meta.get('is_politica', False)
    if policy:
        if meta.get('code') or not slug(meta.get('name', '')):
            raise ValueError('La política requiere nombre y no lleva código.')
    elif not re.fullmatch(r'[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{1,10}', meta.get('code', ''), re.I):
        raise ValueError('Código de documento inválido.')
    if meta.get('operation') == 'inventory':
        return {'success': True, 'complete': True, 'files': cloud.inventory(meta['directory'] + '/')}
    if meta.get('operation', 'document') != 'document':
        raise ValueError('Operación inválida.')
    action, op = meta.get('action'), meta.get('operation_id', '')
    if action not in ('alta', 'version', 'baja', 'finalizar_baja') or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', op):
        raise ValueError('Acción o identificador inválido.')
    content = Path(temporary_file).read_bytes() if temporary_file else None
    extension = Path(temporary_file).suffix.lower().lstrip('.') if temporary_file else None
    if action != 'finalizar_baja' and (not content or len(content) > LIMIT or extension not in EXTENSIONS or not slug(meta.get('name', ''))):
        raise ValueError('Selecciona un PDF o archivo Office de hasta 15 MiB y su nombre.')
    reference = meta.get('reference')
    if action == 'finalizar_baja':
        prefix = meta['directory'] + '/baja/'
        if content is not None or not isinstance(reference, dict) or not valid_path(reference.get('key')) or not reference['key'].startswith(prefix) or '/' in reference['key'][len(prefix):] or not file_matches(meta, reference['key']) or not re.fullmatch(r'[a-f0-9]{64}', str(reference.get('sha256', ''))) or type(reference.get('size')) is not int or reference['size'] <= 0:
            raise ValueError('Referencia de baja inválida.')
    identity = 'POLICY-' + slug(meta['name']) if policy else meta['code'].upper()
    lock_key = meta['directory'] + '/.idpn-lock-' + identity + '.json'
    manifest_key = meta['directory'] + '/.idpn-operations/' + op + '.json'
    try:
        lock_etag = cloud.put(lock_key, json.dumps({'operation_id': op}).encode())
    except StorageError as error:
        if error.status in (409, 412):
            raise ValueError('El documento está siendo procesado o tiene un bloqueo pendiente.') from error
        raise
    record, manifest_etag = None, None

    def checkpoint():
        nonlocal manifest_etag
        manifest_etag = cloud.put(manifest_key, json.dumps(record).encode(), etag=manifest_etag)

    def verify(key, sha, size):
        data, _ = cloud.get(key)
        if len(data) != size or digest(data) != sha:
            raise ValueError('La copia de almacenamiento no coincide; se conservó el original.')

    try:
        try:
            raw, manifest_etag = cloud.get(manifest_key)
            record = json.loads(raw)
        except StorageError as error:
            if error.status != 404:
                raise
        if record:
            if any(record.get(field) != meta.get(field) for field in ('operation_id', 'action', 'code', 'directory')) or record.get('policy_name') != (slug(meta['name']) if policy else None) or record.get('sha256') != (digest(content) if content else None) or (action == 'finalizar_baja' and record.get('reference') != reference):
                raise ValueError('El identificador pertenece a otra operación.')
        if action == 'finalizar_baja':
            verify(reference['key'], reference['sha256'], reference['size'])
        if not record:
            files = [item for item in cloud.inventory(meta['directory'] + '/') if file_matches(meta, item['key'])]
            if action == 'alta' and files:
                raise ValueError('El documento existe; selecciona Nueva versión.')
            if action == 'version' and not files:
                raise ValueError('El documento no existe; selecciona Alta.')
            old = [dict(item, destination=meta['directory'] + '/baja/' + item['key'].split('/')[-1], archived=False)
                   for item in files if not item['key'][len(meta['directory']) + 1:].startswith('baja/')]
            version = next_stamp(files)
            filename = (meta['code'] + '-' if not policy else '') + slug(meta['name']) + '-' + version + '.' + (extension or 'pdf')
            key = meta['directory'] + ('/baja/' if action == 'baja' else '/') + filename
            if action == 'finalizar_baja':
                version, key, filename = reference['version'], reference['key'], reference['key'].split('/')[-1]
            record = {'operation_id': op, 'action': action, 'code': meta['code'], 'directory': meta['directory'],
                'policy_name': slug(meta['name']) if policy else None, 'sha256': digest(content) if content else None,
                'size': len(content) if content else None, 'reference': reference, 'version': version, 'filename': filename,
                'key': key, 'phase': 'reserved', 'uploaded': False,
                'old': [] if action == 'alta' or (action == 'baja' and meta.get('defer_archive')) else old}
            checkpoint()
        if record['phase'] != 'complete':
            if content:
                try:
                    data, _ = cloud.get(record['key'])
                except StorageError as error:
                    if error.status != 404:
                        raise
                    cloud.put(record['key'], content, metadata={'idpn-sha256': digest(content), 'idpn-operation': op},
                              mime=mimetypes.guess_type(record['filename'])[0] or 'application/octet-stream')
                    data, _ = cloud.get(record['key'])
                if digest(data) != digest(content) or len(data) != len(content):
                    raise ValueError('La versión existe con otro contenido; no se sobrescribió.')
                record['uploaded'] = True
                checkpoint()
            for old in record['old']:
                if old['archived']:
                    continue
                try:
                    data, _ = cloud.get(old['key'], etag=old['etag'])
                except StorageError as error:
                    if error.status != 404 or not old.get('sha256'):
                        raise
                    data = None
                if data is not None:
                    if old.get('sha256') and digest(data) != old['sha256']:
                        raise ValueError('El antecedente cambió; se conservó el original.')
                    old.update(sha256=digest(data), size=len(data))
                    if action == 'finalizar_baja' and digest(data) == reference['sha256'] and len(data) == reference['size']:
                        old['destination'] = reference['key']
                        old['duplicate'] = True
                    checkpoint()
                    if not old.get('duplicate'):
                        try:
                            cloud.put(old['destination'], data, metadata={'idpn-sha256': digest(data), 'idpn-operation': op},
                                      mime=mimetypes.guess_type(old['key'])[0] or 'application/octet-stream')
                        except StorageError as error:
                            if error.status not in (409, 412):
                                raise
                verify(old['destination'], old['sha256'], old['size'])
                try:
                    cloud.delete(old['key'], old['etag'])
                except StorageError as error:
                    if error.status != 404:
                        raise
                old['archived'] = True
                checkpoint()
            record['phase'] = 'complete'
            checkpoint()
        url = cloud.public_url(record['key'])
        return {'success': True, 'operation_id': op, 'action': action, 'code': meta['code'],
                **{key: record[key] for key in ('key', 'filename', 'version', 'sha256', 'size')},
                'name': meta.get('name', ''), 'storage': storage, 'url': url,
                'download_url': url,
                'archived': [{'source_key': item['key'], 'key': item['destination']} for item in record['old'] if item['archived']]}
    finally:
        try:
            cloud.delete(lock_key, lock_etag)
        except StorageError as error:
            if error.status != 404:
                raise ValueError('No se pudo liberar el bloqueo; revisa el almacenamiento antes de reintentar.') from error
