// Global State
let configsList = [];
let selectedConfigDomain = null;
let sseConnection = null;
let browserSseConnection = null;
let activeProcessMode = 'create';

// Page Initialization
document.addEventListener('DOMContentLoaded', () => {
  ['p1','p2'].forEach((process, index) => {
    const entryGrid = document.querySelector(`#tab-process${index+1} .dashboard-grid`);
    const previewColumn = document.getElementById(`right-panel-container-${process}`);
    if (!entryGrid || !previewColumn) return;
    const syncEntryHeight = () => {
      if (!previewColumn.contains(document.getElementById('right-panel'))) return;
      const height = previewColumn.getBoundingClientRect().height;
      if (height > 0) entryGrid.style.setProperty('--entry-card-height', `${Math.max(320, height)}px`);
    };
    new ResizeObserver(syncEntryHeight).observe(previewColumn);
    syncEntryHeight();
  });

  loadConfigs();
  initFolioAutoDetect();
  
  // Auto-detect site by folio prefix for Process 1
  const folioInputP1 = document.getElementById('input-folio-p1');
  if (folioInputP1) {
    folioInputP1.addEventListener('input', (e) => {
      autoDetectSite(e.target.value, 'site-select-p1');
    });
  }

  // Auto-detect site by folio prefix for Process 2
  const folioInputP2 = document.getElementById('input-folio-p2');
  if (folioInputP2) {
    folioInputP2.addEventListener('input', (e) => {
      autoDetectSite(e.target.value, 'site-select-p2');
    });
  }

  // Dynamic input label changing (Process 1 Only)
  const embedTypeRadios = document.querySelectorAll('input[name="input-embed-type-p1"]');
  const enlaceLabel = document.querySelector('label[for="input-enlace-p1"]');
  const enlaceInput = document.getElementById('input-enlace-p1');
  const campoPolitica = document.getElementById('campo-politica-p1');
  const politicaChk = document.getElementById('input-is-politica-p1');
  const campoCodigo = document.getElementById('campo-codigo-p1');
  
  embedTypeRadios.forEach(radio => {
    radio.addEventListener('change', (e) => {
      const type = e.target.value;
      if (type === 'form') {
        if (enlaceLabel) enlaceLabel.textContent = 'Enlace del Formulario / Web';
        if (enlaceInput) enlaceInput.placeholder = 'Ej. https://bolco-idpn.com/formulario';
        if (campoPolitica) campoPolitica.style.display = 'none';
        if (politicaChk) {
          politicaChk.checked = false;
          if (campoCodigo) campoCodigo.style.display = '';
        }
      } else {
        if (enlaceLabel) enlaceLabel.textContent = 'Enlace del Documento (Excel/S3)';
        if (enlaceInput) enlaceInput.placeholder = 'Ej. https://s3.amazonaws.com/...';
        if (campoPolitica) campoPolitica.style.display = '';
        if (politicaChk && campoCodigo) {
          campoCodigo.style.display = politicaChk.checked ? 'none' : '';
        }
      }
    });
  });

  // Live Interactive Browser View Event Listeners
  const screencastImg = document.getElementById('browser-screencast');
  const screenContainer = document.getElementById('browser-screen-container');

  if (screencastImg && screenContainer) {
    // Mouse Down (Click/Drag Start)
    screencastImg.addEventListener('mousedown', (e) => {
      if (screencastImg.style.display === 'none') return;
      
      const rect = screencastImg.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const clickY = e.clientY - rect.top;
      
      const remoteWidth = screencastImg.naturalWidth || 1280;
      const remoteHeight = screencastImg.naturalHeight || 800;
      
      const scaledX = Math.round((clickX / rect.width) * remoteWidth);
      const scaledY = Math.round((clickY / rect.height) * remoteHeight);
      
      sendBrowserEvent({
        type: 'mousedown',
        x: scaledX,
        y: scaledY,
        button: e.button === 2 ? 'right' : 'left'
      });
      
      // Explicitly focus screen container to listen for keydown and paste
      setTimeout(() => screenContainer.focus(), 10);
    });

    // Mouse Up (Click/Drag End)
    screencastImg.addEventListener('mouseup', (e) => {
      if (screencastImg.style.display === 'none') return;
      
      const rect = screencastImg.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const clickY = e.clientY - rect.top;
      
      const remoteWidth = screencastImg.naturalWidth || 1280;
      const remoteHeight = screencastImg.naturalHeight || 800;
      
      const scaledX = Math.round((clickX / rect.width) * remoteWidth);
      const scaledY = Math.round((clickY / rect.height) * remoteHeight);
      
      sendBrowserEvent({
        type: 'mouseup',
        x: scaledX,
        y: scaledY,
        button: e.button === 2 ? 'right' : 'left'
      });
    });

    // Prevent local image dragging
    screencastImg.addEventListener('dragstart', (e) => {
      e.preventDefault();
    });

    // Prevent local context menu on right click
    screencastImg.addEventListener('contextmenu', (e) => {
      e.preventDefault();
    });

    // Mouse Wheel Scroll
    screencastImg.addEventListener('wheel', (e) => {
      if (screencastImg.style.display === 'none') return;
      e.preventDefault();
      
      sendBrowserEvent({
        type: 'wheel',
        deltaX: Math.round(e.deltaX),
        deltaY: Math.round(e.deltaY)
      });
    });

    // Keyboard Input (Press Key)
    screenContainer.addEventListener('keydown', (e) => {
      if (screencastImg.style.display === 'none') return;
      
      // Allow copy, cut, and paste shortcuts to pass through so their events fire
      const isClipboardShortcut = (e.ctrlKey || e.metaKey) && ['v', 'c', 'x'].includes(e.key.toLowerCase());
      if (isClipboardShortcut) {
        return;
      }
      
      // Stop defaults like page scrolls, backspace history, tab navigation, etc.
      e.preventDefault();
      
      sendBrowserEvent({
        type: 'keypress',
        key: e.key
      });
    });

    // Clipboard Paste Text
    screenContainer.addEventListener('paste', (e) => {
      if (screencastImg.style.display === 'none') return;
      e.preventDefault();
      
      const text = (e.clipboardData || window.clipboardData).getData('text');
      if (text) {
        sendBrowserEvent({
          type: 'type',
          text: text
        });
      }
    });
  }
  // Initialize dashboard as Proceso 1 (Crear / Incrustar)
  restoreSectionFromUrl();
});

