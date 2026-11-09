# MiniMarket Web — Portal de Operaciones y Administración

Portal web desarrollado en **Django** para la gestión operativa en sucursales (empleados) y administración global de **MiniMarket**. Diseñado para operar en tiempo real sobre la base de datos **PostgreSQL** compartida con el backend de **FastAPI** (`managed = False`).

---

## Características Principales

* **Identificador de Negocio (`codigo_pedido`)**:
  * Búsqueda y trazabilidad inmediata de pedidos mediante su código alfanumérico (ej. `PED-000002`, `PED-A7K92M`) y respaldo por ID numérico.
  * Visualización prioritaria en listas, detalles y reportes de incidencias.
* **Operaciones de Sucursal (Empleados)**:
  * Gestión de flujo de pedidos: pendientes, preparados, pagos por transferencia y cancelación con devolución automática de inventario.
  * Asignación y liberación transaccional de repartidores disponibles.
  * Control de existencias por sucursal y fechas de vencimiento.
* **Administración Global**:
  * Métricas en tiempo real, histórico consolidado de ventas y balance por sucursales.
  * Gráficas mensuales de ingresos, días con más ventas y productos más demandados.
* **Módulo de Soporte Técnico (Reportes / Denuncias)**:
  * Bandeja de incidencias reportadas por clientes sobre entregas y repartidores.
  * Registro de atención con hora de entrada, hora de salida, cálculo de duración, prioridad y medida correctiva.
* **Paginación Uniforme (25 registros)**:
  * Paginación directa en SQL (`Paginator`) en pedidos, catálogo, inventario, usuarios, sucursales y reportes.
* **Interfaz y Estilo**:
  * Diseñado con **Tailwind CSS** y CSS personalizado respetando la paleta de MiniMarket (`#9caa17`, `#f67300`).
  * Layout responsivo con espaciado constante y navegación lateral unificada.

---

## Requisitos

* **Python 3.10+** (probado en Python 3.13 y 3.14).
* **PostgreSQL** con la base de datos `minimarket_db` (local o en VPS remota).

---

## Instalación y Configuración Local

### 1. Clonar el repositorio y acceder a la carpeta
```bash
git clone <URL_DEL_REPOSITORIO>
cd minimarket-web
```

### 2. Entorno virtual
```bash
# Crear entorno virtual
python3 -m venv .venv

# Activar en Linux / macOS:
source .venv/bin/activate

# Activar en Windows (PowerShell):
.venv\Scripts\Activate.ps1
```

### 3. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 4. Configurar variables de entorno (`.env`)
Copia la plantilla `.env.example`:
```bash
cp .env.example .env
```
Configura la conexión a tu base de datos PostgreSQL:
```ini
DEBUG=True
SECRET_KEY=clave-secreta-de-desarrollo
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=postgresql://postgres:password@localhost:5432/minimarket_db
```

### 5. Iniciar el servidor local
```bash
python manage.py runserver 8080
```
Acceso al portal: [http://127.0.0.1:8080/empleados/login/](http://127.0.0.1:8080/empleados/login/)

---

## Usuarios de Prueba (Seed)

| Rol | Correo | Contraseña |
| **Administrador** | `admin@minimarket.test` | `Prueba12345` |
| **Empleado** | `empleado1@minimarket.test` | `Prueba12345` |

---

## Despliegue en VPS (Producción)
El proyecto incluye soporte directo para **Gunicorn** y **WhiteNoise**:

```bash
chmod +x deploy_vps.sh
./deploy_vps.sh
```

---

## Estructura del Proyecto

```text
minimarket-web/
├── core/                   # Configuración del proyecto Django (settings, urls, asgi, wsgi)
├── tienda/                 # App principal (modelos managed=False, vistas, servicios, forms)
│   ├── services.py         # Lógica de dominio, backend de auth y soporte técnico
│   ├── models.py           # Mapeo SQLAlchemy/FastAPI a Django ORM
│   ├── admin.py            # Personalización de Django Admin
│   └── static/tienda/      # CSS, favicon y branding oficial
├── templates/tienda/       # Plantillas HTML estructuradas
│   ├── base_portal.html    # Plantilla base con Tailwind CSS
│   ├── admin/              # Vistas administrativas y soporte
│   ├── empleado/           # Vistas de operaciones de sucursal
│   └── partials/           # Componentes reutilizables (sidebar, etc.)
├── deploy_vps.sh           # Despliegue directo con Gunicorn y WhiteNoise
├── requirements.txt        # Dependencias de Python
└── cambios.md              # Registro detallado de optimizaciones y cambios
```
