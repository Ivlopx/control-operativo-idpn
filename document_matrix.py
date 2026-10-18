"""Company-specific document classification; independent of storage providers."""
from copy import deepcopy
import re
import unicodedata
from pathlib import PurePosixPath
from document_stamp import STAMP_SUFFIX, next_stamp, publication_day, stamp_from_filename, stamp_sort_key


TYPES = [
    {'id': 'procedimiento', 'name': 'Procedimientos', 'government_prefix': 'PG-D', 'specific_prefix': 'PE', 'store_file': True},
    {'id': 'instructivo', 'name': 'Instructivos', 'government_prefix': 'IG-D', 'specific_prefix': 'IE', 'store_file': True},
    {'id': 'formato', 'name': 'Formatos', 'government_prefix': 'FG-D', 'specific_prefix': 'FE', 'store_file': True},
    {'id': 'formulario', 'name': 'Formularios', 'government_prefix': 'FG-D', 'specific_prefix': 'FE', 'store_file': False},
]
ALLOWED_EXTENSIONS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'}


def slug(value):
    value = unicodedata.normalize('NFKD', str(value))
    value = value.encode('ascii', 'ignore').decode().lower()
    value = re.sub(r'[^a-z0-9]+', '-', value).strip('-')
    if not value:
        raise ValueError('El nombre debe contener letras o números.')
    return value


def directory_name(value):
    """Normalize directory labels, including existing camel case and slugs."""
    value = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode()
    value = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', value)
    value = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', value)
    words = re.findall(r'[a-zA-Z0-9]+', value)
    if not words:
        raise ValueError('El nombre debe contener letras o números.')
    return words[0].lower() + ''.join(word.lower().capitalize() for word in words[1:])


def default_matrix(company, zhipos=False):
    matrix = {'schema_version': 1, 'company': company, 'omit_scope_directory': False, 'manual_prefix': 'MG', 'manual_directory': 'manualesDelSistemaDeGestion',
              'government_directory': 'procedimientosDeGobierno', 'specific_directory': 'procedimientosEspecificos',
              'storage': {'provider': '', 'target': '', 'root': directory_name(company)},
              'types': deepcopy(TYPES), 'government': [], 'specific': []}
    if zhipos:
        for name, abbreviation in [
            ('Control de Documentos', 'CON'), ('Control de Registros', ''),
            ('Control de No Conformes', 'CNO'), ('Acciones Correctivas', ''),
            ('Gestión de Riesgos', ''), ('Auditoría Interna', ''),
            ('Infraestructura', ''), ('Mejora Continua', ''), ('Revisión por la Dirección', '')
        ]:
            matrix['government'].append({'id': slug(name), 'name': name, 'abbreviation': abbreviation})
        for name, abbreviation, areas in [
            ('Comercial', 'C', ['Administración de Ventas', 'Atención de Cliente']),
            ('Operaciones', 'O', ['Producción', 'Mantenimiento', 'Logística y Distribución', 'Almacén']),
            ('Administración', 'A', ['Contabilidad', 'Recursos Humanos', 'Tesorería', 'Compras']),
            ('Ingeniería', 'I', ['Desarrollo de Productos Nuevos', 'Ingeniería de Calidad'])
        ]:
            matrix['specific'].append({'id': slug(name), 'name': name, 'abbreviation': abbreviation,
                'areas': [{'id': slug(area), 'name': area, 'abbreviation': 'RHS' if area == 'Recursos Humanos' else ''} for area in areas]})
    return matrix


def _segment(value):
    value = str(value).strip()
    if '/' in value or '\\' in value or value in ('.', '..'):
        raise ValueError('Cada directorio debe ser un nombre, sin separadores ni rutas relativas.')
    return directory_name(value)


def _storage(value):
    if not isinstance(value, dict):
        raise ValueError('Configuración de almacenamiento inválida.')
    provider = value.get('provider', '')
    if provider not in ('', 's3', 'azure'):
        raise ValueError('El almacenamiento debe ser S3 o Azure.')
    target = str(value.get('target', '')).strip()
    if target and not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', target):
        raise ValueError('El bucket/contenedor debe tener un nombre válido de 3 a 63 caracteres.')
    if provider == 'azure' and target and not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', target):
        raise ValueError('Un contenedor de Azure admite letras minúsculas, números y guiones simples.')
    root = str(value.get('root', '')).strip('/')
    if root:
        root = '/'.join(_segment(part) for part in root.split('/'))
    region = str(value.get('region', 'us-east-1')).strip()
    account = str(value.get('account', '')).strip().lower()
    if region and not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d+', region):
        raise ValueError('Región de S3 inválida.')
    if account and not re.fullmatch(r'[a-z0-9]{3,24}', account):
        raise ValueError('La cuenta de Azure debe tener de 3 a 24 letras minúsculas o números.')
    return {'provider': provider, 'target': target, 'root': root, 'region': region, 'account': account}


