"""Primary-process transfers are mocked: no WordPress or cloud calls."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app as application
from document_matrix import default_matrix, validate_matrix
from document_transfer import transfer_document


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.original = dict(application.app.config)
        application.app.config.update(IDPN_AUTH_ENABLED=False,IDPN_CONFIG_DIR=str(self.root),IDPN_UPLOAD_DIR=str(self.root/'uploads'),
            N8N_DOCUMENTS_WEBHOOK_URL='https://n8n.example.test/webhook/idpn-documentos',N8N_DOCUMENTS_WEBHOOK_TOKEN='mock-token')
        from storage_credentials import save_credentials
        with application.app.app_context():
            save_credentials({'s3':{'access_key_id':'test-key','secret_access_key':'test-secret'}})
        application.is_running = False
        config = {'site_name':'ZHIPOS','base_url':'https://example.test','login_url':'https://example.test',
                  'create_page_url':'https://example.test/new','cognito_script':''}
        (self.root/'zhipos-idpn.net.json').write_text(json.dumps(config))
        matrix = validate_matrix(default_matrix('ZHIPOS',zhipos=True))
        matrix['storage'].update(provider='s3',target='company-files',region='us-east-1')
        (self.root/'matrices').mkdir()
        (self.root/'matrices/zhipos-idpn.net.json').write_text(json.dumps(matrix))
        self.classification = {'scope':'government','branch_id':'control-de-no-conformes','type_id':'procedimiento','number':'003','action':'alta'}
        self.client = application.app.test_client()

    def tearDown(self):
        application.app.config.clear()
        application.app.config.update(self.original)
        application.is_running = False
        self.temporary.cleanup()

    def payload(self):
        return {'site':'zhipos-idpn.net','process_type':'create','folio':'268123','nombre':'Control de no conformes',
                'codigo':'FE-A-RHS-003','embed_type':'document','is_politica':'false',
                'classification':json.dumps(self.classification),'operation_id':'operation-1234567890',
                'file':(io.BytesIO(b'%PDF-1.4 test'),'original.pdf')}

    def test_primary_process_accepts_manual_without_branch_area_or_type(self):
        payload = self.payload()
        payload.update(codigo='MG-003', nombre='Manual del sistema', classification=json.dumps({'scope':'manuals','action':'alta'}))
        with patch('app.threading.Thread') as thread:
            response = self.client.post('/api/run', data=payload, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200, response.get_json())
        page_data = thread.call_args.kwargs['args'][2]
        self.assertEqual(page_data['document_plan']['classification_directory'], 'zhipos/manualesDelSistemaDeGestion')
        self.assertEqual(page_data['document_plan']['type_id'], 'manual')
        self.assertEqual(page_data['codigo'], 'MG-003')

    def test_primary_process_preserves_user_code_and_saves_file_temporarily(self):
        with patch('app.threading.Thread') as thread:
            response = self.client.post('/api/run',data=self.payload(),content_type='multipart/form-data')
        self.assertEqual(response.status_code,200,response.get_json())
        page_data = thread.call_args.kwargs['args'][2]
        self.assertEqual(page_data['codigo'],'FE-A-RHS-003')
        self.assertIn('/controlDeNoConformes/procedimientos',page_data['document_plan']['classification_directory'])
        self.assertFalse(page_data['is_politica'])
        self.assertEqual(Path(page_data['temporary_file']).read_bytes(),b'%PDF-1.4 test')
        self.assertEqual(page_data['document_plan']['site'],'zhipos-idpn.net')

    def test_web_form_bypasses_matrix_and_storage_and_preserves_code(self):
        (self.root/'matrices/zhipos-idpn.net.json').unlink()
        application.app.config['N8N_DOCUMENTS_WEBHOOK_URL'] = ''
        payload = {'site':'zhipos-idpn.net', 'process_type':'create', 'folio':'268123',
                   'codigo':'MI-CODIGO-LIBRE', 'nombre':'Encuesta',
                   'enlace':'https://example.test/formulario', 'embed_type':'form',
                   'classification':'invalid obsolete classification'}
        with patch('app.threading.Thread') as thread:
            response = self.client.post('/api/run', json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        args = thread.call_args.kwargs['args']
        self.assertNotIn('document_plan', args[2])
        self.assertNotIn('temporary_file', args[2])
        with patch('app.transfer_document') as storage, patch('app.run_wp_automation', return_value={'url':'https://example.test/pagina'}) as wp:
            application.run_in_background(*args)
            storage.assert_not_called()
        self.assertEqual(wp.call_args.args[2]['codigo'], 'MI-CODIGO-LIBRE')
        self.assertEqual(wp.call_args.args[2]['enlace'], payload['enlace'])

    def test_policy_upload_has_no_code_and_uses_area_directory(self):
        payload = self.payload()
        payload.update(codigo='', is_politica='true', nombre='Política de calidad',
            classification=json.dumps({'scope':'specific','branch_id':'administracion','area_id':'recursos-humanos','action':'alta'}))
        with patch('app.threading.Thread') as thread:
            response = self.client.post('/api/run', data=payload, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200, response.get_json())
        page_data = thread.call_args.kwargs['args'][2]
        plan = page_data['document_plan']
        self.assertEqual(plan['code'], '')
        self.assertEqual(page_data['codigo'], '')
        self.assertTrue(plan['is_politica'])
        self.assertTrue(plan['classification_directory'].endswith('/recursosHumanos/politicas'))
        result = {'success':True,'operation_id':page_data['operation_id'],'code':'','action':'alta','version':'v001',
                  'filename':'politica-de-calidad-v001.pdf', 'key':plan['classification_directory']+'/politica-de-calidad-v001.pdf',
                  'url':'https://files.test/politica.pdf', 'download_url':'https://files.test/download'}
        with application.app.app_context(), patch('storage_engine.execute_storage', return_value=result) as remote:
            stored = transfer_document(plan, page_data['operation_id'], page_data['temporary_file'])
            self.assertEqual(stored['filename'], 'politica-de-calidad-v001.pdf')

    def test_server_normalizes_code_and_name(self):
        payload = self.payload(); payload.update(codigo='fe-a-rhs-003', nombre='GESTIÓN_de-calidad Y revisión')
        with patch('app.threading.Thread') as thread:
            response = self.client.post('/api/run', data=payload, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200, response.get_json())
        page_data = thread.call_args.kwargs['args'][2]
        self.assertEqual(page_data['codigo'], 'FE-A-RHS-003')
        self.assertEqual(page_data['nombre'], 'Gestión De Calidad Y Revisión')

    def test_form_baja_only_publishes_witness_page_without_storage(self):
        payload = {'site':'zhipos-idpn.net','process_type':'create','folio':'268123','codigo':'FG-D-CON-005',
                   'nombre':'Solicitud','enlace':'https://example.test/formulario','embed_type':'form','action':'baja'}
        application.app.config['N8N_DOCUMENTS_WEBHOOK_URL'] = ''
        (self.root/'matrices/zhipos-idpn.net.json').unlink()
        with patch('app.threading.Thread') as thread:
            response = self.client.post('/api/run', json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        args = thread.call_args.kwargs['args']
        self.assertNotIn('document_plan', args[2])
        with patch('app.transfer_document') as storage, patch('app.run_wp_automation', return_value={'url':'https://example.test/baja'}) as wp:
            application.run_in_background(*args)
        storage.assert_not_called()
        self.assertEqual(wp.call_args.args[2]['document_status'], 'baja')
        self.assertEqual(wp.call_args.args[2]['enlace'], payload['enlace'])

    def test_baja_reference_file_is_uploaded_to_archive_and_used_for_page(self):
        payload=self.payload(); self.classification['action']='baja'; payload['classification']=json.dumps(self.classification)
        with patch('app.threading.Thread') as thread:
            response=self.client.post('/api/run',data=payload,content_type='multipart/form-data')
        self.assertEqual(response.status_code,200,response.get_json())
        args=thread.call_args.kwargs['args']; plan=args[2]['document_plan']
        result={'success':True,'operation_id':args[2]['operation_id'],'code':plan['code'],'action':'baja', 'version':'v001','sha256':hashlib.sha256(b'%PDF-1.4 test').hexdigest(),'size':len(b'%PDF-1.4 test'),
                'filename':'FE-A-RHS-003-control-de-no-conformes-v001.pdf',
                'key':plan['classification_directory']+'/baja/FE-A-RHS-003-control-de-no-conformes-v001.pdf',
                'url':'https://files.test/baja/reference.pdf','download_url':'https://files.test/download'}
        with application.app.app_context(),patch('storage_engine.execute_storage', return_value=result) as remote:
            self.assertEqual(transfer_document(plan,args[2]['operation_id'],args[2]['temporary_file'])['key'],result['key'])
            self.assertEqual(remote.call_count, 1)
        with patch('app.transfer_document',return_value=result),patch('app.run_wp_automation',return_value={'url':'https://example.test/baja'}) as wp:
            application.run_in_background(*args)
        self.assertEqual(wp.call_args.args[2]['enlace'],result['url'])
        self.assertFalse(Path(args[2]['temporary_file']).exists())

    def test_second_process_finalizes_saved_baja_only_after_wordpress_cleanup(self):
        from baja_records import prepare_record, attach_reference, save_record, load_record
        plan = {'site':'zhipos-idpn.net','action':'baja','code':'PG-D-CNO-003','type_id':'procedimiento',
                'name':'Control','classification_directory':'zhipos/gobierno/procedimientos','storage':{'provider':'s3','target':'bucket','region':'us-east-1'}}
        with application.app.app_context():
            record = prepare_record(plan,'operation-1234567890','268123')
            attach_reference(record,{'key':plan['classification_directory']+'/baja/file.pdf','sha256':'a'*64,'size':12,'version':'v001'})
            record['published']=True;save_record(record)
        ordering=[]
        def cleanup(**kwargs):
            ordering.append('wordpress_saved')
            calls = remote.call_count
            kwargs['after_remove']('zhipos-idpn.net','268123',page_metadata={'action':'alta','resource':'document'})
            kwargs['after_remove']('zhipos-idpn.net','268123',page_metadata={'action':'baja','resource':'form'})
            self.assertEqual(remote.call_count,calls)
            kwargs['after_remove']('zhipos-idpn.net','268123',page_metadata={'action':'baja','resource':'document'})
        def finalize(metadata):
            ordering.append('storage')
            return {'success':True,'operation_id':metadata['operation_id'],'code':metadata['code'],'action':'finalizar_baja','key':metadata['reference']['key']}
        with patch('app.run_wp_remove_multiple_automation',side_effect=cleanup),patch('baja_records.call_storage',side_effect=finalize) as remote:
            application.run_in_background({},'',{'process_type':'remove','folios':['268123']})
            application.run_in_background({},'',{'process_type':'remove','folios':['268123']})
        self.assertEqual(ordering,['wordpress_saved','storage','wordpress_saved'])
        self.assertEqual(remote.call_count,1)
        with application.app.app_context():
            self.assertTrue(load_record('zhipos-idpn.net','268123')['completed'])

    def test_normal_folio_without_baja_record_never_calls_storage(self):
        from baja_records import finalize_folio
        with application.app.app_context(),patch('baja_records.call_storage') as remote:
            finalize_folio('zhipos-idpn.net','268999')
            remote.assert_not_called()

    def test_unpublished_baja_cannot_be_finalized(self):
        from baja_records import prepare_record, finalize_folio
        plan={'site':'zhipos-idpn.net','code':'PG-D-CNO-003'}
        with application.app.app_context(),patch('baja_records.call_storage') as remote:
            prepare_record(plan,'operation-1234567890','268123')
            with self.assertRaises(ValueError):finalize_folio('zhipos-idpn.net','268123')
            remote.assert_not_called()

    def test_missing_cloud_credentials_rejects_upload_before_creating_a_temporary_file(self):
        (self.root/'integrations/storage.json').unlink()
        response = self.client.post('/api/run',data=self.payload(),content_type='multipart/form-data')
        self.assertEqual(response.status_code,400)
        self.assertFalse((self.root/'uploads').exists())

    def test_upload_failure_does_not_start_wordpress_and_cleans_temporary_file(self):
        with patch('app.threading.Thread') as thread:
            self.client.post('/api/run',data=self.payload(),content_type='multipart/form-data')
        args = thread.call_args.kwargs['args']
        with patch('app.transfer_document',side_effect=ValueError('Fallo simulado')),patch('app.run_wp_automation') as wp:
            application.run_in_background(*args)
            wp.assert_not_called()
        self.assertFalse(Path(args[2]['temporary_file']).exists())

    def test_successful_upload_publishes_with_returned_links(self):
        with patch('app.threading.Thread') as thread:
            self.client.post('/api/run',data=self.payload(),content_type='multipart/form-data')
        args = thread.call_args.kwargs['args']
        stored={'url':'https://files.test/file.pdf','download_url':'https://n8n.example.test/download','filename':'normalized-v001.pdf'}
        with patch('app.transfer_document',return_value=stored),patch('app.run_wp_automation',return_value={'url':'https://wp.example.test/268123/'}) as wp:
            application.run_in_background(*args)
        self.assertEqual(wp.call_args.args[2]['enlace'],stored['url'])
        self.assertEqual(wp.call_args.args[2]['download_url'],stored['download_url'])
        self.assertFalse(Path(args[2]['temporary_file']).exists())

    def test_baja_without_reference_file_is_rejected(self):
        payload=self.payload();payload.pop('file');self.classification['action']='baja';payload['classification']=json.dumps(self.classification)
        response=self.client.post('/api/run',data=payload)
        self.assertEqual(response.status_code,400)

    def test_baja_requires_folio_before_storage_can_run(self):
        payload = self.payload(); payload.pop('file'); payload['folio'] = ''
        self.classification['action'] = 'baja'; payload['classification'] = json.dumps(self.classification)
        with patch('app.threading.Thread') as thread:
            response = self.client.post('/api/run', data=payload)
        self.assertEqual(response.status_code, 400)
        thread.assert_not_called()

    def test_baja_validates_latest_archived_link_and_rejects_older_version(self):
        plan = {'site':'zhipos-idpn.net', 'code':'PG-D-CNO-003', 'type_id':'procedimiento', 'action':'baja',
                'classification_directory':'zhipos/government/procedimientos', 'storage':{'provider':'s3'}}
        base = plan['classification_directory']
        names = ['PG-D-CNO-003-control-v001.pdf', 'PG-D-CNO-003-control-v002.pdf']
        result = {'success':True, 'operation_id':'operation-1234567890', 'code':plan['code'], 'action':'baja',
                  'filename':names[-1], 'key':base+'/baja/'+names[-1], 'version':'v002',
                  'url':'https://files.test/baja/file.pdf', 'download_url':'https://files.test/download',
                  'archived':[{'source_key':base+'/'+name, 'key':base+'/baja/'+name} for name in names]}
        with application.app.app_context(), patch('storage_engine.execute_storage', return_value=result):
            self.assertEqual(transfer_document(plan, 'operation-1234567890')['version'], 'v002')
            result['filename'] = names[0]; result['key'] = base+'/baja/'+names[0]
            with self.assertRaises(ValueError):
                transfer_document(plan, 'operation-1234567890')

    def test_transfer_validates_returned_version_key_and_download_url(self):
        file=self.root/'source.pdf';file.write_bytes(b'%PDF test')
        plan={'site':'zhipos-idpn.net','code':'PG-D-CNO-003','type_id':'procedimiento','action':'alta','name':'Documento',
              'classification_directory':'zhipos/government/procedimientos','storage':{'provider':'s3','target':'company-files','region':'us-east-1'}}
        result={'success':True,'operation_id':'operation-1234567890','code':plan['code'],'action':'alta','version':'v001',
                'filename':'PG-D-CNO-003-documento-v001.pdf','key':'zhipos/government/procedimientos/PG-D-CNO-003-documento-v001.pdf',
                'url':'https://files.test/file.pdf','download_url':'https://n8n.example.test/download'}
        with application.app.app_context(),patch('storage_engine.execute_storage', return_value=result) as remote:
            self.assertEqual(transfer_document(plan,'operation-1234567890',str(file))['version'],'v001')
            self.assertEqual(remote.call_count, 1)
            result['key']='wrong-directory/file.pdf'
            with self.assertRaises(ValueError):
                transfer_document(plan,'operation-1234567890',str(file))


if __name__ == '__main__':
    unittest.main()
