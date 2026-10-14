> Referencia histórica: la aplicación ahora usa almacenamiento propio en Python. Para configurar la versión actual, consulta [almacenamiento propio](almacenamiento-propio.md).

# Matriz documental y conexión con n8n

## Lo disponible en la aplicación

El apartado **Configuración de empresas → Matriz y almacenamiento** administra exclusivamente la estructura y los destinos por empresa. El **primer proceso** integra clasificación, detección de códigos, validación del código capturado y cálculo del directorio, selección del archivo y ejecución. No existe una pantalla separada de preparación. El código introducido se detecta automáticamente: si coincide con la matriz se muestra un resumen y se ocultan los campos; si no coincide se habilita la clasificación manual. El botón «Modificar clasificación» permite corregir los campos detectados.

Las matrices están en `config/matrices/<dominio>.json`. ZHIPOS contiene la
estructura proporcionada por el administrador; las otras empresas empiezan con
Gobierno, Específicos y los cuatro tipos, sin procesos inventados.

Solo se asignaron las siglas proporcionadas (`CON`, `CNO`, `RHS`) y las iniciales
de los procesos (`C`, `O`, `A`, `I`). El administrador puede editar todas ellas.
Las siglas pendientes impiden la detección automática de esa rama; se permite seleccionar su clasificación manualmente y conservar el código capturado.

Cada empresa tiene un servicio predeterminado (`s3` o `azure`), un bucket o
contenedor y una raíz. Los procedimientos de Gobierno, procesos y áreas pueden
tener un destino propio. La prioridad es **área → proceso/procedimiento → empresa**.
Es posible usar S3 en una rama y Azure en otra de la misma empresa.

Las carpetas se expresan mediante nombres normalizados (sin acentos, con guiones).
Los nombres visibles mantienen sus acentos. El campo **Directorio** permite
ajustar un segmento; después de guardarlo, cambiar el nombre visible no cambia
su directorio existente. Cambiar el directorio no migra archivos guardados.

```text
zhipos/
  procedimientos-de-gobierno/
    control-de-no-conformes/
      procedimientos/
        PG-D-CNO-003-control-de-no-conformes-v002.pdf
        BAJA/
          PG-D-CNO-003-control-de-no-conformes-v001.pdf
  procedimientos-especificos/
    administracion/
      recursos-humanos/
        formatos/
          FE-A-RHS-015-alta-de-personal-v001.xlsx
          BAJA/
```

Formularios generan un código, pero no un archivo, una versión ni operaciones de
almacenamiento. Formatos y Formularios pueden compartir prefijo; la aplicación
requiere seleccionar explícitamente el tipo cuando el código es ambiguo.

## Versiones automáticas

El código documental lo introduce el usuario. No se reconstruye al cambiar la clasificación y no existe un segundo campo de «código resultante». Solo se calculan el directorio, el nombre del archivo y la versión.

No hay campo de versión manual. La aplicación consulta el inventario de n8n y
calcula el mayor número encontrado para ese código más uno. Incluye `BAJA` y
conserva los ceros del número documental introducido por el usuario.

- **Alta:** si no hay ningún archivo del código, corresponde `v001`. Si ya existe
  algún archivo, se debe usar Nueva versión.
- **Nueva versión:** se prepara otro archivo, sin sobrescribir. Las versiones
  activas anteriores se trasladarán a `BAJA` después de confirmar la nueva subida.
- **Baja:** se sube el archivo de referencia directamente a `BAJA` y se trasladan las versiones activas al confirmar ese folio en el segundo proceso. Las versiones que ya están en `BAJA` se conservan.

Los archivos anteriores que ya incluyen `CÓDIGO-nombre` pero no tienen `vNNN` se
consideran antecedentes sin número: se conservarán en `BAJA` y la primera versión
numerada será `v001`. Archivos sin un código reconocible no se atribuyen a ningún
documento automáticamente; requieren una clasificación inicial.

El nombre normalizado tiene esta forma:

```text
CÓDIGO-nombre-del-documento-vNNN.extensión
```

Sin inventario verificado la aplicación muestra `vNNN`, no asigna un número ni
supone que el almacenamiento esté vacío. La vista previa no reserva una versión.

## API local

- `GET /api/matrices/<dominio>`: obtiene la matriz de la empresa.
- `PUT /api/matrices/<dominio>`: valida y guarda su matriz completa.
- `POST /api/document-code`: detecta una clasificación según la matriz.
- `POST /api/document-plan`: prepara código, destino, versión y operaciones de archivo.

Ejemplo de planificación:

```json
{
  "site": "zhipos-idpn.net",
  "action": "version",
  "scope": "specific",
  "branch_id": "administracion",
  "area_id": "recursos-humanos",
  "type_id": "formato",
  "code": "FE-A-RHS-015",
  "name": "Alta de personal",
  "filename": "archivo-original.xlsx"
}
```