// Helper to automatically select the WordPress site based on the folio prefix
function autoDetectSite(val, selectId) {
  const cleanVal = val.trim().toUpperCase();
  if (!cleanVal) return;
  const siteSelect = document.getElementById(selectId);
  if (!siteSelect) return;
  
  const sortedConfigs = [...configsList]
    .filter(c => c.folio_prefix)
    .sort((a, b) => b.folio_prefix.length - a.folio_prefix.length);
    
  for (let c of sortedConfigs) {
    const prefix = c.folio_prefix.trim().toUpperCase();
    if (prefix && cleanVal.startsWith(prefix)) {
      if (siteSelect.value !== c.domain) {siteSelect.value = c.domain;siteSelect.dispatchEvent(new Event('change'));}
      break;
    }
  }
}

// Tab Navigation (Dashboard vs Settings)
function toggleCompanyMenu() {
  const collapsed = document.getElementById('company-settings-layout').classList.toggle('company-menu-collapsed');
  const toggle = document.getElementById('company-menu-toggle');
  toggle.setAttribute('aria-expanded', String(!collapsed));
  toggle.setAttribute('aria-label', collapsed ? 'Desplegar menú de configuración' : 'Plegar menú de configuración');
}

function switchCompanySection(section, updateHistory = true) {
  if (section === 'storage' && window.idpnUser && !window.idpnUser.admin) section = 'site';
  if (!['site','matrix','destination','storage'].includes(section)) section = 'site';
  document.getElementById('company-toolbar').hidden = section === 'storage';
  const deleteAction = document.getElementById('company-delete-action');
  deleteAction.hidden = section === 'storage' || !!(window.idpnUser && !window.idpnUser.admin);
  ['site', 'matrix', 'destination', 'storage'].forEach(name => {
    document.getElementById(`company-${name}-panel`).hidden = name !== section;
    document.getElementById(`company-${name}-btn`).classList.toggle('active', name === section);
  });
  if (updateHistory && location.pathname === '/configuracion') {
    const target = section === 'site' ? '/configuracion' : '/configuracion?seccion='+section;
    if (location.pathname+location.search !== target) history.pushState({}, '', target);
  }
}

function updateSectionUrl(section) {
  const paths = {create:'/primer-proceso', remove:'/segundo-proceso', settings:'/configuracion'};
  const path = paths[section];
  if (path && location.pathname !== path) history.pushState({section}, '', path);
}

function restoreSectionFromUrl() {
  const path = location.pathname.replace(/\/$/, '');
  if (path === '/configuracion') {switchTab('settings', false);switchCompanySection(new URLSearchParams(location.search).get('seccion') || 'site', false);}
  else switchProcessTab(path === '/segundo-proceso' ? 'remove' : 'create', false);
}
window.addEventListener('popstate', restoreSectionFromUrl);

function switchTab(tabId, updateHistory = true) {
  if (document.getElementById('right-panel').classList.contains('is-maximized')) togglePreviewSize(false);
  if (tabId === 'process1' || tabId === 'process2') {
    switchProcessTab(tabId === 'process1' ? 'create' : 'remove', updateHistory);
    return;
  }
  if (tabId === 'matrix') { tabId = 'settings'; switchCompanySection('matrix'); }
  document.querySelectorAll('.nav-menu .nav-item').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  
  const navEl = document.getElementById(`nav-${tabId}`);
  if (navEl) navEl.classList.add('active');
  const tabEl = document.getElementById(`tab-${tabId}`);
  if (tabEl) tabEl.classList.add('active');
  
  if (updateHistory && tabId === 'settings') {
    updateSectionUrl('settings');
    const active = ['site','matrix','destination','storage'].find(name => document.getElementById(`company-${name}-btn`).classList.contains('active')) || 'site';
    history.replaceState({}, '', active === 'site' ? '/configuracion' : '/configuracion?seccion='+active);
  }

  // Refresh configs if switching
  if (tabId === 'settings') {
    loadConfigs();
  }
}

// Tab Navigation for Process Types (Proceso 1 vs Proceso 2)
function switchProcessTab(mode, updateHistory = true) {
  if (document.getElementById('right-panel').classList.contains('is-maximized')) togglePreviewSize(false);
  if (updateHistory) updateSectionUrl(mode);
  activeProcessMode = mode;
  // Clear any active form alerts
  clearFormAlerts();
  
  // Set active class on menu items and dashboard tab content
  document.querySelectorAll('.nav-menu .nav-item').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  
  const navId = mode === 'create' ? 'nav-process1' : 'nav-process2';
  const navEl = document.getElementById(navId);
  if (navEl) navEl.classList.add('active');
  
  const tabId = mode === 'create' ? 'tab-process1' : 'tab-process2';
  const tabEl = document.getElementById(tabId);
  if (tabEl) tabEl.classList.add('active');
  
  // Move the right panel to the active tab's placeholder
  const rightPanel = document.getElementById('right-panel');
  const containerId = mode === 'create' ? 'right-panel-container-p1' : 'right-panel-container-p2';
  const container = document.getElementById(containerId);
  
  if (rightPanel && container) {
    container.prepend(rightPanel);
  }
}

// Settings Subtabs Navigation
function switchSubTab(subTabId) {
  document.querySelectorAll('#settings-form .settings-tab-btn').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.settings-tab-content').forEach(el => el.classList.remove('active'));
  
  document.getElementById(`subtab-${subTabId}-btn`).classList.add('active');
  document.getElementById(`subtab-${subTabId}`).classList.add('active');
}

// Load and Render configs from API
function loadConfigs() {
  fetch('/api/configs')
    .then(res => res.json())
    .then(data => {
      configsList = data;
      populateSiteSelect();
      autoDetectSite(document.getElementById('input-folio-p1').value, 'site-select-p1');
      renderConfigsList();
      document.dispatchEvent(new CustomEvent('idpn:configs-loaded', { detail: configsList }));
    })
    .catch(err => console.error('Error cargando configuraciones:', err));
}

