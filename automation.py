import re
import os
import json
from html import escape
import time
import queue
from urllib.parse import quote, unquote, urlparse
from playwright.sync_api import sync_playwright
from document_stamp import valid_stamp


TITLE_CONNECTORS = frozenset("a al ante bajo con contra de del desde durante e el en entre hacia hasta la las los ni o para pero por que según sin sobre tras u un una unas unos y".split())


def clean_name(name, mode='title'):
    """Normalize display names while preserving commas and slashes such as y/o."""
    cleaned = re.sub(r'[-_]', ' ', name)
    cleaned = re.sub(r'[^a-zA-Z0-9\s/,áéíóúüñÁÉÍÓÚÜÑ]', '', cleaned)
    cleaned = ' '.join(cleaned.split())
    if mode == 'upper':
        return cleaned.upper()
    if mode == 'lower':
        return cleaned.lower()
    # Treat either side of a slash as a word, preserving the separator.
    index = 0
    def title_word(match):
        nonlocal index
        word = match.group().lower()
        result = word if index and word in TITLE_CONNECTORS else word.title()
        index += 1
        return result
    return re.sub(r'[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]+', title_word, cleaned)

def get_doc_title_from_url(url):
    """
    Extracts a clean document title from the file URL:
    - Extracts filename without extension.
    - Decodes URL encoding (e.g. + and %20).
    - Replaces hyphens, underscores, and plus signs with spaces.
    - Normalizes spaces.
    """
    try:
        path = urlparse(url).path
        filename = os.path.basename(path)
        filename_without_ext, _ = os.path.splitext(filename)
        title = unquote(filename_without_ext)
        title = title.replace('+', ' ').replace('-', ' ').replace('_', ' ')
        title = ' '.join(title.split())
        return title
    except Exception:
        return "Document"

def exit_code_editor_if_active(page, log_callback, wait_timeout_func):
    """
    Checks if the Gutenberg editor is in Code Editor mode.
    If the "Salir del editor de código" or "Exit code editor" button is visible, clicks it to switch to Visual Editor.
    """
    try:
        selectors = [
            'button:has-text("Salir del editor de código")',
            'button:has-text("Exit code editor")',
            'button:has-text("Exit Code Editor")',
            'button.components-button:has-text("editor de código")',
            'button.components-button:has-text("code editor")'
        ]
        
        for selector in selectors:
            btn = page.locator(selector)
            if btn.count() > 0 and btn.first.is_visible():
                log_callback("Modo editor de código detectado. Intentando cambiar al editor visual...", "info")
                btn.first.click(force=True)
                wait_timeout_func(2000)
                log_callback("Se hizo clic para salir del editor de código.", "success")
                return True
    except Exception as e:
        log_callback(f"Advertencia al verificar/salir del editor de código: {e}", "warning")
    return False

def generate_gutenberg_html(site_config, page_data):
    """Build document HTML or a native WordPress embed with owned review blocks."""
    folio = page_data.get('folio', '').strip()
    codigo = page_data.get('codigo', '').strip().upper()
    nombre = clean_name(page_data.get('nombre', '').strip(), site_config.get('document_title_case', 'title'))
    resource_url = page_data.get('enlace', '').strip()
    resource_type = page_data.get('embed_type', 'document').strip()
    document_action = ((page_data.get('document_plan') or {}).get('action') or page_data.get('document_action')
                       or page_data.get('document_status') or page_data.get('action') or 'alta')
    if document_action not in (('alta', 'cambio', 'baja') if resource_type == 'form' else ('alta', 'version', 'baja')):
        raise ValueError('Acción documental no válida.')
    if resource_type not in ('form', 'document'):
        raise ValueError('Tipo de recurso no válido.')
    metadata = f'data-idpn-action="{document_action}" data-idpn-resource="{resource_type}"'

    stamp = page_data.get('storage_version', '')
    if stamp:
        if not valid_stamp(stamp):
            raise ValueError('La marca de fecha del archivo no es válida.')
        metadata += f' data-idpn-revision="{escape(stamp, quote=True)}"'
        if not stamp.startswith('v'):
            metadata += f' data-idpn-published-date="{stamp[:10]}"'

    heading = nombre if page_data.get('is_politica', False) else f"{codigo} {nombre}".strip()

    def checked_url(value):
        parsed = urlparse(value)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError('El enlace del recurso o de descarga debe ser una URL HTTP/HTTPS válida.')
        return value

    checked_url(resource_url)
    filename = page_data.get('filename', '').strip() or unquote(os.path.basename(urlparse(resource_url).path))
    mime_type = page_data.get('mime_type', '').split(';')[0].strip().lower()
    is_pdf = mime_type == 'application/pdf' or filename.lower().endswith('.pdf')
    if resource_type == 'form' or is_pdf:
        viewer_url = resource_url
    else:
        # Preserve the entire source URL, including S3/Azure signatures, as one parameter.
        viewer_url = 'https://view.officeapps.live.com/op/embed.aspx?src=' + quote(resource_url, safe='')

    title = nombre or get_doc_title_from_url(resource_url) or 'Documento'
    if resource_type != 'form':
        download_url = checked_url(page_data.get('download_url', '').strip() or resource_url)
        action = (
            f'<a href="{escape(download_url, quote=True)}" download="{escape(filename, quote=True)}" '
            'target="_blank" rel="noopener noreferrer" '
            'style="display:inline-flex;align-items:center;justify-content:center;gap:10px;box-sizing:border-box;max-width:100%;padding:13px 24px;border:1px solid #173b57;border-radius:10px;background:#173b57;color:#fff;text-decoration:none;font:600 15px/1.4 system-ui,sans-serif;box-shadow:0 3px 8px rgba(23,59,87,.18);">'
            '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" style="display:block;flex-shrink:0;">'
            '<path d="M12 3v12m-5-5 5 5 5-5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
            f'{"Descargar archivo PDF" if is_pdf else "Descargar documento"}</a>'
        )
    cognito_script = site_config.get('cognito_script', '').strip()
    cognito_section = (
        f'<div data-idpn-component="cognito" style="margin:0;">\n{cognito_script}\n</div>'
        if cognito_script else ''
    )
    if resource_type == 'form':
        # Keep core/embed outside core/html so Gutenberg and WordPress oEmbed render it.
        attributes = {'url': resource_url, 'type': 'wp-embed', 'align': 'center',
                      'className': 'idpn-primary-form-resource'}
        serialized = json.dumps(attributes, ensure_ascii=False, separators=(',', ':'))
        for literal, encoded in [('--', r'\u002d\u002d'), ('<', r'\u003c'), ('>', r'\u003e'), ('&', r'\u0026')]:
            serialized = serialized.replace(literal, encoded)
        return f'''<!-- wp:html -->
<section data-idpn-page="1" {metadata} style="box-sizing:border-box;width:100%;padding:0 clamp(12px,2vw,24px);text-align:left;">
  <h2 class="wp-block-heading" data-idpn-component="heading" style="text-align:left;">{escape(heading)}</h2>
</section>
<!-- /wp:html -->

<!-- wp:embed {serialized} -->
<figure class="wp-block-embed aligncenter is-type-wp-embed idpn-primary-form-resource"><div class="wp-block-embed__wrapper">
{escape(resource_url)}
</div></figure>
<!-- /wp:embed -->

<!-- wp:html -->
<section data-idpn-page="1" {metadata} style="box-sizing:border-box;width:100%;padding:0 clamp(12px,2vw,24px);text-align:left;">
  <div data-idpn-component="folio" style="margin:0 0 24px;text-align:left;">
    <h3 style="margin:0;color:#ff0000;text-align:left;">Folio: {escape(folio)}</h3>
  </div>
  {cognito_section}
</section>
<!-- /wp:html -->'''
    return f'''<!-- wp:html -->
<section data-idpn-page="1" {metadata} style="box-sizing:border-box;width:100%;max-width:none;margin:0 auto;padding:0 clamp(12px,2vw,24px);text-align:left;">
  <h2 class="wp-block-heading" data-idpn-component="heading" style="text-align:left;">{escape(heading)}</h2>
  <div data-idpn-component="resource" style="width:100%;margin:0 auto;">
    <iframe src="{escape(viewer_url, quote=True)}" title="{escape(title, quote=True)}" width="100%" height="800" allowfullscreen style="display:block;width:100%;height:80vh;min-height:480px;max-height:1000px;border:0;"></iframe>
  </div>
  <div data-idpn-component="download" style="margin:0;text-align:center;">{action}</div>
  <div data-idpn-component="folio" style="margin:0 0 24px;text-align:left;">
    <h3 style="margin:0;color:#ff0000;text-align:left;">Folio: {escape(folio)}</h3>
  </div>
  {cognito_section}
</section>
<!-- /wp:html -->'''


