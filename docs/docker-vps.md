# Docker para el VPS

La imagen incluye Python 3.12, Chromium, los SDK de S3/Azure y Gunicorn. No incluye las claves, cookies, usuarios ni configuraciones de este equipo. La aplicación no necesita PostgreSQL: sus cuentas se guardan en SQLite dentro del volumen persistente.

## Arranque

Clonar el proyecto en el VPS:

```sh
git clone https://github.com/Ivlopx/control-operativo-idpn.git
cd control-operativo-idpn
```

Desde ese directorio:

```sh
cp .env.example .env
docker compose up -d --build
docker compose ps
```

O importar la imagen preparada para Linux AMD64:

```sh
docker load -i idpn-documental-1.0.0-linux-amd64.tar.gz
docker compose up -d --no-build
```

Si el VPS usa ARM64, construir allí con `docker compose build` en vez de importar el archivo AMD64. Comprobar la arquitectura con `uname -m` (x86_64 = AMD64; aarch64 = ARM64).

El servicio escucha en el puerto 5001 y, por defecto, se publica solo en la interfaz local del VPS. Si ese puerto ya lo usa otra aplicación, cambiar `IDPN_PORT` en `.env`. El puerto interno sigue siendo 5001.

## Nginx Proxy Manager

La cookie de acceso está preparada para HTTPS. Para usar el Nginx Proxy Manager que ya tienes:

1. Identificar su red Docker:

```sh
docker inspect infraestructura-nginx-proxy-manager-1 --format '{{json .NetworkSettings.Networks}}'
```

2. Poner ese nombre en `IDPN_PROXY_NETWORK` de `.env`.
3. Iniciar la aplicación unida a esa red:

```sh
docker compose -f compose.yaml -f compose.proxy.yaml up -d --build
```

4. En Proxy Hosts, crear el dominio de esta plataforma con Scheme `http`, Forward Hostname `idpn-documental`, Forward Port `5001` y SSL habilitado. Activar Force SSL. En Advanced:

```nginx
proxy_buffering off;
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
client_max_body_size 16m;
```

El buffering desactivado permite recibir consola y capturas del navegador sin retrasos. No usar la dirección localhost del contenedor de Nginx para llegar a la aplicación; se comunican por la red compartida.

Si se prueba directamente por HTTP, configurar temporalmente `IDPN_COOKIE_SECURE=false`. Para el dominio HTTPS usar `true`.

## Administrador inicial

Usuario predeterminado: `admin`. La primera vez se genera una contraseña aleatoria. Consultarla en el VPS:

```sh
docker compose exec app cat /data/config/auth/initial-admin.txt
```

Al entrar se obliga a cambiar la contraseña. El archivo inicial se elimina después de ese cambio. Opcionalmente se puede definir una contraseña inicial de al menos 12 caracteres en `IDPN_ADMIN_PASSWORD` antes del primer arranque. Esa variable solo se usa cuando no existen cuentas; cambiarla después no restablece una cuenta existente.

En **Usuarios**, el administrador puede crear cuentas, cambiar sus nombres y roles, conceder edición de empresas, desactivar cuentas o restablecer contraseñas. Los usuarios nuevos y las contraseñas restablecidas requieren cambio en el siguiente inicio de sesión.

- Todos los usuarios activos pueden ejecutar el primer y segundo proceso.
- Solo administradores pueden añadir/eliminar empresas, administrar cuentas o configurar las credenciales globales de almacenamiento.
- Un usuario con “Editar empresas” puede modificar sitios y matrices de empresas existentes; no puede crear/eliminar empresas ni consultar las claves globales.
- Las cookies de WordPress se guardan por usuario y por empresa. Cada usuario inicia su propia sesión en WordPress mediante el navegador del proceso. Las cookies compartidas anteriores no se importan automáticamente a los usuarios nuevos.
- El navegador, eventos y consola de una ejecución solo están disponibles para su propietario.

La aplicación procesa una automatización a la vez. Otros usuarios deben esperar a que termine. Se usa un único worker con varios hilos: no aumentar workers ni levantar réplicas sin migrar primero el estado y las colas de ejecución a un servicio compartido.

## Persistencia y actualizaciones

El volumen `idpn-data` contiene configuración, usuarios, claves de sesión, sesiones individuales de WordPress, registros de baja y archivos temporales. Mantenerlo al actualizar:

```sh
git pull --ff-only
docker compose up -d --build
```

No ejecutar `docker compose down -v` si quieres conservar esos datos.

Para migrar empresas existentes, copiar a `/data/config` sus JSON, el directorio `matrices`, los registros `bajas` y las credenciales `integrations`. Hacerlo con la aplicación detenida y devolver la propiedad a UID/GID 10001 antes de arrancar. Conservar `auth` al migrar una instalación que ya tenga usuarios. No mezclar la base SQLite mientras el proceso esté escribiendo.

La imagen exportada no contiene el volumen de datos. Tras importar la imagen, hay que configurar los sitios, matrices y credenciales o restaurar una copia del volumen.

## Referencias

- [Playwright en Docker](https://playwright.dev/python/docs/docker)
- [Gunicorn con Flask](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/)
- [Protección de sesiones de Flask](https://flask.palletsprojects.com/en/stable/web-security/)
