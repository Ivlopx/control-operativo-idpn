import os
import json
import queue
import threading
import time
import tempfile
from pathlib import Path
import uuid
from flask import Flask, render_template, jsonify, request, Response
from werkzeug.exceptions import RequestEntityTooLarge

from automation import clean_name, run_wp_automation, run_wp_remove_multiple_automation
from matrix_api import matrix_api
from storage_credentials import require_credentials
from auth import initialize_auth
from flask import g
from storage_download import storage_download
from matrix_api import company_paths, load_company_matrix
from document_matrix import plan_document
from document_transfer import transfer_document
from baja_records import prepare_record, attach_reference, save_record, finalize_folio

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Core Directories
CONFIG_DIR = os.getenv('IDPN_CONFIG_DIR', os.path.join(os.path.dirname(__file__), 'config'))
SESSION_DIR = os.getenv('IDPN_SESSION_DIR', os.path.join(os.path.dirname(__file__), 'sessions'))

app.config['IDPN_CONFIG_DIR'] = CONFIG_DIR
app.config['IDPN_UPLOAD_DIR'] = os.getenv('IDPN_UPLOAD_DIR', str(Path(__file__).parent/'.uploads'))
app.register_blueprint(matrix_api)
app.register_blueprint(storage_download)

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(SESSION_DIR, exist_ok=True)
initialize_auth(app)

# Thread-safe queue for streaming logs to frontend
log_queue = queue.Queue()
active_thread = None
is_running = False
run_lock = threading.Lock()

# Global queues for browser streaming and events
browser_frame_queue = queue.Queue(maxsize=1)
browser_event_queue = queue.Queue()

def log_callback(message, level="info", folio_status=None):
    """
    Callback function passed to the automation module to log steps.
    Pushes logs to the global log queue.
    """
    log_entry = {
        "time": time.strftime("%H:%M:%S"),
        "message": message,
        "level": level
    }
    if folio_status is not None:
        log_entry["folio_status"] = folio_status
    log_queue.put(log_entry)
    print(f"[{level.upper()}] {message}")