// Populate the site selection dropdown on Dashboard
function populateSiteSelect() {
  const select1 = document.getElementById('site-select-p1');
  const select2 = document.getElementById('site-select-p2');
  const previous1 = select1?.value;
  const previous2 = select2?.value;
  
  if (select1) {
    select1.innerHTML = '<option value="" disabled selected>Selecciona un sitio...</option>';
  }
  if (select2) {
    select2.innerHTML = '<option value="" disabled selected>Selecciona un sitio...</option>';
  }
  
  configsList.forEach(c => {
    const opt1 = document.createElement('option');
    opt1.value = c.domain;
    opt1.textContent = `${c.site_name} (${c.domain})`;
    if (select1) select1.appendChild(opt1);
    
    const opt2 = document.createElement('option');
    opt2.value = c.domain;
    opt2.textContent = `${c.site_name} (${c.domain})`;
    if (select2) select2.appendChild(opt2);
  });
  if (select1 && configsList.some(c => c.domain === previous1)) select1.value = previous1;
  if (select2 && configsList.some(c => c.domain === previous2)) select2.value = previous2;
}

// Render the list of site configs in Settings
function renderConfigsList() {
  if (window.idpnUser && !window.idpnUser.can_edit_companies) return;
  const select = document.getElementById('config-company-select');
  select.replaceChildren(new Option('Selecciona una empresa...', ''));
  configsList.forEach(company => select.append(new Option(`${company.site_name} (${company.domain})`, company.domain)));
  if (!configsList.length) {
    selectedConfigDomain = null;
    document.getElementById('config-detail-card').style.display = 'none';
    document.getElementById('config-delete-selected').disabled = true;
    document.dispatchEvent(new CustomEvent('idpn:company-selected', {detail:{domain:''}}));
    return;
  }
  if (!configsList.some(company => company.domain === selectedConfigDomain)) selectConfig(configsList[0].domain);
  else select.value = selectedConfigDomain;
  document.getElementById('config-delete-selected').disabled = !selectedConfigDomain;
}

// Select a config and populate the settings detail editor
function selectConfig(domain) {
  selectedConfigDomain = domain;
  document.dispatchEvent(new CustomEvent('idpn:company-selected', {detail: {domain}}));
  
  document.getElementById('config-company-select').value = domain || '';
  document.getElementById('config-delete-selected').disabled = !domain;
  if (!domain) {document.getElementById('config-detail-card').style.display = 'none'; return;}

  fetch(`/api/configs/${domain}`)
    .then(res => res.json())
    .then(data => {
      if (selectedConfigDomain !== domain) return;
      if (data.error) {
        showSettingsAlert(data.error, 'error');
        return;
      }
      
      // Show form
      document.getElementById('config-detail-card').style.display = 'block';
      document.getElementById('config-is-new').value = 'false';
      
      // Populate fields
      document.getElementById('config-name').value = data.site_name;
      document.getElementById('config-domain').value = domain;
      document.getElementById('config-domain').disabled = true; // Lock domain on update
      document.getElementById('config-login-url').value = data.login_url;
      document.getElementById('config-create-url').value = data.create_page_url;
      document.getElementById('config-cognito').value = data.cognito_script;
      document.getElementById('config-folio-prefix').value = data.folio_prefix || '';
      
      // Ultimate Member Settings
      const um = data.ultimate_member || {};
      document.getElementById('config-um-restrict').checked = um.restrict_access ?? true;
      document.getElementById('config-um-accessible').value = um.accessible_to ?? '2';
      document.getElementById('config-um-noaccess').value = um.no_access_action ?? '0';
      
      // Check roles
      const roles = um.roles || ["administrator", "editor", "author", "contributor"];
      document.querySelectorAll('.role-chk').forEach(chk => {
        chk.checked = roles.includes(chk.value);
      });
      
      // Astra Settings
      const astra = data.astra_settings || {};
      document.getElementById('config-astra-container').value = astra.container_layout ?? 'normal-width-container';
      document.getElementById('config-astra-style').value = astra.container_style ?? 'Recuadro';
      document.getElementById('config-astra-sidebar').value = astra.sidebar_layout ?? 'right-sidebar';
      
      // Clear alert messages
      clearSettingsAlerts();
    })
    .catch(err => {
      console.error('Error al obtener detalle:', err);
      showSettingsAlert('Error de red al cargar configuración.', 'error');
    });
}

// Reset settings form for creating a new site
function createNewConfig() {
  selectedConfigDomain = null;
  switchCompanySection('site');
  document.dispatchEvent(new CustomEvent('idpn:company-selected', {detail: {domain: ''}}));
  
  // Unselect item in list
  document.getElementById('config-company-select').value = '';
  document.getElementById('config-delete-selected').disabled = true;
  
  // Show editor and unlock domain input
  document.getElementById('config-detail-card').style.display = 'block';
  document.getElementById('config-is-new').value = 'true';
  
  // Reset fields to defaults
  document.getElementById('config-name').value = '';
  document.getElementById('config-domain').value = '';
  document.getElementById('config-domain').disabled = false;
  document.getElementById('config-login-url').value = '';
  document.getElementById('config-create-url').value = '';
  document.getElementById('config-cognito').value = '';
  document.getElementById('config-folio-prefix').value = '';
  
  // Ultimate Member Defaults
  document.getElementById('config-um-restrict').checked = true;
  document.getElementById('config-um-accessible').value = '2';
  document.getElementById('config-um-noaccess').value = '0';
  document.querySelectorAll('.role-chk').forEach(chk => {
    chk.checked = ["administrator", "editor", "author", "contributor"].includes(chk.value);
  });
  
  // Astra Defaults
  document.getElementById('config-astra-container').value = 'normal-width-container';
  document.getElementById('config-astra-style').value = 'Recuadro';
  document.getElementById('config-astra-sidebar').value = 'right-sidebar';
  
  clearSettingsAlerts();
}

