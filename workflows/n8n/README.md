> Referencia histórica: la aplicación ahora usa almacenamiento propio en Python. Para configurar la versión actual, consulta [almacenamiento propio](../../docs/almacenamiento-propio.md).

# Workflow IDPN: S3 y Azure

Importar **idpn-documentos.json** desde el menú de n8n → **Import from File**.
El archivo se entrega desactivado y no contiene credenciales de almacenamiento.
Es un workflow único con 19 nodos y ramas para S3, Azure y descarga pública.

## Configuración inicial

1. Crear una credencial **Header Auth**: nombre de cabecera `X-IDPN-Token` y un
   valor aleatorio propio. Seleccionarla en **IDPN documentos**.
2. Seleccionar una credencial **AWS (IAM)** en **S3 operación** y **S3 leer archivo**.
3. Seleccionar una credencial **Azure Storage Shared Key API** en **Azure operación**
   y **Azure leer archivo**. La cuenta de esa credencial debe coincidir con la
   cuenta elegida en la matriz.
4. Habilitar el módulo integrado `crypto` para los nodos Code del servidor/runner:

   ```text
   NODE_FUNCTION_ALLOW_BUILTIN=crypto
   ```

   Si se usa un task runner externo, establecerlo en ese runner siguiendo su
   configuración. Si la lista ya contiene otros módulos, añadir `crypto`.
5. Guardar/publicar el workflow y copiar su **Production URL**. En la aplicación,
   abrir **Configuración de empresas → n8n · Global**, guardar esa URL y el token de
   Header Auth. El token se guarda solo en el servidor y no se devuelve al navegador.
6. En cada matriz, seleccionar servicio, región S3 o cuenta Azure, y usar
   **Consultar buckets / contenedores** para elegir el destino. Confirmar la
   región del bucket; el listado de buckets no determina automáticamente su región.

No se solicita introducir claves AWS ni de Azure en la página. La cuenta AWS
puede tener varios buckets. Esta preparación usa una identidad AWS y una cuenta
Azure por workflow; varias cuentas Azure requieren ramas/credenciales adicionales.

Las credenciales deben poder listar destinos y objetos, leer, escribir y retirar
objetos del directorio activo. Para S3 corresponden `s3:ListAllMyBuckets`,
`s3:ListBucket`, `s3:GetObject`, `s3:PutObject` y `s3:DeleteObject` en los destinos
necesarios. En Azure la clave seleccionada debe corresponder a la cuenta.

## Enlaces públicos

El workflow construye enlaces directos a los documentos; no cambia políticas de
acceso de buckets/contenedores. Deben permitir lectura pública de los documentos
que se incrustan. Usa buckets S3 de propósito general y Azure Blob Storage con
espacio de nombres plano; no S3 Express ni Azure Data Lake jerárquico.

También registra un GET público `/webhook/idpn-documentos-descarga`. Obtiene el
documento desde su enlace público **sin credenciales de almacenamiento** y lo
devuelve con `Content-Disposition: attachment`. Así esa ruta no puede convertir
un archivo privado en un archivo público utilizando las credenciales del workflow.
La dirección pública del webhook de producción debe ser accesible desde fuera
del servidor para utilizar el botón de descarga.

## Operaciones

El POST acepta JSON para `inventory`, `destinations` y Baja. Para subir un archivo,
usar `multipart/form-data` con un campo `file` y un campo `metadata` que contenga
un JSON serializado:

```json
{
  "operation": "document",
  "operation_id": "operacion-uuid-unico-y-estable",
  "site": "zhipos-idpn.net",
  "type_id": "formato",
  "action": "alta",
  "code": "FE-A-RHS-003",
  "name": "Alta de personal",
  "directory": "zhipos/procedimientos-especificos/administracion/recursos-humanos/formatos",
  "storage": {
    "provider": "s3",
    "target": "documentos-zhipos",
    "region": "us-east-1",
    "account": ""
  }
}
```

El directorio y el código proceden de la matriz de la aplicación. Para Azure,
cambiar `provider` a `azure`, indicar la cuenta en `account` y el contenedor en
`target`. El archivo recibe su extensión del binario enviado. Para Baja cambiar
`action` a `baja` y enviar el archivo de referencia mediante multipart: se guarda directamente en BAJA. La operación sin archivo sigue disponible para integraciones que reutilicen una versión activa existente. `operation_id` debe ser único por acción y
reutilizarse al reintentar esa misma acción.

Para consultar inventario, usar el contrato de `docs/n8n-documentos.md`. Para
consultar destinos basta `{"operation":"destinations","storage":{...}}`.

