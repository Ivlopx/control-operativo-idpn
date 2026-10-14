"""Permanent signed links for anonymous public-file downloads, without cloud keys."""
import io
import os
import tempfile
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError
from urllib.parse import quote
from flask import Blueprint, current_app, request, send_file, jsonify
from itsdangerous import URLSafeSerializer, BadSignature
from storage_providers import LIMIT

storage_download = Blueprint('storage_download', __name__)


def signer():
    path = Path(current_app.config['IDPN_CONFIG_DIR']) / 'integrations' / 'download-key'
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as file:
            file.write(os.urandom(32).hex())
            temporary = Path(file.name)
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
        finally:
            temporary.unlink()
    return URLSafeSerializer(path.read_text(), salt='idpn-public-download')


def download_url(storage, key, base=None):
    from storage_credentials import load_credentials
    public_base = load_credentials().get('public_url') or current_app.config.get('IDPN_PUBLIC_URL') or base
    if not public_base:
        # Finalization never changes the URL already embedded in a witness page.
        return ('https://' + storage['account'] + '.blob.core.windows.net/' + storage['target'] + '/' + quote(key, safe='/')) if storage['provider'] == 'azure' else ('https://s3.' + storage['region'] + '.amazonaws.com/' + storage['target'] + '/' + quote(key, safe='/'))
    payload = {'provider': storage['provider'], 'target': storage['target'], 'region': storage['region'], 'account': storage['account'], 'key': key}
    return public_base.rstrip('/') + '/documentos/descargar/' + signer().dumps(payload)


@storage_download.route('/documentos/descargar/<token>')
def public_download(token):
    try:
        from document_matrix import _storage
        from storage_engine import valid_path
        data = signer().loads(token)
        storage = _storage(data)
        key = data['key']
        if not valid_path(key) or any(part.startswith('.idpn') for part in key.split('/')):
            raise ValueError('Archivo inválido.')
        if storage['provider'] == 's3':
            url = f'https://s3.{storage["region"]}.amazonaws.com/{storage["target"]}/{quote(key, safe="/")}'
        else:
            url = f'https://{storage["account"]}.blob.core.windows.net/{storage["target"]}/{quote(key, safe="/")}'
        # Anonymous fetch: private objects are never exposed through server credentials.
        with urlopen(url, timeout=60) as response:
            content = response.read(LIMIT + 1)
        if len(content) > LIMIT:
            raise ValueError('Archivo demasiado grande.')
        return send_file(io.BytesIO(content), download_name=key.split('/')[-1], as_attachment=True)
    except (BadSignature, ValueError, KeyError, URLError, OSError):
        return jsonify(error='El archivo público no está disponible.'), 404
