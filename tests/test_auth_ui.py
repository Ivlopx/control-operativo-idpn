"""Login and administrator UI tested with a temporary local account database."""
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
import app as application
from auth import bootstrap


class AuthUITests(unittest.TestCase):
    def test_admin_and_operator_flow_with_first_password_change(self):
        with tempfile.TemporaryDirectory() as directory, patch.multiple(application,CONFIG_DIR=directory,SESSION_DIR=directory+'/sessions'):
            previous=dict(application.app.config)
            application.app.config.update(IDPN_CONFIG_DIR=directory,IDPN_AUTH_ENABLED=True,SESSION_COOKIE_SECURE=False)
            with patch.dict(os.environ,{'IDPN_ADMIN_PASSWORD':'Initial-browser-password'}), application.app.app_context():bootstrap()
            server=make_server('127.0.0.1',0,application.app,threaded=True)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with sync_playwright() as pw:
                    browser=pw.chromium.launch(headless=True)
                    page=browser.new_page(viewport={'width':1440,'height':1000})
                    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    page.route('**/*',lambda route:route.continue_() if route.request.url.startswith('http://127.0.0.1:') else route.abort())
                    base=f'http://127.0.0.1:{server.server_port}'
                    page.goto(base+'/primer-proceso');self.assertIn('/login',page.url)
                    page.locator('#username').fill('admin');page.locator('#password').fill('Initial-browser-password');page.get_by_role('button',name='Entrar',exact=True).click()
                    page.wait_for_url('**/cuenta')
                    self.assertEqual(page.get_by_role('heading',level=1).inner_text(),'Cambiar contraseña')
                    page.screenshot(path='/private/tmp/idpn-password-desktop.png',full_page=True)
                    page.set_viewport_size({'width':390,'height':844});page.screenshot(path='/private/tmp/idpn-password-mobile.png',full_page=True)
                    page.set_viewport_size({'width':1440,'height':1000})
                    page.locator('#current_password').fill('Initial-browser-password');page.locator('#new_password').fill('Changed-browser-password');page.locator('#confirm_password').fill('Changed-browser-password');page.get_by_role('button',name='Guardar contraseña').click()
                    page.wait_for_url('**/primer-proceso')
                    self.assertTrue(page.locator('#nav-settings').is_visible())
                    for tab in ('browser','console'):
                        page.locator(f'#right-tab-{tab}-btn').click()
                        chip = page.locator('.account-menu > summary').bounding_box()
                        page.get_by_role('button',name='Ampliar visor',exact=True).click()
                        self.assertEqual(page.locator('body > #right-panel').count(), 1)
                        self.assertTrue(page.locator('body > .container').evaluate('element => element.inert'))
                        self.assertTrue(page.evaluate('(point) => !!document.elementFromPoint(point.x, point.y).closest("#right-panel")', {'x':chip['x']+chip['width']/2,'y':chip['y']+chip['height']/2}))
                        page.keyboard.press('Escape')
                        self.assertEqual(page.locator('#right-panel-container-p1 #right-panel').count(), 1)
                        self.assertFalse(page.locator('body > .container').evaluate('element => element.inert'))

                    page.screenshot(path='/private/tmp/idpn-main-header-desktop.png',full_page=True)
                    page.locator('.account-menu > summary').click()
                    page.get_by_role('link',name='Usuarios',exact=True).click()
                    page.locator('#new-username').fill('operador');page.locator('#new-user-password').fill('Operator-browser-password');page.get_by_role('button',name='Crear usuario',exact=True).click()
                    page.wait_for_function("document.querySelectorAll('#users-list .user-card').length === 2")
                    self.assertEqual(page.locator('#new-user-password').input_value(),'')
                    operator=page.locator('.user-card').filter(has=page.get_by_role('heading',name='operador',exact=True))
                    permission=operator.get_by_role('switch',name='Puede editar empresas: operador',exact=True)
                    permission.check();operator.get_by_role('button',name='Guardar',exact=True).click()
                    page.wait_for_function("document.getElementById('users-message').textContent === 'Usuario actualizado.'")
                    page.wait_for_function("document.getElementById('users-list').querySelector('[aria-label=\"Puede editar empresas: operador\"]').checked")
                    users=page.request.get(base+'/api/users').json()
                    self.assertTrue(next(user for user in users if user['username']=='operador')['can_edit_companies'])
                    permission.uncheck();operator.get_by_role('button',name='Guardar',exact=True).click()
                    page.wait_for_function("document.getElementById('users-list').querySelector('[aria-label=\"Puede editar empresas: operador\"]').checked === false")
                    page.wait_for_timeout(100)
                    page.screenshot(path='/private/tmp/idpn-auth-users-desktop.png',full_page=True)
                    page.set_viewport_size({'width':390,'height':844});page.screenshot(path='/private/tmp/idpn-auth-users-mobile.png',full_page=True)
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                    page.locator('.account-menu > summary').click()
                    page.get_by_role('button',name='Cerrar sesión',exact=True).click();page.wait_for_url('**/login')
                    page.locator('#username').fill('operador');page.locator('#password').fill('Operator-browser-password');page.get_by_role('button',name='Entrar',exact=True).click()
                    page.wait_for_url('**/cuenta')
                    page.locator('#current_password').fill('Operator-browser-password');page.locator('#new_password').fill('Operator-changed-password');page.locator('#confirm_password').fill('Operator-changed-password');page.get_by_role('button',name='Guardar contraseña').click()
                    page.wait_for_url('**/primer-proceso')
                    self.assertFalse(page.locator('#nav-settings').is_visible());self.assertEqual(page.get_by_role('link',name='Usuarios',exact=True).count(),0)
                    page.locator('#nav-process2').click();self.assertIn('/segundo-proceso',page.url)
                    self.assertEqual(errors,[])
                    browser.close()
            finally:
                server.shutdown();thread.join();application.app.config.clear();application.app.config.update(previous)


if __name__=='__main__':unittest.main()