def _abbreviation(value, prefix=False):
    value = str(value).strip().upper()
    pattern = r'[A-Z0-9]+(?:-[A-Z0-9]+)*' if prefix else r'[A-Z0-9]+'
    if value and not re.fullmatch(pattern, value):
        raise ValueError('Las siglas deben contener letras y números; los prefijos pueden incluir guiones.')
    return value


def validate_matrix(payload):
    if not isinstance(payload, dict) or payload.get('schema_version') != 1:
        raise ValueError('Versión de matriz no compatible.')
    omit_scope = payload.get('omit_scope_directory', False)
    if type(omit_scope) is not bool:
        raise ValueError('La opción de omitir Gobierno y Específicos debe ser booleana.')
    result = {'schema_version': 1, 'company': str(payload.get('company', '')).strip(),
              'omit_scope_directory': omit_scope,
              'manual_prefix': _abbreviation(payload.get('manual_prefix', 'MG'), prefix=True),
              'manual_directory': _segment(payload.get('manual_directory') or 'Manuales del Sistema de Gestión'),
              'government_directory': _segment(payload.get('government_directory') or 'Procedimientos de Gobierno'),
              'specific_directory': _segment(payload.get('specific_directory') or 'Procedimientos Específicos'),
              'storage': _storage(payload.get('storage', {})), 'types': [], 'government': [], 'specific': []}
    if not result['manual_prefix']:
        raise ValueError('El prefijo de manuales es obligatorio.')
    if not result['company']:
        raise ValueError('El nombre de la empresa es obligatorio.')
    types = payload.get('types', [])
    if not isinstance(types, list) or {t.get('id') for t in types if isinstance(t, dict)} != {t['id'] for t in TYPES} or len(types) != 4:
        raise ValueError('Se requieren los cuatro tipos: Procedimientos, Instructivos, Formatos y Formularios.')
    for item in types:
        entry = {'id': item['id'], 'name': str(item.get('name', '')).strip(),
                 'government_prefix': _abbreviation(item.get('government_prefix', ''), prefix=True),
                 'specific_prefix': _abbreviation(item.get('specific_prefix', ''), prefix=True),
                 'store_file': item['id'] != 'formulario'}
        if not entry['name'] or not entry['government_prefix'] or not entry['specific_prefix']:
            raise ValueError('Cada tipo necesita nombre y prefijos.')
        entry['directory'] = _segment(item.get('directory') or entry['name'])
        result['types'].append(entry)
    for prefix_key in ('government_prefix', 'specific_prefix'):
        prefixes = [t[prefix_key] for t in result['types'] if t['store_file']]
        if len(set(prefixes)) != len(prefixes):
            raise ValueError('Los tipos que almacenan archivos deben tener prefijos distintos.')

    def entries(items, with_areas=False):
        if not isinstance(items, list):
            raise ValueError('La lista de procedimientos, procesos o áreas es inválida.')
        cleaned, ids, siglas, paths = [], set(), set(), set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('Entrada de matriz inválida.')
            name = str(item.get('name', '')).strip()
            ident = str(item.get('id', '')).strip()
            if not name or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', ident):
                raise ValueError('Cada entrada necesita nombre e identificador válido.')
            abbreviation = _abbreviation(item.get('abbreviation', ''))
            directory = _segment(item.get('directory') or name)
            if ident in ids or directory in paths or (abbreviation and abbreviation in siglas):
                raise ValueError('Hay identificadores, directorios o siglas repetidos dentro de la misma sección.')
            ids.add(ident)
            paths.add(directory)
            if abbreviation:
                siglas.add(abbreviation)
            entry = {'id': ident, 'name': name, 'abbreviation': abbreviation, 'directory': directory}
            if item.get('storage'):
                entry['storage'] = _storage(item['storage'])
            if with_areas:
                entry['areas'] = entries(item.get('areas', []))
            cleaned.append(entry)
        return cleaned

    result['government'] = entries(payload.get('government', []))
    result['specific'] = entries(payload.get('specific', []), with_areas=True)
    return result