def get_document_metadata(page, persisted=False):
    """Read persisted action markers without depending on a visible status legend."""
    return page.evaluate("""async persisted => {
        const found = [];
        const visit = blocks => {
            for (const block of blocks) {
                const html = block.attributes?.content || block.originalContent || block.innerHTML || '';
                const doc = new DOMParser().parseFromString(html, 'text/html');
                for (const node of doc.querySelectorAll('[data-idpn-page="1"][data-idpn-action]')) {
                    const action = node.getAttribute('data-idpn-action');
                    const resource = node.getAttribute('data-idpn-resource');
                    if (!['form','document'].includes(resource) || !(resource === 'form' ? ['alta','cambio','baja'] : ['alta','version','baja']).includes(action)) {
                        throw new Error('La marca interna de la acción no es válida.');
                    }
                    const item = {action, resource};
                    const revision = node.getAttribute('data-idpn-revision');
                    const publishedDate = node.getAttribute('data-idpn-published-date');
                    if (revision) item.revision = revision;
                    if (publishedDate) item.published_date = publishedDate;
                    found.push(item);
                }
                visit(block.innerBlocks || []);
            }
        };
        let blocks = wp.data.select('core/block-editor').getBlocks();
        if (persisted) {
            const editor = wp.data.select('core/editor');
            const type = editor.getCurrentPostType();
            const entity = wp.data.select('core').getEntityConfig('postType', type);
            const record = await wp.apiFetch({path:`${entity.baseURL}/${editor.getCurrentPostId()}?context=edit`, cache:'no-store'});
            if (typeof record.content?.raw !== 'string') throw new Error('No se pudo verificar la marca interna guardada.');
            blocks = wp.blocks.parse(record.content.raw);
        }
        visit(blocks);
        if (found.some(item => JSON.stringify(item) !== JSON.stringify(found[0]))) {
            throw new Error('La página contiene marcas de acción contradictorias.');
        }
        return found[0] || null;
    }""", persisted)


def get_document_heading(page):
    """Read headings from block content even when the HTML preview is closed."""
    return page.evaluate("""() => {
        const select = wp.data.select('core/block-editor');
        const find = blocks => {
            for (const block of blocks) {
                const html = (block.attributes && block.attributes.content) || block.originalContent || block.innerHTML || '';
                const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html');
                const marked = doc.querySelector('[data-idpn-component="heading"]');
                if (marked && marked.textContent.trim()) return marked.textContent.trim();
                if (block.name === 'core/heading' && !/folio\s*:/i.test(doc.body.textContent)) {
                    const text = doc.body.textContent.trim();
                    if (text) return text;
                }
                for (const heading of doc.querySelectorAll('h2.wp-block-heading,h2')) {
                    if (/folio\s*:/i.test(heading.textContent)) continue;
                    if (heading.closest('.cognito,.cognito-form,.cog-cognito,.cog-form,[data-idpn-component="cognito"]')) continue;
                    if (heading.textContent.trim()) return heading.textContent.trim();
                }
                const nested = find(block.innerBlocks || []);
                if (nested) return nested;
            }
            return null;
        };
        return find(select.getBlocks()) || wp.data.select('core/editor').getCurrentPostAttribute('title');
    }""")


def remove_cognito_and_folio(page, log_callback):
    """Clean nested Gutenberg blocks and confirm the content persisted in WordPress."""
    script_path = os.path.join(os.path.dirname(__file__), 'wp_cleanup.js')
    with open(script_path, encoding='utf-8') as source:
        script = source.read()
    page.wait_for_function(
        "typeof wp !== 'undefined' && !!wp.data && !!wp.blocks", timeout=15000
    )
    log_callback("Buscando Cognito y folio en todos los niveles de bloques Gutenberg...", "info")
    result = page.evaluate(script, {"verify": False})
    if not result or not result.get('success'):
        raise RuntimeError(f"No se pudo limpiar la página: {(result or {}).get('reason', 'sin resultado')}")
    log_callback(
        f"Bloques revisados: {result['originalCount']}. "
        f"Coincidencias Cognito: {result['cognitoCount']}; folio: {result['folioCount']}.", "info"
    )
    if result.get('removedCognito') or result.get('removedFolio'):
        log_callback("Guardando mediante la API del editor de WordPress...", "info")
        save_result = page.evaluate("""async () => {
            try {
                const editor = wp.data.select('core/editor');
                const dispatch = wp.data.dispatch('core/editor');
                if (typeof dispatch.savePost !== 'function') throw new Error('savePost no disponible');
                if (editor.isPostSavingLocked && editor.isPostSavingLocked()) throw new Error('WordPress tiene bloqueado el guardado');
                await dispatch.savePost();
                const core = wp.data.select('core');
                const error = core.getLastEntitySaveError('postType', editor.getCurrentPostType(), editor.getCurrentPostId());
                if (error) throw new Error(error.message || JSON.stringify(error));
                return { success: true };
            } catch (error) {
                return { success: false, reason: error.message || String(error) };
            }
        }""")
        if not save_result.get('success'):
            raise RuntimeError(f"WordPress no pudo guardar: {save_result.get('reason')}")
        page.wait_for_function(
            "!wp.data.select('core/editor').isSavingPost()", timeout=30000
        )
    else:
        log_callback("No se encontraron Cognito ni folio en los bloques; verificando el contenido guardado.", "info")
    verified = page.evaluate(script, {"verify": True})
    if not verified or not verified.get('success'):
        raise RuntimeError(f"No se confirmó la limpieza: {(verified or {}).get('reason', 'sin resultado')}")
    log_callback("Contenido guardado verificado: sin bloques de Cognito ni folio detectables.", "success")
    return verified.get('url', '')