def run_in_background(config_data, session_file, page_data):
    """
    Runs the Playwright automation script in a background thread and updates global status.
    """
    global is_running
    is_running = True
    final_url = ""
    header_text = ""
    success = False
    try:
        log_callback("Iniciando tarea de automatización en segundo plano...", "info")
        
        # Clear browser queues
        while not browser_frame_queue.empty():
            try:
                browser_frame_queue.get_nowait()
            except queue.Empty:
                break
        while not browser_event_queue.empty():
            try:
                browser_event_queue.get_nowait()
            except queue.Empty:
                break
                
        def frame_cb(base64_data):
            if browser_frame_queue.full():
                try:
                    browser_frame_queue.get_nowait()
                except queue.Empty:
                    pass
            browser_frame_queue.put(base64_data)
            
        process_type = page_data.get('process_type', 'create')
        
        baja_record = None
        if process_type == 'remove':
            failures = []
            def batch_log(*args, **kwargs):
                if (kwargs.get('folio_status') or {}).get('status') in ('failed', 'unknown', 'error'):
                    failures.append(kwargs['folio_status'])
                log_callback(*args, **kwargs)
            def after_remove(domain, folio):
                with app.app_context():
                    if finalize_folio(domain, folio):
                        log_callback('Baja finalizada: antecedentes archivados y duplicados retirados; se conserva el testigo.', 'success')
            # Call multiple remove function
            run_wp_remove_multiple_automation(
                config_dir=CONFIG_DIR,
                session_dir=page_data.get('_session_dir', SESSION_DIR),
                folios=page_data.get('folios', []),
                log_callback=batch_log,
                after_remove=after_remove,
                frame_callback=frame_cb,
                event_queue=browser_event_queue
            )
            final_url = ""
            header_text = ""
            log_callback("Proceso por lotes finalizado.", 'warning' if failures else 'success')
            success = not failures
        else:
            if page_data.get('document_plan'):
                plan = page_data['document_plan']
                log_callback('Procesando el documento en el almacenamiento...', 'info')
                with app.app_context():
                    if plan.get('defer_archive'):
                        baja_record = prepare_record(plan, page_data['operation_id'], page_data['folio'])
                    stored = transfer_document(plan, page_data['operation_id'], page_data.get('temporary_file'))
                    if baja_record:
                        attach_reference(baja_record, stored)
                log_callback('Operación de almacenamiento confirmada.', 'success')
                if plan['action'] == 'baja':
                    page_data['document_status'] = 'baja'
                    page_data['nombre'] = page_data.get('nombre') or stored.get('name') or stored['filename']
                    log_callback('Publicando el registro de baja con la última versión archivada...', 'info')
                page_data.update(enlace=stored['url'], download_url=stored['download_url'], filename=stored['filename'])
            automation_result = run_wp_automation(
                config_data, session_file, page_data, log_callback,
                frame_callback=frame_cb, event_queue=browser_event_queue
            )
            
            if isinstance(automation_result, dict):
                final_url = automation_result.get('url', '')
                header_text = automation_result.get('header', '')
            else:
                final_url = automation_result or ""
                header_text = page_data.get('folio', '')
                
            if final_url:
                if baja_record:
                    with app.app_context():
                        baja_record.update(published=True, page_url=final_url)
                        save_record(baja_record)
                success = True
                log_callback(f"Proceso finalizado con éxito. Página: {final_url}", "success")
            else:
                log_callback("El proceso finalizó pero no se pudo confirmar la publicación de la página.", "warning")
    except Exception as e:
        log_callback(f"Error inesperado durante la automatización: {str(e)}", "error")
    finally:
        if page_data.get('temporary_file'):
            Path(page_data['temporary_file']).unlink(missing_ok=True)
        with run_lock:
            # Send a special finish event so the client knows it's done
            log_queue.put({
                "time": time.strftime("%H:%M:%S"),
                "message": "FIN_AUTOMATIZACION",
                "level": "done",
                "success": success,
                "document_action": (page_data.get('document_plan') or {}).get('action') or page_data.get('document_status'),
                "url": final_url,
                "folio": header_text or page_data.get('folio', '')
            })
            is_running = False

@app.route('/healthz')
def health():
    return jsonify(status='ok')


@app.route('/')
@app.route('/primer-proceso')
@app.route('/segundo-proceso')
@app.route('/configuracion')
def index():
    """Renders the main dashboard page."""
    return render_template('index.html')

@app.route('/api/configs', methods=['GET'])
def get_configs():
    """Lists all available website configurations."""
    configs = []
    for file in os.listdir(CONFIG_DIR):
        if file.endswith('.json'):
            file_path = os.path.join(CONFIG_DIR, file)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    configs.append({
                        "filename": file,
                        "domain": file.replace('.json', ''),
                        "site_name": data.get('site_name', file),
                        "folio_prefix": data.get('folio_prefix', '')
                    })
            except Exception:
                pass
    return jsonify(configs)

@app.route('/api/configs/<domain>', methods=['GET'])
def get_config_detail(domain):
    """Retrieves detailed settings for a specific website configuration."""
    file_path = os.path.join(CONFIG_DIR, f"{domain}.json")
    if not os.path.exists(file_path):
        return jsonify({"error": "Configuración no encontrada"}), 404
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": f"Error al leer archivo: {str(e)}"}), 500