def plan_document(matrix, data, inventory=None):
    matrix = validate_matrix(matrix)
    action = data.get('action', 'alta')
    if action not in ('alta', 'version', 'baja'):
        raise ValueError('Acción documental inválida.')
    scope = data.get('scope')
    if scope not in ('government', 'specific', 'manuals'):
        raise ValueError('Selecciona Gobierno, Específicos o Manuales del Sistema de Gestión.')

    def selected(items, key, label):
        item = next((item for item in items if item['id'] == data.get(key)), None)
        if item is None:
            raise ValueError(f'Selecciona {label} de la matriz de esta empresa.')
        return item

    policy = data.get('is_politica') is True
    if scope == 'manuals' and policy:
        raise ValueError('Los manuales se registran como documentos, sin la opción de política.')
    policy_name = str(data.get('name', '')).strip()
    if policy and not policy_name:
        raise ValueError('El nombre de la política es obligatorio para identificar sus versiones.')
    doc_type = ({'id':'manual', 'store_file':True} if scope == 'manuals' else
                {'id':'politica', 'directory':'politicas', 'store_file':True} if policy else selected(matrix['types'], 'type_id', 'el tipo de documento'))
    branch = {'id':None, 'abbreviation':''} if scope == 'manuals' else selected(matrix[scope], 'branch_id', 'el procedimiento o proceso')
    area = selected(branch['areas'], 'area_id', 'el área') if scope == 'specific' else None
    supplied_code = str(data.get('code', '')).strip().upper()
    if policy:
        code = ''
    elif supplied_code:
        if len(supplied_code) > 160 or not re.fullmatch(r'[A-Z0-9]+(?:-[A-Z0-9]+)*-[0-9]{1,10}', supplied_code, re.I):
            raise ValueError('Introduce un código válido terminado en el número del documento, por ejemplo FE-A-RHS-003.')
        code = supplied_code
    else:
        # Compatibility for existing integrations; the primary UI always supplies its user-entered code.
        for item in ([] if scope == 'manuals' else [branch] + ([area] if area else [])):
            if not item['abbreviation']:
                raise ValueError(f'El administrador debe asignar siglas a «{item["name"]}».')
        number = str(data.get('number', '')).strip()
        if not re.fullmatch(r'[0-9]{1,10}', number):
            raise ValueError('El número de documento debe contener de 1 a 10 dígitos. Se conservan los ceros iniciales.')
        code = (matrix['manual_prefix'] + '-' + number) if scope == 'manuals' else '-'.join([doc_type['government_prefix' if scope == 'government' else 'specific_prefix'],
                         branch['abbreviation']] + ([area['abbreviation']] if area else []) + [number])
    storage = deepcopy(matrix['storage'])
    for item in [branch] + ([area] if area else []):
        if item.get('storage'):
            storage.update(item['storage'])
    if scope == 'manuals':
        folders = [matrix['manual_directory']]
    else:
        folders = [] if matrix['omit_scope_directory'] else [matrix['government_directory' if scope == 'government' else 'specific_directory']]
        folders.append(branch['directory'])
        if area:
            folders.append(area['directory'])
        folders.append(doc_type['directory'])
    directory = '/'.join(([storage['root']] if storage['root'] else []) + folders)
    result = {'company': matrix['company'], 'action': action, 'scope': scope,
              'type_id': doc_type['id'], 'branch_id': branch['id'], 'area_id': area['id'] if area else None,
              'code': code, 'is_politica': policy, 'name': policy_name, 'store_file': doc_type['store_file'], 'storage': storage,
              'storage_ready': bool(storage['provider'] and storage['target'] and (storage['region'] if storage['provider'] == 's3' else storage['account'])),
              'classification_directory': directory, 'directory': None,
              'filename': None, 'active_key': None, 'archive_key': None, 'key': None,
              'inventory_verified': inventory is not None}
    # Formularios have classification and code, but never a file or storage operation.
    if not doc_type['store_file']:
        return result
    # Inventory is supplied by the server/storage connector, never by the form.
    versions = []
    if inventory is not None:
        if not isinstance(inventory, list):
            raise ValueError('Inventario de almacenamiento inválido.')
        identity = re.escape(slug(policy_name)) if policy else re.escape(code) + r'-.+'
        pattern = re.compile(r'^' + identity + r'-v([0-9]{3,})\.(?:pdf|docx?|xlsx?|pptx?)$', re.I)
        legacy_pattern = re.compile(r'^' + identity + (STAMP_SUFFIX if policy else '') + r'\.(?:pdf|docx?|xlsx?|pptx?)$', re.I)
        for item in inventory:
            if not isinstance(item, dict) or not isinstance(item.get('key'), str):
                raise ValueError('El inventario debe contener objetos con key.')
            key = item['key']
            if not key.startswith(directory + '/'):
                continue
            relative = key[len(directory) + 1:]
            if '/' in relative and not (relative.startswith('baja/') and '/' not in relative[5:]):
                continue
            basename = PurePosixPath(relative).name
            match = pattern.fullmatch(basename)
            if match or legacy_pattern.fullmatch(basename):
                versions.append({'key': key, 'version': stamp_from_filename(basename) or 0, 'legacy': stamp_from_filename(basename) is None, 'archived': relative.startswith('baja/'),
                                 'etag': item.get('etag')})
    active = [item for item in versions if not item['archived']]
    archive_operations = [{'source_key': item['key'], 'destination_key': f'{directory}/baja/{PurePosixPath(item["key"]).name}',
                           'source_etag': item['etag']} for item in active]
    result.update({'stored_versions': sorted(versions, key=lambda item: stamp_sort_key(item['key'])),
                   'archive_operations': archive_operations if action in ('version', 'baja') else [],
                   'overwrite': False, 'preserve_previous_versions': True,
                   'archive_after_upload': action == 'version', 'archive_after_second_process': action == 'baja'})
    baja_upload = action == 'baja' and bool(data.get('filename'))
    if action == 'baja' and not baja_upload:
        if inventory is not None and not active:
            raise ValueError('No hay versiones activas de este documento para dar de baja.')
        result.update({'directory': f'{directory}/baja', 'version': None})
        return result
    if inventory is not None and versions and action == 'alta':
        raise ValueError('Este código ya tiene versiones guardadas. Usa «Cambio».')
    name = str(data.get('name', '')).strip()
    if not name:
        raise ValueError('El nombre del documento es obligatorio.')
    source_name = str(data.get('filename', ''))
    ext = PurePosixPath(source_name.replace('\\', '/')).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError('Selecciona un archivo PDF, Word, Excel o PowerPoint.')
    if inventory is None:
        result.update({'name': name, 'directory': directory + ('/baja' if action == 'baja' else ''), 'version': None,
                       'filename_template': f'{(code+"-") if code else ""}{slug(name)}-{publication_day()}{ext}'})
        return result
    version = next_stamp(versions)
    filename = f'{(code+"-") if code else ""}{slug(name)}-{version}{ext}'
    active_key = f'{directory}/{filename}'
    archive_key = f'{directory}/baja/{filename}'
    result.update({'name': name, 'version': version, 'filename': filename,
                   'active_key': active_key, 'archive_key': archive_key,
                   'directory': directory + ('/baja' if action == 'baja' else ''),
                   'key': archive_key if action == 'baja' else active_key,
                   'preserve_previous_versions': True, 'overwrite': False})
    return result