def run_wp_automation(config_data, session_file, page_data, log_callback, frame_callback=None, event_queue=None):
    """
    Launches browser via Playwright to create, format, restrict, and publish the WordPress page.
    """
    log_callback("Iniciando navegador automatizado...", "info")
    
    with sync_playwright() as p:
        # Launch browser in headless mode by default (screencast streams to panel)
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        
        # Load cookies if session file exists
        if os.path.exists(session_file):
            log_callback("Cargando sesión guardada...", "info")
            try:
                with open(session_file, 'r') as f:
                    cookies = json.load(f)
                    context.add_cookies(cookies)
            except Exception as e:
                log_callback(f"Error al cargar cookies: {e}. Se requerirá iniciar sesión.", "warning")
        
        page = context.new_page()
        
        # Local helper to wait and process UI events in sync Playwright
        def wait_timeout(timeout_ms):
            if not event_queue:
                page.wait_for_timeout(timeout_ms)
                return
            
            start = time.time()
            limit = start + (timeout_ms / 1000.0)
            while time.time() < limit:
                # Process active events in queue
                while not event_queue.empty():
                    try:
                        event = event_queue.get_nowait()
                        ev_type = event.get("type")
                        if ev_type == "click":
                            x, y = event.get("x"), event.get("y")
                            button = event.get("button", "left")
                            if x is not None and y is not None:
                                page.mouse.click(x, y, button=button)
                        elif ev_type == "mousedown":
                            x, y = event.get("x"), event.get("y")
                            button = event.get("button", "left")
                            if x is not None and y is not None:
                                page.mouse.move(x, y)
                                page.mouse.down(button=button)
                        elif ev_type == "mouseup":
                            x, y = event.get("x"), event.get("y")
                            button = event.get("button", "left")
                            if x is not None and y is not None:
                                page.mouse.move(x, y)
                                page.mouse.up(button=button)
                        elif ev_type == "mousemove":
                            x, y = event.get("x"), event.get("y")
                            if x is not None and y is not None:
                                page.mouse.move(x, y)
                        elif ev_type == "keypress":
                            key = event.get("key")
                            if key:
                                page.keyboard.press(key)
                        elif ev_type == "type":
                            text = event.get("text")
                            if text:
                                page.keyboard.type(text)
                        elif ev_type == "wheel":
                            dx = event.get("deltaX", 0)
                            dy = event.get("deltaY", 0)
                            try:
                                page.mouse.wheel(dx, dy)
                            except Exception:
                                pass
                    except queue.Empty:
                        break
                    except Exception as err:
                        print(f"Error executing remote browser event: {err}")
                
                # short wait to yield back to browser
                page.wait_for_timeout(50)

        # Setup Chrome DevTools Protocol Screencast
        cdp = None
        if frame_callback:
            try:
                cdp = page.context.new_cdp_session(page)
                cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 60, "everyNthFrame": 1})
                
                def on_cdp_frame(params):
                    data = params.get("data")
                    sid = params.get("sessionId")
                    if frame_callback and data:
                        frame_callback(data)
                    try:
                        cdp.send("Page.screencastFrameAck", {"sessionId": sid})
                    except Exception:
                        pass
                
                cdp.on("Page.screencastFrame", on_cdp_frame)
            except Exception as e:
                log_callback(f"Advertencia: No se pudo iniciar el streaming de pantalla: {e}", "warning")

        # Go to the login page first
        log_callback(f"Navegando a la página de inicio de sesión: {config_data['login_url']}...", "info")
        page.goto(config_data['login_url'])
        
        # Check if we are redirected to wp-admin or if the WordPress admin bar is visible
        is_logged_in = "wp-admin" in page.url or page.locator('#wpadminbar').is_visible()
        
        if not is_logged_in:
            log_callback("Sesión no activa. Por favor, inicia sesión en la pestaña 'Navegador En Vivo' del panel.", "warning")
            
            # Wait for user to log in manually (timeout 5 minutes)
            login_timeout = 300
            start_time = time.time()
            while time.time() - start_time < login_timeout:
                # We know the user is logged in if URL contains wp-admin or admin bar is visible
                if "wp-admin" in page.url or page.locator('#wpadminbar').is_visible():
                    wait_timeout(2000) # Give it a second to render and complete redirects
                    is_logged_in = True
                    break
                wait_timeout(1000)
                
            if not is_logged_in:
                log_callback("Error: Se agotó el tiempo de espera para iniciar sesión manualmente.", "error")
                browser.close()
                return False
            
            log_callback("¡Inicio de sesión exitoso detectado! Guardando cookies para el futuro...", "success")
            # Save the fresh cookies
            cookies = context.cookies()
            os.makedirs(os.path.dirname(session_file), exist_ok=True)
            with open(session_file, 'w', opener=lambda path,flags:os.open(path,flags,0o600)) as f:
                json.dump(cookies, f)
        else:
            log_callback("Sesión activa recuperada con éxito.", "success")
            
        process_type = page_data.get('process_type', 'create')
        
        if process_type == 'remove':
            folio = page_data.get('folio', '').strip()
            base_url = config_data.get('base_url', '').rstrip('/')
            target_url = f"{base_url}/{folio}/"
            
            log_callback(f"Navegando a la página del folio: {target_url}...", "info")
            page.goto(target_url)
            
            log_callback("Esperando que cargue la barra de administración de WordPress...", "info")
            edit_link_selector = '#wp-admin-bar-edit a, .ab-item[href*="action=edit"]'
            try:
                page.wait_for_selector(edit_link_selector, timeout=15000)
            except Exception:
                log_callback("Advertencia: Barra de administración no detectada. Intentando recargar la página...", "warning")
                page.reload()
                try:
                    page.wait_for_selector(edit_link_selector, timeout=10000)
                except Exception:
                    pass
            
            edit_btn = page.locator(edit_link_selector)
            if edit_btn.count() > 0:
                log_callback("Haciendo clic en 'Editar la página'...", "info")
                edit_btn.first.click()
            else:
                log_callback("Error: No se encontró el enlace de edición de página. ¿Estás logueado?", "error")
                browser.close()
                return False
                
            # Wait for Gutenberg editor
            log_callback("Esperando que cargue el editor Gutenberg...", "info")
            try:
                page.wait_for_selector('.interface-interface-skeleton', timeout=30000)
            except Exception:
                log_callback("Tiempo de espera agotado cargando la interfaz del editor. Reintentando...", "warning")
                page.reload()
                page.wait_for_selector('.interface-interface-skeleton', timeout=30000)
                
            # Close welcome guide modals if they appear
            wait_timeout(2000)
            try:
                guide_close = page.locator('.components-guide__close-button, .components-modal__header button[aria-label="Cerrar"], button[aria-label="Close dialog"]')
                if guide_close.first.is_visible():
                    guide_close.first.click()
                    log_callback("Guía de bienvenida cerrada.", "info")
            except Exception:
                pass
                
            # Exit code editor mode if it's active
            exit_code_editor_if_active(page, log_callback, wait_timeout)
                
            # Verify Gutenberg JS API availability
            page.wait_for_function("typeof wp !== 'undefined' && typeof wp.data !== 'undefined' && typeof wp.blocks !== 'undefined'", timeout=15000)
            
            # Extract header text (heading block containing the code / document name, fallback to title or folio)
            log_callback("Buscando encabezado del documento (h2.wp-block-heading)...", "info")
            header_text = folio # Fallback initially to folio
            try:
                extracted = get_document_heading(page)
                if extracted:
                    header_text = extracted
                    log_callback(f"Encabezado de página detectado: '{header_text}'", "success")
                else:
                    log_callback(f"No se encontró h2.wp-block-heading específico, usando el folio como encabezado: {folio}", "info")
            except Exception as e:
                log_callback(f"Advertencia al extraer encabezado: {e}. Usando folio: {folio}", "warning")

            updated_url = remove_cognito_and_folio(page, log_callback) or target_url

            wait_timeout(2000)
            browser.close()
            return {"url": updated_url, "header": header_text}

        # Navigate to the new page creation URL
        log_callback(f"Navegando a la página de creación: {config_data['create_page_url']}...", "info")
        page.goto(config_data['create_page_url'])
            
        # Wait for the block editor to load
        log_callback("Esperando que cargue el editor Gutenberg...", "info")
        try:
            page.wait_for_selector('.interface-interface-skeleton', timeout=30000)
        except Exception:
            log_callback("Tiempo de espera agotado cargando la interfaz del editor. Reintentando...", "warning")
            page.reload()
            page.wait_for_selector('.interface-interface-skeleton', timeout=30000)
            
        # Close welcome guide modals if they appear
        wait_timeout(2000) # Wait a brief moment for guides to pop up
        try:
            guide_close = page.locator('.components-guide__close-button, .components-modal__header button[aria-label="Cerrar"], button[aria-label="Close dialog"]')
            if guide_close.first.is_visible():
                guide_close.first.click()
                log_callback("Guía de bienvenida cerrada automáticamente.", "info")
        except Exception:
            pass

        # Exit code editor mode if it's active
        exit_code_editor_if_active(page, log_callback, wait_timeout)

        # Verify Gutenberg JS API availability
        page.wait_for_function("typeof wp !== 'undefined' && typeof wp.data !== 'undefined' && typeof wp.blocks !== 'undefined'", timeout=15000)
        
        # Generate Block HTML and Title
        folio = page_data.get('folio', '').strip()
        block_html = generate_gutenberg_html(config_data, page_data)
        
        log_callback("Configurando título y bloques Gutenberg a través del Core JS API...", "info")
        
        # Inject JavaScript to set the title and reset editor blocks
        page.evaluate(f"""
            var title = {json.dumps(folio)};
            var html = {json.dumps(block_html)};
            
            // Set title
            wp.data.dispatch('core/editor').editPost({{ title: title }});
            
            // Parse blocks and reset editor
            var parsedBlocks = wp.blocks.parse(html);
            var store = wp.data.dispatch('core/block-editor') || wp.data.dispatch('core/editor');
            store.resetBlocks(parsedBlocks);
        """)
        log_callback("Título y contenido insertados con éxito.", "success")
        
        # Configure Title Visibility (Hide Title Checkbox) and Ultimate Member Content Restriction
        log_callback("Configurando opciones en el panel inferior (Cajas meta)...", "info")
        
        # 1. Scroll to bottom first to bring the meta boxes area into view
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        wait_timeout(1000)
        
        # 2. Ensure Meta Boxes panel is open
        try:
            metaboxes_btn = page.locator('.edit-post-meta-boxes-main__presenter button[aria-expanded], .edit-post-meta-boxes-main__presenter button, button:has-text("Cajas meta"), button:has-text("Meta boxes")')
            if metaboxes_btn.count() > 0:
                btn = metaboxes_btn.first
                btn.scroll_into_view_if_needed()
                
                # Check status
                aria_expanded = btn.get_attribute('aria-expanded')
                if aria_expanded == 'false':
                    log_callback("Detectado 'Cajas meta' colapsado. Intentando abrir...", "info")
                    
                    # Try normal click
                    btn.click(force=True)
                    wait_timeout(1500)
                    
                    # Verify if it was successfully expanded
                    aria_expanded = btn.get_attribute('aria-expanded')
                    if aria_expanded == 'false':
                        log_callback("Clic normal falló. Forzando despliegue de Cajas meta vía JS...", "warning")
                        # Perform JS click fallback
                        page.evaluate("el => el.click()", btn.element_handle())
                        wait_timeout(1500)
                        
                    # Scroll to bottom again since expanding it changes page height
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    wait_timeout(1000)
        except Exception as e:
            log_callback(f"Advertencia al expandir panel inferior: {e}", "warning")

        # 3. Configure "Hide Title" (Primary: Astra Eye Icon click via JS; Fallback: metabox checkbox)
        try:
            log_callback("Configurando ocultación del título...", "info")
            title_hidden_success = False

            # Wait and check for Astra Eye Icon in both main document and editor iframe (editor-canvas)
            # Wait and check for Astra Eye Icon in both main document and editor iframe (editor-canvas)
            for attempt in range(6):
                # Trigger eye visibility natively using Playwright hover and focus
                try:
                    main_input = page.locator('h1.editor-post-title, h1.wp-block-post-title').first
                    main_wrapper = page.locator('.editor-visual-editor__post-title-wrapper').first
                    if main_input.is_visible():
                        main_input.focus()
                    if main_wrapper.is_visible():
                        main_wrapper.hover()
                except Exception:
                    pass
                    
                try:
                    iframe_canvas = page.frame_locator('iframe[name="editor-canvas"]')
                    iframe_input = iframe_canvas.locator('h1.editor-post-title, h1.wp-block-post-title').first
                    iframe_wrapper = iframe_canvas.locator('.editor-visual-editor__post-title-wrapper').first
                    if iframe_input.is_visible():
                        iframe_input.focus()
                    if iframe_wrapper.is_visible():
                        iframe_wrapper.hover()
                except Exception:
                    pass
                
                # Check for eye icon in main page or iframe
                eye_locator = None
                location = ""
                
                main_eye = page.locator('span.title-visibility, .ast-title.title-visibility, span[data-tooltip="Disable Title"], span[data-tooltip="Ocultar título"]').first
                if main_eye.is_visible():
                    eye_locator = main_eye
                    location = "main"
                else:
                    iframe = page.frame_locator('iframe[name="editor-canvas"]')
                    iframe_eye = iframe.locator('span.title-visibility, .ast-title.title-visibility, span[data-tooltip="Disable Title"], span[data-tooltip="Ocultar título"]').first
                    if iframe_eye.is_visible():
                        eye_locator = iframe_eye
                        location = "iframe"
                        
                if eye_locator:
                    tooltip = eye_locator.get_attribute('data-tooltip') or ""
                    tooltip_lower = tooltip.lower()
                    
                    # If it's already disabled, tooltip will contain "enable" or "mostrar" or "activar"
                    is_already_disabled = "enable" in tooltip_lower or "mostrar" in tooltip_lower or "activar" in tooltip_lower
                    
                    if not is_already_disabled:
                        log_callback(f"Ícono de Astra encontrado en {location} con tooltip '{tooltip}'. Haciendo clic nativo (Intento {attempt + 1})...", "info")
                        try:
                            eye_locator.click(force=True)
                            wait_timeout(1000)
                            # Verify tooltip updated
                            new_tooltip = eye_locator.get_attribute('data-tooltip') or ""
                            log_callback(f"Clic nativo en el ojo realizado. Nuevo tooltip: '{new_tooltip}'", "success")
                            title_hidden_success = True
                        except Exception as click_err:
                            log_callback(f"Advertencia: Clic nativo en el ojo falló (ej. fuera del viewport): {click_err}. Continuando con los métodos de respaldo.", "warning")
                    else:
                        log_callback(f"El título ya estaba configurado como oculto en Astra (tooltip: '{tooltip}').", "info")
                        title_hidden_success = True
                    break
                    
                wait_timeout(1000)

            # Always execute the wp.data meta API update to ensure the title is hidden on the database side
            log_callback("Configurando meta keys del post en Gutenberg (wp.data) para desactivar el título...", "info")
            meta_result = page.evaluate("""() => {
                try {
                    if (typeof wp !== 'undefined' && wp.data && wp.data.dispatch) {
                        const editor = wp.data.select('core/editor');
                        const dispatch = wp.data.dispatch('core/editor');
                        if (editor && dispatch) {
                            const currentMeta = editor.getCurrentPostAttribute('meta') || {};
                            const newMeta = { ...currentMeta };
                            
                            newMeta['_ast-disable-title'] = 'yes';
                            newMeta['site-post-title'] = 'disabled';
                            newMeta['ast-title-disable'] = 'yes';
                            
                            // Also handle nested _astra_display_meta object if it exists
                            let astraMeta = currentMeta['_astra_display_meta'] || {};
                            if (typeof astraMeta === 'object' && astraMeta !== null) {
                                astraMeta['site-post-title'] = 'disabled';
                                newMeta['_astra_display_meta'] = astraMeta;
                            }
                            
                            dispatch.editPost({ meta: newMeta });
                            return { success: true, metaKeys: Object.keys(newMeta) };
                        }
                    }
                } catch (e) {
                    return { success: false, error: e.toString() };
                }
                return { success: false, reason: "wp.data not available" };
            }""")
            if meta_result and meta_result.get('success'):
                log_callback(f"Meta keys configuradas con éxito vía wp.data: {meta_result.get('metaKeys')}", "success")
                title_hidden_success = True
            else:
                log_callback(f"Advertencia: No se pudo configurar las meta keys vía wp.data: {meta_result.get('reason') or meta_result.get('error')}", "warning")

            if not title_hidden_success:
                log_callback("Ícono de Astra no encontrado o fallo al hacer clic. Intentando panel lateral de Astra, metaboxes o meta vía JS...", "info")
                # Try finding Astra specific elements first or set metadata directly
                try:
                    astra_js_result = page.evaluate("""() => {
                        try {
                            const textOptions = ["desactivar título", "disable title", "ocultar título", "hide title"];
                            
                            // 1. Look for checkbox or toggle in the document (metaboxes or Astra sidebar panel)
                            const elements = document.querySelectorAll('label, button, span, div.components-base-control__label, h2, h3, p');
                            for (let el of elements) {
                                const text = el.textContent.trim().toLowerCase();
                                if (textOptions.some(opt => text === opt || text.includes(opt))) {
                                    // Case A: It's a label with a 'for' attribute
                                    if (el.tagName === 'LABEL' && el.getAttribute('for')) {
                                        const inputId = el.getAttribute('for');
                                        const input = document.getElementById(inputId);
                                        if (input && input.type === 'checkbox') {
                                            if (!input.checked) {
                                                input.click();
                                                return { success: true, method: "label_for_checkbox", clicked: true };
                                            }
                                            return { success: true, method: "label_for_checkbox", clicked: false };
                                        }
                                    }
                                    
                                    // Case B: ToggleControl or CheckboxControl label
                                    const container = el.closest('.components-base-control, .components-panel__row, .disable-section-meta div, #astra_settings_meta_box div');
                                    if (container) {
                                        const checkbox = container.querySelector('input[type="checkbox"]');
                                        if (checkbox) {
                                            if (!checkbox.checked) {
                                                checkbox.click();
                                                return { success: true, method: "checkbox_in_container", clicked: true };
                                            }
                                            return { success: true, method: "checkbox_in_container", clicked: false };
                                        }
                                        
                                        const toggleSwitch = container.querySelector('button[role="switch"]');
                                        if (toggleSwitch) {
                                            const isChecked = toggleSwitch.getAttribute('aria-checked') === 'true';
                                            if (!isChecked) {
                                                toggleSwitch.click();
                                                return { success: true, method: "toggle_switch_in_container", clicked: true };
                                            }
                                            return { success: true, method: "toggle_switch_in_container", clicked: false };
                                        }
                                    }
                                }
                            }
                            
                            // 2. Fallback to setting post meta via Gutenberg wp.data API
                            if (typeof wp !== 'undefined' && wp.data && wp.data.dispatch) {
                                const editor = wp.data.select('core/editor');
                                const dispatch = wp.data.dispatch('core/editor');
                                if (editor && dispatch) {
                                    const currentMeta = editor.getCurrentPostAttribute('meta') || {};
                                    const newMeta = { ...currentMeta };
                                    
                                    newMeta['_ast-disable-title'] = 'yes';
                                    newMeta['site-post-title'] = 'disabled';
                                    newMeta['ast-title-disable'] = 'yes';
                                    
                                    // Also handle nested _astra_display_meta object if it exists
                                    let astraMeta = currentMeta['_astra_display_meta'] || {};
                                    if (typeof astraMeta === 'object' && astraMeta !== null) {
                                        astraMeta['site-post-title'] = 'disabled';
                                        newMeta['_astra_display_meta'] = astraMeta;
                                    }
                                    
                                    dispatch.editPost({ meta: newMeta });
                                    return { success: true, method: "wp_data_meta", clicked: true };
                                }
                            }
                        } catch (e) {
                            return { success: false, reason: e.toString() };
                        }
                        return { success: false, reason: "no_astra_element_found" };
                    }""")
                    
                    if astra_js_result and astra_js_result.get('success'):
                        log_callback(f"Título de la página configurado como OCULTO vía Astra ({astra_js_result.get('method')}).", "success")
                        title_hidden_success = True
                except Exception as ex:
                    log_callback(f"Advertencia al ejecutar scripts de Astra: {ex}", "warning")

            if not title_hidden_success:
                log_callback("Astra title hidden falló. Intentando método de respaldo (checkbox de cajas meta de plugins)...", "info")
                # Fallback to the metabox checkbox
                # Determine selectors dynamically (support both wptr and wp4me)
                container_selector = ""
                checkbox_selector = ""
                label_selector = ""
                checkbox_id = ""
                
                if page.locator('input[type="checkbox"]#wp4me-hide-title-checkbox, input[type="checkbox"][name="wp4me-hide-title-checkbox"]').count() > 0:
                    container_selector = '#wp4me-hide-title'
                    checkbox_selector = 'input[type="checkbox"]#wp4me-hide-title-checkbox, input[type="checkbox"][name="wp4me-hide-title-checkbox"]'
                    label_selector = 'label[for="wp4me-hide-title-checkbox"]'
                    checkbox_id = 'wp4me-hide-title-checkbox'
                else:
                    container_selector = '#wptr-hide-title'
                    checkbox_selector = 'input[type="checkbox"]#wptr-hide-title-checkbox, input[type="checkbox"][name="wptr-hide-title-checkbox"]'
                    label_selector = 'label[for="wptr-hide-title-checkbox"]'
                    checkbox_id = 'wptr-hide-title-checkbox'
                    
                # Check if the Hide Title metabox is collapsed
                title_metabox = page.locator(container_selector)
                if title_metabox.count() > 0:
                    classes = title_metabox.first.get_attribute('class') or ""
                    if "closed" in classes:
                        title_header = page.locator(f'{container_selector} h2.hndle, {container_selector} button.handlediv')
                        if title_header.count() > 0:
                            title_header.first.click()
                            log_callback("Abriendo sección colapsada de Hide Title...", "info")
                            wait_timeout(1000)

                # Check the checkbox specifically
                hide_title_chk = page.locator(checkbox_selector)
                if hide_title_chk.count() > 0:
                    checkbox = hide_title_chk.first
                    if not checkbox.is_checked():
                        # Try clicking the label first
                        label = page.locator(label_selector)
                        if label.count() > 0:
                            label.first.click(force=True)
                        else:
                            checkbox.click(force=True)
                        
                        wait_timeout(500)
                        # Verify and fallback to JS check if not selected
                        if not checkbox.is_checked():
                            page.evaluate(f"const el = document.getElementById('{checkbox_id}'); if (el) {{ el.checked = true; el.dispatchEvent(new Event('change')); }}")
                        
                        log_callback("Título de la página configurado como OCULTO (vía checkbox de respaldo).", "success")
                        title_hidden_success = True
                    else:
                        log_callback("El título ya estaba configurado como oculto en el checkbox.", "info")
                        title_hidden_success = True
                else:
                    # Force fallback using document-level selectors just in case
                    page.evaluate("""() => {
                        const chk = document.querySelector('#wp4me-hide-title-checkbox') || document.querySelector('#wptr-hide-title-checkbox') || document.querySelector('input[name="wp4me-hide-title-checkbox"]') || document.querySelector('input[name="wptr-hide-title-checkbox"]');
                        if (chk && !chk.checked) {
                            chk.checked = true;
                            chk.dispatchEvent(new Event('change'));
                        }
                    }""")
                    wait_timeout(500)
                    # Verify if it got checked via JS
                    is_checked_js = page.evaluate("""() => {
                        const chk = document.querySelector('#wp4me-hide-title-checkbox') || document.querySelector('#wptr-hide-title-checkbox') || document.querySelector('input[name="wp4me-hide-title-checkbox"]') || document.querySelector('input[name="wptr-hide-title-checkbox"]');
                        return chk ? chk.checked : false;
                    }""")
                    if is_checked_js:
                        log_callback("Título de la página configurado como OCULTO (vía inyección JS de respaldo).", "success")
                        title_hidden_success = True
                    else:
                        log_callback("Advertencia: No se pudo ocultar el título mediante ningún método.", "warning")
        except Exception as e:
            log_callback(f"Error al ocultar el título: {e}", "warning")

        # 4. Configure Ultimate Member Content Restriction
        um_config = config_data.get('ultimate_member', {})
        if um_config.get('restrict_access', False):
            log_callback("Configurando restricciones de acceso (Ultimate Member)...", "info")
            
            try:
                # Check if the Ultimate Member metabox is collapsed
                um_metabox = page.locator('#um-admin-restrict-content')
                if um_metabox.count() > 0:
                    classes = um_metabox.first.get_attribute('class') or ""
                    if "closed" in classes:
                        um_header = page.locator('#um-admin-restrict-content h2.hndle, #um-admin-restrict-content button.handlediv')
                        if um_header.count() > 0:
                            um_header.first.click()
                            log_callback("Abriendo sección colapsada de Ultimate Member...", "info")
                            wait_timeout(1000)

                # Check Custom Access Settings checkbox if not checked
                custom_access_chk = page.locator('#um_content_restriction__um_custom_access_settings')
                custom_access_chk.wait_for(state="attached", timeout=5000)
                if not custom_access_chk.is_checked():
                    custom_access_chk.click()
                    log_callback("Opción '¿Restringir el acceso?' activada.", "info")
            except Exception as e:
                log_callback(f"Advertencia al abrir panel Ultimate Member: {e}", "warning")
                
            # Who can access
            accessible_to = um_config.get('accessible_to', '2')
            accessible_select = page.locator('#um_content_restriction__um_accessible')
            accessible_select.wait_for(state="visible", timeout=5000)
            accessible_select.select_option(accessible_to)
            log_callback("Acceso configurado para: Usuarios conectados.", "info")
            
            # Set allowed roles
            wait_timeout(1000) # Wait for checkboxes to expand
            roles_to_allow = um_config.get('roles', ["administrator", "editor", "author", "contributor"])
            
            # Locate all checkboxes in the restriction area
            role_checkboxes = page.locator('input[id^="um_content_restriction__um_access_roles_"]')
            count = role_checkboxes.count()
            
            for i in range(count):
                chk = role_checkboxes.nth(i)
                chk_id = chk.get_attribute('id')
                
                # Check if this checkbox's ID suffix matches one of our configured roles
                should_be_checked = False
                for r in roles_to_allow:
                    suffix = r.lower()
                    # Check direct, um_ or bbp_ prefixes
                    if chk_id.endswith(f"_roles_{suffix}") or chk_id.endswith(f"_roles_um_{suffix}") or chk_id.endswith(f"_roles_bbp_{suffix}"):
                        should_be_checked = True
                        break
                
                if should_be_checked:
                    if not chk.is_checked():
                        chk.click()
                else:
                    if chk.is_checked():
                        chk.click()
                        
            # No-access action
            noaccess_action = um_config.get('no_access_action', '0')
            noaccess_select = page.locator('#um_content_restriction__um_noaccess_action')
            if noaccess_select.is_visible():
                noaccess_select.select_option(noaccess_action)
                
            log_callback("Restricciones de Ultimate Member guardadas temporalmente en el formulario.", "success")

        # Configure Astra Settings
        astra_config = config_data.get('astra_settings', {})
        if astra_config:
            log_callback("Abriendo ajustes de tema Astra...", "info")
            # Open Astra sidebar panel if closed
            astra_btn = page.locator('button[aria-controls="astra-theme-layout:theme-meta-panel"]')
            if astra_btn.is_visible():
                if astra_btn.get_attribute('aria-pressed') == 'false':
                    astra_btn.click()
                    wait_timeout(1000)
            else:
                # Try opening post settings sidebar first, then check if Astra is pinned
                settings_btn = page.locator('button[aria-label="Ajustes"]')
                if settings_btn.is_visible():
                    settings_btn.click()
                    wait_timeout(500)
                    if astra_btn.is_visible() and astra_btn.get_attribute('aria-pressed') == 'false':
                        astra_btn.click()
                        wait_timeout(1000)
                        
            # Configure Container Layout
            container_layout = astra_config.get('container_layout', 'normal-width-container')
            input_id = None
            for potential_name in ["ast-site-content-layout", "ast-content-layout"]:
                radio = page.locator(f'input[name="{potential_name}"][value="{container_layout}"]')
                if radio.count() > 0:
                    input_id = radio.first.get_attribute('id')
                    break
            
            if input_id:
                label_for = page.locator(f'label[for="{input_id}"]')
                if label_for.count() > 0:
                    label_for.first.click()
                else:
                    page.locator(f'#{input_id}').click(force=True)
                log_callback(f"Astra: Contenedor establecido en '{container_layout}'.", "info")
            else:
                log_callback(f"Advertencia: No se encontró el selector de contenedor para '{container_layout}'.", "warning")
                
            # Configure Container Style
            container_style = astra_config.get('container_style', 'Recuadro')
            style_btn = page.locator(f'#site-content-style button:has-text("{container_style}"), #site-content-style button[aria-label="{container_style}"]')
            if style_btn.is_visible():
                style_btn.click()
                log_callback(f"Astra: Estilo del contenedor establecido en '{container_style}'.", "info")
                
            # Configure Sidebar Layout
            sidebar_accordion = page.locator('button.components-panel__body-toggle:has-text("Barra lateral"), button.components-panel__body-toggle:has-text("Sidebar")')
            if sidebar_accordion.is_visible():
                if sidebar_accordion.get_attribute('aria-expanded') == 'false':
                    sidebar_accordion.click()
                    wait_timeout(1000)
                    
                sidebar_layout = astra_config.get('sidebar_layout', 'right-sidebar')
                
                # Check for inputs inside the accordion panel by value matching sidebar_layout
                input_id = None
                for potential_name in ["site-sidebar-layout", "ast-site-sidebar-layout", "ast-sidebar-layout"]:
                    radio = page.locator(f'input[name="{potential_name}"][value="{sidebar_layout}"]')
                    if radio.count() > 0:
                        input_id = radio.first.get_attribute('id')
                        break
                        
                if not input_id:
                    # Generic input search with value and containing sidebar in name
                    radio = page.locator(f'input[name*="sidebar"][value="{sidebar_layout}"]')
                    if radio.count() > 0:
                        input_id = radio.first.get_attribute('id')
                        
                if input_id:
                    label_for = page.locator(f'label[for="{input_id}"]')
                    if label_for.count() > 0:
                        label_for.first.click()
                    else:
                        page.locator(f'#{input_id}').click(force=True)
                    log_callback(f"Astra: Barra lateral establecida en '{sidebar_layout}'.", "info")
                else:
                    # Tooltip visual click fallback
                    tooltip = None
                    if sidebar_layout == 'right-sidebar':
                        tooltip = page.locator('.ast-meta-image-tooltip:has-text("Barra lateral derecha"), .ast-meta-image-tooltip:has-text("Right Sidebar")')
                    elif sidebar_layout == 'left-sidebar':
                        tooltip = page.locator('.ast-meta-image-tooltip:has-text("Barra lateral izquierda"), .ast-meta-image-tooltip:has-text("Left Sidebar")')
                    elif sidebar_layout == 'no-sidebar':
                        tooltip = page.locator('.ast-meta-image-tooltip:has-text("Sin barra lateral"), .ast-meta-image-tooltip:has-text("No Sidebar")')
                    
                    if tooltip and tooltip.first.is_visible():
                        tooltip.first.locator('..').click()
                        log_callback(f"Astra: Barra lateral establecida en '{sidebar_layout}' (clic visual).", "info")
                    else:
                        log_callback(f"Advertencia: No se pudo configurar la barra lateral a '{sidebar_layout}'.", "warning")
                        
            log_callback("Ajustes de Astra configurados con éxito.", "success")
            
        # Click Publish button
        log_callback("Publicando la página...", "info")
        publish_btn = page.locator('.editor-post-publish-panel__toggle, .editor-post-publish-button__button')
        publish_btn.wait_for(state="visible", timeout=15000)
        
        # Wait a moment for editor state to settle
        wait_timeout(2000)
        
        # Try to click the publish button (up to 3 attempts if it is saving)
        published_triggered = False
        for attempt in range(3):
            # Check if button is disabled (saving draft)
            is_disabled = publish_btn.get_attribute('aria-disabled') == 'true'
            if is_disabled:
                log_callback(f"El botón de publicar está inactivo (guardando cambios). Esperando... (Intento {attempt + 1}/3)", "info")
                wait_timeout(2000)
                continue
            
            publish_btn.click()
            log_callback(f"Clic en el botón de publicar principal (Intento {attempt + 1}/3).", "info")
            wait_timeout(1500)
            
            # Check if confirm panel is visible
            confirm_btn = page.locator('.editor-post-publish-panel__header-publish-button button, .editor-post-publish-panel__publish-button')
            if confirm_btn.is_visible():
                confirm_btn.click()
                log_callback("Clic en el botón de confirmación de publicación.", "info")
                published_triggered = True
                break
                
            # If the post is publishing/published directly, check if the success notice is visible
            success_box = page.locator('.post-publish-panel__postpublish-header, .editor-post-publish-panel__header, a.editor-post-publish-panel__viewpost-link')
            if success_box.count() > 0 and success_box.first.is_visible():
                published_triggered = True
                log_callback("Publicación directa detectada.", "info")
                break
        
        # Wait for publishing confirmation
        log_callback("Guardando cambios y esperando confirmación de WordPress...", "info")
        success_selector = '.post-publish-panel__postpublish, .post-publish-panel__postpublish-header, .editor-post-publish-panel__header, a.editor-post-publish-panel__viewpost-link'
        page.wait_for_selector(success_selector, timeout=30000)
        
        # Wait up to 10 seconds for the URL address input or success container to populate
        log_callback("Esperando que se genere el enlace de la página (hasta 10s)...", "info")
        address_input = page.locator('.post-publish-panel__postpublish-post-address input, #inspector-text-control-0')
        try:
            address_input.wait_for(state="attached", timeout=10000)
            # Give it an extra second to populate
            wait_timeout(1000)
        except Exception:
            pass

        # Try to retrieve the final URL using multiple strategies
        final_url = ""
        
        # Strategy A1: Extract from the Gutenberg editor header settings bar (Ver página button)
        header_view_link = page.locator('.editor-header__settings a[aria-label="Ver página"], .editor-header__settings a[aria-label="View page"], .editor-header__settings a[aria-label="View Page"], .editor-header__settings a[target="_blank"]')
        if header_view_link.count() > 0:
            val = header_view_link.first.get_attribute('href')
            if val:
                # Exclude preview links if it somehow matched one
                if "?preview=" not in val:
                    final_url = val
        
        # Strategy A: Extract from the post-publish address input
        if not final_url:
            address_input = page.locator('.post-publish-panel__postpublish-post-address input, #inspector-text-control-0')
            if address_input.count() > 0:
                val = address_input.first.get_attribute('value')
                if val:
                    final_url = val
                
        # Strategy B: Extract from the external link in the post-publish header
        if not final_url:
            header_link = page.locator('.post-publish-panel__postpublish-header a, .post-publish-panel__postpublish-header a.components-external-link')
            if header_link.count() > 0:
                val = header_link.first.get_attribute('href')
                if val:
                    final_url = val
                    
        # Strategy C: Extract from the "Ver página" link button
        if not final_url:
            view_btn = page.locator('.post-publish-panel__postpublish-buttons a:has-text("Ver página"), .post-publish-panel__postpublish-buttons a:has-text("View page"), .post-publish-panel__postpublish-buttons a:has-text("View Page")')
            if view_btn.count() > 0:
                val = view_btn.first.get_attribute('href')
                if val:
                    final_url = val
                    
        # Strategy D: Old editor view link fallback
        if not final_url:
            view_link = page.locator('a.editor-post-publish-panel__viewpost-link')
            if view_link.count() > 0 and view_link.first.is_visible():
                final_url = view_link.first.get_attribute('href')

        if final_url:
            log_callback(f"¡Página publicada con éxito! URL pública: {final_url}", "success")
        else:
            log_callback("Página publicada con éxito (no se pudo extraer la URL en el panel).", "success")
            
        # Wait 2 seconds before closing context
        wait_timeout(2000)
        browser.close()
        return final_url


