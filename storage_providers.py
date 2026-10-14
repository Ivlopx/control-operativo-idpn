"""Conditional cloud operations using the official AWS and Azure Python SDKs."""
import base64
import hashlib
from urllib.parse import quote
from storage_credentials import require_credentials

LIMIT = 15 * 1024 * 1024


class StorageError(ValueError):
    def __init__(self, status, message='No se pudo completar la operación de almacenamiento.'):
        self.status = status
        super().__init__(message)


class S3Storage:
    def __init__(self, storage, credentials):
        import boto3
        from botocore.config import Config
        self.storage = storage
        self.client = boto3.client('s3', region_name=storage['region'],
            aws_access_key_id=credentials['access_key_id'], aws_secret_access_key=credentials['secret_access_key'],
            aws_session_token=credentials.get('session_token') or None,
            config=Config(connect_timeout=10, read_timeout=60, retries={'max_attempts': 3, 'mode': 'standard'}))
        self.target = storage.get('target')

    def call(self, method, **kwargs):
        from botocore.exceptions import ClientError, BotoCoreError
        try:
            return getattr(self.client, method)(**kwargs)
        except ClientError as error:
            raise StorageError(error.response.get('ResponseMetadata', {}).get('HTTPStatusCode', 502)) from error
        except BotoCoreError as error:
            raise StorageError(502) from error

    def targets(self):
        result = []
        token = None
        while True:
            page = self.call('list_buckets', **({'ContinuationToken': token} if token else {}))
            result.extend({'name': item['Name']} for item in page.get('Buckets', []))
            next_token = page.get('ContinuationToken')
            if not next_token:
                return result
            if next_token == token:
                raise StorageError(502, 'Paginación repetida.')
            token = next_token

    def inventory(self, prefix):
        result, token = [], None
        while True:
            page = self.call('list_objects_v2', Bucket=self.target, Prefix=prefix,
                             **({'ContinuationToken': token} if token else {}))
            result.extend({'key': item['Key'], 'etag': item['ETag'], 'size': item['Size']} for item in page.get('Contents', []))
            if not page.get('IsTruncated'):
                return result
            next_token = page.get('NextContinuationToken')
            if not next_token or next_token == token:
                raise StorageError(502, 'Inventario incompleto.')
            token = next_token

    def get(self, key, etag=None):
        response = self.call('get_object', Bucket=self.target, Key=key, **({'IfMatch': etag} if etag else {}))
        stream = response['Body']
        try:
            data = stream.read(LIMIT + 1)
        finally:
            stream.close()
        if len(data) > LIMIT:
            raise StorageError(413, 'Un archivo excede 15 MiB; se conservó el original.')
        return data, response['ETag']

    def put(self, key, data, *, etag=None, metadata=None, mime='application/json'):
        response = self.call('put_object', Bucket=self.target, Key=key, Body=data,
            ContentMD5=base64.b64encode(hashlib.md5(data).digest()).decode(),
            ContentType=mime, ContentDisposition='inline', Metadata=metadata or {},
            **({'IfMatch': etag} if etag else {'IfNoneMatch': '*'}))
        return response['ETag']

    def delete(self, key, etag):
        self.call('delete_object', Bucket=self.target, Key=key, IfMatch=etag)

    def public_url(self, key):
        return f'https://s3.{self.storage["region"]}.amazonaws.com/{self.target}/{quote(key, safe="/")}'


class AzureStorage:
    def __init__(self, storage, credentials):
        from azure.storage.blob import BlobServiceClient
        self.storage = storage
        self.client = BlobServiceClient(f'https://{storage["account"]}.blob.core.windows.net', credential=credentials['account_key'],
                                        connection_timeout=10, read_timeout=60, retry_total=3)
        self.target = storage.get('target')

    def call(self, fn, **kwargs):
        from azure.core.exceptions import AzureError
        try:
            return fn(**kwargs)
        except AzureError as error:
            raise StorageError(getattr(error, 'status_code', None) or 502) from error

    def blob(self, key):
        return self.client.get_blob_client(self.target, key)

    def targets(self):
        return self.call(lambda: [{'name': item.name} for item in self.client.list_containers()])

    def inventory(self, prefix):
        return self.call(lambda: [{'key': item.name, 'etag': item.etag, 'size': item.size}
            for item in self.client.get_container_client(self.target).list_blobs(name_starts_with=prefix)])

    def get(self, key, etag=None):
        from azure.core import MatchConditions
        blob = self.blob(key)
        props = self.call(blob.get_blob_properties)
        if props.size > LIMIT:
            raise StorageError(413, 'Un archivo excede 15 MiB; se conservó el original.')
        expected = etag or props.etag
        download = self.call(blob.download_blob, etag=expected, match_condition=MatchConditions.IfNotModified)
        data = self.call(download.readall)
        if len(data) > LIMIT:
            raise StorageError(413, 'Archivo demasiado grande.')
        return data, expected

    def put(self, key, data, *, etag=None, metadata=None, mime='application/json'):
        from azure.core import MatchConditions
        from azure.storage.blob import ContentSettings
        kwargs = {'etag': etag, 'match_condition': MatchConditions.IfNotModified} if etag else {}
        response = self.call(self.blob(key).upload_blob, data=data, overwrite=bool(etag), metadata={k.replace('-', '_'):v for k,v in (metadata or {}).items()},
            content_settings=ContentSettings(content_type=mime, content_disposition='inline', content_md5=hashlib.md5(data).digest()),
            validate_content=True, **kwargs)
        return response['etag']

    def delete(self, key, etag):
        from azure.core import MatchConditions
        self.call(self.blob(key).delete_blob, etag=etag, match_condition=MatchConditions.IfNotModified)

    def public_url(self, key):
        return f'https://{self.storage["account"]}.blob.core.windows.net/{self.target}/{quote(key, safe="/")}'


def make_storage(storage):
    credentials = require_credentials(storage)
    try:
        return (S3Storage if storage['provider'] == 's3' else AzureStorage)(storage, credentials)
    except ImportError as error:
        raise ValueError('Instala las dependencias de almacenamiento de requirements.txt y reinicia la aplicación.') from error
