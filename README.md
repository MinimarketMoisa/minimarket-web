# Minimarket Web

Portal de operaciones para empleados y panel administrativo en Django, disenado para compartir la base de datos PostgreSQL con el backend FastAPI.

---

## Requisitos Previos

* Python 3.10 o superior (probado en Python 3.14)
* PostgreSQL instalado y en ejecucion con la base de datos `minimarket_db`

---

## Instalacion y Configuracion

### 1. Clonar el repositorio y entrar al directorio
```bash
git clone <URL_DEL_REPOSITORIO>
cd minimarket-web
```

### 2. Crear y activar el entorno virtual
* En Windows (PowerShell):
  ```powershell
  python -m venv .venv
  .venv\Scripts\Activate.ps1
  ```
* En Linux / macOS:
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```

### 3. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 4. Configurar variables de entorno
Copia el archivo de ejemplo `.env.example` como `.env`:
```bash
cp .env.example .env
```
Edita `.env` e ingresa las credenciales de tu PostgreSQL local:
```ini
DATABASE_URL=postgresql://postgres:TU_PASSWORD@localhost:5432/minimarket_db
```

### 5. Aplicar migraciones base de Django
```bash
python manage.py migrate
```

---

## Ejecutar el Proyecto

Inicia el servidor de desarrollo en el puerto 8001:
```bash
python manage.py runserver 8001
```

Empleados y administradores comparten el acceso:
http://127.0.0.1:8001/empleados/login/

El sistema redirige a cada usuario según su rol: el empleado entra a su sucursal y el administrador a un resumen global independiente de las sucursales.

El portal del empleado permite:
* Ver pedidos pendientes y preparados de la sucursal.
* Consultar el detalle de entrega y los productos de cada pedido.
* Marcar pedidos como preparados o cancelarlos; al cancelar se devuelve el stock.
* Confirmar pagos pendientes por transferencia.
* Asignar o cambiar el repartidor de un pedido a domicilio.
* Consultar y actualizar existencias y fechas de vencimiento de la sucursal.

Las plantillas HTML del portal están separadas en `templates/tienda/empleado/`; sus estilos están en `tienda/static/tienda/`.
El logo oficial y la paleta verde/naranja de MiniMarket se aplican al portal de personal, al panel global y a la consola Django.

El portal de administración global presenta pedidos, pagos, repartidores, existencias y rendimiento de todas las sucursales. El menú lateral abre los módulos de Django para gestionar pedidos, inventario, productos, categorías, cuentas, empleados y sucursales usando los mismos modelos y permisos.

Las pantallas de Django Admin conservan sus operaciones de alta, consulta, edición y eliminación, integradas con la navegación y el diseño del portal. El empleado ve las operaciones de su sucursal y consulta el catálogo sin permisos de edición.

---

## Acceso de Administrador

* Si la base de datos ya fue inicializada con los datos de prueba (seed de FastAPI):
  * Email: `admin@minimarket.test`
  * Contrasena: `Prueba12345`
* O puedes crear un superusuario nuevo mediante:
  ```bash
  python manage.py createsuperuser
  ```

---

## Arquitectura y Roles

* Modelos de negocio configurados con `managed = False`.
* FastAPI administra el esquema de la base de datos (DDL).
* Django lee y escribe directamente en la base de datos compartida en tiempo real.
* El portal de empleados y el Admin Django aplican permisos por rol y restringen los datos de empleados a su sucursal.
