# Minimarket Web 🛒

Panel administrativo y entorno web desarrollado en **Django**, diseñado para compartir y operar sobre la misma base de datos PostgreSQL que el backend de **FastAPI** (`minimarket-api`).

---

## 🚀 Requisitos Previos

* **Python 3.10+** (probado en Python 3.14)
* **PostgreSQL** instalado y corriendo con la base de datos `minimarket_db`

---

## 🛠️ Instalación y Configuración

### 1. Clonar el repositorio y entrar a la carpeta
```bash
git clone <URL_DEL_REPOSITORIO>
cd minimarket-web
```

### 2. Crear y activar el entorno virtual
* **En Windows (PowerShell):**
  ```powershell
  python -m venv .venv
  .venv\Scripts\Activate.ps1
  ```
* **En Linux / macOS:**
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```

### 3. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 4. Configurar variables de entorno
Copia el archivo de ejemplo `.env.example` y nómbralo `.env`:
```bash
cp .env.example .env
```
Abre `.env` y ajusta la contraseña de tu PostgreSQL:
```ini
DATABASE_URL=postgresql://postgres:TU_PASSWORD@localhost:5432/minimarket_db
```

### 5. Aplicar migraciones básicas de Django
```bash
python manage.py migrate
```

---

## ▶️ Ejecutar el Proyecto

Inicia el servidor en el puerto 8001:
```bash
python manage.py runserver 8001
```

Accede al panel de administración en tu navegador:
👉 **http://127.0.0.1:8001/admin/**

---

## 👤 Acceso de Administrador

* Si la base de datos ya fue poblada con los datos de prueba (`seed` de FastAPI):
  * **Email:** `admin@minimarket.test`
  * **Contraseña:** `Prueba12345`
* O crea un superusuario nuevo:
  ```bash
  python manage.py createsuperuser
  ```

---

## 📌 Arquitectura
* Todos los modelos de la tienda tienen `managed = False`.
* FastAPI es el encargado del esquema (DDL).
* Django lee y escribe directamente en la base de datos compartida en tiempo real.
