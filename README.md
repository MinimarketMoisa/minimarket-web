# Minimarket Web

Panel administrativo y entorno web desarrollado en Django, disenado para compartir y operar sobre la misma base de datos PostgreSQL que el backend de FastAPI (minimarket-api).

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

Accede al panel de administracion en el navegador:
http://127.0.0.1:8001/admin/

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
* Permisos de acceso segregados segun el rol del usuario (ADMIN y EMPLEADO con acceso restringido por sucursal).
