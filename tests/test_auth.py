"""Account permissions and browser-session isolation without external services."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash
import app as application
from auth import bootstrap, database


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.original=dict(application.app.config)
        self.paths=patch.multiple(application,CONFIG_DIR=str(self.root),SESSION_DIR=str(self.root/'sessions'))
        self.paths.start()
        application.app.config.update(IDPN_CONFIG_DIR=str(self.root),IDPN_AUTH_ENABLED=True,TESTING=True)
        application.is_running=False
        with patch.dict('os.environ',{'IDPN_ADMIN_PASSWORD':'Initial-test-password'}),application.app.app_context():
            bootstrap()
            with database() as db:
                db.execute('UPDATE users SET must_change=0')
                db.execute('INSERT INTO users(id,username,password,admin,edit_companies,must_change) VALUES(?,?,?,?,?,0)',('1'*32,'operator',generate_password_hash('Operator-test-password',method='pbkdf2:sha256:1000000'),0,0))
                db.execute('INSERT INTO users(id,username,password,admin,edit_companies,must_change) VALUES(?,?,?,?,?,0)',('2'*32,'editor',generate_password_hash('Editor-test-password',method='pbkdf2:sha256:1000000'),0,1))
        (self.root/'company.test.json').write_text(json.dumps({'site_name':'Empresa','base_url':'https://company.test','folio_prefix':'123'}))
        self.client=application.app.test_client()

    def tearDown(self):
        application.app.config.clear();application.app.config.update(self.original)
        application.is_running=False;self.paths.stop();self.temp.cleanup()

    def login(self,client=None,name='admin',password='Initial-test-password'):
        client=client or self.client;client.get('/login')
        with client.session_transaction() as session:token=session['csrf']
        response=client.post('/login',data={'username':name,'password':password,'csrf_token':token})
        self.assertEqual(response.status_code,302)
        return client

    def headers(self,client=None):
        with (client or self.client).session_transaction() as session:return {'X-CSRF-Token':session['csrf']}

    def test_anonymous_cannot_read_configuration_run_or_view_browser(self):
        for path in ['/api/configs','/api/storage-settings','/stream-browser','/api/users']:
            self.assertEqual(self.client.get(path).status_code,401)
        self.assertEqual(self.client.get('/primer-proceso').status_code,302)
        self.assertEqual(self.client.get('/healthz').status_code,200)

    def test_operator_can_use_processes_but_cannot_manage_companies_users_or_cloud_keys(self):
        self.login(name='operator',password='Operator-test-password')
        self.assertEqual(self.client.get('/primer-proceso').status_code,200)
        self.assertEqual(self.client.get('/segundo-proceso').status_code,200)
        self.assertEqual(self.client.get('/api/configs').status_code,200)
        for path in ['/api/configs/company.test','/api/storage-settings','/api/users','/configuracion']:
            self.assertEqual(self.client.get(path).status_code,403)
        self.assertEqual(self.client.post('/api/configs',json={'domain':'company.test'},headers=self.headers()).status_code,403)

    def test_delegated_editor_can_edit_existing_company_but_not_create_delete_or_manage_keys(self):
        self.login(name='editor',password='Editor-test-password')
        self.assertEqual(self.client.get('/api/configs/company.test').status_code,200)
        self.assertEqual(self.client.post('/api/configs',json={'domain':'company.test','site_name':'Cambio'},headers=self.headers()).status_code,200)
        self.assertEqual(self.client.post('/api/configs',json={'domain':'new.test'},headers=self.headers()).status_code,403)
        self.assertEqual(self.client.delete('/api/configs/company.test',headers=self.headers()).status_code,403)
        self.assertEqual(self.client.get('/api/storage-settings').status_code,403)

    def test_admin_creates_user_and_password_hash_is_not_exposed(self):
        self.login()
        response=self.client.post('/api/users',json={'username':'new-user','password':'New-user-test-password','can_edit_companies':True},headers=self.headers())
        self.assertEqual(response.status_code,201)
        text=self.client.get('/api/users').get_data(as_text=True)
        self.assertNotIn('New-user-test-password',text);self.assertNotIn('scrypt',text)
        user=next(item for item in json.loads(text) if item['username']=='new-user')
        self.assertTrue(user['can_edit_companies']);self.assertTrue(user['must_change'])

    def test_initial_password_must_change_before_accessing_processes(self):
        with application.app.app_context(),database() as db:db.execute("UPDATE users SET must_change=1 WHERE username='operator'")
        self.login(name='operator',password='Operator-test-password')
        self.assertEqual(self.client.get('/primer-proceso').location,'/cuenta')
        self.assertEqual(self.client.get('/api/configs').status_code,403)
        response=self.client.post('/cuenta',data={'current_password':'Operator-test-password','new_password':'Updated-test-password','confirm_password':'Updated-test-password','csrf_token':self.headers()['X-CSRF-Token']})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.get('/primer-proceso').status_code,200)

    def test_mutations_require_csrf_even_for_administrator(self):
        self.login()
        response=self.client.post('/api/users',json={'username':'forged','password':'New-user-test-password'})
        self.assertEqual(response.status_code,403)
        self.assertFalse(any(row['username']=='forged' for row in self.client.get('/api/users').get_json()))

    def test_other_user_cannot_read_or_control_owner_browser(self):
        self.login(name='operator',password='Operator-test-password')
        application.app.config['IDPN_JOB_OWNER']='2'*32
        for path in ['/stream-browser','/stream-logs']:
            self.assertEqual(self.client.get(path).status_code,403)
        self.assertEqual(self.client.post('/api/browser-event',json={'type':'click'},headers=self.headers()).status_code,403)

    def test_both_processes_use_operator_cookie_directory_not_shared_cookies(self):
        self.login(name='operator',password='Operator-test-password')
        payload={'site':'company.test','folio':'123001','codigo':'FORM-001','nombre':'Formulario','enlace':'https://company.test/form','embed_type':'form','process_type':'create'}
        with patch('app.threading.Thread') as thread:
            response=self.client.post('/api/run',json=payload,headers=self.headers())
        self.assertEqual(response.status_code,200,response.get_json())
        args=thread.call_args.kwargs['args'];expected=str(self.root/'sessions'/('1'*32))
        self.assertEqual(args[1],expected+'/company.test.json');self.assertEqual(args[2]['_session_dir'],expected)
        self.assertEqual(application.app.config['IDPN_JOB_OWNER'],'1'*32)
        application.is_running=False
        with patch('app.threading.Thread') as thread:
            response=self.client.post('/api/run',json={'process_type':'remove','folios':['123001']},headers=self.headers())
        self.assertEqual(response.status_code,200)
        self.assertEqual(thread.call_args.kwargs['args'][2]['_session_dir'],expected)

    def test_permission_change_invalidates_old_session_and_disabled_users_cannot_log_in(self):
        operator=self.login(application.app.test_client(),name='operator',password='Operator-test-password');self.login()
        self.assertEqual(self.client.patch('/api/users/'+('1'*32),json={'active':False},headers=self.headers()).status_code,200)
        self.assertEqual(operator.get('/api/configs').status_code,401)
        operator.get('/login')
        response=operator.post('/login',data={'username':'operator','password':'Operator-test-password','csrf_token':self.headers(operator)['X-CSRF-Token']})
        self.assertEqual(response.status_code,401)

    def test_losing_runner_race_does_not_clear_another_users_logs(self):
        self.login(name='operator',password='Operator-test-password')
        marker={'message':'otra ejecución conserva su consola'}
        def company(_domain):
            application.is_running=True
            application.log_queue.put(marker)
            return {'base_url':'https://company.test'},None
        payload={'site':'company.test','folio':'123001','codigo':'FORM-001','nombre':'Formulario','enlace':'https://company.test/form','embed_type':'form','process_type':'create'}
        with patch('app.company_paths',side_effect=company):
            response=self.client.post('/api/run',json=payload,headers=self.headers())
        self.assertEqual(response.status_code,400)
        entries=[]
        while not application.log_queue.empty():entries.append(application.log_queue.get_nowait())
        self.assertIn(marker,entries)

    def test_administrator_cannot_disable_or_demote_their_own_account(self):
        self.login()
        admin=next(u for u in self.client.get('/api/users').get_json() if u['admin'])
        self.assertEqual(self.client.patch('/api/users/'+admin['id'],json={'admin':False},headers=self.headers()).status_code,400)
        self.assertEqual(self.client.patch('/api/users/'+admin['id'],json={'active':False},headers=self.headers()).status_code,400)


if __name__=='__main__':unittest.main()