@app.route('/api/configs', methods=['POST'])
def save_config():
    """Creates or updates a website configuration JSON file."""
    data = request.json
    domain = data.get('domain', '').strip().replace(' ', '_').lower()
    
    if not domain:
        return jsonify({"error": "El dominio/nombre es obligatorio"}), 400
        
    # Ensure domain ends properly or clean it
    domain = domain.replace('https://', '').replace('http://', '').split('/')[0]
    import re
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]*',domain) or '..' in domain:
        return jsonify(error='Identificador de empresa inválido.'),400
    
    file_path = os.path.join(CONFIG_DIR, f"{domain}.json")
    
    # Structure of config
    config_payload = {
        "site_name": data.get('site_name', domain).strip(),
        "base_url": data.get('base_url', f"https://{domain}").strip(),
        "login_url": data.get('login_url', f"https://{domain}/wp-login.php").strip(),
        "create_page_url": data.get('create_page_url', f"https://{domain}/wp-admin/post-new.php?post_type=page").strip(),
        "cognito_script": data.get('cognito_script', '').strip(),
        "folio_prefix": data.get('folio_prefix', '').strip().upper(),
        "ultimate_member": {
            "restrict_access": data.get('restrict_access', True),
            "accessible_to": data.get('accessible_to', '2'),
            "roles": data.get('roles', ["administrator", "editor", "author", "contributor"]),
            "no_access_action": data.get('no_access_action', '0')
        },
        "astra_settings": {
            "container_layout": data.get('container_layout', 'normal-width-container').strip(),
            "container_style": data.get('container_style', 'Recuadro').strip(),
            "sidebar_layout": data.get('sidebar_layout', 'right-sidebar').strip()
        }
    }
    
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(config_payload, f, indent=2, ensure_ascii=False)
        return jsonify({"success": True, "domain": domain})
    except Exception as e:
        return jsonify({"error": f"Error al guardar: {str(e)}"}), 500

@app.route('/api/configs/<domain>', methods=['DELETE'])
def delete_config(domain):
    """Deletes a website configuration."""
    file_path = os.path.join(CONFIG_DIR, f"{domain}.json")
    if os.path.exists(file_path):
        os.remove(file_path)
        # Remove sessions for this company, including individual user sessions.
        for individual in Path(SESSION_DIR).glob(f"*/{domain}.json"):
            individual.unlink(missing_ok=True)
        # Also remove matching legacy session file if exists
        session_file = os.path.join(SESSION_DIR, f"{domain}.json")
        if os.path.exists(session_file):
            os.remove(session_file)
        return jsonify({"success": True})
    return jsonify({"error": "Configuración no encontrada"}), 404

