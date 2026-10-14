"""Offline browser regressions. No site login or external requests are made."""
from pathlib import Path
import json
import re
import sys
import unittest

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from automation import generate_gutenberg_html, get_document_heading, get_document_metadata, remove_cognito_and_folio


def block(name, content=None, children=None, **attrs):
    if content is not None:
        attrs['content'] = content
    return {'name': name, 'attributes': attrs, 'innerBlocks': children or []}


class CleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)
        cls.script = (ROOT / 'wp_cleanup.js').read_text()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.route('**/*', lambda route: route.abort())

    def tearDown(self):
        self.page.close()

    def setup_editor(self, blocks, save_error=False, ignore_save=False):
        self.page.evaluate('''({ blocks, saveError, ignoreSave }) => {
            window.current = blocks;
            window.saved = structuredClone(blocks);
            window.resets = 0;
            const editor = {
                getCurrentPostId: () => 12, getCurrentPostType: () => 'page',
                isSavingPost: () => false, isPostSavingLocked: () => false
            };
            window.wp = {
                blocks: { parse: JSON.parse },
                apiFetch: async () => ({content: {raw: JSON.stringify(saved)}, link: 'https://example.test/folio/'}),
                data: {
                    select: name => name === 'core/block-editor' ? {getBlocks: () => current} :
                        name === 'core/editor' ? editor : {
                            getEntityConfig: () => ({baseURL: '/wp/v2/pages'}),
                            getLastEntitySaveError: () => saveError ? {message: 'Sin permiso'} : null
                        },
                    dispatch: name => name === 'core/block-editor' ? {
                        resetBlocks: blocks => {window.current = blocks; window.resets++;}
                    } : {savePost: async () => {if (!saveError && !ignoreSave) window.saved = structuredClone(current);}}
                }
            };
        }''', {'blocks': blocks, 'saveError': save_error, 'ignoreSave': ignore_save})

    def test_nested_form_and_folio_preserve_document_and_group(self):
        doc = block('embedpress/embedpress', url='https://files.test/book.xlsx')
        self.setup_editor([block('core/group', children=[
            doc, block('core/embed', url='https://www.cognitoforms.com/embed/123'),
            block('core/heading', 'Folio: COD123')])])
        result = self.page.evaluate(self.script)
        self.assertTrue(result['success'])
        self.assertTrue(result['removedCognito'])
        self.assertTrue(result['removedFolio'])
        self.assertEqual(self.page.evaluate('current[0].innerBlocks'), [doc])

    def test_mixed_html_keeps_office_iframe_and_download(self):
        html = ('<h2>Documento</h2><iframe src="https://view.officeapps.live.com/op/embed.aspx?src=file"></iframe>'
                '<a href="https://files.test/file.xlsx">Descargar</a>'
                '<div class="cognito"><iframe src="https://www.cognitoforms.com/embed/123"></iframe></div>'
                '<script src="https://www.cognitoforms.com/f/seamless.js"></script>'
                '<h2><span>Folio: COD123</span></h2>')
        self.setup_editor([block('core/html', html)])
        result = self.page.evaluate(self.script)
        self.assertTrue(result['success'], result)
        content = self.page.evaluate('current[0].attributes.content')
        self.assertIn('officeapps.live.com', content)
        self.assertIn('Descargar', content)
        self.assertNotIn('cognito', content)
        self.assertNotIn('Folio:', content)

    def test_script_only_and_legacy_freeform(self):
        self.setup_editor([
            block('core/html', '<script src="https://www.cognitoforms.com/f/seamless.js"></script>'),
            block('core/freeform', '<p>Conservar</p><iframe src="https://www.cognitoforms.com/embed/123"></iframe>')])
        result = self.page.evaluate(self.script)
        self.assertTrue(result['success'], result)
        self.assertEqual(len(self.page.evaluate('current')), 1)
        self.assertIn('Conservar', self.page.evaluate('current[0].attributes.content'))

    def test_shortcode_and_alternate_attributes(self):
        self.setup_editor([block('core/shortcode', text='[cognitoforms id="123"]'),
                           block('vendor/embed', html='<iframe src="https://www.cognitoforms.com/embed/123"></iframe>')])
        self.assertTrue(self.page.evaluate(self.script)['removedCognito'])
        self.assertEqual(self.page.evaluate('current'), [])

    def test_unrecognized_mixed_content_fails_without_reset(self):
        original = [block('core/html', '<a href="https://www.cognitoforms.com/form">Enlace</a><p>Conservar</p>')]
        self.setup_editor(original)
        self.assertFalse(self.page.evaluate(self.script)['success'])
        self.assertEqual(self.page.evaluate('resets'), 0)
        self.assertEqual(self.page.evaluate('current'), original)

    def test_unrelated_iframe_and_script_are_untouched(self):
        original = [block('core/html', '<iframe src="https://files.test/doc.pdf"></iframe><script src="https://other.test/seamless.js"></script>')]
        self.setup_editor(original)
        self.assertFalse(self.page.evaluate(self.script)['removedCognito'])
        self.assertEqual(self.page.evaluate('resets'), 0)

    def test_synced_pattern_is_not_mistaken_for_a_clean_page(self):
        self.setup_editor([block('core/block', ref=99)])
        self.assertFalse(self.page.evaluate(self.script)['success'])
        self.assertEqual(self.page.evaluate('resets'), 0)

    def test_generated_html_removes_only_marked_sections(self):
        data = {'folio': 'COD123', 'codigo': 'FE-001', 'nombre': 'Mi documento',
                'enlace': 'https://files.test/documento.pdf'}
        html = generate_gutenberg_html({'cognito_script': '<script src="https://www.cognitoforms.com/f/seamless.js"></script>'}, data)
        self.assertEqual(html.count('<!-- wp:html -->'), 1)
        self.assertEqual(html.count('<!-- /wp:html -->'), 1)
        self.assertNotIn('embedpress', html.lower())
        self.setup_editor([block('core/html', html)])
        self.assertEqual(get_document_heading(self.page), 'FE-001 Mi Documento')
        result = self.page.evaluate(self.script)
        self.assertTrue(result['success'], result)
        content = self.page.evaluate('current[0].attributes.content')
        self.assertIn('documento.pdf', content)
        self.assertIn('Descargar archivo PDF', content)
        self.assertIn('FE-001 Mi Documento', content)
        self.assertNotIn('data-idpn-component="cognito"', content)
        self.assertNotIn('data-idpn-component="folio"', content)
        self.assertFalse(self.page.evaluate(self.script)['removedFolio'])

    def test_markers_work_with_changed_text_order_and_cognito_code(self):
        html = ('<section data-idpn-page="1"><div data-idpn-component="cognito"><script src="https://new-provider.test/form.js"></script></div>'
                '<div data-idpn-component="download"><a href="https://files.test/file.pdf">Descargar</a></div>'
                '<div data-idpn-component="folio">Referencia cambiada</div>'
                '<h2 data-idpn-component="heading">Folio: explicación del documento</h2>'
                '<div data-idpn-component="resource"><iframe src="https://www.cognitoforms.com/primary-resource"></iframe></div></section>')
        self.setup_editor([block('core/html', html)])
        result = self.page.evaluate(self.script)
        self.assertTrue(result['success'], result)
        self.assertTrue(result['removedCognito'])
        self.assertTrue(result['removedFolio'])
        content = self.page.evaluate('current[0].attributes.content')
        self.assertIn('primary-resource', content)
        self.assertIn('Folio: explicación', content)
        self.assertNotIn('new-provider.test', content)
        self.assertNotIn('Referencia cambiada', content)

    def test_document_uses_available_width_and_keeps_headings_left_aligned(self):
        html = generate_gutenberg_html({}, {'codigo':'FE-O-ALM-026', 'nombre':'Requisición a Producción', 'folio':'268202', 'enlace':'https://files.test/document.pdf'})
        for width in (1440,390):
            self.page.set_viewport_size({'width':width,'height':1000})
            self.page.set_content('<style>.entry-content,h2,h3{text-align:center}</style><div class="entry-content">'+html+'</div>')
            wrapper = self.page.locator('[data-idpn-page]').bounding_box()
            iframe = self.page.locator('iframe').bounding_box()
            self.assertGreater(iframe['width'], wrapper['width'] * .90)
            self.assertLess(iframe['width'], wrapper['width'])
            for selector in ('[data-idpn-component="heading"]','[data-idpn-component="folio"] h3'):
                self.assertEqual(self.page.locator(selector).evaluate('element => getComputedStyle(element).textAlign'), 'left')
            button = self.page.locator('[data-idpn-component="download"] a')
            bounds = button.bounding_box()
            self.assertAlmostEqual(bounds['x']+bounds['width']/2, wrapper['x']+wrapper['width']/2, delta=2)
            self.assertEqual(button.locator('svg').count(),1)
            self.assertEqual(button.evaluate('element => getComputedStyle(element).backgroundColor'), 'rgb(23, 59, 87)')
            self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
            self.page.screenshot(path=f'/private/tmp/idpn-published-document-{width}.png', full_page=True)

    def test_office_url_encoding_and_download_metadata(self):
        url = 'https://files.test/documento.xlsx?sig=a+b&token=valor%20firmado'
        html = generate_gutenberg_html({}, {'nombre': 'Libro', 'folio': 'COD123', 'enlace': url,
                    'download_url': 'https://files.test/download?file=1&attachment=true', 'filename': 'Libro.xlsx'})
        self.page.set_content(html)
        from urllib.parse import parse_qs, urlparse
        src = self.page.locator('iframe').get_attribute('src')
        self.assertEqual(parse_qs(urlparse(src).query)['src'], [url])
        self.assertEqual(self.page.locator('a').get_attribute('href'), 'https://files.test/download?file=1&attachment=true')
        self.assertEqual(self.page.locator('a').get_attribute('download'), 'Libro.xlsx')

    def test_baja_page_shows_archived_document_and_registration_status(self):
        html = generate_gutenberg_html({}, {'codigo':'PG-D-CNO-003', 'nombre':'Control', 'folio':'268123',
                    'document_status':'baja', 'enlace':'https://files.test/BAJA/control-v002.pdf',
                    'download_url':'https://files.test/download', 'filename':'control-v002.pdf'})
        self.page.set_content(html)
        self.assertEqual(self.page.locator('iframe').get_attribute('src'), 'https://files.test/BAJA/control-v002.pdf')
        self.assertEqual(self.page.locator('[data-idpn-action]').first.get_attribute('data-idpn-action'), 'baja')
        self.assertEqual(self.page.locator('[data-idpn-component="status"]').count(), 0)
        self.assertIn('268123', self.page.locator('[data-idpn-component="folio"]').inner_text())
        self.assertEqual(self.page.locator('a').get_attribute('download'), 'control-v002.pdf')

    def test_form_baja_page_preserves_link_and_marks_internal_record(self):
        html = generate_gutenberg_html({}, {'codigo':'FG-D-CON-005','nombre':'Solicitud','folio':'268123',
                    'document_status':'baja','embed_type':'form','enlace':'https://example.test/formulario'})
        self.page.set_content(html)
        self.assertEqual(self.page.locator('[data-idpn-action]').first.get_attribute('data-idpn-action'), 'baja')
        self.assertEqual(self.page.locator('[data-idpn-component="status"]').count(), 0)
        self.assertEqual(self.page.locator('iframe').count(), 0)
        self.assertEqual(self.page.locator('figure.aligncenter .wp-block-embed__wrapper').inner_text().strip(), 'https://example.test/formulario')
        self.assertEqual(html.count('<!-- wp:embed '), 1)
        self.assertIn('268123', self.page.locator('[data-idpn-component="folio"]').inner_text())

    def test_native_form_embed_survives_review_cleanup_and_has_correct_block_order(self):
        url = 'https://www.cognitoforms.com/example/form?value=a&other=-->'
        html = generate_gutenberg_html({'cognito_script':'<script src="https://www.cognitoforms.com/f/seamless.js" data-form="818"></script>'},
                    {'codigo':'FE-A-COM-015','nombre':'Solicitud de efectivo','folio':'268202','embed_type':'form','enlace':url})
        matches = list(re.finditer(r'<!-- wp:(html|embed)(?: (\{.*?\}))? -->(.*?)<!-- /wp:\1 -->', html, re.S))
        self.assertEqual([match[1] for match in matches], ['html','embed','html'])
        attributes = json.loads(matches[1][2])
        self.assertEqual(attributes['url'], url)
        self.assertEqual(attributes['type'], 'wp-embed')
        self.assertEqual(attributes['align'], 'center')
        self.assertNotIn('height=', matches[1][3])
        self.assertNotIn('<iframe', html)
        self.assertIn('data-idpn-component="heading"', matches[0][3])
        self.assertIn('data-idpn-component="folio"', matches[2][3])
        self.assertIn('data-idpn-component="cognito"', matches[2][3])
        embed = block('core/embed', **attributes)
        self.setup_editor([block('core/html', matches[0][3]), embed, block('core/html', matches[2][3])])
        self.assertEqual(get_document_heading(self.page), 'FE-A-COM-015 Solicitud De Efectivo')
        self.assertEqual(remove_cognito_and_folio(self.page, lambda *args: None), 'https://example.test/folio/')
        self.assertIn(embed, self.page.evaluate('current'))
        self.assertNotIn('data-idpn-component="folio"', self.page.evaluate('JSON.stringify(current)'))
        self.assertEqual(remove_cognito_and_folio(self.page, lambda *args: None), 'https://example.test/folio/')

    def test_internal_actions_are_invisible_and_survive_second_process(self):
        for resource, actions in [('document', ('alta','version','baja')), ('form', ('alta','baja'))]:
            for action in actions:
                html = generate_gutenberg_html({'cognito_script':'<script src="https://www.cognitoforms.com/f/seamless.js"></script>'},
                            {'codigo':'FE-A-COM-015','nombre':'Solicitud','folio':'268202','embed_type':resource,
                             'document_action':action,'enlace':'https://files.test/file.pdf'})
                self.page.set_content(html)
                self.assertNotIn('Registro de baja', self.page.locator('body').inner_text())
                blocks = []
                for match in re.finditer(r'<!-- wp:(html|embed)(?: (\{.*?\}))? -->(.*?)<!-- /wp:\1 -->', html, re.S):
                    blocks.append(block('core/html', match[3]) if match[1] == 'html' else block('core/embed', **json.loads(match[2])))
                self.setup_editor(blocks)
                expected = {'action':action, 'resource':resource}
                self.assertEqual(get_document_metadata(self.page), expected)
                remove_cognito_and_folio(self.page, lambda *args: None)
                self.assertEqual(get_document_metadata(self.page, persisted=True), expected)
                if resource == 'form':
                    self.assertEqual(self.page.evaluate('current.filter(block => block.name === "core/embed").length'), 1)

    def test_conflicting_internal_actions_are_rejected(self):
        self.setup_editor([block('core/html', '<section data-idpn-page="1" data-idpn-action="alta" data-idpn-resource="document"></section><section data-idpn-page="1" data-idpn-action="baja" data-idpn-resource="document"></section>')])
        with self.assertRaisesRegex(Exception, 'contradictorias'):
            get_document_metadata(self.page)

    def test_pdf_mime_type_without_filename_extension(self):
        html = generate_gutenberg_html({}, {'nombre': 'PDF', 'enlace': 'https://files.test/opaque-id', 'mime_type': 'application/pdf'})
        self.page.set_content(html)
        self.assertEqual(self.page.locator('iframe').get_attribute('src'), 'https://files.test/opaque-id')

    def test_policy_and_form_resource(self):
        html = generate_gutenberg_html({}, {'nombre': 'Política', 'codigo': 'IGNORAR', 'is_politica': True,
                    'enlace': 'https://forms.test/form', 'embed_type': 'form'})
        self.page.set_content(html)
        self.assertEqual(self.page.locator('h2').first.inner_text(), 'Política')
        self.assertEqual(self.page.locator('iframe').count(), 0)
        self.assertEqual(self.page.locator('figure.aligncenter .wp-block-embed__wrapper').inner_text().strip(), 'https://forms.test/form')
        self.assertEqual(self.page.locator('[data-idpn-component="download"]').count(), 0)

    def test_text_is_escaped_and_non_http_urls_are_rejected(self):
        html = generate_gutenberg_html({}, {'nombre': 'Documento', 'codigo': '<script>alert(1)</script>',
                    'folio': '<img src=x onerror=alert(1)>', 'enlace': 'https://files.test/file.pdf'})
        self.page.set_content(html)
        self.assertEqual(self.page.locator('script,img').count(), 0)
        with self.assertRaises(ValueError):
            generate_gutenberg_html({}, {'enlace': 'javascript:alert(1)'})

    def test_save_and_read_back_is_idempotent(self):
        self.setup_editor([block('core/html', '<script src="https://www.cognitoforms.com/f/seamless.js"></script>')])
        logs = []
        self.assertEqual(remove_cognito_and_folio(self.page, lambda *args: logs.append(args)), 'https://example.test/folio/')
        self.assertEqual(remove_cognito_and_folio(self.page, lambda *args: logs.append(args)), 'https://example.test/folio/')
        self.assertEqual(self.page.evaluate('resets'), 1)

    def test_save_error_is_not_success(self):
        self.setup_editor([block('core/shortcode', text='[cognitoforms id="123"]')], save_error=True)
        with self.assertRaisesRegex(RuntimeError, 'Sin permiso'):
            remove_cognito_and_folio(self.page, lambda *args: None)

    def test_ignored_save_is_detected_in_persisted_content(self):
        self.setup_editor([block('core/shortcode', text='[cognitoforms id="123"]')], ignore_save=True)
        with self.assertRaisesRegex(RuntimeError, 'todavía presentes'):
            remove_cognito_and_folio(self.page, lambda *args: None)


if __name__ == '__main__':
    unittest.main()
