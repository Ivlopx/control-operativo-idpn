"""Native lifecycle tests: cloud effects are simulated, never production writes."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from flask import Flask
from storage_engine import execute_storage
from storage_providers import StorageError
from storage_download import storage_download, download_url
from storage_credentials import save_credentials, require_credentials


class MemoryStorage:
    def __init__(self):
        self.objects = {}
        self.sequence = 0
        self.fail_delete = False
        self.fail_archive = False
        self.corrupt_archive = False
        self.change_source = False

    def inventory(self, prefix):
        return [{'key': key, 'etag': etag, 'size': len(data)} for key, (data, etag) in self.objects.items() if key.startswith(prefix)]

    def targets(self):
        return [{'name': 'documents'}]

    def get(self, key, etag=None):
        if key not in self.objects:
            raise StorageError(404)
        data, actual = self.objects[key]
        if etag and actual != etag:
            raise StorageError(412)
        return data, actual

    def put(self, key, data, *, etag=None, **kwargs):
        if etag:
            if key not in self.objects or self.objects[key][1] != etag:
                raise StorageError(412)
        elif key in self.objects:
            raise StorageError(412)
        if '/baja/' in key:
            if self.fail_archive:
                raise StorageError(500)
            if self.corrupt_archive:
                data = b'corrupted'
        self.sequence += 1
        self.objects[key] = (data, str(self.sequence))
        return str(self.sequence)

    def delete(self, key, etag):
        if '.idpn' not in key and self.fail_delete:
            raise StorageError(500)
        if '.idpn' not in key and self.change_source:
            raise StorageError(412)
        self.get(key, etag)
        del self.objects[key]

    def public_url(self, key):
        return 'https://files.example.test/' + key


class NativeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = Flask(__name__)
        self.app.config['IDPN_CONFIG_DIR'] = str(self.root)
        self.app.register_blueprint(storage_download)
        self.ctx = self.app.app_context(); self.ctx.push()
        self.cloud = MemoryStorage()
        self.file = self.root / 'test.pdf'; self.file.write_bytes(b'original')
        self.meta = {'operation':'document','operation_id':'operation-1234567890','action':'alta','code':'PG-D-CON-001',
            'name':'Control Documental','type_id':'procedimiento','directory':'company/government/control/procedimientos',
            'storage':{'provider':'s3','target':'documents','region':'us-east-1'}, 'download_base_url':'https://app.example.test'}

    def tearDown(self):
        self.ctx.pop(); self.tmp.cleanup()

    def run_op(self, **changes):
        return execute_storage({**self.meta, **changes}, str(self.file), self.cloud)

    def active_keys(self):
        return [k for k in self.cloud.objects if '.idpn' not in k and '/baja/' not in k]

    def test_creates_directory_and_first_version_and_retry_is_identical(self):
        first = self.run_op()
        again = self.run_op()
        self.assertEqual(first, again)
        self.assertEqual(first['version'], 'v001')
        self.assertEqual(len(self.active_keys()), 1)
        self.assertEqual(first['download_url'], first['url'])

    def test_version_moves_original_only_after_verified_upload(self):
        first = self.run_op(); self.file.write_bytes(b'new')
        second = self.run_op(action='version', operation_id='operation-2234567890')
        self.assertEqual(second['version'], 'v002')
        self.assertNotIn(first['key'], self.cloud.objects)
        self.assertEqual(self.cloud.get(second['archived'][0]['key'])[0], b'original')

    def test_two_stage_baja_preserves_active_then_deduplicates_against_witness(self):
        first = self.run_op()
        witness = self.run_op(action='baja', defer_archive=True, operation_id='operation-2234567890')
        self.assertIn(first['key'], self.cloud.objects)
        self.assertIn('/baja/', witness['key'])
        reference = {k:witness[k] for k in ['key','sha256','size','version']}
        final = execute_storage({**self.meta, 'action':'finalizar_baja','operation_id':'final-operation-1234567890', 'reference':reference}, provider=self.cloud)
        again = execute_storage({**self.meta, 'action':'finalizar_baja','operation_id':'final-operation-1234567890', 'reference':reference}, provider=self.cloud)
        self.assertEqual(final, again)
        self.assertEqual(self.active_keys(), [])
        self.assertEqual([k for k in self.cloud.objects if '/baja/' in k], [witness['key']])

    def test_changed_witness_blocks_all_archival(self):
        first = self.run_op()
        witness = self.run_op(action='baja', defer_archive=True, operation_id='operation-2234567890')
        reference = {k:witness[k] for k in ['key','sha256','size','version']}
        self.cloud.objects[witness['key']] = (b'changed', 'newetag')
        with self.assertRaises(ValueError):
            execute_storage({**self.meta, 'action':'finalizar_baja', 'operation_id':'final-operation-1234567890', 'reference':reference}, provider=self.cloud)
        self.assertIn(first['key'], self.cloud.objects)

    def test_conflicting_archive_preserves_original(self):
        first = self.run_op()
        archive = self.meta['directory']+'/baja/'+first['filename']
        self.cloud.put(archive, b'other')
        self.file.write_bytes(b'new')
        with self.assertRaises(ValueError):
            self.run_op(action='version',operation_id='operation-2234567890')
        self.assertIn(first['key'], self.cloud.objects)

    def test_failed_archive_preserves_original_and_retries_same_version(self):
        first = self.run_op(); self.file.write_bytes(b'new'); self.cloud.fail_archive = True
        with self.assertRaises(StorageError): self.run_op(action='version', operation_id='operation-2234567890')
        self.assertIn(first['key'], self.cloud.objects)
        self.cloud.fail_archive = False
        result = self.run_op(action='version', operation_id='operation-2234567890')
        self.assertEqual(result['version'], 'v002')
        self.assertEqual(len(self.active_keys()), 1)

    def test_failed_delete_resumes_without_overwriting_archive(self):
        first = self.run_op(); self.file.write_bytes(b'new'); self.cloud.fail_delete = True
        with self.assertRaises(StorageError): self.run_op(action='version', operation_id='operation-2234567890')
        self.assertIn(first['key'], self.cloud.objects)
        self.cloud.fail_delete = False
        self.assertEqual(self.run_op(action='version', operation_id='operation-2234567890')['version'], 'v002')
        self.assertNotIn(first['key'], self.cloud.objects)

    def test_checksum_failure_preserves_source(self):
        first = self.run_op(); self.file.write_bytes(b'new'); self.cloud.corrupt_archive = True
        with self.assertRaises(ValueError): self.run_op(action='version', operation_id='operation-2234567890')
        self.assertIn(first['key'], self.cloud.objects)

    def test_changed_source_etag_is_never_deleted(self):
        first = self.run_op(); self.file.write_bytes(b'new'); self.cloud.change_source = True
        with self.assertRaises(StorageError): self.run_op(action='version', operation_id='operation-2234567890')
        self.assertIn(first['key'], self.cloud.objects)

    def test_policy_versions_are_independent_and_use_no_code(self):
        first = self.run_op(code='', name='Política de Calidad', is_politica=True)
        second = self.run_op(code='', name='Política Comercial', is_politica=True, operation_id='operation-2234567890')
        self.assertEqual((first['version'], second['version']), ('v001','v001'))
        self.assertTrue(first['filename'].startswith('politica-de-calidad-'))

    def test_competing_lock_blocks_upload(self):
        key = self.meta['directory']+'/.idpn-lock-'+self.meta['code']+'.json'
        self.cloud.put(key, b'locked')
        with self.assertRaisesRegex(ValueError, 'bloqueo'):
            self.run_op()
        self.assertEqual(self.active_keys(), [])

    def test_reused_operation_with_changed_content_is_rejected(self):
        self.run_op(); self.file.write_bytes(b'other')
        with self.assertRaisesRegex(ValueError, 'otra operación'):
            self.run_op()

    def test_public_download_uses_anonymous_fetch_and_rejects_forged_token(self):
        result = self.run_op()
        legacy_url = download_url({**self.meta['storage'],'account':''}, result['key'], 'https://app.example.test')
        path = legacy_url.split('https://app.example.test')[1]
        with patch('storage_download.urlopen') as remote:
            remote.return_value.__enter__.return_value.read.return_value = b'original'
            response = self.app.test_client().get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('attachment', response.headers['Content-Disposition'])
            self.assertIsInstance(remote.call_args.args[0], str)
        self.assertEqual(self.app.test_client().get(path+'forged').status_code, 404)

    def test_account_change_requires_new_key_and_credentials_never_leave_status(self):
        status = save_credentials({'azure':{'account':'firstaccount','account_key':'secret'}})
        self.assertNotIn('secret', json.dumps(status))
        with self.assertRaises(ValueError): save_credentials({'azure':{'account':'secondaccount'}})
        with self.assertRaises(ValueError): require_credentials({'provider':'azure','account':'otheraccount'})

    def test_azure_and_s3_share_lifecycle_protocol(self):
        self.meta['storage'] = {'provider':'azure','target':'documents','account':'companyaccount'}
        first = self.run_op(); self.file.write_bytes(b'new')
        second = self.run_op(action='version', operation_id='operation-2234567890')
        self.assertEqual(second['version'], 'v002')
        self.assertNotIn(first['key'], self.cloud.objects)


if __name__ == '__main__': unittest.main()
