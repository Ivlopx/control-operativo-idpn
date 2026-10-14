# IDPN — Administración de páginas y documentos

Panel local Flask/Playwright para crear páginas de WordPress y retirar su
formulario Cognito y folio. Las nuevas páginas usan un bloque HTML propio con
encabezado, visor, enlace de descarga y secciones identificadas para su limpieza.

## Docker en VPS

```sh
git clone https://github.com/Ivlopx/control-operativo-idpn.git
cd control-operativo-idpn
cp .env.example .env
docker compose up -d --build
```

Consultar [administrador inicial y despliegue con Nginx Proxy Manager](docs/docker-vps.md). Los usuarios de la plataforma tienen sesiones de WordPress individuales.

El repositorio incluye el código y ejemplos en `examples/`. La configuración real de empresas, las claves, los usuarios y las cookies se mantienen fuera de Git. Docker los conserva en su volumen de datos. Una instalación nueva se configura desde el panel o se restaura con una copia privada de los datos existentes.

## Ejecutar

```sh
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
python3 app.py
```

Abrir `http://127.0.0.1:5001`. Las configuraciones por sitio están en `config/` y
las cookies de sesión en `sessions/`. El navegador del panel permite iniciar
sesión cuando las cookies ya no son válidas.

## Matriz documental

El apartado **Configuración de empresas → Matriz y almacenamiento** administra procedimientos, procesos, áreas,
siglas y destinos por empresa. Incluye el ejemplo de ZHIPOS y conserva el código capturado y calcula
directorios para altas, nuevas versiones y bajas. Los formularios no se almacenan.

Las operaciones de S3 y Azure se ejecutan directamente en Python con sus SDK oficiales. Las versiones se calculan consultando el inventario completo. Configura **Acceso al almacenamiento · Global**, después el destino en la matriz de cada empresa. No se necesita n8n.

La baja requiere un archivo testigo: el primer proceso lo guarda en BAJA y publica la página, dejando activos los antecedentes. El segundo proceso, con el mismo folio, limpia Cognito y el folio en WordPress y después archiva antecedentes, verificando las copias y retirando los duplicados del testigo.

Ver [configuración del almacenamiento propio](docs/almacenamiento-propio.md). Los workflows anteriores permanecen como referencia histórica; la aplicación ya no los invoca.

## Pruebas

```sh
python3 -m unittest discover -s tests -v
```

Las pruebas usan inventarios simulados, configuraciones temporales y un servidor
local de prueba. No inician sesión ni modifican los sitios WordPress reales.