def resolve_document_code(matrix, code, type_id=None):
    """Resolve company-defined abbreviations; shared Formato/Formulario codes stay explicit."""
    matrix = validate_matrix(matrix)
    code = str(code).strip().upper()
    if not code or len(code) > 160:
        raise ValueError('Introduce un código de documento válido.')
    matches = []
    manual = re.fullmatch(re.escape(matrix['manual_prefix']) + r'-([0-9]{1,10})', code)
    if manual and (not type_id or type_id == 'manual'):
        matches.append({'scope':'manuals','branch_id':None,'area_id':None,'type_id':'manual',
                        'type_name':'Manual del Sistema de Gestión','number':manual.group(1), 'code':code,'store_file':True})
    for scope in ('government', 'specific'):
        for branch in matrix[scope]:
            if not branch['abbreviation']:
                continue
            areas = branch['areas'] if scope == 'specific' else [None]
            for area in areas:
                if area and not area['abbreviation']:
                    continue
                for doc_type in matrix['types']:
                    if type_id and doc_type['id'] != type_id:
                        continue
                    prefix = '-'.join([doc_type['government_prefix' if scope == 'government' else 'specific_prefix'],
                                       branch['abbreviation']] + ([area['abbreviation']] if area else []))
                    match = re.fullmatch(re.escape(prefix) + r'-([0-9]{1,10})', code)
                    if match:
                        matches.append({'scope': scope, 'branch_id': branch['id'], 'area_id': area['id'] if area else None,
                                        'type_id': doc_type['id'], 'type_name': doc_type['name'],
                                        'number': match.group(1), 'code': code, 'store_file': doc_type['store_file']})
    if not matches:
        raise ValueError('El código no coincide con las siglas de esta empresa. Revisa o completa su matriz.')
    return matches