El workflow:

- Lista todas las páginas del inventario y considera las versiones de `BAJA`.
- Calcula la siguiente versión; no recibe una versión manual.
- Guarda archivos con toda su ruta, creando directorios virtuales al escribir.
- Adquiere un bloqueo por directorio/código para evitar altas simultáneas.
- Conserva un registro de cada operación para reanudar un reintento sin aumentar
  la versión otra vez.
- Escribe con `If-None-Match: *` para no sobrescribir archivos.
- En una nueva versión, primero sube y verifica el archivo nuevo. Después copia
  cada antecedente a `BAJA`, verifica sus bytes mediante SHA-256 y tamaño, y retira
  el original solo si conserva su ETag. La copia viaja mediante n8n para permitir
  la comparación de contenido en ambos proveedores.
- Devuelve `url`, `download_url`, `version`, `filename`, `key` y antecedentes
  archivados. Si hay un fallo, devuelve `success: false` y el estado parcial.
- Excluye Formularios; se mantienen en el flujo de WordPress.

## Verificación y límites

El JSON, el motor de operaciones y sus casos de fallos/reintentos están probados
localmente con almacenamientos simulados. **La importación y ejecución real en tu
instancia de n8n no se han verificado**, porque todavía no conocemos su versión ni
tenemos conexión con ella. Usa HTTP Request 4.2, Code 2, If 2, Webhook 2 y Respond
to Webhook 1.4. Confirmar que esos nodos/versiones están disponibles al importar.
Puedes consultar la versión con `n8n --version` o, si está en Docker, con
`docker exec NOMBRE_DEL_CONTENEDOR n8n --version`.

Límite inicial de 15 MiB por archivo, incluidos antecedentes que se archiven.
El webhook estándar de n8n admite 16 MB; esta preparación deja margen para el
formulario multipart. Si se necesitan documentos más grandes hay que ampliar
ambos límites y comprobar la configuración del proxy y del runner.

Los bloqueos y registros usan `.idpn-lock-CÓDIGO.json` y `.idpn-operations/` dentro
del directorio del tipo documental. No deben tener reglas de limpieza que borren
registros de operaciones necesarios para reintentos. Si n8n se apaga o cancela
durante una operación, puede quedar un bloqueo: comprobar que esa ejecución ya
terminó y revisar el objeto/ETag antes de retirarlo. No se elimina un bloqueo ajeno
automáticamente. En un fallo normal el workflow intenta liberar el bloqueo propio.

La aplicación consulta inventario/listados y permite configurar su conexión. El primer proceso envía archivos temporales con este contrato y publica cuando n8n devuelve una respuesta válida. Una baja publica en WordPress el archivo de referencia subido directamente a BAJA. Todas las acciones requieren folio. Los reintentos con los mismos datos y archivo reutilizan su identificador de operación mientras la página siga abierta. El temporal se elimina al finalizar, incluso ante un error. La ejecución real con las credenciales de producción todavía debe verificarse.

Antes de mover archivos publicados a `BAJA`, hay que conectar la actualización de
sus enlaces WordPress o un enlace estable por versión para conservar las páginas
anteriores. La aplicación no modifica esos enlaces automáticamente en esta etapa.

## Desarrollo y pruebas

```sh
python3 workflows/n8n/build_workflow.py
node tests/test_n8n_engine.js
python3 -m unittest discover -s tests -v
```

`engine.js` es la fuente del motor y `build_workflow.py` genera el JSON; mantener
ambos junto al exportado para poder actualizarlo y volver a probarlo.

Referencias: [HTTP Request](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/),
[módulos en Code](https://docs.n8n.io/hosting/configuration/configuration-examples/modules-in-code-node/),
[Shared Key de Azure](https://docs.n8n.io/integrations/builtin/credentials/azurestorage/),
[borrado condicional de S3](https://docs.aws.amazon.com/AmazonS3/latest/API/API_DeleteObject.html).

### Confirmación por folio en el segundo proceso

La aplicación añade `defer_archive: true` a la baja con archivo: solo guarda el testigo en BAJA. Persiste la asociación con el folio de la página testigo. Tras confirmar la limpieza de Cognito y del folio en el segundo proceso, envía `action: finalizar_baja`, otro identificador estable y `reference` con key/hash/tamaño/versión. El workflow verifica el testigo y archiva antecedentes; una copia activa idéntica se elimina únicamente después de verificar el testigo, que permanece disponible. Esta separación exige importar el JSON actualizado y conservar `config/bajas` entre despliegues.