// Save config via POST to API
function saveConfig(e) {
  e.preventDefault();
  
  const isNew = document.getElementById('config-is-new').value === 'true';
  const domain = document.getElementById('config-domain').value.trim();
  
  // Get checked roles
  const roles = [];
  document.querySelectorAll('.role-chk:checked').forEach(chk => {
    roles.push(chk.value);
  });
  
  const payload = {
    domain: domain,
    site_name: document.getElementById('config-name').value.trim(),
    login_url: document.getElementById('config-login-url').value.trim(),
    create_page_url: document.getElementById('config-create-url').value.trim(),
    cognito_script: document.getElementById('config-cognito').value.trim(),
    folio_prefix: document.getElementById('config-folio-prefix').value.trim().toUpperCase(),
    restrict_access: document.getElementById('config-um-restrict').checked,
    accessible_to: document.getElementById('config-um-accessible').value,
    roles: roles,
    no_access_action: document.getElementById('config-um-noaccess').value,
    container_layout: document.getElementById('config-astra-container').value,
    container_style: document.getElementById('config-astra-style').value,
    sidebar_layout: document.getElementById('config-astra-sidebar').value
  };
  
  fetch('/api/configs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
    .then(res => res.json())
    .then(data => {
      if (data.error) {
        showSettingsAlert(data.error, 'error');
        return;
      }
      
      showSettingsAlert('Configuración guardada exitosamente.', 'success');
      
      // Reload configs and select the saved domain
      selectedConfigDomain = data.domain;
      document.getElementById('config-is-new').value = 'false';
      document.getElementById('config-domain').disabled = true;
      document.dispatchEvent(new CustomEvent('idpn:company-selected', {detail: {domain: data.domain}}));
      loadConfigs();
    })
    .catch(err => {
      console.error('Error al guardar:', err);
      showSettingsAlert('Error de red al guardar la configuración.', 'error');
    });
}

// Delete config from API
function deleteConfig(e, domain) {
  e.stopPropagation(); // Avoid selecting deleted config
  
  if (!confirm(`¿Estás seguro de que deseas eliminar la configuración de "${domain}" y su sesión guardada?`)) {
    return;
  }
  
  fetch(`/api/configs/${domain}`, {
    method: 'DELETE'
  })
    .then(res => res.json())
    .then(data => {
      if (data.error) {
        alert(data.error);
        return;
      }
      
      if (selectedConfigDomain === domain) {
        selectedConfigDomain = null;
      }
      loadConfigs();
    })
    .catch(err => console.error('Error eliminando configuración:', err));
}

// Console Terminal Log Append
function appendToTerminal(message, level = 'info', time = null) {
  const consoleBody = document.getElementById('terminal-console');
  
  if (!time) {
    const now = new Date();
    time = now.toTimeString().split(' ')[0];
  }
  
  const line = document.createElement('div');
  line.className = 'terminal-line';
  
  line.innerHTML = `
    <span class="terminal-time">${time}</span>
    <span class="terminal-text ${level}">${escapeHtml(message)}</span>
  `;
  
  consoleBody.appendChild(line);
  consoleBody.scrollTop = consoleBody.scrollHeight;
}

// Set up loader indicator inside terminal
function setTerminalLoading(active) {
  const consoleBody = document.getElementById('terminal-console');
  
  // Remove existing loader
  const existingLoader = document.getElementById('active-loader');
  if (existingLoader) {
    existingLoader.remove();
  }
  
  if (active) {
    const loader = document.createElement('div');
    loader.className = 'terminal-line';
    loader.id = 'active-loader';
    loader.innerHTML = `
      <span class="terminal-time">${new Date().toTimeString().split(' ')[0]}</span>
      <span class="terminal-loader">
        <span class="spinner"></span> Ejecutando automatización en segundo plano...
      </span>
    `;
    consoleBody.appendChild(loader);
    consoleBody.scrollTop = consoleBody.scrollHeight;
  }
}

function normalizeDocumentName(value) {
  return value.replace(/[-_]/g, ' ').replace(/[^a-zA-Z0-9\sáéíóúüñÁÉÍÓÚÜÑ]/g, '')
    .trim().replace(/\s+/g, ' ').toLocaleLowerCase('es').replace(/(^|[^\p{L}])(\p{L})/gu, (_, prefix, letter) => prefix + letter.toLocaleUpperCase('es'));
}

// Run the Playwright Automation Task
function runAutomation(processType, e) {
  e.preventDefault();
  
  clearFormAlerts(processType);
  
  let site = '';
  let folio = '';
  let folios = [];
  let is_politica = false;
  let codigo = '';
  let nombre = '';
  let enlace = '';
  let embed_type = 'document';
  
  if (processType === 'create') {
    site = document.getElementById('site-select-p1').value;
    folio = document.getElementById('input-folio-p1').value.trim();
    const politicaChk = document.getElementById('input-is-politica-p1');
    is_politica = politicaChk ? politicaChk.checked : false;
    codigo = is_politica ? '' : document.getElementById('input-codigo-p1').value.trim().toUpperCase();
    nombre = normalizeDocumentName(document.getElementById('input-nombre-p1').value);
    document.getElementById('input-nombre-p1').value = nombre;
    enlace = document.getElementById('input-enlace-p1').value.trim();
    embed_type = document.querySelector('input[name="input-embed-type-p1"]:checked').value;
    
    if (!site) {
      showFormAlert('Por favor, selecciona un sitio de WordPress.', 'error', processType);
      return;
    }
  } else {
    // Process 2: Read multiple folios
    const inputs = document.querySelectorAll('.input-folio-p2-item');
    inputs.forEach(input => {
      const val = input.value.trim();
      if (val) folios.push(val);
    });
    
    if (folios.length === 0) {
      showFormAlert('Por favor, ingresa al menos un Folio.', 'error', processType);
      return;
    }
    folio = folios[0]; // fallback
    
    // Initialize results container in frontend
    const resultsContainer = document.getElementById('results-container-p2');
    if (resultsContainer) {
      resultsContainer.style.display = 'flex';
    }
    
    // Reset sections
    document.getElementById('results-exitosos-section').style.display = 'none';
    document.getElementById('results-fallidos-section').style.display = 'none';
    document.getElementById('results-desconocidos-section').style.display = 'none';
    
    // Reset counts
    document.getElementById('results-exitosos-count').textContent = '0';
    document.getElementById('results-fallidos-count').textContent = '0';
    document.getElementById('results-desconocidos-count').textContent = '0';
    
    // Reset table bodies
    document.getElementById('results-exitosos-tbody').innerHTML = '';
    document.getElementById('results-fallidos-tbody').innerHTML = '';
    document.getElementById('results-desconocidos-tbody').innerHTML = '';
  }
  
  // Prepare Submit Button and Terminal UI
  const submitBtn = document.getElementById(processType === 'create' ? 'submit-btn-p1' : 'submit-btn-p2');
  if (submitBtn) submitBtn.disabled = true;
  
  // Clear terminal and append start line
  const consoleBody = document.getElementById('terminal-console');
  consoleBody.innerHTML = '';
  
  const startMsg = processType === 'remove' ? 'Iniciando proceso de eliminación de iframe y folio en lote...' : 'Iniciando proceso de creación de página...';
  appendToTerminal(startMsg, 'info');
  setTerminalLoading(true);
  
  // Payload
  const payload = {
    site, folio, folios, codigo, nombre, enlace, embed_type, is_politica, process_type: processType
  };
  
  if (processType === 'create') payload.action = document.getElementById('matrix-action').value;

  // Send POST request to execute automation
  let body = JSON.stringify(payload);
  let headers = {'Content-Type':'application/json'};
  if (processType === 'create' && embed_type === 'document') {
    try {
      payload.classification = window.documentOperation.metadata();
      payload.operation_id = window.documentOperation.requestId();
      body = new FormData();
      Object.entries(payload).forEach(([key,value]) => body.append(key, typeof value === 'object' ? JSON.stringify(value) : String(value)));
      const file = document.getElementById('matrix-file').files[0];
      if (embed_type === 'document' && file) body.append('file', file);
      headers = {};
    } catch(error) {
      showFormAlert(error.message,'error',processType);
      if (submitBtn) submitBtn.disabled = false;
      setTerminalLoading(false);
      return;
    }
  }
  fetch('/api/run', {method:'POST', headers, body})
    .then(res => res.json())
    .then(data => {
      if (data.error) {
        showFormAlert(data.error, 'error', processType);
        if (submitBtn) submitBtn.disabled = false;
        setTerminalLoading(false);
        appendToTerminal(`ERROR: ${data.error}`, 'error');
        return;
      }
      
      // Connect to SSE streams
      connectToLogStream();
      connectToBrowserStream();
    })
    .catch(err => {
      console.error(err);
      showFormAlert('Error al conectar con el servidor local.', 'error', processType);
      if (submitBtn) submitBtn.disabled = false;
      setTerminalLoading(false);
      appendToTerminal('Error de red al iniciar la tarea.', 'error');
    });
}

// Server-Sent Events (SSE) logs connection
function connectToLogStream() {
  if (sseConnection) {
    sseConnection.close();
  }
  
  sseConnection = new EventSource('/stream-logs');
  
  sseConnection.onmessage = (event) => {
    const data = JSON.parse(event.data);
    
    // Heartbeats keep connection open
    if (data.heartbeat) return;
    
    // Check if the task is completed
    if (data.message === 'FIN_AUTOMATIZACION' || data.level === 'done') {
      sseConnection.close();
      if (browserSseConnection) {
        browserSseConnection.close();
        browserSseConnection = null;
      }
      setTerminalLoading(false);
      
      const submitBtnP1 = document.getElementById('submit-btn-p1');
      if (submitBtnP1) submitBtnP1.disabled = false;
      const submitBtnP2 = document.getElementById('submit-btn-p2');
      if (submitBtnP2) submitBtnP2.disabled = false;
      
      appendToTerminal('Tarea finalizada.', 'success', data.time);
      
      // Reset live browser view status
      resetBrowserStreamUI();
      
      const url = data.url || '';
      const processType = activeProcessMode;
      
      if (data.success === false) {
        showFormAlert(processType === 'remove' ? 'Hay folios pendientes o fallidos. Revisa los resultados y reintenta esos folios.' : 'La tarea no se completó. Revisa el error en la consola; puedes reintentar con el mismo documento.','error',processType);
      } else if (processType === 'remove') {
        showFormAlert('¡Procesamiento en lote de folios finalizado! Revisa los resultados detallados abajo.', 'success', processType);
      } else {
        if (url) {
          appendToTerminal(`Enlace publicado: ${url}`, 'success', data.time);
          showFormAlert(`¡Página creada y publicada correctamente!<div style="display: flex; align-items: center; gap: 8px; margin-top: 12px; flex-wrap: wrap;"><a href="${url}" target="_blank" class="btn btn-secondary" style="font-size: 13px; display: inline-flex;"><i class="fa-solid fa-arrow-up-right-from-square"></i> Abrir página publicada</a><button type="button" onclick="copyPageUrl('${url}')" class="btn btn-secondary" id="copy-url-btn" style="font-size: 13px; display: inline-flex;"><i class="fa-solid fa-copy"></i> Copiar enlace</button></div>`, 'success', processType);
        } else {
          showFormAlert('¡Página creada y publicada correctamente!', 'success', processType);
        }
      }
      return;
    }
    
    // Intercept dynamic folio status updates
    if (data.folio_status) {
      const fs = data.folio_status;
      const status = fs.status;
      const folio = fs.folio;
      const domain = fs.domain || 'Desconocido';
      
      if (status === 'success') {
        const header = fs.header || folio;
        const formattedHeader = formatHeaderToTitleCase(header);
        const url = fs.url || '';
        
        const section = document.getElementById('results-exitosos-section');
        if (section) section.style.display = 'block';
        
        const countEl = document.getElementById('results-exitosos-count');
        if (countEl) countEl.textContent = parseInt(countEl.textContent) + 1;
        
        const tbody = document.getElementById('results-exitosos-tbody');
        if (tbody) {
          const row = document.createElement('tr');
          row.innerHTML = `
            <td style="padding: 10px 12px; border-right: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0; vertical-align: middle; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
              <div class="result-copy-box" onclick="copyValueToClipboard('${escapeHtml(formattedHeader)}', this)" style="cursor: pointer; padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 6px; background: #f1f5f9; color: #334155; font-family: monospace; font-size: 12px; display: inline-flex; align-items: center; gap: 6px; width: 100%; box-sizing: border-box;" title="Haz clic para copiar">
                <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0;">${escapeHtml(formattedHeader)}</span> 
                <i class="fa-solid fa-copy" style="font-size: 11px; opacity: 0.6; flex-shrink: 0;"></i>
              </div>
            </td>
            <td style="padding: 10px 12px; border-right: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0; vertical-align: middle; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
              ${url ? `
                <div class="result-copy-box" onclick="copyValueToClipboard('${escapeHtml(url)}', this)" style="cursor: pointer; padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 6px; background: #f1f5f9; color: #0397D1; font-family: monospace; font-size: 12px; display: inline-flex; align-items: center; gap: 6px; width: 100%; box-sizing: border-box;" title="Haz clic para copiar">
                  <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0;">${escapeHtml(url)}</span> 
                  <i class="fa-solid fa-copy" style="font-size: 11px; opacity: 0.6; flex-shrink: 0;"></i>
                </div>
              ` : `
                <span style="font-size: 12px; color: #94a3b8; font-style: italic;">(No se pudo extraer la URL)</span>
              `}
            </td>
            <td style="padding: 10px 12px; border-bottom: 1px solid #e2e8f0; text-align: center; vertical-align: middle;">
              ${url ? `
                <a href="${url}" target="_blank" class="btn btn-secondary" style="padding: 6px; min-width: unset; width: 32px; height: 32px; display: inline-flex; align-items: center; justify-content: center; border-radius: 6px; font-size: 13px; background: #f1f5f9; border: 1px solid #cbd5e1; box-sizing: border-box; color: #64748b;" title="Visitar página">
                  <i class="fa-solid fa-arrow-up-right-from-square"></i>
                </a>
              ` : `<span style="font-size: 12px; color: #94a3b8;">-</span>`}
            </td>
          `;
          tbody.appendChild(row);
        }
      } else if (status === 'failed') {
        const error = fs.error || 'Fallo desconocido';
        
        const section = document.getElementById('results-fallidos-section');
        if (section) section.style.display = 'block';
        
        const countEl = document.getElementById('results-fallidos-count');
        if (countEl) countEl.textContent = parseInt(countEl.textContent) + 1;
        
        const tbody = document.getElementById('results-fallidos-tbody');
        if (tbody) {
          const row = document.createElement('tr');
          row.innerHTML = `
            <td style="padding: 10px 12px; border-right: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0; font-family: monospace; font-size: 12px; color: #334155; word-break: break-all;">
              ${escapeHtml(folio)}
            </td>
            <td style="padding: 10px 12px; border-right: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0; font-size: 12px; color: var(--error-color); word-break: break-word;">
              ${escapeHtml(error)}
            </td>
            <td style="padding: 10px 12px; border-bottom: 1px solid #e2e8f0; font-size: 12px; color: #64748b; text-align: center;">
              ${escapeHtml(domain)}
            </td>
          `;
          tbody.appendChild(row);
        }
      } else if (status === 'unknown') {
        const error = fs.error || 'Prefijo de folio no reconocido';
        
        const section = document.getElementById('results-desconocidos-section');
        if (section) section.style.display = 'block';
        
        const countEl = document.getElementById('results-desconocidos-count');
        if (countEl) countEl.textContent = parseInt(countEl.textContent) + 1;
        
        const tbody = document.getElementById('results-desconocidos-tbody');
        if (tbody) {
          const row = document.createElement('tr');
          row.innerHTML = `
            <td style="padding: 10px 12px; border-right: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0; font-family: monospace; font-size: 12px; color: #334155;">
              ${escapeHtml(folio)}
            </td>
            <td style="padding: 10px 12px; border-bottom: 1px solid #e2e8f0; font-size: 12px; color: var(--warning-color); font-style: italic;">
              ${escapeHtml(error)}
            </td>
          `;
          tbody.appendChild(row);
        }
      }
    }
    
    // Append to terminal
    appendToTerminal(data.message, data.level, data.time);
    
    // If there is an error, highlight it in form alert too
    if (data.level === 'error') {
      showFormAlert(data.message, 'error', activeProcessMode);
    }
  };
  
  sseConnection.onerror = (err) => {
    console.error('SSE Error:', err);
    sseConnection.close();
    if (browserSseConnection) {
      browserSseConnection.close();
      browserSseConnection = null;
    }
    resetBrowserStreamUI();
    
    setTerminalLoading(false);
    const submitBtnP1 = document.getElementById('submit-btn-p1');
    if (submitBtnP1) submitBtnP1.disabled = false;
    const submitBtnP2 = document.getElementById('submit-btn-p2');
    if (submitBtnP2) submitBtnP2.disabled = false;
    appendToTerminal('Error de conexión con el log en tiempo real.', 'error');
  };
}

// Alert messaging helper functions
function showSettingsAlert(msg, type) {
  const alertEl = document.getElementById('settings-alert');
  const successEl = document.getElementById('settings-success');
  
  clearSettingsAlerts();
  
  if (type === 'error') {
    alertEl.textContent = msg;
    alertEl.style.display = 'block';
  } else {
    successEl.innerHTML = msg;
    successEl.style.display = 'block';
  }
}

function clearSettingsAlerts() {
  document.getElementById('settings-alert').style.display = 'none';
  document.getElementById('settings-success').style.display = 'none';
}

function showFormAlert(msg, type, processType) {
  const pMode = processType || activeProcessMode;
  const suffix = pMode === 'create' ? '-p1' : '-p2';
  const alertEl = document.getElementById('form-alert' + suffix);
  const successEl = document.getElementById('form-success' + suffix);
  
  clearFormAlerts(pMode);
  
  if (alertEl && successEl) {
    if (type === 'error') {
      alertEl.textContent = msg;
      alertEl.style.display = 'block';
    } else {
      successEl.innerHTML = msg;
      successEl.style.display = 'block';
    }
  }
}

function clearFormAlerts(processType) {
  if (processType) {
    const suffix = processType === 'create' ? '-p1' : '-p2';
    const alertEl = document.getElementById('form-alert' + suffix);
    const successEl = document.getElementById('form-success' + suffix);
    if (alertEl) alertEl.style.display = 'none';
    if (successEl) successEl.style.display = 'none';
  } else {
    const alertElP1 = document.getElementById('form-alert-p1');
    const successElP1 = document.getElementById('form-success-p1');
    const alertElP2 = document.getElementById('form-alert-p2');
    const successElP2 = document.getElementById('form-success-p2');
    if (alertElP1) alertElP1.style.display = 'none';
    if (successElP1) successElP1.style.display = 'none';
    if (alertElP2) alertElP2.style.display = 'none';
    if (successElP2) successElP2.style.display = 'none';
  }
}

// HTML Escaping utility
function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// Copy published page URL to clipboard
function copyPageUrl(url) {
  navigator.clipboard.writeText(url).then(() => {
    const btn = document.getElementById('copy-url-btn');
    if (btn) {
      const original = btn.innerHTML;
      btn.innerHTML = '<i class="fa-solid fa-check"></i> ¡Copiado!';
      btn.style.background = 'var(--accent-green, #22c55e)';
      btn.style.color = '#fff';
      setTimeout(() => {
        btn.innerHTML = original;
        btn.style.background = '';
        btn.style.color = '';
      }, 2000);
    }
  }).catch(() => {
    // Fallback for older browsers
    const el = document.createElement('textarea');
    el.value = url;
    document.body.appendChild(el);
    el.select();
    document.execCommand('copy');
    document.body.removeChild(el);
    const btn = document.getElementById('copy-url-btn');
    if (btn) {
      const original = btn.innerHTML;
      btn.innerHTML = '<i class="fa-solid fa-check"></i> ¡Copiado!';
      setTimeout(() => { btn.innerHTML = original; }, 2000);
    }
  });
}

// Format document header text to Title Case, keeping the code token in uppercase
function formatHeaderToTitleCase(str) {
  if (!str) return "";
  return str.split(/\s+/).map(word => {
    // A code token typically has hyphens, underscores, or digits and matches [A-Z0-9_-]
    const isCode = /^[A-Z0-9_\-]+$/.test(word) && (word.includes('-') || word.includes('_') || /[0-9]/.test(word));
    if (isCode) {
      return word; // Keep uppercase
    }
    if (word.length === 0) return "";
    return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
  }).join(" ");
}

// Copy a custom value from result boxes to clipboard with premium feedback animation
function copyValueToClipboard(text, element) {
  navigator.clipboard.writeText(text).then(() => {
    // Save original styles
    const originalBorder = element.style.borderColor;
    const originalBackground = element.style.background;
    const originalColor = element.style.color;
    
    // Add success feedback style
    element.style.borderColor = '#22c55e';
    element.style.background = '#f0fdf4';
    element.style.color = '#15803d';
    
    // Change icon temporarily if possible
    const icon = element.querySelector('i');
    let originalClass = '';
    if (icon) {
      originalClass = icon.className;
      icon.className = 'fa-solid fa-check';
      icon.style.color = '#15803d';
    }
    
    setTimeout(() => {
      element.style.borderColor = originalBorder;
      element.style.background = originalBackground;
      element.style.color = originalColor;
      if (icon) {
        icon.className = originalClass;
        icon.style.color = '';
      }
    }, 1500);
  }).catch(err => {
    console.error('Error copying text to clipboard:', err);
  });
}

// Right Panel Tab Navigation (Console vs Browser View)
let previewOrigin = null;
let previewBackground = null;
function togglePreviewSize(force) {
  const panel = document.getElementById('right-panel');
  const button = document.getElementById('preview-expand-btn');
  const expanded = typeof force === 'boolean' ? force : !panel.classList.contains('is-maximized');
  if (expanded && !panel.classList.contains('is-maximized')) {
    previewOrigin = document.createComment('preview-original-position');
    panel.before(previewOrigin);
    document.body.append(panel);
    const workspace = document.querySelector('body > .container');
    previewBackground = {workspace, inert:workspace?.inert || false, overflow:document.body.style.overflow};
    if (workspace) workspace.inert = true;
    document.body.style.overflow = 'hidden';
    document.querySelectorAll('.account-menu[open]').forEach(menu => menu.open = false);
  } else if (!expanded && panel.classList.contains('is-maximized')) {
    if (previewOrigin?.parentNode) previewOrigin.replaceWith(panel);
    if (previewBackground?.workspace) previewBackground.workspace.inert = previewBackground.inert;
    document.body.style.overflow = previewBackground?.overflow || '';
    previewOrigin = null;
    previewBackground = null;
  }
  panel.classList.toggle('is-maximized', expanded);
  button.setAttribute('aria-expanded', String(expanded));
  button.setAttribute('aria-label', expanded ? 'Restaurar visor' : 'Ampliar visor');
  button.title = expanded ? 'Restaurar visor' : 'Ampliar visor';
  button.querySelector('use').setAttribute('href', expanded ? '#icon-collapse' : '#icon-expand');
  if (expanded) {panel.setAttribute('role','dialog');panel.setAttribute('aria-label','Navegador y consola ampliados');panel.setAttribute('aria-modal','true');}
  else {panel.removeAttribute('role');panel.removeAttribute('aria-modal');panel.removeAttribute('aria-label');}
  button.focus();
}

document.addEventListener('keydown', event => {
  const panel = document.getElementById('right-panel');
  if (!panel?.classList.contains('is-maximized')) return;
  if (event.key === 'Escape') {event.preventDefault();event.stopPropagation();togglePreviewSize(false);}
  if (event.key === 'Tab') {
    const items = [...panel.querySelectorAll('button, [tabindex="0"]')].filter(item => item.getClientRects().length);
    const first = items[0], last = items[items.length-1];
    if (event.shiftKey && document.activeElement === first) {event.preventDefault();last.focus();}
    else if (!event.shiftKey && document.activeElement === last) {event.preventDefault();first.focus();}
  }
}, true);

function switchRightTab(tabId) {
  document.querySelectorAll('.right-panel-tabs .tab-btn').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.right-tab-content').forEach(el => el.classList.remove('active'));
  
  document.getElementById(`right-tab-${tabId}-btn`).classList.add('active');
  document.getElementById(`right-tab-${tabId}`).classList.add('active');
}

// Connect to Server-Sent Events (SSE) for Browser Screencast
function connectToBrowserStream() {
  if (browserSseConnection) {
    browserSseConnection.close();
  }
  
  const statusDot = document.getElementById('browser-status-dot');
  const statusText = document.getElementById('browser-status-text');
  const screencastImg = document.getElementById('browser-screencast');
  const placeholder = document.getElementById('browser-placeholder');
  
  if (statusDot) {
    statusDot.className = 'status-indicator online';
  }
  if (statusText) {
    statusText.textContent = 'Conectando...';
  }
  
  browserSseConnection = new EventSource('/stream-browser');
  
  browserSseConnection.onmessage = (event) => {
    const data = JSON.parse(event.data);
    
    if (data.heartbeat) return;
    
    if (data.init) {
      if (statusText) statusText.textContent = 'Inicializando...';
      return;
    }
    
    if (data.frame) {
      if (screencastImg) {
        screencastImg.src = 'data:image/jpeg;base64,' + data.frame;
        if (screencastImg.style.display === 'none') {
          screencastImg.style.display = 'block';
        }
      }
      if (placeholder && placeholder.style.display !== 'none') {
        placeholder.style.display = 'none';
      }
      if (statusText) {
        statusText.textContent = 'Conectado (En Vivo)';
      }
    }
  };
  
  browserSseConnection.onerror = (err) => {
    console.error('Browser stream error:', err);
    browserSseConnection.close();
    browserSseConnection = null;
    resetBrowserStreamUI();
  };
}

// Reset Browser View UI on stop/disconnect
function resetBrowserStreamUI() {
  const statusDot = document.getElementById('browser-status-dot');
  const statusText = document.getElementById('browser-status-text');
  const screencastImg = document.getElementById('browser-screencast');
  const placeholder = document.getElementById('browser-placeholder');
  
  if (statusDot) {
    statusDot.className = 'status-indicator offline';
  }
  if (statusText) {
    statusText.textContent = 'Desconectado';
  }
  if (screencastImg) {
    screencastImg.style.display = 'none';
    screencastImg.src = '';
  }
  if (placeholder) {
    placeholder.style.display = 'flex';
  }
}

// Send mouse/keyboard event to API
function sendBrowserEvent(eventData) {
  fetch('/api/browser-event', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(eventData)
  })
  .then(res => res.json())
  .then(data => {
    if (data.error) {
      console.warn('Browser event warn:', data.error);
    }
  })
  .catch(err => console.error('Error sending browser event:', err));
}

