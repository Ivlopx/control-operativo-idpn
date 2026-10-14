"""Persist the association between a witness-page folio and its storage operation."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

from flask import current_app
from matrix_api import call_storage


def record_path(domain, folio):
    root = Path(current_app.config['IDPN_CONFIG_DIR']) / 'bajas'
    key = hashlib.sha256((domain+'\0'+folio.strip().upper()).encode()).hexdigest()
    return root / (key+'.json')


def load_record(domain, folio):
    path = record_path(domain, folio)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_record(record):
    path = record_path(record['domain'], record['folio'])
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, mode='w', delete=False) as temp:
        json.dump(record, temp, ensure_ascii=False)
        temporary = temp.name
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def prepare_record(plan, operation_id, folio):
    previous = load_record(plan['site'], folio)
    if previous and previous['operation_id'] != operation_id:
        raise ValueError('Ese folio ya está asociado a otra baja. Utiliza un folio distinto.')
    record = previous or {'domain':plan['site'], 'folio':folio, 'operation_id':operation_id,
                          'plan':plan, 'published':False, 'completed':False}
    save_record(record)
    return record


def attach_reference(record, stored):
    record['reference'] = {key:stored[key] for key in ['key','sha256','size','version']}
    save_record(record)


def finalize_folio(domain, folio):
    record = load_record(domain, folio)
    if not record or record['completed']:
        return
    if not record['published'] or not record.get('reference'):
        raise ValueError('La página testigo de baja todavía no está confirmada.')
    plan = record['plan']
    operation_id = 'final-'+hashlib.sha256(record['operation_id'].encode()).hexdigest()[:40]
    metadata = {'operation':'document','action':'finalizar_baja','operation_id':operation_id,
                'site':domain,'code':plan['code'],'name':plan.get('name',''),'is_politica':plan.get('is_politica',False),
                'type_id':plan['type_id'],'directory':plan['classification_directory'],'storage':plan['storage'],
                'reference':record['reference']}
    result = call_storage(metadata)
    if not isinstance(result,dict) or result.get('success') is not True or result.get('operation_id') != operation_id or result.get('action') != 'finalizar_baja' or result.get('code') != plan['code'] or result.get('key') != record['reference']['key']:
        raise ValueError('El almacenamiento no confirmó el archivo de la baja; puedes reintentar este mismo folio.')
    record['completed'] = True
    save_record(record)
    return True