@app.route('/api/run', methods=['POST'])
def start_automation():
    """Starts the page-creation/removal automation process in the background."""
    global active_thread, is_running
    
    if is_running:
        return jsonify({"error": "Ya hay una tarea de automatización ejecutándose."}), 400
        
    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    if not isinstance(data,dict):
        return jsonify(error='Solicitud inválida.'),400
    process_type = data.get('process_type', 'create').strip()
    if process_type not in ['create', 'remove']:
        process_type = 'create'
        
    user = getattr(g, 'user', None)
    user_session_dir = os.path.join(SESSION_DIR, user['id']) if user else SESSION_DIR
    os.makedirs(user_session_dir, exist_ok=True, mode=0o700)
    config_data = None
    session_file = None
    
    page_data = {
        '_session_dir': user_session_dir,
        "codigo": data.get('codigo', '').strip().upper(),
        "nombre": clean_name(data.get('nombre', '').strip()),
        "enlace": data.get('enlace', '').strip(),
        "download_url": data.get('download_url', '').strip(),
        "filename": data.get('filename', '').strip(),
        "mime_type": data.get('mime_type', '').strip(),
        "embed_type": data.get('embed_type', 'document').strip(),
        "is_politica": str(data.get('is_politica', False)).lower() in ('true','1'),
        "process_type": process_type
    }
    
    if process_type == 'create':
        domain = data.get('site', '').strip()
        try:
            config_data, _ = company_paths(domain)
        except (ValueError,FileNotFoundError) as error:
            return jsonify(error=str(error)),400
            
        session_file = os.path.join(user_session_dir, f"{domain}.json")
        
        page_data["folio"] = data.get('folio', '').strip()
        if page_data['is_politica'] and page_data['embed_type'] == 'document':
            page_data['codigo'] = ''
        if not page_data['folio']:
            return jsonify(error='El campo Folio es obligatorio.'),400
        if page_data['embed_type'] == 'form':
            form_action = data.get('action', 'alta')
            if form_action not in ('alta', 'baja'):
                return jsonify(error='El formulario solo admite Alta o Baja.'),400
            if not page_data['codigo'] or not page_data['nombre']:
                return jsonify(error='Captura el código y el nombre del formulario.'),400
            if form_action == 'baja':
                page_data['document_status'] = 'baja'
        classification = data.get('classification')
        if classification and page_data['embed_type'] != 'form':
            try:
                if isinstance(classification,str):
                    classification = json.loads(classification)
                if not isinstance(classification,dict):
                    raise ValueError('Clasificación inválida.')
                classification.update(is_politica=page_data['is_politica'],code=page_data['codigo'],name=page_data['nombre'],filename=request.files['file'].filename if 'file' in request.files else '')
                plan = plan_document(load_company_matrix(domain),classification)
                page_data['codigo'] = plan['code']
                page_data['embed_type'] = 'document' if plan['store_file'] else 'form'
                if plan['store_file']:
                    require_credentials(plan['storage'])
                    if not plan['storage_ready']:
                        raise ValueError('Configura el destino S3/Azure de esta rama en la matriz.')
                    operation_id = str(data.get('operation_id') or uuid.uuid4())
                    if len(operation_id) > 80 or not all(c.isalnum() or c in '-_' for c in operation_id) or len(operation_id) < 16:
                        raise ValueError('Identificador de operación inválido.')
                    plan['site'] = domain
                    if plan['action'] == 'baja':
                        if 'file' not in request.files:
                            raise ValueError('Selecciona el archivo testigo de la baja.')
                        plan['defer_archive'] = True
                    page_data.update(document_plan=plan,operation_id=operation_id)
                    if plan['action'] != 'baja' or 'file' in request.files:
                        if not page_data['folio']:
                            raise ValueError('El campo Folio es obligatorio para publicar.')
                        file = request.files.get('file')
                        if not file:
                            raise ValueError('Selecciona el documento.')
                        upload_dir = Path(app.config.get('IDPN_UPLOAD_DIR', str(Path(__file__).parent/'.uploads')))
                        upload_dir.mkdir(parents=True,exist_ok=True)
                        with tempfile.NamedTemporaryFile(dir=upload_dir,suffix=Path(file.filename).suffix.lower(),delete=False) as temporary:
                            file.save(temporary)
                            page_data['temporary_file'] = temporary.name
                        size = Path(temporary.name).stat().st_size
                        if not size or size > 15*1024*1024:
                            Path(temporary.name).unlink()
                            page_data.pop('temporary_file',None)
                            raise ValueError('El archivo debe contener datos y pesar hasta 15 MiB.')
            except (ValueError,TypeError,KeyError) as error:
                return jsonify(error=str(error)),400
        if not page_data['folio']:
            return jsonify({"error": "El campo Folio es obligatorio."}), 400
        if not page_data['enlace'] and not page_data.get('document_plan'):
            return jsonify({"error": "El campo Enlace (URL del documento) es obligatorio para el proceso de creación."}), 400
    else:
        # Process 2 (multiple folios)
        folios = data.get('folios', [])
        folios = [f.strip() for f in folios if f and f.strip()]
        if not folios and data.get('folio'):
            folios = [data.get('folio').strip()]
        if not folios:
            return jsonify({"error": "Debes ingresar al menos un folio."}), 400
        page_data["folios"] = folios
        page_data["folio"] = folios[0] # fallback

    # Start thread
    with run_lock:
        if is_running:
            if page_data.get('temporary_file'):
                Path(page_data['temporary_file']).unlink(missing_ok=True)
            return jsonify(error='Ya hay una tarea ejecutándose.'),400
        # Clear only after acquiring the runner, never another user's live logs.
        while not log_queue.empty():
            try:
                log_queue.get_nowait()
            except queue.Empty:
                break
        is_running = True
        app.config['IDPN_JOB_OWNER'] = user['id'] if user else None
        active_thread = threading.Thread(target=run_in_background,args=(config_data, session_file, page_data))
        active_thread.start()
    
    return jsonify({"success": True, "message": "Automatización iniciada."})