El cliente no puede asignar la versión ni aportar un inventario de confianza.
La extensión procede del archivo original; se admiten PDF, DOC/DOCX, XLS/XLSX y
PPT/PPTX. Baja de documentos pide un archivo de referencia; Formularios pide únicamente un enlace.

## Un workflow compartido

El workflow importable está en `workflows/n8n/idpn-documentos.json`. Las
instrucciones de credenciales, operaciones y compatibilidad están en
`workflows/n8n/README.md`. Está desactivado; su ejecución real en el servidor del
usuario sigue pendiente de verificar.

El workflow debe recibir empresa, operación y destino resuelto por la matriz,
con ramas para S3 y Azure. No necesita duplicar la matriz ni mantener una lista de
siglas dentro de n8n. La aplicación conserva el código introducido y resuelve el directorio según la clasificación.

Configure estas variables en el proceso Flask y reinicie la aplicación:

También se puede guardar la URL y el token desde **Configuración de empresas → n8n · Global**, sin editar archivos. Región S3, cuenta Azure y destino se eligen en
la matriz; **Consultar buckets / contenedores** obtiene opciones del workflow.
Las variables de entorno, si existen, tienen prioridad al iniciar la aplicación.

```sh
export N8N_DOCUMENTS_WEBHOOK_URL='https://tu-n8n/webhook/idpn-documentos'
export N8N_DOCUMENTS_WEBHOOK_TOKEN='tu-token'
python3 app.py
```

El token se envía en `X-IDPN-Token`. Las credenciales de S3/Azure se guardarán en
n8n, no en las matrices ni en el navegador.

### Inventario: contrato implementado

La aplicación ya envía esta solicitud JSON mediante `POST` al webhook configurado:

```json
{
  "operation": "inventory",
  "site": "zhipos-idpn.net",
  "code": "FE-A-RHS-015",
  "type_id": "formato",
  "storage": {
    "provider": "s3",
    "target": "documentos-zhipos",
    "root": "zhipos"
  },
  "directory": "zhipos/procedimientos-especificos/administracion/recursos-humanos/formatos",
  "include_baja": true
}
```

n8n lista objetos/blobs del prefijo, incluyendo `BAJA`, recorre todas las páginas
del resultado y responde como un objeto JSON:

```json
{
  "success": true,
  "complete": true,
  "files": [
    {
      "key": "zhipos/procedimientos-especificos/administracion/recursos-humanos/formatos/FE-A-RHS-015-alta-de-personal-v001.xlsx",
      "etag": "identificador-de-la-version-almacenada"
    }
  ]
}
```

`files: []` con `complete: true` confirma que no hay antecedentes. Sin
`complete: true`, con errores HTTP o sin una respuesta válida no se asigna una
versión. Los objetos de otros códigos/directorios se ignoran para el cálculo.

### Subida y baja desde el primer proceso

El **primer proceso** envía el archivo a n8n desde una copia temporal del servidor, valida la respuesta y publica el bloque HTML con los enlaces devueltos. Baja exige folio y un archivo de referencia. n8n lo sube directamente a BAJA, verifica su integridad y devuelve el enlace para publicar la página testigo. Funciona también cuando no hay ningún documento previo en el destino. Los Formularios mantienen su campo de enlace y no se envían al almacenamiento. Es necesario configurar y verificar el workflow real antes de utilizar estos recorridos con documentos de producción.

Al incorporar la transferencia al workflow, el recorrido acordado es:

1. Recibir el archivo temporal y los metadatos calculados por la matriz. Recibir
   un identificador de operación para que un reintento no cree otra versión.
2. Volver a consultar el inventario dentro de un bloqueo por empresa, destino,
   directorio y código. La versión de la vista previa no autoriza sobrescribir.
3. Calcular `max(versiones) + 1` incluyendo `BAJA` y escribir el objeto nuevo con
   una condición que impida reemplazar un objeto existente.
4. Confirmar la subida (tamaño/integridad) antes de archivar antecedentes.
5. Para cada versión anterior: copiar a `BAJA`, verificar la copia y después
   retirar el objeto del directorio activo. No reemplazar una copia diferente ya
   existente en `BAJA`. Para Baja, aplicar este paso a todas las versiones activas.
6. Devolver el resultado, enlaces de visualización y descarga, y la lista de
   archivos archivados. Si el traslado es parcial, devolverlo como parcial y
   conservar suficiente información para reanudar la misma operación.
7. Con una subida confirmada, la aplicación publicará el bloque HTML y limpiará
   su archivo temporal. Un fallo de WordPress no debe provocar una nueva subida
   ni aumentar la versión al reintentar la publicación.