def group_folios_by_site(config_dir, folios):
    """
    Groups folios by site based on the folio_prefix in configuration files.
    Returns:
        grouped (dict): domain -> {config, site_name, domain, folios}
        unknowns (list): list of folios with unrecognized prefixes
    """
    configs = []
    for file in os.listdir(config_dir):
        if file.endswith('.json'):
            file_path = os.path.join(config_dir, file)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    configs.append({
                        "filename": file,
                        "domain": file.replace('.json', ''),
                        "site_name": data.get('site_name', file),
                        "folio_prefix": data.get('folio_prefix', ''),
                        "config_data": data
                    })
            except Exception:
                pass
                
    # Sort configs by folio_prefix length descending to match longest prefix first
    configs_with_prefix = [c for c in configs if c['folio_prefix']]
    configs_with_prefix.sort(key=lambda x: len(x['folio_prefix']), reverse=True)
    
    grouped = {}
    unknowns = []
    
    for f in folios:
        clean_f = f.strip().upper()
        matched = False
        for c in configs_with_prefix:
            prefix = c['folio_prefix'].strip().upper()
            if prefix and clean_f.startswith(prefix):
                domain = c['domain']
                if domain not in grouped:
                    grouped[domain] = {
                        "config": c['config_data'],
                        "site_name": c['site_name'],
                        "domain": domain,
                        "folios": []
                    }
                grouped[domain]['folios'].append(f.strip())
                matched = True
                break
        if not matched:
            unknowns.append(f.strip())
            
    return grouped, unknowns