// Add a new folio input row (Process 2)
function addFolioRow() {
  const container = document.getElementById('folios-container-p2');
  if (!container) return;
  
  const rows = container.querySelectorAll('.folio-input-row');
  if (rows.length >= 10) {
    alert('Has alcanzado el límite máximo de 10 folios.');
    return;
  }
  
  const newRow = document.createElement('div');
  newRow.className = 'folio-input-row';
  newRow.style.display = 'flex';
  newRow.style.gap = '8px';
  newRow.style.alignItems = 'center';
  newRow.style.marginTop = '8px';
  
  newRow.innerHTML = `
    <div style="flex: 1; position: relative;">
      <input type="text" class="input-folio-p2-item" placeholder="Ej. COD541403" required style="padding-right: 130px;">
      <span class="detected-site-badge">Sin detectar</span>
    </div>
    <button type="button" class="btn btn-secondary delete-folio-row-btn" onclick="deleteFolioRow(this)" style="padding: 0; width: 44px; height: 44px; display: inline-flex; align-items: center; justify-content: center; border-radius: 10px; margin-bottom: 0; color: var(--error-color); border-color: rgba(239, 68, 68, 0.2); flex-shrink: 0;" title="Quitar folio" aria-label="Quitar folio">
      <span aria-hidden="true" style="font-size:22px;line-height:1">×</span>
    </button>
  `;
  
  container.appendChild(newRow);
}

