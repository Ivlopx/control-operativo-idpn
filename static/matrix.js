// Company matrices are separate from WordPress plugin settings.
(() => {
  let domain = '', matrix = null, lastPlan = null, pendingLoad = 0, dirty = false, planRevision = 0, operationMatrix = null, operationDomain = '', operationLoad = 0, previewTimer = null, operationId = null, detectionTimer = null, detectionRevision = 0, manualClassification = false;
  const el = id => document.getElementById({'matrix-existing-code':'input-codigo-p1','matrix-document-name':'input-nombre-p1','matrix-document-name-field':'campo-nombre-p1'}[id] || id);
  const node = (tag, text, className) => {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    if (className) result.className = className;
    return result;
  };
  const newId = () => `item-${crypto.randomUUID()}`;
  const message = (id, text, error = false) => {
    const box = el(id);
    box.textContent = text;
    box.hidden = !text;
    box.classList.toggle('matrix-error', error);
  };
  async function api(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || 'No se pudo completar la solicitud.');
    return data;
  }
  function field(container, label, value, change, placeholder = '') {
    const wrapper = node('div', undefined, 'form-group');
    const input = node('input');
    const id = newId();
    input.id = id;
    const caption = node('label', label);
    caption.htmlFor = id;
    input.value = value || '';
    input.placeholder = placeholder;
    input.addEventListener('input', () => {change(input.value); dirty = true; invalidatePlan();});
    wrapper.append(caption, input);
    container.append(wrapper);
    return input;
  }
  function button(label, action, className = 'btn btn-secondary') {
    const result = node('button', label, className);
    result.type = 'button';
    result.addEventListener('click', action);
    return result;
  }
  function invalidatePlan() {
    planRevision++;
    lastPlan = null;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(updatePreview, 350);
    el('matrix-result').hidden = true;
    message('matrix-plan-error', '');
  }
  function storageOverride(container, item) {
    const details = node('details', undefined, 'matrix-storage-override');
    details.append(node('summary', 'Destino de almacenamiento propio (opcional)'));
    const label = node('label', undefined, 'checkbox-label');
    const checkbox = node('input');
    checkbox.type = 'checkbox';
    checkbox.checked = !!item.storage;
    label.append(checkbox, document.createTextNode(' Usar un destino propio en esta rama'));
    const fields = node('div', undefined, 'matrix-fields');
    const storage = item.storage || {...matrix.storage};
    const providerGroup = node('div', undefined, 'form-group');
    providerGroup.append(node('label', 'Servicio'));
    const provider = node('select');
    for (const [value, caption] of [['s3', 'Amazon S3'], ['azure', 'Azure Blob Storage']]) {
      const option = node('option', caption); option.value = value; provider.append(option);
    }
    provider.value = storage.provider || 's3';
    providerGroup.append(provider); fields.append(providerGroup);
    const target = field(fields, 'Bucket', storage.target, value => storage.target = value);
    field(fields, 'Directorio raíz', storage.root, value => storage.root = value);
    const region = field(fields, 'Región S3', storage.region || matrix.storage.region || 'us-east-1', value => storage.region = value);
    const providerFields = () => {
      storage.provider = provider.value;
      target.parentElement.querySelector('label').textContent = provider.value === 'azure' ? 'Contenedor' : 'Bucket';
      region.parentElement.hidden = provider.value !== 's3';
    };
    providerFields();
    provider.addEventListener('change', () => {providerFields(); dirty = true; invalidatePlan();});
    const refresh = () => {
      fields.hidden = !checkbox.checked;
      if (checkbox.checked) {storage.provider = provider.value; item.storage = storage;}
      else delete item.storage;
      dirty = true;
      invalidatePlan();
    };
    fields.hidden = !checkbox.checked;
    checkbox.addEventListener('change', refresh);
    details.append(label, fields);
    container.append(details);
  }
  function entryRow(item, remove, process = false) {
    const row = node('div', undefined, 'matrix-entry');
    const fields = node('div', undefined, 'matrix-fields');
    field(fields, process ? 'Proceso' : 'Nombre', item.name, value => {item.name = value; renderChoices();});
    field(fields, 'Siglas', item.abbreviation, value => {item.abbreviation = value.toUpperCase(); renderChoices();}, 'Pendientes');
    field(fields, 'Directorio', item.directory, value => item.directory = value, 'Se deriva del nombre al guardar');
    row.append(fields, button('Quitar de la matriz', remove, 'btn matrix-remove'));

    return row;
  }
  function renderDestinationOverrides() {
    const container = el('destination-overrides');
    container.replaceChildren();
    for (const [scope, branches] of [['Gobierno', matrix.government], ['Específicos', matrix.specific]]) {
      branches.forEach(branch => {
        const group = node('div', undefined, 'destination-branch');
        group.append(node('h4', `${scope} · ${branch.name}`));
        storageOverride(group, branch);
        (branch.areas || []).forEach(area => {
          const row = node('div', undefined, 'destination-area');
          row.append(node('h5', area.name));
          storageOverride(row, area);
          group.append(row);
        });
        container.append(group);
      });
    }
  }
  function renderAdmin() {
    el('matrix-name').value = matrix.company;
    el('matrix-manual-prefix').value = matrix.manual_prefix || 'MG';
    el('matrix-manual-directory').value = matrix.manual_directory || 'manualesDelSistemaDeGestion';
    el('matrix-government-directory').value = matrix.government_directory || 'procedimientosDeGobierno';
    el('matrix-specific-directory').value = matrix.specific_directory || 'procedimientosEspecificos';
    el('matrix-omit-scope').checked = matrix.omit_scope_directory === true;
    el('matrix-provider').value = matrix.storage.provider;
    el('matrix-target').value = matrix.storage.target;
    el('matrix-root').value = matrix.storage.root;
    el('matrix-region').value = matrix.storage.region || 'us-east-1';
    renderStorageFields();
    el('matrix-target').setAttribute('list', 'matrix-targets');
    renderDestinationOverrides();
    el('matrix-types').replaceChildren();
    matrix.types.forEach(type => {
      const row = node('div', undefined, 'matrix-fields matrix-type-row');
      field(row, type.store_file ? type.name : `${type.name} (sin archivo)`, type.name, value => {type.name = value; renderChoices();});
      field(row, 'Prefijo Gobierno', type.government_prefix, value => type.government_prefix = value.toUpperCase());
      field(row, 'Prefijo Específicos', type.specific_prefix, value => type.specific_prefix = value.toUpperCase());
      el('matrix-types').append(row);
    });
    el('matrix-government').replaceChildren();
    matrix.government.forEach(item => {
      el('matrix-government').append(entryRow(item, () => {
        matrix.government = matrix.government.filter(entry => entry.id !== item.id);
        dirty = true;
        renderAdmin(); renderChoices(); invalidatePlan();
      }));
    });
    el('matrix-specific').replaceChildren();
    matrix.specific.forEach(process => {
      const group = node('div', undefined, 'matrix-process');
      const header = node('div', undefined, 'matrix-fields matrix-process-header');
      field(header, 'Nombre del proceso', process.name, value => {process.name = value; renderChoices();});
      field(header, 'Sigla del proceso', process.abbreviation, value => {process.abbreviation = value.toUpperCase(); renderChoices();});
      field(header, 'Directorio del proceso', process.directory, value => {process.directory = value;});
      group.append(header);
      const settings = node('details', undefined, 'matrix-process-settings');
      settings.append(node('summary', 'Ajustes del proceso'));

      settings.append(button('Quitar proceso', () => {
        matrix.specific = matrix.specific.filter(entry => entry.id !== process.id);
        dirty = true; renderAdmin(); renderChoices(); invalidatePlan();
      }, 'btn matrix-remove'));
      group.append(settings);
      const areas = node('details', undefined, 'matrix-process-areas');
      areas.append(node('summary', `Áreas (${process.areas.length})`));
      const list = node('div', undefined, 'matrix-process-area-list');
      process.areas.forEach(area => {
        const row = entryRow(area, () => {
          process.areas = process.areas.filter(entry => entry.id !== area.id);
          dirty = true; renderAdmin(); renderChoices(); invalidatePlan();
          el('matrix-specific').children[matrix.specific.indexOf(process)].querySelector('.matrix-process-areas').open = true;
        });
        const labels = row.querySelectorAll('.matrix-fields label');
        labels[0].textContent = 'Nombre del área';
        labels[1].textContent = 'Siglas del área';
        labels[2].textContent = 'Directorio del área';
        list.append(row);
      });
      areas.append(list);
      areas.append(button('Agregar área', () => {
        process.areas.push({id: newId(), name: '', abbreviation: ''});
        dirty = true; renderAdmin(); renderChoices(); invalidatePlan();
        el('matrix-specific').children[matrix.specific.indexOf(process)].querySelector('.matrix-process-areas').open = true;
      }));
      group.append(areas);
      el('matrix-specific').append(group);
    });
  }

  function options(select, items, selected) {
    select.replaceChildren();
    const empty = node('option', 'Selecciona...'); empty.value = ''; select.append(empty);
    items.forEach(item => {
      const option = node('option', `${item.name || 'Sin nombre'}${item.abbreviation !== undefined ? ' · ' + (item.abbreviation || 'sin siglas') : ''}`);
      option.value = item.id; select.append(option);
    });
    if (items.some(item => item.id === selected)) select.value = selected;
  }
  function isManuals() { return el('matrix-scope').value === 'manuals'; }
  function renderChoices() {
    const scope = el('matrix-scope').value;
    el('matrix-branch').closest('.form-group').hidden = isManuals();
    el('matrix-branch').required = !isManuals();
    el('matrix-branch').disabled = isManuals();
    el('matrix-area-field').hidden = scope !== 'specific';
    el('matrix-area').required = scope === 'specific';
    el('matrix-branch-label').textContent = scope === 'specific' ? 'Procedimiento específico / proceso' : 'Procedimiento de Gobierno';
    if (!operationMatrix) {
      options(el('matrix-branch'), [], '');
      options(el('matrix-area'), [], '');
      refreshAction();
      return;
    }
    options(el('matrix-branch'), operationMatrix[scope] || [], el('matrix-branch').value);
    options(el('matrix-type'), isManuals() ? [{id:'manual',name:'Manual del Sistema de Gestión'}] : operationMatrix.types.filter(type => type.store_file), isManuals() ? 'manual' : el('matrix-type').value);
    const process = operationMatrix.specific.find(item => item.id === el('matrix-branch').value);
    options(el('matrix-area'), process ? process.areas : [], el('matrix-area').value);
    refreshAction();
  }
  function isPolicy() {
    return el('input-is-politica-p1').checked && !isWebForm();
  }
  function isWebForm() {
    return document.querySelector('input[name="input-embed-type-p1"]:checked').value === 'form';
  }
  function refreshAction() {
    const isForm = el('matrix-type').value === 'formulario' || (!el('matrix-type').value && document.querySelector('input[name="input-embed-type-p1"]:checked').value === 'form');
    if (isForm && el('matrix-action').value === 'version') el('matrix-action').value = 'cambio';
    if (!isForm && el('matrix-action').value === 'cambio') el('matrix-action').value = 'version';
    el('matrix-action').disabled = false;
    el('document-action-label').textContent = isForm ? 'Acción del formulario' : 'Acción documental';
    if (isManuals()) el('input-is-politica-p1').checked = false;
    const policy = isPolicy();
    el('campo-codigo-p1').hidden = policy;
    el('input-codigo-p1').required = !policy;
    el('matrix-detect-code').hidden = isForm || policy;
    el('matrix-type').closest('.form-group').hidden = policy || isManuals();
    el('matrix-type').required = !policy && !isManuals();
    el('matrix-type').disabled = policy || isManuals();
    if (policy) {
      el('classification-fields').hidden = false;
      el('classification-fields').disabled = false;
      el('classification-summary').textContent = '';
    } else if (!manualClassification) {
      el('classification-fields').hidden = true;
      el('classification-fields').disabled = !el('matrix-branch').value && !isManuals();
    }
    document.querySelector('label[for="input-codigo-p1"]').textContent = isForm ? 'Código del formulario' : isManuals() ? 'Código del manual' : 'Código del documento';
    document.querySelector('label[for="input-nombre-p1"]').textContent = isForm ? 'Nombre del formulario' : isManuals() ? 'Nombre del manual' : policy ? 'Nombre de la política' : 'Nombre del documento';
    el('document-routing').hidden = isForm;
    el('classification-summary').hidden = isForm || policy;
    if (isForm) {
      el('classification-fields').hidden = true;
      el('classification-fields').disabled = true;
      el('matrix-result').hidden = true;
      message('matrix-plan-error', '');
    }
    document.querySelectorAll('input[name="document-action"]').forEach(radio => {
      radio.checked = radio.value === el('matrix-action').value;
      const unavailable = isForm ? radio.value === 'version' : radio.value === 'cambio';
      radio.disabled = unavailable;
      radio.closest('.action-choice').hidden = unavailable;
    });
    document.querySelector('.document-actions').classList.toggle('form-actions', isForm);
    const baja = el('matrix-action').value === 'baja';
    el('matrix-file-field').hidden = isForm;
    el('matrix-file').required = !isForm;
    el('matrix-document-name-field').hidden = false;
    el('matrix-document-name').placeholder = 'Nombre del documento o formulario';
    el('matrix-document-name').required = true;
    el('campo-enlace-p1').hidden = !isForm;
    el('input-enlace-p1').required = isForm;
    el('input-folio-p1').required = true;
    el('input-folio-p1').closest('.form-group').hidden = false;
    el('campo-politica-p1').hidden = isForm || isManuals();
    const resource = document.querySelector(`input[name="input-embed-type-p1"][value="${isForm ? 'form' : 'document'}"]`);
    if (resource && !resource.checked) {resource.checked = true; resource.dispatchEvent(new Event('change', {bubbles:true}));}
    el('submit-btn-p1').textContent = isForm ? (baja ? 'Publicar registro de baja del formulario' : 'Crear y publicar formulario') : baja ? 'Dar de baja y publicar registro' : 'Subir y publicar documento';
    el('matrix-operation-note').hidden = true;
    el('matrix-operation-note').textContent = '';
  }
  function showClassificationFields(show) {
    if (isWebForm()) show = false;
    if (isPolicy()) show = true;
    manualClassification = show;
    el('classification-fields').hidden = !show;
    el('classification-fields').disabled = !show && !el('matrix-branch').value && !isManuals();
    el('matrix-detect-code').querySelector('span').textContent = (el('matrix-branch').value || isManuals()) ? 'Modificar clasificación' : 'Clasificar manualmente';
  }
  async function detectClassification() {
    if (isPolicy() || isWebForm() || !operationMatrix || manualClassification) return;
    const code = el('matrix-existing-code').value.trim();
    const revision = ++detectionRevision;
    if (!code) {showClassificationFields(false);el('classification-summary').textContent = 'Introduce el código o clasifica manualmente para crear uno nuevo.';return;}
    el('classification-summary').textContent = 'Detectando clasificación…';
    try {
      const data = await api('/api/document-code', {method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({site:operationDomain,code})});
      if (revision !== detectionRevision || manualClassification) return;
      const isFile = document.querySelector('input[name="input-embed-type-p1"]:checked').value === 'document';
      const candidates = data.matches.filter(item => item.store_file === isFile);
      if (!candidates.length) throw new Error('El código no corresponde al tipo de recurso seleccionado.');
      if (candidates.length !== 1) throw new Error('El código admite varias clasificaciones. Selecciona manualmente la que corresponde.');
      const match = candidates[0];
      el('matrix-scope').value = match.scope; renderChoices();
      el('matrix-branch').value = match.branch_id; renderChoices();
      if (match.area_id) el('matrix-area').value = match.area_id;
      el('matrix-number').value = match.number;
      el('matrix-type').value = match.type_id;
      refreshAction(); showClassificationFields(false);
      const branch = (operationMatrix[match.scope] || []).find(item => item.id === match.branch_id);
      const area = match.area_id ? branch.areas.find(item => item.id === match.area_id) : null;
      el('classification-summary').textContent = match.scope === 'manuals' ? 'Manuales del Sistema de Gestión' : [match.scope === 'government' ? 'Gobierno' : 'Específicos',branch.name,...(area ? [area.name] : []),match.type_name].join(' → ');
      message('matrix-plan-error', '');
      invalidatePlan();
    } catch(error) {
      if (revision !== detectionRevision || manualClassification) return;
      options(el('matrix-branch'), operationMatrix[el('matrix-scope').value] || [], '');
      el('matrix-type').value = '';el('matrix-number').value = '';
      showClassificationFields(true); refreshAction();invalidatePlan();
      el('classification-summary').textContent = error.message + ' Puedes clasificar el documento manualmente.';
    }
  }
  async function loadOperation(selected) {
    operationDomain = selected; operationMatrix = null; detectionRevision++; manualClassification = false; showClassificationFields(false);
    const request = ++operationLoad;
    options(el('matrix-branch'), [], ''); options(el('matrix-type'), [], '');
    invalidatePlan();
    if (!selected || isWebForm()) {refreshAction(); return;}
    try {
      const data = await api(`/api/matrices/${encodeURIComponent(selected)}`);
      if (request !== operationLoad) return;
      operationMatrix = data; renderChoices(); invalidatePlan(); detectClassification();

    } catch(error) {if (request === operationLoad) message('matrix-plan-error', error.message, true);}
  }
  function operationMetadata() {
    if (!operationMatrix || operationDomain !== el('site-select-p1').value) throw new Error('Selecciona una empresa y espera que cargue su matriz.');
    return {site:operationDomain,action:el('matrix-action').value,scope:el('matrix-scope').value,
      branch_id:el('matrix-branch').value,area_id:el('matrix-area').value,type_id:isManuals() ? 'manual' : isPolicy() ? 'politica' : el('matrix-type').value,is_politica:isPolicy(),
      code:isPolicy() ? '' : el('input-codigo-p1').value.trim(),number:el('matrix-number').value.trim(),name:el('matrix-document-name').value.trim(),filename:el('matrix-file').files[0]?.name || ''};
  }
  async function updatePreview() {
    if (isWebForm() || !operationMatrix) return;
    const data = operationMetadata();
    if ((data.scope !== 'manuals' && !data.branch_id) || !data.type_id || (!data.is_politica && !data.code) || (data.scope === 'specific' && !data.area_id)) return;
    if (data.type_id !== 'formulario' && (!data.name || !data.filename)) {
      return;
    }
    const revision = planRevision;
    try {
      const result = await api('/api/document-plan', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
      if (revision !== planRevision) return;
      lastPlan = result.plan; renderResult(lastPlan);
    } catch(error) {if (revision === planRevision) message('matrix-plan-error', error.message, true);}
  }
  window.documentOperation = {
    metadata: () => {
      const data = operationMetadata();
      if ((data.scope !== 'manuals' && !data.branch_id) || !data.type_id || (!data.is_politica && !data.code) || (data.scope === 'specific' && !data.area_id)) {
        showClassificationFields(true);
        throw new Error('Completa la clasificación del documento antes de continuar.');
      }
      return data;
    },
    requestId: () => {
      const file = el('matrix-file').files[0];
      const fingerprint = JSON.stringify({...operationMetadata(),size:file?.size,lastModified:file?.lastModified});
      if (!operationId || operationId.fingerprint !== fingerprint) operationId = {fingerprint,id:crypto.randomUUID()};
      return operationId.id;
    }
  };
  async function load(domainValue) {
    domain = domainValue;
    const requestId = ++pendingLoad;
    matrix = null; invalidatePlan();
    el('matrix-workspace').hidden = true;
    el('destination-workspace').hidden = true;
    if (!domain) {
      message('matrix-alert', 'Selecciona o crea una empresa para configurar su matriz.');
      message('destination-alert', 'Selecciona o crea una empresa para configurar su almacenamiento.');
      return;
    }
    message('matrix-alert', 'Cargando matriz…');
    try {
      const data = await api(`/api/matrices/${encodeURIComponent(domain)}`);
      if (requestId !== pendingLoad) return;
      matrix = data; dirty = false;
      renderAdmin(); renderChoices();
      el('matrix-workspace').hidden = false; el('destination-workspace').hidden = false;
      message('matrix-alert', '');
      message('destination-alert', '');
    } catch (error) {if (requestId === pendingLoad) {message('matrix-alert', error.message, true);message('destination-alert', error.message, true);}}
  }
  function renderStorageFields() {
    const provider = el('matrix-provider').value;
    el('matrix-destination-fields').hidden = !provider;
    el('matrix-region-group').hidden = provider !== 's3';
    el('matrix-target-label').textContent = provider === 'azure' ? 'Contenedor' : 'Bucket';
    el('matrix-list-targets').hidden = !provider;
    el('matrix-list-targets-label').textContent = provider === 'azure' ? 'Consultar contenedores' : 'Consultar buckets';
  }
  function collectStorage() {
    matrix.company = el('matrix-name').value.trim();
    matrix.manual_prefix = el('matrix-manual-prefix').value.trim().toUpperCase();
    matrix.manual_directory = el('matrix-manual-directory').value.trim();
    matrix.government_directory = el('matrix-government-directory').value.trim();
    matrix.specific_directory = el('matrix-specific-directory').value.trim();
    matrix.omit_scope_directory = el('matrix-omit-scope').checked;
    matrix.storage = {provider: el('matrix-provider').value, target: el('matrix-target').value.trim(), root: el('matrix-root').value.trim(),
      region: el('matrix-region').value.trim(), account: matrix.storage.account || ''};
    renderStorageFields();
  }
  function renderResult(plan) {
    const result = el('matrix-result-fields'); result.replaceChildren();
    const icon = name => {
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('class', 'ui-icon'); svg.setAttribute('aria-hidden', 'true');
      const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
      use.setAttribute('href', `#icon-${name}`); svg.append(use); return svg;
    };
    const badge = el('matrix-result-action');
    badge.textContent = {alta:'Alta', version:'Cambio', cambio:'Cambio', baja:'Baja'}[plan.action] || 'Alta';
    badge.dataset.action = plan.action;
    const field = (label, value, className = '') => {
      const item = node('div', undefined, `plan-summary-field ${className}`);
      const content = node('dd', value);
      item.append(node('dt', label), content); result.append(item); return content;
    };
    field('Código', plan.is_politica ? 'No aplica (política)' : plan.code, 'plan-code');
    field('Fecha del archivo', !plan.store_file || (plan.action === 'baja' && !plan.filename && !plan.filename_template)
      ? 'No aplica' : (plan.version || 'Se confirma al subir'));
    const provider = plan.store_file ? plan.storage.provider : 'wordpress';
    const service = field('Servicio', {s3:'Amazon S3', azure:'Azure Blob Storage', wordpress:'WordPress'}[provider] || 'Pendiente', 'plan-service');
    service.prepend(icon(['s3','azure'].includes(provider) ? provider : 'database'));
    field(provider === 'azure' ? 'Contenedor' : provider === 's3' ? 'Bucket' : 'Bucket / contenedor',
      plan.store_file ? (plan.storage.target || 'Pendiente') : 'No aplica');
    const directory = plan.directory || (plan.store_file ? plan.classification_directory : 'No se almacena');
    const path = field('Directorio', undefined, 'plan-wide plan-directory');
    const crumbs = node('div', undefined, 'plan-path');
    crumbs.title = String(directory || 'Pendiente');
    String(directory || 'Pendiente').split('/').forEach((segment, index) => {
      if (index) {const separator = node('span', '/', 'plan-path-separator'); separator.setAttribute('aria-hidden', 'true'); crumbs.append(separator);}
      crumbs.append(node('span', segment, 'plan-path-segment'));
    });
    path.append(crumbs);
    const file = field('Archivo', undefined, 'plan-wide plan-file');
    file.append(icon('form'), node('span', plan.filename || plan.filename_template || 'No se sube un archivo'));
    if (plan.store_file && plan.inventory_verified) {
      field('Versiones a BAJA', String(plan.archive_operations.length), 'plan-wide plan-archive');
    }
    el('matrix-result').hidden = false;
  }
  document.addEventListener('idpn:company-selected', event => {
    const selected = event.detail.domain || '';
    if (selected !== domain || !matrix) load(selected);
  });
  document.addEventListener('DOMContentLoaded', () => {
    function showCredentialStatus(settings) {
      el('storage-azure-account').value = settings.azure.account || '';
      el('storage-s3-status').textContent = settings.s3.configured ? 'Credenciales guardadas' : 'Sin configurar';
      el('storage-azure-status').textContent = settings.azure.configured ? 'Credenciales guardadas' : 'Sin configurar';
      ['s3','azure'].forEach(provider => el(`storage-${provider}-status`).classList.toggle('configured', settings[provider].configured));
      for (const [id, saved] of [
        ['storage-s3-key', settings.s3.access_key_configured],
        ['storage-s3-secret', settings.s3.secret_key_configured],
        ['storage-s3-session', settings.s3.session_token_configured],
        ['storage-azure-key', settings.azure.account_key_configured]
      ]) {
        el(id).placeholder = saved ? '••••••••••••' : '';
        el(id).title = saved ? 'Clave guardada. Escribe para reemplazarla.' : '';
      }

    }
    if (!window.idpnUser || window.idpnUser.admin) api('/api/storage-settings').then(showCredentialStatus).catch(error => message('storage-alert', error.message, true));
    el('storage-credentials-form').addEventListener('submit', async event => {
      event.preventDefault();
      el('storage-credentials-save').disabled = true;
      try {
        const settings = await api('/api/storage-settings', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
          s3:{access_key_id:el('storage-s3-key').value,secret_access_key:el('storage-s3-secret').value,session_token:el('storage-s3-session').value,clear_session_token:el('storage-s3-clear-session').checked},
          azure:{account:el('storage-azure-account').value,account_key:el('storage-azure-key').value}
        })});
        ['storage-s3-key','storage-s3-secret','storage-s3-session','storage-azure-key'].forEach(id => el(id).value = '');
        el('storage-s3-clear-session').checked = false;
        showCredentialStatus(settings);
        message('storage-alert','Credenciales guardadas. Selecciona el bucket o contenedor en Almacenamiento de cada empresa.');
        invalidatePlan();
      } catch(error) {message('storage-alert',error.message,true);}
      finally {el('storage-credentials-save').disabled = false;}
    });
    el('matrix-add-government').addEventListener('click', () => {
      matrix.government.push({id: newId(), name: '', abbreviation: ''});
      dirty = true;
      renderAdmin(); renderChoices(); invalidatePlan();
    });
    el('matrix-add-process').addEventListener('click', () => {
      matrix.specific.push({id: newId(), name: '', abbreviation: '', areas: []});
      dirty = true;
      renderAdmin(); renderChoices(); invalidatePlan();
      el('matrix-specific').lastChild.querySelector('.matrix-process-areas').open = true;
    });
    ['matrix-scope', 'matrix-branch', 'matrix-type'].forEach(id => el(id).addEventListener('change', () => {renderChoices(); invalidatePlan();}));
    document.querySelectorAll('input[name="document-action"]').forEach(radio => radio.addEventListener('change', () => {
      if (radio.checked) {
        el('matrix-action').value = radio.value;
        el('matrix-action').dispatchEvent(new Event('change', {bubbles:true}));
      }
    }));
    el('matrix-action').addEventListener('change', () => {refreshAction(); invalidatePlan();});
    el('page-form-p1').addEventListener('input', invalidatePlan);
    el('site-select-p1').addEventListener('change', () => loadOperation(el('site-select-p1').value));
    document.querySelectorAll('input[name="input-embed-type-p1"]').forEach(radio => radio.addEventListener('change', () => {
      el('matrix-type').value = radio.value === 'form' ? 'formulario' : (el('matrix-type').value === 'formulario' ? '' : el('matrix-type').value);
      refreshAction(); invalidatePlan();
      if (radio.value === 'document' && !operationMatrix) loadOperation(el('site-select-p1').value);
      if (operationMatrix && !manualClassification) {clearTimeout(detectionTimer);detectionTimer=setTimeout(detectClassification,100);}
    }));
    el('input-is-politica-p1').addEventListener('change', () => {
      detectionRevision++; clearTimeout(detectionTimer);
      manualClassification = isPolicy();
      refreshAction(); invalidatePlan();
      if (!isPolicy()) detectClassification();
    });
    el('matrix-detect-code').addEventListener('click', () => {
      detectionRevision++; clearTimeout(detectionTimer);
      showClassificationFields(true);
      el('classification-summary').textContent = 'Puedes modificar la clasificación y cada campo manualmente.';
    });
    el('matrix-document-name').addEventListener('blur', () => {
      el('matrix-document-name').value = normalizeDocumentName(el('matrix-document-name').value);
      invalidatePlan();
    });
    el('matrix-existing-code').addEventListener('input', () => {
      const input = el('matrix-existing-code');
      const start = input.selectionStart, end = input.selectionEnd;
      input.value = input.value.toUpperCase();
      input.setSelectionRange(start, end);
      detectionRevision++; clearTimeout(detectionTimer);
      manualClassification = false;
      el('matrix-branch').value = '';el('matrix-type').value = '';el('matrix-number').value = '';
      el('classification-fields').disabled = true;
      detectionTimer = setTimeout(detectClassification, 450);
    });
    ['matrix-name', 'matrix-manual-prefix', 'matrix-manual-directory', 'matrix-government-directory', 'matrix-specific-directory', 'matrix-provider', 'matrix-target', 'matrix-root', 'matrix-region'].forEach(id => el(id).addEventListener('input', () => {collectStorage(); dirty = true; invalidatePlan();}));
    el('matrix-omit-scope').addEventListener('change', () => {collectStorage(); dirty = true; invalidatePlan();});
    el('matrix-list-targets').addEventListener('click', async () => {
      collectStorage();
      el('matrix-list-targets').disabled = true;
      try {
        const result = await api('/api/storage-targets', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({storage:matrix.storage})});
        el('matrix-targets').replaceChildren();
        result.targets.forEach(target => {const option=node('option',target.name);option.value=target.name;el('matrix-targets').append(option);});
        message('destination-alert', `${result.targets.length} destinos disponibles. Selecciona uno en Bucket / contenedor y confirma su región si usas S3.`);
      } catch(error) {message('destination-alert',error.message,true);}
      finally {el('matrix-list-targets').disabled = false;}
    });
    async function saveCompanyMatrix(event, storageOnly) {
      event.preventDefault(); collectStorage();
      const savingDomain = domain;
      const saveButton = el(storageOnly ? 'destination-save' : 'matrix-save');
      const alert = storageOnly ? 'destination-alert' : 'matrix-alert';
      saveButton.disabled = true;
      try {
        const result = await api(`/api/matrices/${encodeURIComponent(domain)}`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(matrix)});
        if (domain !== savingDomain) return;
        matrix = result.matrix; dirty = false; renderAdmin(); if (operationDomain === domain) {operationMatrix = structuredClone(matrix);renderChoices();} invalidatePlan();
        message(alert, storageOnly ? 'Almacenamiento guardado.' : 'Matriz guardada. Los cambios de directorio se aplicarán a documentos futuros; no mueven archivos existentes.');
      } catch (error) {if (domain === savingDomain) message(alert, error.message, true);}
      finally {saveButton.disabled = false;}
    }
    el('matrix-admin-form').addEventListener('submit', event => saveCompanyMatrix(event, false));
    el('company-destination-form').addEventListener('submit', event => saveCompanyMatrix(event, true));
  });
})();
