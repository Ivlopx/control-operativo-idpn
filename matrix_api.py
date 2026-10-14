"""Matrix administration and native cloud inventory queries."""
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from flask import Blueprint, current_app, jsonify, request

from document_matrix import default_matrix, plan_document, resolve_document_code, validate_matrix

matrix_api = Blueprint('document_matrix', __name__)
matrix_write_lock = threading.Lock()


def call_storage(payload):
    from storage_engine import execute_storage
    return execute_storage(payload)


def company_paths(domain):
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', domain) or '..' in domain:
        raise ValueError('Identificador de empresa inválido.')
    config_dir = Path(current_app.config['IDPN_CONFIG_DIR'])
    config_path = config_dir / f'{domain}.json'
    if not config_path.is_file():
        raise FileNotFoundError('Empresa no encontrada.')
    config = json.loads(config_path.read_text(encoding='utf-8'))
    return config, config_dir / 'matrices' / f'{domain}.json'


def bind_matrix_accounts(matrix):
    from storage_credentials import bind_global_account, load_credentials
    settings = load_credentials()
    bind_global_account(matrix['storage'], settings)
    for branch in matrix['government'] + matrix['specific']:
        for item in [branch] + branch.get('areas', []):
            if item.get('storage'):
                bind_global_account(item['storage'], settings)
    return matrix


def load_company_matrix(domain):
    config, path = company_paths(domain)
    if path.is_file():
        data = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            data['company'] = config.get('site_name') or data.get('company', domain)
        return bind_matrix_accounts(validate_matrix(data))
    return bind_matrix_accounts(validate_matrix(default_matrix(config.get('site_name', domain))))


def fetch_inventory(domain, plan):
    """Only queries when company storage and global credentials are configured."""
    from storage_credentials import require_credentials
    if not plan['storage_ready']:
        return None
    try:
        require_credentials(plan['storage'])
    except ValueError:
        return None
    payload = {'operation': 'inventory', 'site': domain, 'code': plan['code'],
               'type_id': plan['type_id'], 'storage': plan['storage'], 'name':plan.get('name',''), 'is_politica':plan.get('is_politica',False),
               'directory': plan['classification_directory'], 'include_baja': True}
    result = call_storage(payload)
    if not isinstance(result, dict) or result.get('success') is not True or not isinstance(result.get('files'), list):
        raise ValueError('El almacenamiento debe devolver {success: true, files: [...]} con el inventario completo.')
    if result.get('complete') is not True:
        raise ValueError('El almacenamiento debe confirmar complete: true después de listar todas las páginas del inventario.')
    return result['files']


@matrix_api.route('/api/storage-settings', methods=['GET', 'PUT'])
def storage_settings():
    from storage_credentials import save_credentials, credentials_status, load_credentials
    try:
        if request.method == 'PUT':
            with matrix_write_lock:
                result = save_credentials(request.get_json(silent=True))
        else:
            result = credentials_status()
        return jsonify(result)
    except (ValueError, TypeError) as error:
        return jsonify(error=str(error)), 400


@matrix_api.route('/api/storage-targets', methods=['POST'])
def storage_targets():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('Solicitud inválida.')
        from document_matrix import _storage
        storage = _storage(data.get('storage', {}))
        from storage_credentials import bind_global_account
        bind_global_account(storage)
        if not storage['provider']:
            raise ValueError('Selecciona S3 o Azure.')
        result = call_storage({'operation': 'destinations', 'storage': storage})
        if not isinstance(result, dict) or result.get('success') is not True or result.get('complete') is not True or not isinstance(result.get('targets'), list):
            raise ValueError('El almacenamiento no devolvió el listado completo de destinos.')
        if any(not isinstance(item, dict) or not isinstance(item.get('name'), str) for item in result['targets']):
            raise ValueError('Destinos inválidos en la respuesta de almacenamiento.')
        return jsonify(success=True, targets=result['targets'])
    except (ValueError, TypeError, KeyError) as error:
        return jsonify(error=str(error)), 400


@matrix_api.route('/api/matrices/<domain>', methods=['GET'])
def get_matrix(domain):
    try:
        return jsonify(load_company_matrix(domain))
    except FileNotFoundError as error:
        return jsonify(error=str(error)), 404
    except (ValueError, TypeError, KeyError) as error:
        return jsonify(error=str(error)), 400


@matrix_api.route('/api/matrices/<domain>', methods=['PUT'])
def save_matrix(domain):
    try:
        config, path = company_paths(domain)
        data = request.get_json(silent=True)
        if isinstance(data, dict):
            data['company'] = config.get('site_name') or data.get('company', domain)
        matrix = bind_matrix_accounts(validate_matrix(data))
        path.parent.mkdir(parents=True, exist_ok=True)
        with matrix_write_lock:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
                    temporary = Path(file.name)
                    json.dump(matrix, file, ensure_ascii=False, indent=2)
                    file.write('\n')
                os.replace(temporary, path)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
        return jsonify(success=True, matrix=matrix)
    except FileNotFoundError as error:
        return jsonify(error=str(error)), 404
    except (ValueError, TypeError, KeyError) as error:
        return jsonify(error=str(error)), 400


@matrix_api.route('/api/document-plan', methods=['POST'])
def document_plan():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('Solicitud documental inválida.')
        domain = str(data.get('site', ''))
        matrix = load_company_matrix(domain)
        plan = plan_document(matrix, data)
        if plan['store_file']:
            inventory = fetch_inventory(domain, plan)
            if inventory is not None:
                plan = plan_document(matrix, data, inventory=inventory)
        plan['site'] = domain
        return jsonify(success=True, plan=plan)
    except FileNotFoundError as error:
        return jsonify(error=str(error)), 404
    except (ValueError, TypeError, KeyError) as error:
        return jsonify(error=str(error)), 400


@matrix_api.route('/api/document-code', methods=['POST'])
def document_code():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('Solicitud inválida.')
        matrix = load_company_matrix(str(data.get('site', '')))
        matches = resolve_document_code(matrix, data.get('code', ''), data.get('type_id'))
        return jsonify(success=True, matches=matches, requires_type_selection=len(matches) > 1)
    except FileNotFoundError as error:
        return jsonify(error=str(error)), 404
    except (ValueError, TypeError, KeyError) as error:
        return jsonify(error=str(error)), 400