Los directorios de S3 y Azure Blob Storage pueden representarse mediante los
prefijos de los objetos; al subir con la clave calculada aparecen las carpetas
necesarias. El bucket o contenedor sí debe configurarse previamente.

### Crear el directorio cuando no existe

El workflow no debe rechazar una subida porque todavía no exista el directorio.
En S3 y Azure Blob Storage con espacio de nombres plano, se escribe el archivo
con su ruta completa como clave/nombre del objeto. Todos los segmentos nuevos
aparecen como directorios virtuales, sin crear cada carpeta por separado:

```text
zhipos/procedimientos-especificos/administracion/recursos-humanos/formatos/FE-A-RHS-015-alta-de-personal-v001.xlsx
```

La misma regla aplica a `BAJA`: al copiar el primer antecedente a su ruta de
archivo dentro de `BAJA`, ese directorio aparece automáticamente. Si la ruta ya
existe, se reutiliza; la condición de no sobrescribir sigue aplicando al archivo.
No se necesitan objetos vacíos para representar las carpetas de este recorrido.

En n8n, la rama S3 debe utilizar esa ruta completa en el nombre del archivo y la
rama Azure en el nombre del blob, dentro del destino seleccionado por la matriz.
Si se decide usar Azure Data Lake con espacio de nombres jerárquico, esa rama
debe asegurar los directorios mediante su API específica antes de escribir.

Para una descarga externa real, el enlace de descarga debe responder con
`Content-Disposition: attachment`. Para mostrar PDF, el enlace de visualización
debe servirlo con `Content-Type: application/pdf` y permitir la visualización.
El visor de Office debe poder acceder al enlace del documento; un enlace firmado
que expire no debe quedar incrustado como una dirección permanente sin renovación.

Al mover antecedentes a `BAJA`, las páginas antiguas pueden seguir apuntando a
sus claves anteriores. Antes de habilitar el traslado real debe definirse cómo
conservar esos enlaces: actualizar sus referencias o proporcionar un enlace
estable por documento/versión. La baja en almacenamiento y retirar Cognito/folio
de WordPress son operaciones distintas.

## Referencias

- [Webhook de n8n](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/)
- [Nodo AWS S3](https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.awss3)
- [Nodo Azure Storage](https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.azurestorage)
- [Carpetas y prefijos de S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-folders.html)

Para **Formulario / Web** se captura directamente el código del usuario y el enlace. Admite Alta y Baja (esta última solo publica una página testigo): no pide clasificación documental, no consulta la matriz y no envía archivos a n8n, S3 ni Azure. La empresa seleccionada determina únicamente el sitio WordPress de publicación.

### Políticas sin código

Al marcar **Es una política**, el código no se solicita ni se genera. Se elige manualmente Gobierno/procedimiento o Específicos/proceso/área de la matriz. El destino añade `politicas` a esa ubicación y respeta sus ajustes S3/Azure. El nombre identifica la política dentro de ese directorio; el archivo se guarda como `nombre-v001.pdf` y las siguientes versiones se calculan con el inventario, incluyendo BAJA. Para actualizar o dar de baja una política se seleccionan la misma ubicación y el mismo nombre. Todas las acciones requieren folio. Importa nuevamente el workflow generado para habilitar este contrato.

La **Baja de Formulario / Web** solicita folio, código, nombre y enlace, y publica la misma página con una marca de registro de baja. No elimina el formulario ni ejecuta almacenamiento o clasificación documental; la acción consiste únicamente en publicar el registro interno de WordPress.

El archivo de referencia de Baja recibe la siguiente versión disponible (o `v001` si no existe ninguna), conservando todas las versiones anteriores. El workflow verifica la subida; los antecedentes se archivan después, al confirmar el folio en el segundo proceso. Los reintentos reutilizan el mismo identificador y no generan otra versión. El contrato antiguo sin binario se conserva para integraciones que quieran reutilizar un archivo activo existente; la pantalla pide archivo de referencia.

### Baja en dos procesos, vinculada por folio

El primer proceso envía `defer_archive: true`: sube y verifica el testigo en BAJA y publica la página. Los antecedentes siguen activos. La aplicación guarda la asociación folio/empresa/destino/referencia en `config/bajas`, antes de eliminar el temporal. Al ejecutar el segundo proceso con **ese mismo folio**, se confirma primero la limpieza de Cognito y del folio en WordPress; después se envía `action: finalizar_baja`. n8n verifica la integridad del testigo, archiva todos los antecedentes activos y retira las copias idénticas al testigo sin crear otro duplicado. El testigo no se elimina. Los reintentos conservan el identificador de operación y los folios sin baja asociada solo ejecutan la limpieza habitual de WordPress.
