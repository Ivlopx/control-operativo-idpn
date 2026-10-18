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
    def test_directory_normalization_is_idempotent_and_keeps_ids(self):
        matrix = default_matrix('Mi Empresa', zhipos=True)
        matrix['specific'][2]['areas'][1]['directory'] = 'Recursos Humanos'
        normalized = validate_matrix(matrix)
        self.assertEqual(normalized['storage']['root'], 'miEmpresa')
        area = normalized['specific'][2]['areas'][1]
        self.assertEqual(area['directory'], 'recursosHumanos')
        self.assertEqual(area['id'], 'recursos-humanos')
        self.assertEqual(validate_matrix(normalized), normalized)

    def setUp(self):
        for target in ('document_stamp.publication_day', 'document_matrix.publication_day'):
            day_patch = patch(target, return_value='2026-10-18')
            day_patch.start(); self.addCleanup(day_patch.stop)
        self.matrix = validate_matrix(default_matrix('ZHIPOS', zhipos=True))
        self.data = {'action': 'alta', 'scope': 'government', 'branch_id': 'control-de-no-conformes',
                     'type_id': 'procedimiento', 'number': '003', 'name': 'Control de no conformes', 'filename': 'original.PDF'}

    def test_optional_legacy_layout_reuses_root_for_both_scopes_and_versions(self):
        self.matrix['omit_scope_directory'] = True
        self.matrix['storage']['root'] = ''
        government = plan_document(self.matrix, self.data, [])
        self.assertEqual(government['classification_directory'], 'controlDeNoConformes/procedimientos')
        previous = government['classification_directory'] + '/PG-D-CNO-003-anterior-v003.pdf'
        version = plan_document(self.matrix, {**self.data, 'action':'version'}, [{'key':previous}])
        self.assertEqual(version['version'], '2026-10-18')
        self.assertEqual(version['archive_operations'][0]['destination_key'], government['classification_directory']+'/baja/PG-D-CNO-003-anterior-v003.pdf')
        specific = {**self.data, 'scope':'specific', 'branch_id':'administracion', 'area_id':'recursos-humanos', 'type_id':'formato'}
        planned = plan_document(self.matrix, specific, [])
        self.assertEqual(planned['classification_directory'], 'administracion/recursosHumanos/formatos')
        policy = plan_document(self.matrix, {**specific, 'is_politica':True, 'action':'baja'}, [])
        self.assertTrue(policy['key'].startswith('administracion/recursosHumanos/politicas/baja/'))
        self.matrix['storage']['root'] = 'documentos'
        self.assertTrue(plan_document(self.matrix, self.data, [])['key'].startswith('documentos/controlDeNoConformes/'))
        self.matrix['omit_scope_directory'] = 'false'
        with self.assertRaises(ValueError):
            validate_matrix(self.matrix)

    def test_manuals_go_directly_to_their_folder_regardless_of_legacy_layout(self):
        data = {'scope':'manuals','code':'mg-003','name':'Manual del Sistema','filename':'manual.pdf','action':'alta'}
        for omit in (False, True):
            self.matrix['omit_scope_directory'] = omit
            planned = plan_document(self.matrix, data, [])
            self.assertEqual(planned['classification_directory'], 'zhipos/manualesDelSistemaDeGestion')
            self.assertEqual(planned['type_id'], 'manual')
            self.assertIsNone(planned['branch_id'])
            self.assertEqual(planned['code'], 'MG-003')
            self.assertEqual(planned['version'], '2026-10-18')
            next_version = plan_document(self.matrix, {**data, 'action':'version'}, [{'key':planned['key']}])
            self.assertEqual(next_version['version'], '2026-10-18-2')
            baja = plan_document(self.matrix, {**data,'action':'baja'}, [])
            self.assertIn('/manualesDelSistemaDeGestion/baja/', baja['key'])
        self.matrix['storage']['root'] = ''
        self.assertEqual(plan_document(self.matrix, data, [])['classification_directory'], 'manualesDelSistemaDeGestion')
        self.assertEqual(plan_document(self.matrix, {**data,'code':'OTRO-009'}, [])['code'], 'OTRO-009')

    def test_manual_code_detection_uses_the_company_prefix(self):
        result = resolve_document_code(self.matrix,'mg-003')
        self.assertEqual(result[0]['scope'], 'manuals')
        self.assertEqual(result[0]['type_id'], 'manual')
        self.assertEqual(result[0]['number'], '003')
        self.matrix['manual_directory'] = 'Manuales Corporativos'
        self.matrix['omit_scope_directory'] = True
        planned = plan_document(self.matrix, {'scope':'manuals','code':'MG-001','name':'Manual','filename':'file.pdf'}, [])
        self.assertEqual(planned['classification_directory'], 'zhipos/manualesCorporativos')
        self.matrix['manual_directory'] = '../invalid'
        with self.assertRaises(ValueError):
            validate_matrix(self.matrix)
        self.matrix['manual_directory'] = 'manualesDelSistemaDeGestion'
        self.matrix['manual_prefix'] = 'MSG'
        self.assertEqual(resolve_document_code(self.matrix,'MSG-001')[0]['scope'], 'manuals')
        with self.assertRaises(ValueError):
            resolve_document_code(self.matrix,'MG-001')

    def test_main_directories_are_configurable_and_omit_switch_still_applies(self):
        self.matrix.update(government_directory='Gobierno Corporativo', specific_directory='Procesos de Empresa')
        planned = plan_document(self.matrix, self.data, [])
        self.assertEqual(planned['classification_directory'], 'zhipos/gobiernoCorporativo/controlDeNoConformes/procedimientos')
        specific = {**self.data, 'scope':'specific','branch_id':'administracion','area_id':'recursos-humanos','type_id':'formato'}
        self.assertEqual(plan_document(self.matrix,specific,[])['classification_directory'], 'zhipos/procesosDeEmpresa/administracion/recursosHumanos/formatos')
        self.matrix['omit_scope_directory'] = True
        self.assertEqual(plan_document(self.matrix,specific,[])['classification_directory'], 'zhipos/administracion/recursosHumanos/formatos')
        self.assertTrue(plan_document(self.matrix,self.data,[])['classification_directory'].startswith('zhipos/controlDeNoConformes/'))
        self.matrix['government_directory'] = '../escape'
        with self.assertRaises(ValueError):
            validate_matrix(self.matrix)

    def test_baja_can_upload_reference_when_no_document_exists(self):
        self.data.update(action='baja')
        plan = plan_document(self.matrix, self.data, [])
        self.assertEqual(plan['version'], '2026-10-18')
        self.assertIn('/baja/', plan['key'])
        self.assertEqual(plan['archive_operations'], [])

    def test_policy_uses_manual_area_without_code_or_abbreviations(self):
        data = {'scope':'specific','branch_id':'administracion','area_id':'recursos-humanos',
                'is_politica':True,'action':'alta','name':'Política de calidad','filename':'original.pdf'}
        plan = plan_document(self.matrix, data, [])
        self.assertEqual(plan['code'], '')
        self.assertTrue(plan['classification_directory'].endswith('/administracion/recursosHumanos/politicas'))
        self.assertEqual(plan['filename'], 'politica-de-calidad-2026-10-18.pdf')
        data['action'] = 'version'
        inventory = [{'key':plan['classification_directory']+'/baja/politica-de-calidad-v001.pdf'},
                     {'key':plan['classification_directory']+'/politica-de-calidad-v002.pdf'},
                     {'key':plan['classification_directory']+'/politica-ambiental-v009.pdf'}]
        next_plan = plan_document(self.matrix, data, inventory)
        self.assertEqual(next_plan['version'], '2026-10-18')
        self.assertEqual(len(next_plan['archive_operations']), 1)

    def test_government_first_version(self):
        plan = plan_document(self.matrix, self.data, inventory=[])
        self.assertEqual(plan['code'], 'PG-D-CNO-003')
        self.assertEqual(plan['filename'], 'PG-D-CNO-003-control-de-no-conformes-2026-10-18.pdf')
        self.assertEqual(plan['key'], 'zhipos/procedimientosDeGobierno/controlDeNoConformes/procedimientos/' + plan['filename'])

    def test_specific_human_resources(self):
        self.data.update(scope='specific', branch_id='administracion', area_id='recursos-humanos', type_id='formato')
        plan = plan_document(self.matrix, self.data, inventory=[])
        self.assertEqual(plan['code'], 'FE-A-RHS-003')
        self.assertIn('/administracion/recursosHumanos/formatos/', plan['key'])

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
        self.assertIn('-2026-10-18.pdf', plan['filename_template'])

    def test_user_code_is_preserved_when_classification_is_corrected(self):
        self.data.update(code='FE-A-RHS-009',branch_id='control-de-documentos')
        plan=plan_document(self.matrix,self.data,[])
        self.assertEqual(plan['code'],'FE-A-RHS-009')
        self.assertTrue(plan['filename'].startswith('FE-A-RHS-009-'))
        self.assertIn('/controlDeDocumentos/procedimientos/',plan['key'])

    def test_supplied_code_does_not_require_abbreviations_to_generate_a_new_one(self):
        self.data.update(code='PG-D-REG-002',branch_id='control-de-registros')
        self.assertEqual(plan_document(self.matrix,self.data,[])['code'],'PG-D-REG-002')

    def test_new_version_uses_max_including_archived_files(self):
        self.data['action'] = 'version'
        base = plan_document(self.matrix, self.data)['classification_directory']
        inventory = [{'key': f'{base}/PG-D-CNO-003-otro-nombre-v001.pdf', 'etag': 'e1'},
                     {'key': f'{base}/baja/PG-D-CNO-003-otro-nombre-v007.xlsx'},
                     {'key': f'{base}/PG-D-CNO-003-otro-nombre-v003.docx', 'etag': 'e3'}]
        plan = plan_document(self.matrix, self.data, inventory)
        self.assertEqual(plan['version'], '2026-10-18')
        self.assertEqual(len(plan['archive_operations']), 2)
        self.assertTrue(plan['archive_after_upload'])
        self.assertFalse(plan['overwrite'])
        self.assertEqual(plan['archive_operations'][0]['source_etag'], 'e1')
        self.assertIn('/baja/', plan['archive_operations'][0]['destination_key'])

    def test_date_suffix_counts_archived_files_and_other_names_for_same_code(self):
        base = plan_document(self.matrix, self.data)['classification_directory']
        self.data['action'] = 'version'
        inventory = [{'key': base + '/PG-D-CNO-003-anterior-v017.pdf'},
                     {'key': base + '/baja/PG-D-CNO-003-otro-2026-10-18-9.xlsx'},
                     {'key': base + '/PG-D-CNO-0030-otro-2026-10-18-99.pdf'}]
        plan = plan_document(self.matrix, self.data, inventory)
        self.assertEqual(plan['version'], '2026-10-18-10')
        self.assertTrue(plan['filename'].endswith('-2026-10-18-10.pdf'))
        self.assertEqual(len(plan['archive_operations']), 1)

    def test_manual_version_field_cannot_override_calculation(self):
        self.data['version'] = 'v999'
        self.assertEqual(plan_document(self.matrix, self.data, [])['version'], '2026-10-18')

    def test_legacy_unversioned_file_is_preserved_in_baja(self):
        self.data['action'] = 'version'
        base = plan_document(self.matrix, self.data)['classification_directory']
        plan = plan_document(self.matrix, self.data, [{'key': f'{base}/PG-D-CNO-003-documento.pdf'}])
        self.assertEqual(plan['version'], '2026-10-18')
        self.assertEqual(len(plan['archive_operations']), 1)
        self.assertTrue(plan['stored_versions'][0]['legacy'])

    def test_alta_rejects_existing_versions(self):
        base = plan_document(self.matrix, self.data)['classification_directory']
        with self.assertRaisesRegex(ValueError, 'Cambio'):
            plan_document(self.matrix, self.data, [{'key': f'{base}/baja/PG-D-CNO-003-name-v001.pdf'}])

    def test_version_accepts_document_published_before_using_the_app(self):
        self.data['action'] = 'version'
        plan = plan_document(self.matrix, self.data, [])
        self.assertEqual(plan['action'], 'version')
        self.assertEqual(plan['version'], '2026-10-18')
        self.assertEqual(plan['archive_operations'], [])
        self.assertTrue(plan['key'].endswith('-2026-10-18.pdf'))
        self.assertFalse(plan['overwrite'])

    def test_baja_archives_all_active_versions_without_upload(self):
        base = plan_document(self.matrix, self.data)['classification_directory']
        self.data.update(action='baja', filename='', name='')
        plan = plan_document(self.matrix, self.data, [{'key': f'{base}/PG-D-CNO-003-file-v001.pdf'},
                {'key': f'{base}/PG-D-CNO-003-file-v002.pdf'}, {'key': f'{base}/baja/PG-D-CNO-003-file-v003.pdf'}])
        self.assertEqual(len(plan['archive_operations']), 2)
        self.assertIsNone(plan['filename'])
        self.assertEqual(plan['directory'], base + '/baja')

    def test_baja_without_active_versions_is_not_success(self):
        self.data.update(action='baja',filename='')
        with self.assertRaisesRegex(ValueError, 'No hay versiones activas'):
            plan_document(self.matrix, self.data, [])

    def test_similar_codes_and_other_directories_do_not_affect_version(self):
        base = plan_document(self.matrix, self.data)['classification_directory']
        plan = plan_document(self.matrix, self.data, [{'key': f'{base}/PG-D-CNO-0030-file-v900.pdf'},
                     {'key': f'{base}/otra-area/PG-D-CNO-003-file-v900.pdf'},
                     {'key': f'otro/{base}/PG-D-CNO-003-file-v900.pdf'}])
        self.assertEqual(plan['version'], '2026-10-18')

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
        self.assertIn('/sistemas/soporteTecnico/instructivos/', plan['key'])

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
        self.matrix['omit_scope_directory'] = True
        self.matrix['company'] = 'Empresa actualizada'
        self.assertEqual(self.client.put('/api/matrices/zhipos-idpn.net', json=self.matrix).status_code, 200)
        self.assertEqual(self.client.get('/api/matrices/other.net').get_json()['company'], 'Otra empresa')
        self.assertTrue(self.client.get('/api/matrices/zhipos-idpn.net').get_json()['omit_scope_directory'])
        self.assertFalse(self.client.get('/api/matrices/other.net').get_json()['omit_scope_directory'])
        self.assertEqual(self.client.get('/api/matrices/zhipos-idpn.net').get_json()['company'], 'ZHIPOS')
        (self.root / 'zhipos-idpn.net.json').write_text(json.dumps({'site_name':'Empresa renombrada en WordPress'}))
        self.assertEqual(self.client.get('/api/matrices/zhipos-idpn.net').get_json()['company'], 'Empresa renombrada en WordPress')
        saved = self.client.put('/api/matrices/zhipos-idpn.net', json=self.matrix).get_json()
        self.assertEqual(saved['matrix']['company'], 'Empresa renombrada en WordPress')

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
        self.assertEqual(plan['version'], '2026-10-18')
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
