"""Server-only cloud credentials. Public API responses never contain secrets."""
import json
import os
import re
import tempfile
from pathlib import Path
from flask import current_app


def credentials_path():
    return Path(current_app.config['IDPN_CONFIG_DIR']) / 'integrations' / 'storage.json'


def load_credentials():
    path = credentials_path()
    return json.loads(path.read_text()) if path.exists() else {'s3': {}, 'azure': {}}


def credentials_status(settings=None):
    settings = settings or load_credentials()
    aws, azure = settings.get('s3', {}), settings.get('azure', {})
    return {'s3': {'configured': bool(aws.get('access_key_id') and aws.get('secret_access_key')),
                   'access_key_configured': bool(aws.get('access_key_id')),
                   'secret_key_configured': bool(aws.get('secret_access_key')),
                   'session_token_configured': bool(aws.get('session_token'))},
            'azure': {'account': azure.get('account', ''), 'configured': bool(azure.get('account') and azure.get('account_key')),
                      'account_key_configured': bool(azure.get('account_key'))}}


def save_credentials(data):
    if not isinstance(data, dict):
        raise ValueError('Configuración inválida.')
    result = load_credentials()
    for provider, fields in [('s3', ['access_key_id', 'secret_access_key', 'session_token']), ('azure', ['account', 'account_key'])]:
        values = data.get(provider, {})
        if not isinstance(values, dict):
            raise ValueError('Credenciales inválidas.')
        old = result.setdefault(provider, {})
        account = str(values.get('account', '')).strip().lower()
        if provider == 'azure' and account and account != old.get('account'):
            if not values.get('account_key'):
                raise ValueError('Introduce la clave para la nueva cuenta de Azure.')
        for field in fields:
            value = str(values.get(field, '')).strip()
            if any(c in value for c in ['\n', '\r', '\x00']):
                raise ValueError('Credenciales inválidas.')
            if value:
                old[field] = value
        if values.get('clear_session_token'):
            old.pop('session_token', None)
        if provider == 'azure' and old.get('account') and not re.fullmatch(r'[a-z0-9]{3,24}', old['account']):
            raise ValueError('Cuenta de Azure inválida.')
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as file:
        json.dump(result, file)
        temporary = file.name
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return credentials_status(result)


def bind_global_account(storage, settings=None):
    if storage.get('provider') == 'azure':
        settings = settings if settings is not None else load_credentials()
        storage['account'] = settings.get('azure', {}).get('account', '')
    return storage


def require_credentials(storage):
    settings = load_credentials()
    provider = storage.get('provider')
    if provider not in ('s3', 'azure') or not credentials_status(settings)[provider]['configured']:
        raise ValueError('Configura las credenciales de S3 o Azure en Credenciales de almacenamiento · Global.')
    bind_global_account(storage, settings)
    return settings[provider]
