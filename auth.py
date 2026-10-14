"""Application accounts, role permissions, CSRF and per-user browser ownership."""
from collections import defaultdict, deque
from datetime import timedelta
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import time
import uuid

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash

auth = Blueprint('auth', __name__)
bootstrap_lock = threading.Lock()
login_lock = threading.Lock()
attempts = defaultdict(deque)


def auth_root():
    root = Path(current_app.config['IDPN_CONFIG_DIR']) / 'auth'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def database():
    path = auth_root() / 'users.sqlite'
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, admin INTEGER NOT NULL, edit_companies INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, must_change INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1)')
    path.chmod(0o600)
    return db


def public_user(row):
    return {'id':row['id'],'username':row['username'],'admin':bool(row['admin']),
            'can_edit_companies':bool(row['admin'] or row['edit_companies']), 'active':bool(row['active']), 'must_change':bool(row['must_change'])}


def validate_password(value):
    if not isinstance(value, str) or len(value) < 12 or len(value) > 256:
        raise ValueError('La contraseña debe tener entre 12 y 256 caracteres.')


def validate_username(value):
    import re
    name = str(value).strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,63}', name):
        raise ValueError('El usuario debe tener entre 3 y 64 caracteres: letras, números, punto, guion o guion bajo.')
    return name


def secret_key():
    path = auth_root() / 'session-key'
    if not path.exists():
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as file:
            file.write(secrets.token_hex(32)); temp = Path(file.name)
        try:
            os.link(temp, path)
        except FileExistsError:
            pass
        finally:
            temp.unlink()
    return path.read_text()


def bootstrap():
    with bootstrap_lock, database() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]:
            return
        username = validate_username(os.getenv('IDPN_ADMIN_USERNAME', 'admin'))
        password = os.getenv('IDPN_ADMIN_PASSWORD') or secrets.token_urlsafe(18)
        validate_password(password)
        db.execute('INSERT INTO users(id,username,password,admin,edit_companies,must_change) VALUES(?,?,?,1,1,1)',
                   (uuid.uuid4().hex,username,generate_password_hash(password, method='pbkdf2:sha256:1000000')))
        path = auth_root() / 'initial-admin.txt'
        # Server-only bootstrap credential; never baked into a Docker image.
        with open(path, 'w', opener=lambda path, flags:os.open(path,flags,0o600)) as file:
            file.write('Usuario: '+username+'\nContraseña inicial: '+password+'\n')
        path.chmod(0o600)


def current_user():
    if not current_app.config.get('IDPN_AUTH_ENABLED', True):
        return None
    user_id = session.get('user_id')
    if not user_id:
        return None
    with database() as db:
        row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    if not row or not row['active'] or row['version'] != session.get('version'):
        session.clear()
        return None
    return row


def csrf_token():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']


def initialize_auth(app):
    app.config.setdefault('IDPN_AUTH_ENABLED', True)
    with app.app_context():
        app.secret_key = secret_key() if app.config.get('IDPN_CONFIG_DIR') else secrets.token_hex(32)
        bootstrap()
    app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=os.getenv('IDPN_COOKIE_SECURE','false').lower() == 'true',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    app.register_blueprint(auth)
    app.jinja_env.globals['csrf_token'] = csrf_token

    @app.context_processor
    def user_context():
        user = getattr(g,'user',None)
        return {'idpn_user':public_user(user) if user else None}

    @app.before_request
    def guard():
        if not app.config.get('IDPN_AUTH_ENABLED', True):
            return
        g.user = current_user()
        public = request.endpoint in ('auth.login','health','static','storage_download.public_download')
        if not public and not g.user:
            if request.path.startswith('/api/') or request.path.startswith('/stream-'):
                return jsonify(error='Inicia sesión para continuar.'),401
            return redirect(url_for('auth.login'))
        if request.method not in ('GET','HEAD','OPTIONS'):
            expected = session.get('csrf','')
            actual = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token','')
            if not expected or not hmac.compare_digest(expected,actual):
                return jsonify(error='La sesión del formulario caducó. Recarga la página.'),403
        if g.user and g.user['must_change'] and request.endpoint not in ('auth.account','auth.logout','static','health'):
            if request.path.startswith('/api/') or request.path.startswith('/stream-'):
                return jsonify(error='Cambia tu contraseña inicial para continuar.'),403
            return redirect(url_for('auth.account'))
        if not g.user:
            return
        admin, edit = bool(g.user['admin']), bool(g.user['admin'] or g.user['edit_companies'])
        path = request.path
        denied = False
        if path.startswith('/api/users') or path == '/usuarios' or path == '/api/storage-settings':
            denied = not admin
        elif path == '/configuracion' or (path.startswith('/api/configs/') and request.method == 'GET'):
            denied = not edit
        elif path.startswith('/api/matrices/') and request.method == 'PUT':
            denied = not edit
        elif path == '/api/storage-targets':
            denied = not edit
        elif path.startswith('/api/configs/') and request.method == 'DELETE':
            denied = not admin
        elif path == '/api/configs' and request.method == 'POST':
            data = request.get_json(silent=True) or {}
            domain = str(data.get('domain','')).strip().lower().replace('https://','').replace('http://','').split('/')[0]
            exists = '/' not in domain and '..' not in domain and (Path(app.config['IDPN_CONFIG_DIR'])/(domain+'.json')).is_file()
            denied = not (admin or (edit and exists))
        if denied:
            return jsonify(error='No tienes permiso para esta acción.'),403
        if path in ('/stream-logs','/stream-browser','/api/browser-event'):
            owner = app.config.get('IDPN_JOB_OWNER')
            if owner != g.user['id']:
                return jsonify(error='El navegador pertenece a otro usuario o no hay una ejecución propia.'),403


