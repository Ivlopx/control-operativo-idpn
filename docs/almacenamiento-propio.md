# Almacenamiento propio

La aplicación conecta directamente con Amazon S3 y Azure Blob Storage. Ya no requiere importar workflows, webhooks ni configurar runners de n8n.

## Preparación

En el entorno que ejecuta la aplicación, instalar `requirements.txt` y reiniciar. Si se despliega en Docker, reconstruir la imagen de esta aplicación con esas dependencias. No hay que modificar el contenedor de n8n.

En **Configuración → Credenciales de almacenamiento · Global**:

- Amazon S3: Access Key ID y Secret Access Key. Session Token solo para credenciales temporales; deberán renovarse al caducar. Existe una opción para quitar el token al cambiar a claves permanentes.
- Azure: nombre de cuenta y Account Key, disponible en Storage Account → Access keys.


Guardar y luego seleccionar, en **Almacenamiento** de la empresa, el proveedor y su destino: S3 muestra bucket, directorio raíz y región; Azure muestra contenedor y directorio raíz. La cuenta Azure se toma automáticamente de las credenciales globales, también en los destinos propios de procesos y áreas. Mientras el proveedor esté pendiente, los campos de destino permanecen ocultos. “Consultar destinos” comprueba acceso y lista los destinos existentes. No crea buckets ni contenedores: las rutas dentro de ellos aparecen automáticamente al subir objetos.

Se mantiene una identidad AWS y una cuenta Azure globales. Pueden emplearse múltiples buckets S3 y múltiples contenedores de esa cuenta Azure, seleccionados por empresa o rama. Cambiar de cuenta Azure requiere introducir su nueva clave.

El visor y el botón de descarga usan el enlace público directo de S3 o Azure. No hace falta configurar una URL pública de la aplicación. Los archivos PDF/Office deben tener lectura pública. El navegador puede descargar el archivo o abrirlo en una pestaña, especialmente los PDF; el atributo HTML download no fuerza descargas entre dominios. No se modifica la política de acceso del bucket/contenedor.

La ruta antigua de descarga firmada se mantiene para enlaces que ya se hubieran generado; accede anónimamente al archivo y no expone archivos privados con las credenciales del servidor.

Permisos para la identidad AWS: listar buckets (para el selector), listar objetos del destino, leer, escribir y eliminar objetos. Azure Shared Key debe tener permitido el acceso a la cuenta. La aplicación guarda manifiestos y bloqueos dentro del destino; estos también requieren lectura y escritura.

## Operaciones

- Alta: primera versión `v001`; rechaza un documento que ya exista.
- Nueva versión: máximo de las versiones activas y baja + 1; verifica el nuevo archivo antes de archivar las versiones activas.
- Baja, primer proceso: requiere archivo y folio, guarda el testigo directamente en baja, publica la página y deja activos los antecedentes.
- Baja, segundo proceso: usar el folio de la página testigo. Primero confirma la limpieza de Cognito y folio en WordPress; después verifica el testigo, copia los antecedentes a baja y verifica sus bytes antes de retirar los originales. Si un antecedente es idéntico al testigo, conserva este y retira la copia activa sin generar otro duplicado.
- Formularios Web: alta/baja solo publican en WordPress; no usan el almacenamiento. Publican encabezado, un bloque nativo `core/embed` centrado con el enlace capturado, folio y Cognito. No incrustan la página completa en un iframe ni fuerzan altura o anchura del recurso. El segundo proceso conserva el bloque del enlace y retira únicamente Cognito y folio.
- Políticas: sin código; identidad por nombre y directorio, ubicación manual y versiones automáticas.

Se mantiene el límite de 15 MiB para archivos nuevos y antecedentes. Los reintentos con el mismo identificador de operación recuperan el manifiesto y su versión. Un bloqueo abandonado tras detener el proceso abruptamente requiere comprobar que no hay una operación en curso antes de recuperarlo manualmente; no se eliminan bloqueos ajenos automáticamente.

Las páginas nuevas guardan la acción (`alta`, `version` o `baja`) y el tipo de recurso en atributos `data-idpn-action` y `data-idpn-resource`, sin leyendas visibles. El segundo proceso lee esa marca y verifica que siga guardada después de limpiar Cognito y el folio. Solo las bajas documentales finalizan el archivo de antecedentes; los formularios no operan sobre almacenamiento. Las páginas antiguas sin marca mantienen el comportamiento basado en el registro de baja por folio.

## Persistencia y migración

Mantener `config/` en un volumen persistente. Incluye `integrations/storage.json` (credenciales, permisos 0600), `integrations/download-key` (firma de enlaces públicos) y `bajas/` (relación folio/testigo). Las claves se guardan en el servidor y no se devuelven en las respuestas de configuración. El archivo no está cifrado: proteger el acceso al volumen y sus respaldos.

Se conserva el formato de nombres, rutas y manifiestos del workflow anterior. Las bajas pendientes guardadas por la aplicación pueden finalizar con el motor propio usando las credenciales del mismo destino. Conservar los archivos de `config/bajas/`.

Las páginas publicadas antes de la migración pueden tener botones de descarga que apuntan a n8n. La aplicación no reescribe esas páginas automáticamente: mantener ese endpoint para esas descargas o actualizar sus enlaces antes de apagarlo. Los enlaces directos a S3/Azure siguen apuntando al mismo archivo.

Antes de producción: validar con un documento de prueba alta, descarga desde WordPress, nueva versión y ambos procesos de baja. Las pruebas locales simulan almacenamiento y WordPress; no sustituyen la validación con las credenciales reales.

## Referencias de los SDK

- [AWS: escrituras condicionales](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html)
- [AWS: eliminación con ETag](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/delete_object.html)
- [Azure: BlobClient y condiciones de acceso](https://learn.microsoft.com/python/api/azure-storage-blob/azure.storage.blob.blobclient)

- [Comportamiento del enlace HTML y atributo download](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/a)

Los directorios se normalizan a lowerCamelCase, sin acentos ni espacios: `procedimientosDeGobierno/controlDeDocumentos` o `procedimientosEspecificos/administracion/recursosHumanos`. La carpeta de archivo es `baja`. Los códigos, identificadores internos y nombres de archivo conservan su convención. Al actualizar una instalación sin documentos, las matrices existentes se normalizan al cargarse y guardarse; este cambio no mueve objetos previamente almacenados.
