"""Browser tests against a local fixture server; production configs are not edited."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from flask import Flask, jsonify, render_template
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from document_matrix import default_matrix, validate_matrix
from matrix_api import matrix_api
from document_stamp import publication_day


class MatrixUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'matrices').mkdir()
        matrix = validate_matrix(default_matrix('ZHIPOS', zhipos=True))
        (self.root / 'matrices/zhipos-idpn.net.json').write_text(json.dumps(matrix))
        config = {'site_name': 'ZHIPOS', 'base_url': 'https://example.test', 'login_url': 'https://example.test',
                  'create_page_url': 'https://example.test/new', 'cognito_script': '', 'folio_prefix': '268'}
        (self.root / 'zhipos-idpn.net.json').write_text(json.dumps(config))
        other_config = {**config, 'site_name': 'Otra empresa', 'folio_prefix': '999'}
        (self.root / 'otra.net.json').write_text(json.dumps(other_config))
        other_matrix = validate_matrix(default_matrix('Otra empresa'))
        (self.root / 'matrices/otra.net.json').write_text(json.dumps(other_matrix))
        app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
        app.config.update(IDPN_CONFIG_DIR=str(self.root), N8N_DOCUMENTS_WEBHOOK_URL='')
        app.register_blueprint(matrix_api)
        app.add_url_rule('/', endpoint='index', view_func=lambda: render_template('index.html'))
        for section in ['primer-proceso','segundo-proceso','configuracion']:
            app.add_url_rule('/'+section, endpoint=section, view_func=lambda: render_template('index.html'))
        app.add_url_rule('/api/configs', endpoint='configs', view_func=lambda: jsonify([{'domain': 'zhipos-idpn.net', 'site_name': 'ZHIPOS', 'folio_prefix': '268'}, {'domain': 'otra.net', 'site_name': 'Otra empresa', 'folio_prefix': '999'}]))
        app.add_url_rule('/api/configs/<domain>', endpoint='config_detail', view_func=lambda domain: jsonify(config if domain == 'zhipos-idpn.net' else other_config))
        self.server = make_server('127.0.0.1', 0, app, threaded=True)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.page = self.browser.new_page(viewport={'width': 1440, 'height': 1100})
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.route('**/*', lambda route: route.continue_() if route.request.url.startswith('http://127.0.0.1:') else route.abort())
        self.page.goto(f'http://127.0.0.1:{self.server.server_port}')
        self.page.wait_for_function("document.getElementById('matrix-workspace').hidden === false")
        self.page.locator('#site-select-p1').select_option('zhipos-idpn.net')
        self.page.locator('#matrix-branch option[value="control-de-no-conformes"]').wait_for(state='attached')
        self.page.locator('#matrix-detect-code').click()

    def tearDown(self):
        self.page.close()
        self.server.shutdown()
        self.server_thread.join()
        self.temp.cleanup()

    def test_company_selection_updates_site_and_matrix_together(self):
        self.page.locator('#nav-settings').click()
        self.page.locator('#config-company-select').select_option('otra.net')
        self.page.wait_for_function("document.getElementById('config-domain').value === 'otra.net' && document.getElementById('matrix-name').value === 'Otra empresa'")
        self.page.locator('#company-matrix-btn').click()
        self.assertTrue(self.page.locator('#matrix-admin-form').is_visible())
        self.assertFalse(self.page.locator('#settings-form').is_visible())
        self.assertEqual(self.page.locator('#nav-matrix').count(), 0)
        self.page.locator('#company-site-btn').click()
        self.assertEqual(self.page.locator('#config-name').input_value(), 'Otra empresa')
        # The primary process has its own selected company, unaffected by configuration browsing.
        self.assertEqual(self.page.locator('#site-select-p1').input_value(), 'zhipos-idpn.net')
        self.assertEqual(self.errors, [])

    def test_new_company_cannot_edit_previous_company_matrix(self):
        self.page.locator('#nav-settings').click()
        self.page.get_by_role('button', name='Nueva empresa').click()
        self.assertEqual(self.page.locator('#config-domain').input_value(), '')
        self.assertTrue(self.page.locator('#config-domain').is_enabled())
        self.page.locator('#company-matrix-btn').click()
        self.assertFalse(self.page.locator('#matrix-admin-form').is_visible())
        self.assertIn('Selecciona o crea una empresa', self.page.locator('#matrix-alert').inner_text())
        self.assertEqual(self.errors, [])

    def test_action_buttons_are_exclusive_and_keep_code_button_inline(self):
        self.page.locator('.action-choice:visible').filter(has_text='Cambio').click()
        self.assertEqual(self.page.locator('#matrix-action').input_value(), 'version')
        self.assertEqual(self.page.locator('input[name="document-action"]:checked').count(), 1)
        self.page.locator('.action-choice').filter(has_text='Baja').click()
        self.assertEqual(self.page.locator('#matrix-action').input_value(), 'baja')
        self.assertTrue(self.page.locator('#matrix-file-field').is_visible())
        self.page.locator('.resource-choice').filter(has_text='Formulario / Web').click()
        self.assertEqual(self.page.locator('#matrix-action').input_value(), 'baja')
        self.assertEqual(self.page.locator('input[name="document-action"]:disabled').count(), 1)
        self.assertEqual(self.page.locator('.action-choice:visible').count(), 3)
        self.page.locator('.resource-choice').filter(has_text='Documento').click()
        for width in [1440, 390]:
            self.page.set_viewport_size({'width': width, 'height': 900})
            code = self.page.locator('#input-codigo-p1').bounding_box()
            button = self.page.locator('#matrix-detect-code').bounding_box()
            self.assertAlmostEqual(code['y'], button['y'], delta=1)
            self.assertLessEqual(code['x'] + code['width'], button['x'])
            self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        self.assertEqual(self.errors, [])

    def test_web_form_requests_link_even_before_company_is_selected(self):
        self.page.locator('#site-select-p1').evaluate("select => { select.value = ''; select.dispatchEvent(new Event('change', {bubbles:true})); }")
        self.page.locator('.resource-choice').filter(has_text='Formulario / Web').click()
        self.assertTrue(self.page.locator('#campo-enlace-p1').is_visible())
        self.assertTrue(self.page.locator('#input-enlace-p1').evaluate('input => input.required'))
        self.assertFalse(self.page.locator('#matrix-file-field').is_visible())
        self.assertFalse(self.page.locator('#matrix-file').evaluate('input => input.required'))
        self.assertIn('formulario', self.page.locator('#submit-btn-p1').inner_text())
        self.page.locator('.resource-choice').filter(has_text='Documento').click()
        self.assertFalse(self.page.locator('#campo-enlace-p1').is_visible())
        self.assertTrue(self.page.locator('#matrix-file-field').is_visible())
        self.assertEqual(self.errors, [])

    def test_web_form_submits_user_code_and_link_without_classification(self):
        self.page.locator('.resource-choice').filter(has_text='Formulario / Web').click()
        self.page.locator('#input-codigo-p1').fill('MI-CODIGO-LIBRE')
        self.page.locator('#input-nombre-p1').fill('Encuesta')
        self.page.locator('#input-folio-p1').fill('268123')
        self.page.locator('#input-enlace-p1').fill('https://example.test/formulario')
        submitted = []
        def receive(route):
            submitted.append(route.request.post_data_json)
            route.fulfill(status=400, content_type='application/json', body='{"error":"Prueba local completada"}')
        self.page.route('**/api/run', receive)
        self.page.locator('#submit-btn-p1').click()
        self.page.wait_for_function("document.getElementById('form-alert-p1').textContent.includes('Prueba local completada')")
        self.assertEqual(submitted[0]['codigo'], 'MI-CODIGO-LIBRE')
        self.assertEqual(submitted[0]['enlace'], 'https://example.test/formulario')
        self.assertNotIn('classification', submitted[0])
        self.assertNotIn('operation_id', submitted[0])
        self.assertFalse(self.page.locator('#classification-fields').is_visible())
        self.assertFalse(self.page.locator('#matrix-detect-code').is_visible())
        self.assertEqual(self.errors, [])

    def test_folio_is_first_and_detects_company_with_dynamic_resource_labels(self):
        self.assertEqual(self.page.locator('#page-form-p1 input:not([type="hidden"])').first.get_attribute('id'), 'input-folio-p1')
        self.page.locator('#input-folio-p1').fill('999123')
        self.page.wait_for_function("document.getElementById('site-select-p1').value === 'otra.net'")
        self.page.locator('.resource-choice').filter(has_text='Formulario / Web').click()
        self.assertEqual(self.page.locator('label[for="input-codigo-p1"]').inner_text(), 'Código del formulario')
        self.assertEqual(self.page.locator('label[for="input-nombre-p1"]').inner_text(), 'Nombre del formulario')
        fields = ['input-folio-p1', 'input-codigo-p1', 'input-nombre-p1', 'input-enlace-p1']
        positions = [self.page.locator('#'+field).bounding_box()['y'] for field in fields]
        self.assertEqual(positions, sorted(positions))
        self.page.locator('.resource-choice').filter(has_text='Documento').click()
        self.assertEqual(self.page.locator('label[for="input-codigo-p1"]').inner_text(), 'Código del documento')
        self.assertEqual(self.errors, [])

    def test_baja_requires_folio_and_reference_file(self):
        self.page.locator('.action-choice').filter(has_text='Baja').click()
        self.assertTrue(self.page.locator('#input-folio-p1').is_visible())
        self.assertTrue(self.page.locator('#input-folio-p1').evaluate('input => input.required'))
        self.assertTrue(self.page.locator('#matrix-file-field').is_visible())
        self.assertTrue(self.page.locator('#matrix-file').evaluate('input => input.required'))
        self.assertIn('publicar registro', self.page.locator('#submit-btn-p1').inner_text())
        self.assertEqual(self.errors, [])

    def test_policy_requires_manual_directory_and_no_code(self):
        self.page.locator('#input-is-politica-p1').check()
        self.assertFalse(self.page.locator('#campo-codigo-p1').is_visible())
        self.assertFalse(self.page.locator('#input-codigo-p1').evaluate('input => input.required'))
        self.assertTrue(self.page.locator('#classification-fields').is_visible())
        self.assertFalse(self.page.locator('#matrix-type').is_visible())
        self.page.locator('#matrix-scope').select_option('specific')
        self.page.locator('#matrix-branch').select_option('administracion')
        self.page.locator('#matrix-area').select_option('recursos-humanos')
        self.page.locator('#input-nombre-p1').fill('Política de calidad')
        self.page.locator('#matrix-file').set_input_files({'name':'original.pdf','mimeType':'application/pdf','buffer':b'%PDF test'})
        self.page.locator('#matrix-result').wait_for(state='visible')
        self.assertIn('/administracion/recursosHumanos/politicas', self.page.locator('#matrix-result').inner_text())
        self.assertIn(f'politica-de-calidad-{publication_day()}.pdf', self.page.locator('#matrix-result').inner_text())
        metadata = self.page.evaluate('window.documentOperation.metadata()')
        self.assertEqual(metadata['code'], '')
        self.assertEqual(metadata['type_id'], 'politica')
        self.assertTrue(metadata['is_politica'])
        self.page.locator('#input-is-politica-p1').uncheck()
        self.assertTrue(self.page.locator('#campo-codigo-p1').is_visible())
        self.assertTrue(self.page.locator('#input-codigo-p1').evaluate('input => input.required'))
        self.assertEqual(self.errors, [])

    def test_form_change_has_no_file_or_classification_and_switches_cleanly(self):
        self.page.locator('.resource-choice').filter(has_text='Formulario / Web').click()
        self.page.locator('.action-choice:visible').filter(has_text='Cambio').click()
        self.assertEqual(self.page.locator('#matrix-action').input_value(), 'cambio')
        self.assertEqual(self.page.locator('input[name="document-action"]:checked').count(), 1)
        self.assertFalse(self.page.locator('#matrix-file-field').is_visible())
        self.assertFalse(self.page.locator('#document-routing').is_visible())
        self.assertTrue(self.page.locator('#campo-enlace-p1').is_visible())
        self.page.locator('.resource-choice').filter(has_text='Documento').click()
        self.assertEqual(self.page.locator('#matrix-action').input_value(), 'version')
        self.assertEqual(self.page.locator('.action-choice:visible').count(), 3)
        self.assertTrue(self.page.locator('input[name="document-action"][value="cambio"]').evaluate('input => input.closest("label").hidden'))
        self.assertEqual(self.errors, [])

    def test_name_is_title_case_and_code_is_uppercase(self):
        self.page.locator('#input-codigo-p1').fill('pg-d-cno-003')
        self.assertEqual(self.page.locator('#input-codigo-p1').input_value(), 'PG-D-CNO-003')
        self.page.locator('#input-nombre-p1').fill('GESTIÓN_de-calidad Y revisión')
        self.page.locator('#input-nombre-p1').blur()
        self.assertEqual(self.page.locator('#input-nombre-p1').input_value(), 'Gestión de Calidad y Revisión')
        self.assertEqual(self.errors, [])

    def test_name_casing_and_slashes_follow_selected_company(self):
        self.page.evaluate("configsList.find(c => c.domain === 'zhipos-idpn.net').document_title_case = 'upper'")
        field = self.page.locator('#input-nombre-p1')
        field.fill('gestión del almacén, y/o control por área')
        field.blur()
        self.assertEqual(field.input_value(), 'GESTIÓN DEL ALMACÉN, Y/O CONTROL POR ÁREA')
        self.page.evaluate("configsList.find(c => c.domain === 'zhipos-idpn.net').document_title_case = 'lower'")
        field.fill('GESTIÓN DEL ALMACÉN, Y/O CONTROL POR ÁREA')
        field.blur()
        self.assertEqual(field.input_value(), 'gestión del almacén, y/o control por área')
        self.page.locator('#site-select-p1').select_option('otra.net')
        field.fill('GESTIÓN DEL ALMACÉN, Y/O CONTROL POR ÁREA')
        field.blur()
        self.assertEqual(field.input_value(), 'Gestión del Almacén, y/o Control por Área')
        self.page.locator('#nav-settings').click()
        self.page.locator('#config-company-select').select_option('otra.net')
        self.page.wait_for_function("document.getElementById('config-domain').value === 'otra.net'")
        self.assertEqual(self.page.locator('#config-title-case').input_value(), 'title')
        self.assertEqual(self.page.locator('#config-title-case option').count(), 3)
        self.assertEqual(self.errors, [])

    def test_unchecking_policy_without_company_hides_manual_classification(self):
        self.page.locator('#site-select-p1').evaluate("select => {select.value = ''; select.dispatchEvent(new Event('change'));}")
        self.page.locator('#input-is-politica-p1').check()
        self.assertTrue(self.page.locator('#classification-fields').is_visible())
        self.page.locator('#input-is-politica-p1').uncheck()
        self.assertFalse(self.page.locator('#classification-fields').is_visible())
        self.assertEqual(self.errors, [])

    def test_scope_updates_label_and_area_even_without_company(self):
        self.page.locator('#site-select-p1').evaluate("select => {select.value = ''; select.dispatchEvent(new Event('change'));}")
        self.page.locator('#matrix-detect-code').click()
        self.page.locator('#matrix-scope').select_option('specific')
        self.assertEqual(self.page.locator('#matrix-branch-label').inner_text(), 'Procedimiento específico / proceso')
        self.assertTrue(self.page.locator('#matrix-area-field').is_visible())
        self.page.locator('#matrix-scope').select_option('government')
        self.assertEqual(self.page.locator('#matrix-branch-label').inner_text(), 'Procedimiento de Gobierno')
        self.assertFalse(self.page.locator('#matrix-area-field').is_visible())
        self.page.locator('#input-is-politica-p1').check()
        self.page.locator('#matrix-scope').select_option('specific')
        self.assertEqual(self.page.locator('#matrix-branch-label').inner_text(), 'Procedimiento específico / proceso')
        self.assertFalse(self.page.locator('#matrix-type').is_visible())
        self.assertEqual(self.errors, [])

    def test_classification_is_one_scrollable_row_on_mobile(self):
        self.page.set_viewport_size({'width':390, 'height':844})
        self.page.locator('#matrix-scope').select_option('specific')
        self.page.locator('#matrix-branch').select_option('administracion')
        positions = [self.page.locator('#'+field).bounding_box()['y'] for field in ['matrix-scope','matrix-branch','matrix-area','matrix-type']]
        self.assertLess(max(positions)-min(positions), 2)
        self.assertTrue(self.page.locator('#classification-fields').evaluate('field => field.scrollWidth > field.clientWidth'))
        self.page.locator('#classification-fields').evaluate('field => field.scrollLeft = field.scrollWidth')
        self.assertGreater(self.page.locator('#classification-fields').evaluate('field => field.scrollLeft'), 0)
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        self.assertEqual(self.errors, [])

    def test_publish_button_is_below_preview_and_associated_with_form(self):
        button = self.page.locator('#submit-btn-p1')
        self.assertEqual(button.get_attribute('form'), 'page-form-p1')
        panel = self.page.locator('#right-panel').bounding_box()
        location = button.bounding_box()
        self.assertGreater(location['y'], panel['y']+panel['height'])
        self.assertAlmostEqual(location['x'], panel['x'], delta=1)
        screen = self.page.locator('#browser-screen-container').bounding_box()
        self.assertGreater(screen['height'], 0)
        self.assertEqual(self.page.locator('#browser-screencast').evaluate('image => getComputedStyle(image).objectFit'), 'contain')
        self.assertEqual(self.errors, [])

    def test_entry_card_matches_preview_height_and_scrolls_internally(self):
        card = self.page.locator('#tab-process1 .dashboard-grid > .card')
        column = self.page.locator('#right-panel-container-p1')
        self.page.wait_for_function("Math.abs(document.querySelector('#tab-process1 .dashboard-grid > .card').getBoundingClientRect().height-document.getElementById('right-panel-container-p1').getBoundingClientRect().height)<2")
        self.assertAlmostEqual(card.bounding_box()['height'], column.bounding_box()['height'], delta=2)
        self.assertTrue(card.evaluate('card => card.scrollHeight > card.clientHeight'))
        card.evaluate('card => card.scrollTop = card.scrollHeight')
        self.assertGreater(card.evaluate('card => card.scrollTop'), 0)
        self.page.set_viewport_size({'width':390,'height':844})
        self.page.wait_for_function("document.querySelector('#tab-process1 .dashboard-grid > .card').getBoundingClientRect().height < 560")
        self.assertLess(card.bounding_box()['height'], 560)
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        self.assertEqual(self.errors, [])

    def test_form_baja_submits_only_page_data(self):
        self.page.locator('.resource-choice').filter(has_text='Formulario / Web').click()
        self.page.locator('.action-choice').filter(has_text='Baja').click()
        self.page.locator('#input-folio-p1').fill('268123')
        self.page.locator('#input-codigo-p1').fill('fg-d-con-005')
        self.page.locator('#input-nombre-p1').fill('Solicitud')
        self.page.locator('#input-enlace-p1').fill('https://example.test/formulario')
        submitted = []
        def receive(route):
            submitted.append(route.request.post_data_json)
            route.fulfill(status=400, content_type='application/json', body='{"error":"Registro simulado"}')
        self.page.route('**/api/run', receive)
        self.page.locator('#submit-btn-p1').click()
        self.page.wait_for_function("document.getElementById('form-alert-p1').textContent.includes('Registro simulado')")
        self.assertEqual(submitted[0]['action'], 'baja')
        self.assertEqual(submitted[0]['embed_type'], 'form')
        self.assertEqual(submitted[0]['codigo'], 'FG-D-CON-005')
        self.assertNotIn('classification', submitted[0])
        self.assertFalse(self.page.locator('#matrix-file-field').is_visible())
        self.assertEqual(self.errors, [])

    def test_policy_option_remains_visible_when_action_is_baja(self):
        self.page.locator('#input-is-politica-p1').check()
        self.page.locator('.action-choice').filter(has_text='Baja').click()
        self.assertTrue(self.page.locator('#campo-politica-p1').is_visible())
        self.assertTrue(self.page.locator('#input-is-politica-p1').is_checked())
        self.assertFalse(self.page.locator('#campo-codigo-p1').is_visible())
        self.assertTrue(self.page.locator('#classification-fields').is_visible())
        self.page.locator('#input-is-politica-p1').uncheck()
        self.assertTrue(self.page.locator('#campo-codigo-p1').is_visible())
        self.assertEqual(self.errors, [])

    def test_second_process_layout_add_remove_and_full_result_text(self):
        self.page.locator('#nav-process2').click()
        self.page.get_by_role('button', name='Agregar folio').click()
        self.assertEqual(self.page.locator('.input-folio-p2-item').count(), 2)
        self.page.get_by_role('button', name='Quitar folio').click()
        self.assertEqual(self.page.locator('.input-folio-p2-item').count(), 1)
        self.assertEqual(self.page.locator('#submit-btn-p2').get_attribute('form'), 'page-form-p2')
        self.page.wait_for_function("Math.abs(document.querySelector('#tab-process2 .dashboard-grid > .card').getBoundingClientRect().height-document.getElementById('right-panel-container-p2').getBoundingClientRect().height)<2")
        panel = self.page.locator('#right-panel').bounding_box()
        button = self.page.locator('#submit-btn-p2').bounding_box()
        self.assertGreater(button['y'], panel['y']+panel['height'])
        self.page.evaluate("""() => {
          window.EventSource=class {close(){}};
          connectToLogStream();
          document.getElementById('results-container-p2').style.display='flex';
          sseConnection.onmessage({data:JSON.stringify({message:'Resultado simulado',level:'success',folio_status:{status:'success',folio:'268123',domain:'zhipos-idpn.net',header:'POLÍTICA DE GESTIÓN DOCUMENTAL CON UN NOMBRE EXTENSO PARA VERIFICAR LA LECTURA COMPLETA',url:'https://example.test/documentos/politica-de-gestion-documental-con-un-nombre-extenso/'}})});
        }""")
        span = self.page.locator('#results-exitosos-tbody .result-copy-box span').first
        self.assertIn('Lectura Completa', span.inner_text())
        self.assertEqual(span.evaluate('element => getComputedStyle(element).whiteSpace'), 'normal')
        self.assertGreater(span.bounding_box()['height'], 30)
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        self.assertEqual(self.errors, [])

    def test_sections_have_routes_and_survive_refresh_and_back(self):
        self.page.locator('#nav-process2').click()
        self.assertTrue(self.page.url.endswith('/segundo-proceso'))
        self.page.reload()
        self.page.locator('#tab-process2.active').wait_for(state='visible')
        self.assertTrue(self.page.locator('#right-panel-container-p2 #right-panel').is_visible())
        self.page.locator('#nav-settings').click()
        self.assertTrue(self.page.url.endswith('/configuracion'))
        self.page.reload()
        self.page.locator('#tab-settings.active').wait_for(state='visible')
        self.page.go_back()
        self.page.locator('#tab-process2.active').wait_for(state='visible')
        self.page.locator('#nav-process1').click()
        self.assertTrue(self.page.url.endswith('/primer-proceso'))
        self.assertEqual(self.errors, [])

    def test_console_fills_card_and_preview_can_expand_and_restore(self):
        for process in ['process1','process2']:
            self.page.locator('#nav-'+process).click()
            self.page.locator('#right-tab-console-btn').click()
            panel = self.page.locator('#right-panel').bounding_box()
            console = self.page.locator('#terminal-console').bounding_box()
            self.assertAlmostEqual(console['y']+console['height'], panel['y']+panel['height'], delta=2)
            self.assertEqual(self.page.locator('#right-tab-console').evaluate('el => getComputedStyle(el).backgroundColor'), 'rgb(23, 33, 43)')
            self.page.get_by_role('button', name='Ampliar visor').click()
            expanded = self.page.locator('#right-panel').bounding_box()
            self.assertGreater(expanded['width'], panel['width']*1.5)
            self.assertGreater(expanded['height'], panel['height'])
            self.page.keyboard.press('Escape')
            self.assertEqual(self.page.locator('#preview-expand-btn').get_attribute('aria-expanded'), 'false')
            self.assertAlmostEqual(self.page.locator('#right-panel').bounding_box()['width'],panel['width'],delta=2)
        self.page.locator('#right-tab-browser-btn').click()
        self.assertEqual(self.page.locator('#right-tab-browser-btn use').get_attribute('href'), '#icon-globe')
        self.page.set_viewport_size({'width':390,'height':844})
        self.page.get_by_role('button', name='Ampliar visor').click()
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        self.page.get_by_role('button', name='Restaurar visor').click()
        self.assertEqual(self.errors, [])

    def test_manual_document_types_exclude_formulario_and_policy(self):
        for scope in ['government','specific']:
            self.page.locator('#matrix-scope').select_option(scope)
            choices = self.page.locator('#matrix-type option').evaluate_all('options => options.map(option => option.value).filter(Boolean)')
            self.assertEqual(choices, ['procedimiento','instructivo','formato'])
        self.assertEqual(self.errors, [])

    def test_company_settings_selector_uses_full_width_and_compact_connection(self):
        self.page.locator('#nav-settings').click()
        self.assertEqual(self.page.locator('#configs-list').count(),0)
        self.assertEqual(self.page.locator('#config-company-select option').count(),3)
        self.page.locator('#company-matrix-btn').click()
        self.assertEqual(self.page.locator('#company-matrix-panel .storage-connection').count(),0)
        self.assertFalse(self.page.locator('#matrix-alert').is_visible())
        for width in [1440,390]:
            self.page.set_viewport_size({'width':width,'height':900})
            self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
            for button in self.page.locator('#tab-settings button:visible').all():
                self.assertTrue(button.evaluate('button => button.scrollWidth <= button.clientWidth+2'))
        self.assertEqual(self.errors,[])

    def test_legacy_directory_option_is_saved_only_for_selected_company(self):
        self.page.locator('#nav-settings').click()
        self.page.locator('#company-destination-btn').click()
        self.page.locator('#matrix-provider').select_option('s3')
        self.assertFalse(self.page.locator('#matrix-omit-scope').is_checked())
        self.page.locator('#matrix-root').fill('')
        self.page.locator('#matrix-omit-scope').check()
        with self.page.expect_response(lambda response: '/api/matrices/' in response.url and response.request.method == 'PUT'):
            self.page.locator('#destination-save').click()
        stored = json.loads((self.root / 'matrices/zhipos-idpn.net.json').read_text())
        self.assertTrue(stored['omit_scope_directory'])
        self.assertEqual(stored['storage']['root'], '')
        self.page.locator('#config-company-select').select_option('otra.net')
        self.page.wait_for_function("document.getElementById('matrix-name').value === 'Otra empresa'")
        self.assertFalse(self.page.locator('#matrix-omit-scope').is_checked())
        self.page.locator('#config-company-select').select_option('zhipos-idpn.net')
        self.page.wait_for_function("document.getElementById('matrix-omit-scope').checked")
        self.page.reload()
        self.page.wait_for_function("document.getElementById('matrix-omit-scope').checked")
        self.page.locator('#matrix-omit-scope').uncheck()
        with self.page.expect_response(lambda response: '/api/matrices/' in response.url and response.request.method == 'PUT'):
            self.page.locator('#destination-save').click()
        stored = json.loads((self.root / 'matrices/zhipos-idpn.net.json').read_text())
        self.assertFalse(stored['omit_scope_directory'])
        self.assertEqual(self.errors, [])

    def test_company_menu_collapses_and_preserves_company_and_section(self):
        self.page.locator('#nav-settings').click()
        menu = self.page.locator('.company-settings-menu')
        self.assertEqual(menu.locator('#config-company-select').count(), 1)
        self.assertEqual(menu.locator('.company-create-btn').count(), 0)
        self.page.locator('#company-destination-btn').click()
        self.assertEqual(self.page.locator('#company-destination-panel').is_visible(), True)
        previous = self.page.locator('#config-company-select').input_value()
        before = self.page.locator('.company-config-panels').bounding_box()['width']
        self.page.locator('#company-menu-toggle').click()
        self.assertEqual(self.page.locator('#company-menu-toggle').get_attribute('aria-expanded'), 'false')
        self.assertFalse(self.page.locator('#config-company-select').is_visible())
        self.assertGreater(self.page.locator('.company-config-panels').bounding_box()['width'], before)
        self.page.locator('#company-matrix-btn').click()
        self.assertTrue(self.page.locator('#company-matrix-panel').is_visible())
        self.page.locator('#company-menu-toggle').click()
        self.assertEqual(self.page.locator('#config-company-select').input_value(), previous)
        self.page.locator('#company-storage-btn').click()
        self.assertFalse(self.page.locator('#config-company-select').is_visible())
        self.page.locator('#company-destination-btn').click()
        for width in (1440,390):
            self.page.set_viewport_size({'width':width,'height':1100})
            self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
            self.page.screenshot(path=f'/private/tmp/idpn-company-menu-{width}.png', full_page=True)
        self.assertEqual(self.errors, [])

    def test_matrix_categories_collapse_independently_without_losing_changes(self):
        self.page.locator('#nav-settings').click()
        self.page.locator('#company-matrix-btn').click()
        government = self.page.locator('#matrix-government-section > summary')
        government.click()
        self.assertFalse(self.page.locator('#matrix-government').is_visible())
        self.assertTrue(self.page.locator('#matrix-specific').is_visible())
        self.page.locator('#matrix-add-process').click()
        self.assertFalse(self.page.locator('#matrix-government').is_visible())
        specific = self.page.locator('#matrix-specific-section > summary')
        specific.click()
        self.assertFalse(self.page.locator('#matrix-specific').is_visible())
        government.focus()
        self.page.keyboard.press('Enter')
        self.assertTrue(self.page.locator('#matrix-government').is_visible())
        self.assertFalse(self.page.locator('#matrix-specific').is_visible())
        self.assertEqual(self.errors, [])

    def test_process_metadata_is_separate_from_its_actual_areas(self):
        self.page.locator('#nav-settings').click()
        self.page.locator('#company-matrix-btn').click()
        processes = self.page.locator('#matrix-specific > .matrix-process')
        expected_counts = [2,4,4,2]
        for index, count in enumerate(expected_counts):
            process = processes.nth(index)
            self.assertEqual(process.locator('.matrix-process-header input').count(),3)
            process.locator('.matrix-process-areas > summary').click()
            self.assertEqual(process.locator('.matrix-process-area-list > .matrix-entry').count(),count)
            process_name = process.locator('.matrix-process-header input').first.input_value()
            area_names = process.locator('.matrix-process-area-list .matrix-entry').evaluate_all('rows => rows.map(row => row.querySelector("input").value)')
            self.assertNotIn(process_name,area_names)
        self.assertEqual(self.errors,[])

    def test_storage_settings_are_global_and_survive_refresh(self):
        self.page.locator('#nav-settings').click()
        self.page.locator('#company-storage-btn').click()
        self.assertFalse(self.page.locator('#company-toolbar').is_visible())
        self.assertTrue(self.page.locator('#storage-credentials-form').is_visible())
        self.assertEqual(self.page.locator('#storage-credentials-form').count(),1)
        self.assertIn('seccion=storage',self.page.url)
        self.page.reload()
        self.page.locator('#company-storage-panel').wait_for(state='visible')
        self.assertFalse(self.page.locator('#company-toolbar').is_visible())
        self.page.locator('#company-site-btn').click()
        self.assertTrue(self.page.locator('#config-company-select').is_visible())
        self.assertEqual(self.errors,[])

    def test_wordpress_identity_fields_share_row_and_script_uses_code_box(self):
        self.page.locator('#nav-settings').click()
        self.page.locator('#company-site-btn').click()
        self.page.locator('#config-name').wait_for(state='visible')
        positions=[self.page.locator('#'+field).bounding_box()['y'] for field in ['config-name','config-domain','config-folio-prefix']]
        self.assertLess(max(positions)-min(positions),2)
        script="<script src='https://example.test/cognito.js'>\n  const value = 1;\n</script>"
        self.page.locator('#config-cognito').fill(script)
        self.assertEqual(self.page.locator('#config-cognito').input_value(),script)
        self.assertIn('monospace',self.page.locator('#config-cognito').evaluate('field => getComputedStyle(field).fontFamily'))
        self.assertEqual(self.page.locator('#config-cognito').get_attribute('spellcheck'),'false')
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        self.assertEqual(self.errors,[])

    def file_and_number(self, code='PG-D-CNO-003'):
        self.page.locator('#input-codigo-p1').fill(code)
        self.page.locator('#input-nombre-p1').fill('Control de no conformes')
        self.page.locator('#matrix-file').set_input_files({'name': 'original.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF-1.4\n'})

    def test_manual_detection_routes_directly_without_branch_area_or_type(self):
        self.page.locator('#input-codigo-p1').fill('MG-003')
        self.page.wait_for_function("document.getElementById('matrix-scope').value === 'manuals' && document.getElementById('matrix-number').value === '003'")
        self.assertFalse(self.page.locator('#classification-fields').is_visible())
        self.assertIn('Manuales del Sistema de Gestión', self.page.locator('#classification-summary').inner_text())
        self.page.locator('#matrix-detect-code').click()
        self.assertFalse(self.page.locator('#matrix-branch').is_visible())
        self.assertFalse(self.page.locator('#matrix-area').is_visible())
        self.assertFalse(self.page.locator('#matrix-type').is_visible())
        self.assertFalse(self.page.locator('#campo-politica-p1').is_visible())
        self.page.locator('#input-nombre-p1').fill('Manual del sistema')
        self.page.locator('#matrix-file').set_input_files({'name':'manual.pdf','mimeType':'application/pdf','buffer':b'%PDF test'})
        self.page.locator('#matrix-result').wait_for(state='visible')
        self.assertIn('zhipos/manualesDelSistemaDeGestion', self.page.locator('#matrix-result').inner_text())
        metadata = self.page.evaluate('window.documentOperation.metadata()')
        self.assertEqual(metadata['scope'], 'manuals')
        self.assertEqual(metadata['type_id'], 'manual')
        self.assertEqual(metadata['code'], 'MG-003')
        self.assertEqual(self.errors, [])

    def test_manual_prefix_and_directory_can_be_configured_per_company(self):
        self.page.locator('#nav-settings').click()
        self.page.locator('#company-matrix-btn').click()
        self.page.locator('#matrix-manuals-settings > summary').click()
        self.page.locator('#matrix-manual-prefix').fill('MSG')
        self.page.locator('#matrix-manual-directory').fill('Manuales Corporativos')
        with self.page.expect_response(lambda response: '/api/matrices/' in response.url and response.request.method == 'PUT'):
            self.page.locator('#matrix-save').click()
        self.page.wait_for_function("document.getElementById('matrix-manual-directory').value === 'manualesCorporativos'")
        stored = json.loads((self.root / 'matrices/zhipos-idpn.net.json').read_text())
        self.assertEqual(stored['manual_prefix'], 'MSG')
        self.assertEqual(stored['manual_directory'], 'manualesCorporativos')
        self.page.screenshot(path='/private/tmp/idpn-manuals-settings.png', full_page=True)
        self.page.locator('#nav-process1').click()
        self.page.locator('#input-codigo-p1').fill('MSG-001')
        self.page.wait_for_function("document.getElementById('matrix-scope').value === 'manuals'")
        self.page.locator('#input-nombre-p1').fill('Manual corporativo')
        self.page.locator('#matrix-file').set_input_files({'name':'manual.pdf','mimeType':'application/pdf','buffer':b'%PDF test'})
        self.page.locator('#matrix-result').wait_for(state='visible')
        self.assertIn('zhipos/manualesCorporativos', self.page.locator('#matrix-result').inner_text())
        self.assertEqual(self.errors, [])

    def test_main_scope_directory_names_can_be_configured_per_company(self):
        self.page.locator('#nav-settings').click()
        self.page.locator('#company-matrix-btn').click()
        self.page.locator('#matrix-government-directory').fill('Gobierno Corporativo')
        self.page.locator('#matrix-specific-directory').fill('Procesos de Empresa')
        with self.page.expect_response(lambda response: '/api/matrices/' in response.url and response.request.method == 'PUT'):
            self.page.locator('#matrix-save').click()
        self.page.wait_for_function("document.getElementById('matrix-government-directory').value === 'gobiernoCorporativo'")
        stored = json.loads((self.root / 'matrices/zhipos-idpn.net.json').read_text())
        self.assertEqual(stored['government_directory'], 'gobiernoCorporativo')
        self.assertEqual(stored['specific_directory'], 'procesosDeEmpresa')
        self.page.locator('#config-company-select').select_option('otra.net')
        self.page.wait_for_function("document.getElementById('matrix-name').value === 'Otra empresa'")
        self.assertEqual(self.page.locator('#matrix-government-directory').input_value(), 'procedimientosDeGobierno')
        self.assertEqual(self.page.locator('#matrix-specific-directory').input_value(), 'procedimientosEspecificos')
        self.assertEqual(self.errors, [])

    def test_government_preview_and_apply_code_to_wordpress(self):
        self.page.locator('#matrix-branch').select_option('control-de-no-conformes')
        self.page.locator('#matrix-type').select_option('procedimiento')
        self.file_and_number()
        self.page.locator('#matrix-result').wait_for(state='visible')
        self.assertIn('PG-D-CNO-003', self.page.locator('#matrix-result').inner_text())
        self.assertIn(f'{publication_day()}.pdf', self.page.locator('#matrix-result').inner_text())
        self.page.screenshot(path='/private/tmp/idpn-matrix-preview.png')
        self.assertEqual(self.page.locator('#input-codigo-p1').input_value(), 'PG-D-CNO-003')
        self.assertEqual(self.page.locator('#site-select-p1').input_value(), 'zhipos-idpn.net')
        self.assertEqual(self.errors, [])

    def test_specific_formulario_uses_code_without_file(self):
        self.page.locator('#matrix-scope').select_option('specific')
        self.page.locator('#matrix-branch').select_option('administracion')
        self.page.locator('#matrix-area').select_option('recursos-humanos')
        self.page.locator('.resource-choice').filter(has_text='Formulario / Web').click()
        self.page.locator('#input-codigo-p1').fill('FE-A-RHS-015')
        self.assertFalse(self.page.locator('#matrix-file-field').is_visible())
        self.assertTrue(self.page.locator('#campo-enlace-p1').is_visible())
        self.assertFalse(self.page.locator('#classification-fields').is_visible())
        self.assertFalse(self.page.locator('#matrix-detect-code').is_visible())
        self.assertFalse(self.page.locator('#matrix-result').is_visible())
        self.assertEqual(self.errors, [])

    def test_code_detection_populates_specific_area_and_preserves_ambiguity(self):
        self.page.locator('#input-codigo-p1').fill('FE-A-RHS-003')
        self.page.wait_for_function("document.getElementById('matrix-number').value === '003'")
        self.assertEqual(self.page.locator('#matrix-scope').input_value(), 'specific')
        self.assertEqual(self.page.locator('#matrix-branch').input_value(), 'administracion')
        self.assertEqual(self.page.locator('#matrix-area').input_value(), 'recursos-humanos')
        self.assertEqual(self.page.locator('#matrix-type').input_value(), 'formato')
        self.assertFalse(self.page.locator('#classification-fields').is_visible())
        self.assertIn('Recursos Humanos', self.page.locator('#classification-summary').inner_text())
        self.assertEqual(self.errors, [])

    def test_unknown_code_reveals_fields_for_manual_classification(self):
        self.page.locator('#input-codigo-p1').fill('FE-Z-DES-003')
        self.page.wait_for_function("document.getElementById('classification-summary').textContent.includes('no coincide')")
        self.assertTrue(self.page.locator('#classification-fields').is_visible())
        self.assertEqual(self.page.locator('#matrix-branch').input_value(), '')
        self.assertEqual(self.errors, [])

    def test_recognized_classification_can_be_corrected_without_being_overwritten(self):
        self.page.locator('#input-codigo-p1').fill('PG-D-CNO-003')
        self.page.wait_for_function("document.getElementById('matrix-number').value === '003'")
        self.assertFalse(self.page.locator('#classification-fields').is_visible())
        self.page.locator('#matrix-detect-code').click()
        self.assertTrue(self.page.locator('#classification-fields').is_visible())
        self.page.locator('#matrix-branch').select_option('control-de-documentos')
        self.page.locator('#input-nombre-p1').fill('Documento corregido')
        self.page.locator('#matrix-file').set_input_files({'name':'file.pdf','mimeType':'application/pdf','buffer':b'%PDF test'})
        self.page.locator('#matrix-result').wait_for(state='visible')
        self.assertEqual(self.page.locator('#matrix-branch').input_value(), 'control-de-documentos')
        self.assertEqual(self.page.locator('#input-codigo-p1').input_value(), 'PG-D-CNO-003')
        self.assertIn('controlDeDocumentos', self.page.locator('#matrix-result').inner_text())
        self.assertEqual(self.errors, [])

    def test_storage_fields_follow_provider_and_azure_uses_global_account(self):
        self.page.locator('#nav-settings').click()
        self.page.locator('#company-destination-btn').click()
        self.assertFalse(self.page.locator('#matrix-destination-fields').is_visible())
        self.assertFalse(self.page.locator('#matrix-list-targets').is_visible())
        self.page.locator('#matrix-provider').select_option('s3')
        self.assertTrue(self.page.locator('#matrix-region').is_visible())
        self.assertEqual(self.page.locator('#matrix-target-label').inner_text(), 'Bucket')
        self.page.locator('#matrix-provider').select_option('azure')
        self.assertFalse(self.page.locator('#matrix-region').is_visible())
        self.assertEqual(self.page.locator('#matrix-target-label').inner_text(), 'Contenedor')
        self.assertEqual(self.page.locator('#matrix-account').count(), 0)
        self.page.locator('#company-storage-btn').click()
        self.page.locator('#storage-azure-account').fill('globalaccount')
        self.page.locator('#storage-azure-key').fill('test-key')
        self.page.locator('#storage-credentials-save').click()
        self.page.wait_for_function("document.getElementById('storage-alert').textContent.startsWith('Credenciales guardadas')")
        self.assertEqual(self.page.locator('#storage-azure-key').input_value(), '')
        self.assertEqual(self.page.locator('#storage-azure-key').get_attribute('placeholder'), '••••••••••••')
        with self.page.expect_response(lambda response: '/api/storage-settings' in response.url and response.request.method == 'PUT') as saved:
            self.page.locator('#storage-credentials-save').click()
        self.assertTrue(saved.value.ok)
        credentials = json.loads((self.root / 'integrations/storage.json').read_text())
        self.assertEqual(credentials['azure']['account_key'], 'test-key')

        self.page.locator('#company-destination-btn').click()
        self.page.locator('#matrix-target').fill('company-documents')
        self.page.locator('#destination-save').click()
        self.page.wait_for_function("document.getElementById('destination-alert').textContent.startsWith('Almacenamiento guardado')")
        matrix = json.loads((self.root / 'matrices/zhipos-idpn.net.json').read_text())
        self.assertEqual(matrix['storage']['account'], 'globalaccount')
        self.page.locator('#company-matrix-btn').click()
        self.assertFalse(self.page.locator('#matrix-provider').is_visible())
        self.assertTrue(self.page.locator('#matrix-government').is_visible())
        self.page.locator('#company-storage-btn').click()
        for width in (1440, 390):
            self.page.set_viewport_size({'width':width,'height':1000})
            self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
            self.page.screenshot(path=f'/private/tmp/idpn-credentials-{width}.png', full_page=True)
        self.assertEqual(self.errors, [])

    def test_connection_can_be_saved_and_destinations_selected_without_exposing_token(self):
        self.page.locator('#nav-settings').click()
        self.page.locator('#company-destination-btn').click()
        self.page.locator('#company-storage-btn').click()
        self.page.locator('#storage-s3-key').fill('https://n8n.example.test/webhook/idpn-documentos')
        self.page.locator('#storage-s3-secret').fill('local-test-token')
        self.page.locator('#storage-credentials-save').click()
        self.page.wait_for_function("document.getElementById('storage-alert').textContent.startsWith('Credenciales guardadas')")
        self.assertEqual(self.page.locator('#storage-s3-secret').input_value(), '')
        self.assertEqual(self.page.locator('#storage-s3-secret').get_attribute('placeholder'), '••••••••••••')
        self.page.locator('#company-destination-btn').click()
        self.page.locator('#matrix-provider').select_option('s3')
        self.page.locator('#matrix-region').fill('us-east-1')
        with patch('matrix_api.call_storage') as remote:
            remote.return_value = {'success':True,'complete':True,'targets':[{'name':'company-files'}]}
            self.page.locator('#matrix-list-targets').click()
            self.page.wait_for_function("document.querySelectorAll('#matrix-targets option').length === 1")
        self.assertEqual(self.page.locator('#matrix-targets option').get_attribute('value'), 'company-files')
        self.assertEqual(self.errors, [])

    def test_new_government_entry_is_saved_and_can_be_selected(self):
        self.page.locator('#nav-settings').click()
        self.page.locator('#company-matrix-btn').click()
        self.page.locator('#matrix-add-government').click()
        row = self.page.locator('#matrix-government > .matrix-entry').last
        row.locator('input').nth(0).fill('Gestión de proyectos')
        row.locator('input').nth(1).fill('PRY')
        self.page.locator('#matrix-save').click()
        self.page.wait_for_function("document.getElementById('matrix-alert').textContent.startsWith('Matriz guardada')")
        self.page.locator('#nav-process1').click()
        self.page.locator('#matrix-branch').select_option(label='Gestión de proyectos · PRY')
        self.page.locator('#matrix-type').select_option('instructivo')
        self.file_and_number('IG-D-PRY-003')
        self.page.locator('#matrix-result').wait_for(state='visible')
        self.assertIn('IG-D-PRY-003', self.page.locator('#matrix-result').inner_text())
        stored = json.loads((self.root / 'matrices/zhipos-idpn.net.json').read_text())
        self.assertEqual(stored['government'][-1]['directory'], 'gestionDeProyectos')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