@app.errorhandler(RequestEntityTooLarge)
def too_large(_error):
    return jsonify(error='El archivo excede el límite de carga. Selecciona uno de hasta 15 MiB.'),413

@app.route('/api/status', methods=['GET'])
def get_status():
    """Checks the current runner status."""
    return jsonify({"is_running": is_running})

@app.route('/stream-logs')
def stream_logs():
    """SSE endpoint for streaming real-time automation status logs."""
    owner = getattr(g,'user',None)
    owner_id = owner['id'] if owner else None
    owner_version = owner['version'] if owner else None
    def allowed():
        if not app.config.get('IDPN_AUTH_ENABLED',True):
            return True
        if app.config.get('IDPN_JOB_OWNER') != owner_id:
            return False
        from auth import database
        with app.app_context(), database() as db:
            row = db.execute('SELECT active,version FROM users WHERE id=?',(owner_id,)).fetchone()
            return bool(row and row['active'] and row['version'] == owner_version)
    def event_stream():
        # Yield first connection event
        yield f"data: {json.dumps({'time': time.strftime('%H:%M:%S'), 'message': 'Conexión con el servidor establecida.', 'level': 'info'})}\n\n"
        
        while allowed():
            try:
                # Wait up to 10 seconds for a new log entry
                log_entry = log_queue.get(timeout=10)
                if not allowed():
                    break
                yield f"data: {json.dumps(log_entry)}\n\n"
                
                # Exit connection if runner is done
                if isinstance(log_entry, dict) and log_entry.get('level') == 'done':
                    break
            except queue.Empty:
                # Send heartbeat to keep connection alive
                if not allowed():
                    break
                yield f"data: {json.dumps({'heartbeat': True})}\n\n"
            except Exception as e:
                if not allowed():
                    break
                yield f"data: {json.dumps({'time': time.strftime('%H:%M:%S'), 'message': f'Error en SSE: {str(e)}', 'level': 'error'})}\n\n"
                break
                
    return Response(event_stream(), mimetype="text/event-stream")

@app.route('/stream-browser')
def stream_browser():
    """SSE endpoint for streaming real-time browser base64 screenshots."""
    owner = getattr(g,'user',None)
    owner_id = owner['id'] if owner else None
    owner_version = owner['version'] if owner else None
    def allowed():
        if not app.config.get('IDPN_AUTH_ENABLED',True):
            return True
        if app.config.get('IDPN_JOB_OWNER') != owner_id:
            return False
        from auth import database
        with app.app_context(), database() as db:
            row = db.execute('SELECT active,version FROM users WHERE id=?',(owner_id,)).fetchone()
            return bool(row and row['active'] and row['version'] == owner_version)
    def event_stream():
        # Yield first connection event
        yield f"data: {json.dumps({'init': True})}\n\n"
        
        while allowed():
            try:
                # Wait up to 5 seconds for a new frame
                frame_data = browser_frame_queue.get(timeout=5)
                if not allowed():
                    break
                yield f"data: {json.dumps({'frame': frame_data})}\n\n"
            except queue.Empty:
                # Send heartbeat to keep connection alive
                if not allowed():
                    break
                yield f"data: {json.dumps({'heartbeat': True})}\n\n"
            except Exception as e:
                break
                
    return Response(event_stream(), mimetype="text/event-stream")

@app.route('/api/browser-event', methods=['POST'])
def handle_browser_event():
    """Receives mouse/keyboard events from the frontend and pushes them to browser_event_queue."""
    if not is_running:
        return jsonify({"error": "No hay una automatización activa."}), 400
        
    event_data = request.json
    browser_event_queue.put(event_data)
    return jsonify({"success": True})

if __name__ == '__main__':
    print("Iniciando servidor local en http://127.0.0.1:5001")
    app.run(debug=True, host='127.0.0.1', port=5001)
