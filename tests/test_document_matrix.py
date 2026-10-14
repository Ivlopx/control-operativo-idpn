"""Classification/versioning regressions, with all remote inventory calls mocked."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from document_matrix import default_matrix, plan_document, resolve_document_code, validate_matrix
from flask import Flask
from matrix_api import matrix_api


class MatrixTests(unittest.TestCase):
    def setUp(self):
        self.matrix = validate_matrix(default_matrix('ZHIPOS', zhipos=True))
        self.data = {'action': 'alta', 'scope': 'government', 'branch_id': 'control-de-no-conformes',
                     'type_id': 'procedimiento', 'number': '003', 'name': 'Control de no conformes', 'filename': 'original.PDF'}

    def test_baja_can_upload_reference_when_no_document_exists(self):
        self.data.update(action='baja')
        plan = plan_document(self.matrix, self.data, [])
        self.assertEqual(plan['version'], 'v001')
        self.assertIn('/BAJA/', plan['key'])
        self.assertEqual(plan['archive_operations'], [])

    def test_policy_uses_manual_area_without_code_or_abbreviations(self):
        data = {'scope':'specific','branch_id':'administracion','area_id':'recursos-humanos',
                'is_politica':True,'action':'alta','name':'Política de calidad','filename':'original.pdf'}
        plan = plan_document(self.matrix, data, [])
        self.assertEqual(plan['code'], '')
        self.assertTrue(plan['classification_directory'].endswith('/administracion/recursos-humanos/politicas'))
        self.assertEqual(plan['filename'], 'politica-de-calidad-v001.pdf')
        data['action'] = 'version'
        inventory = [{'key':plan['classification_directory']+'/BAJA/politica-de-calidad-v001.pdf'},
                     {'key':plan['classification_directory']+'/politica-de-calidad-v002.pdf'},
                     {'key':plan['classification_directory']+'/politica-ambiental-v009.pdf'}]
        next_plan = plan_document(self.matrix, data, inventory)
        self.assertEqual(next_plan['version'], 'v003')
        self.assertEqual(len(next_plan['archive_operations']), 1)

    def test_government_first_version(self):
        plan = plan_document(self.matrix, self.data, inventory=[])
        self.assertEqual(plan['code'], 'PG-D-CNO-003')
        self.assertEqual(plan['filename'], 'PG-D-CNO-003-control-de-no-conformes-v001.pdf')
        self.assertEqual(plan['key'], 'zhipos/procedimientos-de-gobierno/control-de-no-conformes/procedimientos/' + plan['filename'])

    def test_specific_human_resources(self):
        self.data.update(scope='specific', branch_id='administracion', area_id='recursos-humanos', type_id='formato')
        plan = plan_document(self.matrix, self.data, inventory=[])
        self.assertEqual(plan['code'], 'FE-A-RHS-003')
        self.assertIn('/administracion/recursos-humanos/formatos/', plan['key'])

    def test_existing_code_resolves_company_classification(self):
        matches = resolve_document_code(self.matrix, 'ie-a-rhs-003')
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['area_id'], 'recursos-humanos')
        self.assertEqual(matches[0]['number'], '003')

    def test_shared_formato_formulario_prefix_is_explicit(self):
        matches = resolve_document_code(self.matrix, 'FE-A-RHS-003')
        self.assertEqual({item['type_id'] for item in matches}, {'formato', 'formulario'})
        self.assertEqual(len(resolve_document_code(self.matrix, 'FE-A-RHS-003', 'formato')), 1)

    def test_code_from_a_different_company_is_not_guessed(self):
        with self.assertRaises(ValueError):
            resolve_document_code(default_matrix('Otra empresa'), 'FE-A-RHS-003')

    def test_all_government_names_and_specific_areas_are_present(self):
        self.assertEqual(len(self.matrix['government']), 9)
        self.assertEqual([len(item['areas']) for item in self.matrix['specific']], [2, 4, 4, 2])

    def test_missing_abbreviation_does_not_invent_a_code(self):
        self.data['branch_id'] = 'control-de-registros'
        with self.assertRaisesRegex(ValueError, 'asignar siglas'):
            plan_document(self.matrix, self.data)

    def test_formularios_have_codes_but_no_storage_or_version(self):
        self.data.update(type_id='formulario', filename='', version='v999')
        plan = plan_document(self.matrix, self.data, inventory=[])
        self.assertEqual(plan['code'], 'FG-D-CNO-003')
        self.assertFalse(plan['store_file'])
        self.assertIsNone(plan['directory'])
        self.assertIsNone(plan['filename'])
        self.assertIsNone(plan['key'])

    def test_without_inventory_version_stays_unassigned(self):
        plan = plan_document(self.matrix, self.data)
        self.assertIsNone(plan['version'])
        self.assertFalse(plan['inventory_verified'])
        self.assertIn('-vNNN.pdf', plan['filename_template'])

    def test_user_code_is_preserved_when_classification_is_corrected(self):
        self.data.update(code='FE-A-RHS-009',branch_id='control-de-documentos')
        plan=plan_document(self.matrix,self.data,[])
        self.assertEqual(plan['code'],'FE-A-RHS-009')
        self.assertTrue(plan['filename'].startswith('FE-A-RHS-009-'))
        self.assertIn('/control-de-documentos/procedimientos/',plan['key'])

    def test_supplied_code_does_not_require_abbreviations_to_generate_a_new_one(self):
        self.data.update(code='PG-D-REG-002',branch_id='control-de-registros')
        self.assertEqual(plan_document(self.matrix,self.data,[])['code'],'PG-D-REG-002')

    def test_new_version_uses_max_including_archived_files(self):
        self.data['action'] = 'version'
        base = plan_document(self.matrix, self.data)['classification_directory']
        inventory = [{'key': f'{base}/PG-D-CNO-003-otro-nombre-v001.pdf', 'etag': 'e1'},
                     {'key': f'{base}/BAJA/PG-D-CNO-003-otro-nombre-v007.xlsx'},
                     {'key': f'{base}/PG-D-CNO-003-otro-nombre-v003.docx', 'etag': 'e3'}]
        plan = plan_document(self.matrix, self.data, inventory)
        self.assertEqual(plan['version'], 'v008')
        self.assertEqual(len(plan['archive_operations']), 2)
        self.assertTrue(plan['archive_after_upload'])
        self.assertFalse(plan['overwrite'])
        self.assertEqual(plan['archive_operations'][0]['source_etag'], 'e1')
        self.assertIn('/BAJA/', plan['archive_operations'][0]['destination_key'])

    def test_manual_version_field_cannot_override_calculation(self):
        self.data['version'] = 'v999'
        self.assertEqual(plan_document(self.matrix, self.data, [])['version'], 'v001')

    def test_legacy_unversioned_file_is_preserved_in_baja(self):
        self.data['action'] = 'version'
        base = plan_document(self.matrix, self.data)['classification_directory']
        plan = plan_document(self.matrix, self.data, [{'key': f'{base}/PG-D-CNO-003-documento.pdf'}])
        self.assertEqual(plan['version'], 'v001')
        self.assertEqual(len(plan['archive_operations']), 1)
        self.assertTrue(plan['stored_versions'][0]['legacy'])

    def test_alta_rejects_existing_versions(self):
        base = plan_document(self.matrix, self.data)['classification_directory']
        with self.assertRaisesRegex(ValueError, 'Nueva versión'):
            plan_document(self.matrix, self.data, [{'key': f'{base}/BAJA/PG-D-CNO-003-name-v001.pdf'}])

    def test_version_rejects_a_code_with_no_files(self):
        self.data['action'] = 'version'
        with self.assertRaisesRegex(ValueError, 'Usa «Alta»'):
            plan_document(self.matrix, self.data, [])

    def test_baja_archives_all_active_versions_without_upload(self):
        base = plan_document(self.matrix, self.data)['classification_directory']
        self.data.update(action='baja', filename='', name='')
        plan = plan_document(self.matrix, self.data, [{'key': f'{base}/PG-D-CNO-003-file-v001.pdf'},
                {'key': f'{base}/PG-D-CNO-003-file-v002.pdf'}, {'key': f'{base}/BAJA/PG-D-CNO-003-file-v003.pdf'}])
        self.assertEqual(len(plan['archive_operations']), 2)
        self.assertIsNone(plan['filename'])
        self.assertEqual(plan['directory'], base + '/BAJA')

    def test_baja_without_active_versions_is_not_success(self):
        self.data.update(action='baja',filename='')
        with self.assertRaisesRegex(ValueError, 'No hay versiones activas'):
            plan_document(self.matrix, self.data, [])

    def test_similar_codes_and_other_directories_do_not_affect_version(self):
        base = plan_document(self.matrix, self.data)['classification_directory']
        plan = plan_document(self.matrix, self.data, [{'key': f'{base}/PG-D-CNO-0030-file-v900.pdf'},
                     {'key': f'{base}/otra-area/PG-D-CNO-003-file-v900.pdf'},
                     {'key': f'otro/{base}/PG-D-CNO-003-file-v900.pdf'}])
        self.assertEqual(plan['version'], 'v001')

    def test_areas_can_override_process_storage(self):
        self.matrix['storage'] = {'provider': 's3', 'target': 'company-files', 'root': 'zhipos'}
        process = self.matrix['specific'][2]
        process['storage'] = {'provider': 's3', 'target': 'admin-files', 'root': 'administracion'}
        process['areas'][1]['storage'] = {'provider': 'azure', 'target': 'recursos-humanos', 'root': 'rrhh'}
        self.data.update(scope='specific', branch_id='administracion', area_id='recursos-humanos', type_id='formato')
        plan = plan_document(self.matrix, self.data, [])
        self.assertEqual(plan['storage']['provider'], 'azure')
        self.assertEqual(plan['storage']['target'], 'recursos-humanos')
        self.assertTrue(plan['key'].startswith('rrhh/'))

    def test_added_process_and_area_create_their_paths(self):
        self.matrix['specific'].append({'id': 'sistemas', 'name': 'Sistemas', 'abbreviation': 'S',
                  'areas': [{'id': 'soporte', 'name': 'Soporte técnico', 'abbreviation': 'SOP'}]})
        self.data.update(scope='specific', branch_id='sistemas', area_id='soporte', type_id='instructivo')
        plan = plan_document(self.matrix, self.data, [])
        self.assertEqual(plan['code'], 'IE-S-SOP-003')
        self.assertIn('/sistemas/soporte-tecnico/instructivos/', plan['key'])

    def test_duplicate_abbreviations_and_unsafe_directories_are_rejected(self):
        bad = deepcopy(self.matrix)
        bad['government'][1]['abbreviation'] = 'CON'
        with self.assertRaises(ValueError):
            validate_matrix(bad)
        bad = deepcopy(self.matrix)
        bad['storage']['root'] = '../../escape'
        with self.assertRaises(ValueError):
            validate_matrix(bad)


class MatrixAPITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / 'zhipos-idpn.net.json').write_text(json.dumps({'site_name': 'ZHIPOS'}))
        (self.root / 'other.net.json').write_text(json.dumps({'site_name': 'Otra empresa'}))
        (self.root / 'matrices').mkdir()
        self.matrix = validate_matrix(default_matrix('ZHIPOS', zhipos=True))
        (self.root / 'matrices/zhipos-idpn.net.json').write_text(json.dumps(self.matrix))
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, IDPN_CONFIG_DIR=str(self.root))
        self.app.register_blueprint(matrix_api)
        self.client = self.app.test_client()
        self.data = {'site': 'zhipos-idpn.net', 'action': 'alta', 'scope': 'government', 'branch_id': 'control-de-no-conformes',
                     'type_id': 'procedimiento', 'number': '003', 'name': 'Documento', 'filename': 'original.pdf'}

    def tearDown(self):
        self.temporary.cleanup()

    def test_company_matrices_are_isolated(self):
        other = self.client.get('/api/matrices/other.net').get_json()
        self.assertEqual(other['government'], [])
        self.matrix['company'] = 'Empresa actualizada'
        self.assertEqual(self.client.put('/api/matrices/zhipos-idpn.net', json=self.matrix).status_code, 200)
        self.assertEqual(self.client.get('/api/matrices/other.net').get_json()['company'], 'Otra empresa')

    def test_rejected_save_preserves_existing_matrix(self):
        self.matrix['government'][0]['directory'] = '../escape'
        self.assertEqual(self.client.put('/api/matrices/zhipos-idpn.net', json=self.matrix).status_code, 400)
        self.assertEqual(self.client.get('/api/matrices/zhipos-idpn.net').get_json()['company'], 'ZHIPOS')

    def test_unknown_company_and_invalid_payload(self):
        self.assertEqual(self.client.get('/api/matrices/missing.net').status_code, 404)
        self.assertEqual(self.client.put('/api/matrices/zhipos-idpn.net', json=[]).status_code, 400)
        self.assertEqual(self.client.post('/api/document-plan', json=[]).status_code, 400)

    def test_inventory_from_client_is_not_trusted(self):
        self.data.update(inventory=[], version='v099')
        with patch('matrix_api.call_storage') as remote:
            plan = self.client.post('/api/document-plan', json=self.data).get_json()['plan']
            remote.assert_not_called()
        self.assertFalse(plan['inventory_verified'])
        self.assertIsNone(plan['version'])

    def configured_webhook(self):
        self.client.put('/api/storage-settings', json={'s3':{'access_key_id':'test-key','secret_access_key':'test-secret'}})
        self.matrix['storage'] = {'provider': 's3', 'target': 'company-files', 'root': 'zhipos'}
        self.client.put('/api/matrices/zhipos-idpn.net', json=self.matrix)

    def test_server_inventory_is_used_to_assign_first_version(self):
        self.configured_webhook()
        with patch('matrix_api.call_storage') as remote:
            remote.return_value = {'success':True,'complete':True,'files':[]}
            plan = self.client.post('/api/document-plan', json=self.data).get_json()['plan']
            sent = remote.call_args.args[0]
        self.assertEqual(plan['version'], 'v001')
        self.assertEqual(sent['operation'], 'inventory')
        self.assertTrue(sent['include_baja'])

    def test_incomplete_inventory_cannot_allocate_a_version(self):
        self.configured_webhook()
        with patch('matrix_api.call_storage') as remote:
            remote.return_value = {'success':True,'files':[]}
            result = self.client.post('/api/document-plan', json=self.data)
        self.assertEqual(result.status_code, 400)
        self.assertIn('complete', result.get_json()['error'])

    def test_formulario_never_calls_storage_inventory(self):
        self.configured_webhook()
        self.data.update(type_id='formulario', filename='')
        with patch('matrix_api.call_storage') as remote:
            plan = self.client.post('/api/document-plan', json=self.data).get_json()['plan']
            remote.assert_not_called()
        self.assertFalse(plan['store_file'])

    def test_cloud_settings_never_return_or_echo_secrets(self):
        response = self.client.put('/api/storage-settings', json={'s3':{'access_key_id':'test-key','secret_access_key':'test-secret'}})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('test-secret', response.get_data(as_text=True))
        self.assertNotIn('test-key', response.get_data(as_text=True))
        self.assertTrue(self.client.get('/api/storage-settings').get_json()['s3']['configured'])
        self.client.put('/api/storage-settings', json={'s3':{'secret_access_key':''}})
        self.assertTrue(self.client.get('/api/storage-settings').get_json()['s3']['configured'])
        self.assertEqual((self.root/'integrations/storage.json').stat().st_mode & 0o777, 0o600)

    def test_storage_targets_come_from_configured_workflow(self):
        self.client.put('/api/storage-settings', json={'s3':{'access_key_id':'test-key','secret_access_key':'test-secret'}})
        with patch('matrix_api.call_storage') as remote:
            remote.return_value = {'success':True,'complete':True,'targets':[{'name':'company-files'}]}
            response = self.client.post('/api/storage-targets', json={'storage':{'provider':'s3','region':'us-east-1'}})
            payload = remote.call_args.args[0]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['operation'], 'destinations')


if __name__ == '__main__':
    unittest.main()