// Delete a folio input row (Process 2)
function deleteFolioRow(button) {
  const row = button.closest('.folio-input-row');
  if (row) {
    row.remove();
  }
}

// Initialize folio autodetect input listeners
function initFolioAutoDetect() {
  const container = document.getElementById('folios-container-p2');
  if (!container) return;
  
  container.addEventListener('input', (e) => {
    if (e.target && e.target.classList.contains('input-folio-p2-item')) {
      const val = e.target.value.trim().toUpperCase();
      const badge = e.target.parentElement.querySelector('.detected-site-badge');
      if (!badge) return;
      
      if (!val) {
        badge.textContent = 'Sin detectar';
        badge.style.color = 'var(--text-muted)';
        badge.style.background = '#f1f5f9';
        badge.style.borderColor = '#e2e8f0';
        return;
      }
      
      const sortedConfigs = [...configsList]
        .filter(c => c.folio_prefix)
        .sort((a, b) => b.folio_prefix.length - a.folio_prefix.length);
        
      let matched = false;
      for (let c of sortedConfigs) {
        const prefix = c.folio_prefix.trim().toUpperCase();
        if (val.startsWith(prefix)) {
          badge.textContent = c.site_name;
          badge.style.color = '#0288d1';
          badge.style.background = '#e1f5fe';
          badge.style.borderColor = '#b3e5fc';
          matched = true;
          break;
        }
      }
      
      if (!matched) {
        badge.textContent = 'Desconocido';
        badge.style.color = 'var(--warning-color)';
        badge.style.background = '#fffbeb';
        badge.style.borderColor = '#fde68a';
      }
    }
  });
}