@auth.route('/login', methods=['GET','POST'])
def login():
    error = None
    if request.method == 'POST':
        name = request.form.get('username','').strip().lower()[:65]
        key = (request.remote_addr,name)
        now = time.monotonic()
        with login_lock:
            if len(attempts) > 1024:
                attempts.clear()
            queue = attempts[key]
            while queue and now - queue[0] > 300:
                queue.popleft()
            blocked = len(queue) >= 5
            if not blocked:
                queue.append(now)
        if blocked:
            return render_template('login.html',error='Demasiados intentos. Espera cinco minutos.'),429
        with database() as db:
            row = db.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
        submitted_password = request.form.get('password','')
        if row and row['active'] and len(submitted_password) <= 256 and check_password_hash(row['password'],submitted_password):
            session.clear(); session.update(user_id=row['id'],version=row['version']); session.permanent=True
            csrf_token()
            with login_lock:
                attempts.pop(key,None)
            return redirect(url_for('auth.account') if row['must_change'] else '/primer-proceso')
        error = 'Usuario o contraseña incorrectos.'
    return render_template('login.html',error=error),401 if error else 200


@auth.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


@auth.route('/cuenta', methods=['GET','POST'])
def account():
    error, success = None, None
    if request.method == 'POST':
        try:
            validate_password(request.form.get('new_password'))
            if request.form.get('new_password') != request.form.get('confirm_password'):
                raise ValueError('La confirmación de contraseña no coincide.')
            if not check_password_hash(g.user['password'],request.form.get('current_password','')):
                raise ValueError('La contraseña actual no coincide.')
            with database() as db:
                db.execute('UPDATE users SET password=?,must_change=0,version=version+1 WHERE id=?',
                           (generate_password_hash(request.form['new_password'], method='pbkdf2:sha256:1000000'),g.user['id']))
            session['version'] = g.user['version'] + 1
            if g.user['admin']:
                (auth_root()/'initial-admin.txt').unlink(missing_ok=True)
            return redirect('/primer-proceso')
        except ValueError as exc:
            error = str(exc)
    return render_template('account.html', error=error)


@auth.route('/usuarios')
def users_page():
    return render_template('users.html')


@auth.route('/api/users', methods=['GET','POST'])
def users():
    if request.method == 'GET':
        with database() as db:
            return jsonify([public_user(row) for row in db.execute('SELECT * FROM users ORDER BY username')])
    try:
        data = request.get_json(silent=True) or {}
        username = validate_username(data.get('username','')); validate_password(data.get('password'))
        with database() as db:
            db.execute('INSERT INTO users(id,username,password,admin,edit_companies,must_change) VALUES(?,?,?,?,?,1)',
                (uuid.uuid4().hex,username,generate_password_hash(data['password'], method='pbkdf2:sha256:1000000'),bool(data.get('admin')),bool(data.get('can_edit_companies'))))
        return jsonify(success=True),201
    except (ValueError,sqlite3.IntegrityError) as error:
        return jsonify(error=str(error) if isinstance(error,ValueError) else 'Ese usuario ya existe.'),400


@auth.route('/api/users/<user_id>', methods=['PATCH'])
def edit_user(user_id):
    try:
        data = request.get_json(silent=True) or {}
        with database() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
            if not row:
                return jsonify(error='Usuario no encontrado.'),404
            admin = bool(data.get('admin',row['admin'])); active = bool(data.get('active',row['active']))
            if row['id'] == g.user['id'] and (not admin or not active):
                raise ValueError('No puedes desactivar ni quitar tu propio acceso de administrador.')
            if row['admin'] and row['active'] and (not admin or not active) and db.execute('SELECT COUNT(*) FROM users WHERE admin=1 AND active=1').fetchone()[0] <= 1:
                raise ValueError('Debe existir al menos un administrador activo.')
            username = validate_username(data.get('username',row['username']))
            password = data.get('password')
            if password:
                validate_password(password)
            db.execute('UPDATE users SET username=?,admin=?,edit_companies=?,active=?,password=?,must_change=?,version=version+1 WHERE id=?',
                (username,admin,bool(data.get('can_edit_companies',row['edit_companies'])),active,
                 generate_password_hash(password, method='pbkdf2:sha256:1000000') if password else row['password'],1 if password else row['must_change'],user_id))
            if row['id'] == g.user['id']:
                session['version'] = row['version'] + 1
        return jsonify(success=True)
    except (ValueError,sqlite3.IntegrityError) as error:
        return jsonify(error=str(error) if isinstance(error,ValueError) else 'Ese usuario ya existe.'),400