def run_wp_remove_multiple_automation(config_dir, session_dir, folios, log_callback, frame_callback=None, event_queue=None, after_remove=None):
    """
    Groups the list of folios by WordPress site based on prefix.
    Processes each site group sequentially by launching a single Playwright instance and context.
    Performs login check once per site, then loops through each folio in that site group.
    """
    log_callback("Iniciando agrupación de folios por sitio...", "info")
    grouped, unknowns = group_folios_by_site(config_dir, folios)
    
    # 1. Report unknowns immediately
    for f in unknowns:
        log_callback(
            message=f"Folio {f} ignorado: no se reconoce el prefijo.",
            level="warning",
            folio_status={
                "status": "unknown",
                "folio": f,
                "error": "Prefijo de folio no reconocido"
            }
        )
        
    # 2. Iterate through each site group
    for domain, group in grouped.items():
        config_data = group["config"]
        site_name = group["site_name"]
        site_folios = group["folios"]
        session_file = os.path.join(session_dir, f"{domain}.json")
        
        log_callback(f"Iniciando procesamiento para el sitio: {site_name} ({domain}) - {len(site_folios)} folios asignados.", "info")
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1280, "height": 800})
                
                # Load cookies if session file exists
                if os.path.exists(session_file):
                    log_callback(f"Cargando sesión guardada para {site_name}...", "info")
                    try:
                        with open(session_file, 'r') as f:
                            cookies = json.load(f)
                            context.add_cookies(cookies)
                    except Exception as e:
                        log_callback(f"Error al cargar cookies para {site_name}: {e}. Se requerirá iniciar sesión.", "warning")
                
                page = context.new_page()
                
                # Local helper to wait and process UI events in sync Playwright
                def wait_timeout(timeout_ms):
                    if not event_queue:
                        page.wait_for_timeout(timeout_ms)
                        return
                    
                    start = time.time()
                    limit = start + (timeout_ms / 1000.0)
                    while time.time() < limit:
                        while not event_queue.empty():
                            try:
                                event = event_queue.get_nowait()
                                ev_type = event.get("type")
                                if ev_type == "click":
                                    x, y = event.get("x"), event.get("y")
                                    button = event.get("button", "left")
                                    if x is not None and y is not None:
                                        page.mouse.click(x, y, button=button)
                                elif ev_type == "mousedown":
                                    x, y = event.get("x"), event.get("y")
                                    button = event.get("button", "left")
                                    if x is not None and y is not None:
                                        page.mouse.move(x, y)
                                        page.mouse.down(button=button)
                                elif ev_type == "mouseup":
                                    x, y = event.get("x"), event.get("y")
                                    button = event.get("button", "left")
                                    if x is not None and y is not None:
                                        page.mouse.move(x, y)
                                        page.mouse.up(button=button)
                                elif ev_type == "mousemove":
                                    x, y = event.get("x"), event.get("y")
                                    if x is not None and y is not None:
                                        page.mouse.move(x, y)
                                elif ev_type == "keypress":
                                    key = event.get("key")
                                    if key:
                                        page.keyboard.press(key)
                                elif ev_type == "type":
                                    text = event.get("text")
                                    if text:
                                        page.keyboard.type(text)
                                elif ev_type == "wheel":
                                    dx = event.get("deltaX", 0)
                                    dy = event.get("deltaY", 0)
                                    try:
                                        page.mouse.wheel(dx, dy)
                                    except Exception:
                                        pass
                            except queue.Empty:
                                break
                            except Exception as err:
                                print(f"Error executing remote browser event: {err}")
                        page.wait_for_timeout(50)
                
                # Setup Chrome DevTools Protocol Screencast
                cdp = None
                if frame_callback:
                    try:
                        cdp = page.context.new_cdp_session(page)
                        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 60, "everyNthFrame": 1})
                        
                        def on_cdp_frame(params):
                            data = params.get("data")
                            sid = params.get("sessionId")
                            if frame_callback and data:
                                frame_callback(data)
                            try:
                                cdp.send("Page.screencastFrameAck", {"sessionId": sid})
                            except Exception:
                                pass
                        
                        cdp.on("Page.screencastFrame", on_cdp_frame)
                    except Exception as e:
                        log_callback(f"Advertencia: No se pudo iniciar el streaming de pantalla para {site_name}: {e}", "warning")
                
                # Go to the login page first to ensure session is active
                log_callback(f"Navegando a la página de inicio de sesión de {site_name}: {config_data.get('login_url')}...", "info")
                page.goto(config_data.get('login_url'))
                
                is_logged_in = "wp-admin" in page.url or page.locator('#wpadminbar').is_visible()
                
                if not is_logged_in:
                    log_callback(f"Sesión no activa en {site_name}. Por favor, inicia sesión en la pestaña 'Navegador' del panel.", "warning")
                    
                    # Wait for user to log in manually (timeout 5 minutes)
                    login_timeout = 300
                    start_time = time.time()
                    while time.time() - start_time < login_timeout:
                        if "wp-admin" in page.url or page.locator('#wpadminbar').is_visible():
                            wait_timeout(2000)
                            is_logged_in = True
                            break
                        wait_timeout(1000)
                        
                    if not is_logged_in:
                        err_msg = f"Error: Se agotó el tiempo de espera para iniciar sesión en {site_name}."
                        log_callback(err_msg, "error")
                        # Fail all folios for this site group
                        for f in site_folios:
                            log_callback(
                                message=f"Folio {f} falló: {err_msg}",
                                level="error",
                                folio_status={
                                    "status": "failed",
                                    "folio": f,
                                    "error": err_msg,
                                    "domain": site_name
                                }
                            )
                        browser.close()
                        continue
                        
                    log_callback(f"¡Inicio de sesión exitoso detectado en {site_name}! Guardando cookies...", "success")
                    cookies = context.cookies()
                    os.makedirs(os.path.dirname(session_file), exist_ok=True)
                    with open(session_file, 'w', opener=lambda path,flags:os.open(path,flags,0o600)) as f:
                        json.dump(cookies, f)
                else:
                    log_callback(f"Sesión activa recuperada con éxito para {site_name}.", "success")
                    
                # Now, process all folios for this site group
                for folio in site_folios:
                    log_callback(f"Procesando folio {folio} en {site_name}...", "info")
                    
                    try:
                        base_url = config_data.get('base_url', '').rstrip('/')
                        target_url = f"{base_url}/{folio}/"
                        
                        log_callback(f"Navegando a la página del folio: {target_url}...", "info")
                        page.goto(target_url)
                        
                        log_callback("Esperando que cargue la barra de administración de WordPress...", "info")
                        edit_link_selector = '#wp-admin-bar-edit a, .ab-item[href*="action=edit"]'
                        try:
                            page.wait_for_selector(edit_link_selector, timeout=15000)
                        except Exception:
                            log_callback("Advertencia: Barra de administración no detectada. Intentando recargar la página...", "warning")
                            page.reload()
                            try:
                                page.wait_for_selector(edit_link_selector, timeout=10000)
                            except Exception:
                                pass
                        
                        edit_btn = page.locator(edit_link_selector)
                        if edit_btn.count() > 0:
                            log_callback("Haciendo clic en 'Editar la página'...", "info")
                            edit_btn.first.click()
                        else:
                            raise Exception("No se encontró el enlace de edición de página. ¿La página existe y estás logueado?")
                            
                        # Wait for Gutenberg editor
                        log_callback("Esperando que cargue el editor Gutenberg...", "info")
                        try:
                            page.wait_for_selector('.interface-interface-skeleton', timeout=30000)
                        except Exception:
                            log_callback("Tiempo de espera agotado cargando la interfaz del editor. Reintentando...", "warning")
                            page.reload()
                            page.wait_for_selector('.interface-interface-skeleton', timeout=30000)
                            
                        # Close welcome guide modals if they appear
                        wait_timeout(2000)
                        try:
                            guide_close = page.locator('.components-guide__close-button, .components-modal__header button[aria-label="Cerrar"], button[aria-label="Close dialog"]')
                            if guide_close.first.is_visible():
                                guide_close.first.click()
                                log_callback("Guía de bienvenida cerrada.", "info")
                        except Exception:
                            pass
                            
                        # Exit code editor mode if it's active
                        exit_code_editor_if_active(page, log_callback, wait_timeout)
                            
                        # Verify Gutenberg JS API availability
                        page.wait_for_function("typeof wp !== 'undefined' && typeof wp.data !== 'undefined' && typeof wp.blocks !== 'undefined'", timeout=15000)
                        
                        # Extract header text
                        log_callback("Buscando encabezado del documento (h2.wp-block-heading)...", "info")
                        header_text = folio
                        try:
                            extracted = get_document_heading(page)
                            if extracted:
                                header_text = extracted
                                log_callback(f"Encabezado de página detectado: '{header_text}'", "success")
                            else:
                                log_callback(f"No se encontró h2.wp-block-heading específico, usando el folio como encabezado: {folio}", "info")
                        except Exception as e:
                            log_callback(f"Advertencia al extraer encabezado: {e}. Usando folio: {folio}", "warning")
                            
                        page_metadata = get_document_metadata(page)
                        if page_metadata:
                            labels = {'alta':'Alta', 'version':'Cambio', 'cambio':'Cambio', 'baja':'Baja'}
                            log_callback(f"Acción de la página: {labels[page_metadata['action']]}", 'info')
                        else:
                            log_callback('Página sin marca interna: limpieza compatible con bloques creados manualmente.', 'info')
                        updated_url = remove_cognito_and_folio(page, log_callback) or target_url
                        if get_document_metadata(page, persisted=True) != page_metadata:
                            raise RuntimeError('La marca interna de la acción cambió al limpiar la página.')

                        if after_remove:
                            after_remove(domain, folio, page_metadata=page_metadata)

                        # Report success to frontend
                        log_callback(
                            message=f"Folio {folio} procesado con éxito.",
                            level="success",
                            folio_status={
                                "status": "success",
                                "folio": folio,
                                "header": header_text,
                                "action": (page_metadata or {}).get('action'),
                                "url": updated_url
                            }
                        )
                        wait_timeout(2000)
                        
                    except Exception as e:
                        err_msg = str(e)
                        log_callback(f"Error procesando folio {folio}: {err_msg}", "error")
                        # Report failure to frontend
                        log_callback(
                            message=f"Folio {folio} falló: {err_msg}",
                            level="error",
                            folio_status={
                                "status": "failed",
                                "folio": folio,
                                "error": err_msg,
                                "domain": site_name
                            }
                        )
                        # Go back to a safe page or reload just in case
                        try:
                            page.goto(config_data.get('login_url'))
                            wait_timeout(2000)
                        except Exception:
                            pass
                            
                browser.close()
        except Exception as outer_e:
            log_callback(f"Fallo grave procesando el sitio {site_name}: {outer_e}", "error")
            # Report failure for all remaining folios in this group if Playwright crashed
            for f in site_folios:
                log_callback(
                    message=f"Folio {f} falló debido a un error crítico de automatización.",
                    level="error",
                    folio_status={
                        "status": "failed",
                        "folio": f,
                        "error": "Error crítico de automatización en el sitio",
                        "domain": site_name
                    }
                )

