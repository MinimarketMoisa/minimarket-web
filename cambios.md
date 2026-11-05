# Registro de Optimizaciones y Cambios (`minimarket-web`)

Este documento detalla todas las optimizaciones de código, correcciones de errores, mejoras de rendimiento hacia PostgreSQL (VPS) y la adaptación visual con **Tailwind CSS** conservando la identidad gráfica original de **MiniMarket**.

---

## 1. Limpieza de Configuración y Dependencias

- **[`requirements.txt`](./requirements.txt) y [`core/settings.py`](./core/settings.py)**:
  - Se eliminaron `djangorestframework` (`rest_framework`) y `Pillow`, ya que el portal `minimarket-web` no expone `ViewSets` ni `Serializers` de DRF ni utiliza `ImageField` (las imágenes de producto usan URLs en `CharField`). Esto reduce el tiempo de arranque, validaciones innecesarias y peso del entorno virtual.
  - Se activó la reutilización persistente de conexiones a PostgreSQL (`CONN_MAX_AGE = 60` y `CONN_HEALTH_CHECKS = True`). Como la base de datos vive en una **VPS remota**, esto evita abrir y negociar una nueva conexión TCP en cada clic del usuario.
  - Se registró el nuevo backend `tienda.services.MinimarketAuthBackend` en `AUTHENTICATION_BACKENDS`.
- **[`core/urls.py`](./core/urls.py)**:
  - Se eliminaron las asignaciones duplicadas de `admin.site.site_header`, `site_title` e `index_title`, dejando a `MinimarketAdminSite` como única fuente de verdad.

---

## 2. Centralización de Lógica y Corrección de Bugs (`tienda/`)

Se creó el módulo **[`tienda/services.py`](./tienda/services.py)** para unificar la autorización y las operaciones transaccionales compartidas entre el portal web y Django Admin:

1. **Bug corregido en `UsuarioAdmin.save_model` ([`tienda/admin.py`](./tienda/admin.py))**:
   - Anteriormente `UsuarioAdmin` tenía **dos métodos `save_model` con el mismo nombre**; el segundo sobrescribía silenciosamente al primero, impidiendo que `token_version` se incrementara al desactivar cuentas desde `/admin/`. Ambos comportamientos (sincronización de `is_staff`/`is_superuser` según rol + invalidación de JWT) fueron fusionados en un único `save_model`.
2. **Bug corregido al desactivar usuarios desde el listado rápido ([`tienda/views.py`](./tienda/views.py))**:
   - `usuario_admin_estado` desactivaba cuentas sin incrementar `token_version`. Ahora revoca de inmediato las sesiones JWT activas de la app móvil al desactivar un usuario.
3. **Bug corregido en validación de repartidores ocupados ([`tienda/services.py`](./tienda/services.py))**:
   - Antes solo se verificaban los estados `("PENDIENTE", "PREPARADO")` al comprobar si un repartidor tenía entregas activas, ignorando `"EN_RUTA"`. Se definió `ESTADOS_ACTIVOS_REPARTIDOR = ("PENDIENTE", "PREPARADO", "EN_RUTA")` y se unificó la asignación y liberación de repartidores entre empleados y administradores.
4. **Bug corregido en cancelación de pedidos desde Django Admin ([`tienda/admin.py`](./tienda/admin.py))**:
   - `PedidoAdmin.save_model` tenía una copia incompleta de la devolución de stock que no liberaba al repartidor asignado ni creaba el registro de `Inventario` si no existía. Ahora reutiliza `cancelar_pedido()` de `tienda/services.py`.
5. **Bug corregido en filtro de fecha de reportes ([`tienda/views.py`](./tienda/views.py))**:
   - Pasar un mes fuera de rango en `?mes_anio=` (ej. `2026-99`) provocaba un error `500` en `calendar.monthrange`. Se creó `_parse_mes_anio()` con validación segura de rango (`1..12`), eliminando además el código duplicado entre `reportes_admin` y `reportes_empleado`.

---

## 3. Eliminación de Código Innecesario / Muerto

- **[`tienda/admin.py`](./tienda/admin.py) (reducido de 420 a ~240 líneas)**:
  - Dado que `MinimarketAdminSite.has_permission` bloquea por diseño el acceso de empleados a `/admin/` (los empleados operan exclusivamente en `/empleados/`), se eliminaron ~140 líneas de código inalcanzable (`es_empleado`, `get_sucursal_empleado`, `CatalogoAdminMixin`, `SoloAdminMixin` repetido en 10 clases y ramas muertas de empleados en `ProductoAdmin`, `InventarioAdmin` y `PedidoAdmin`).
- **[`tienda/views.py`](./tienda/views.py)**:
  - Se creó el helper `_guardar_portal_model_form()` para evitar repetir la misma estructura crear/editar/redirigir en sucursales, productos, categorías e inventario.
  - Se eliminaron las vistas y utilidades del mapa de seguimiento GPS (`pedido_seguimiento_empleado`, `pedido_seguimiento_admin`, `_render_seguimiento`, `_generar_tracking_jwt`, `_coord_float`), ya que el tracking se delega a FastAPI.
  - Se eliminaron imports sin uso (`datetime`, `time`, `jwt`) y consultas redundantes que no se usaban en las plantillas (`productos_activos` y `empleados_activos` en `admin_panel`).
- **[`tienda/models.py`](./tienda/models.py)**:
  - Se mapeó la nueva columna de negocio `codigo_pedido` (`CharField(max_length=30, unique=True, null=True, blank=True)`) y `fecha_creacion = DateTimeField(auto_now_add=True)` en `Pedido`, actualizando `Pedido.__str__` a `"{codigo_pedido or Pedido #id} ({estado})"` y exponiendo el alias `DenunciaRepartidor` / propiedad `Denuncia.pedido`.
  - Se eliminaron propiedades heredadas sin uso (`Denuncia.id_usuario` y `Denuncia.resolucion`) y se sincronizó `Usuario.has_perm` / `has_module_perms` con el rol `ADMIN`, manteniendo intacto `managed = False` y todo el esquema compartido con FastAPI.

---

## 4. Optimización de Consultas SQL (Menos Latencia contra la VPS)

- **Backend de autenticación (`MinimarketAuthBackend`)**: Carga `Usuario`, `Rol` y `Empleado -> Sucursal` en **1 sola consulta `JOIN`** por petición en lugar de disparar hasta 3 consultas separadas en cada vista protegida.
- **Reportes mensuales (`_calcular_metricas_reportes`)**: Se consolidaron **7 consultas `.count()` y `.aggregate()` separadas** en **1 sola agregación condicional SQL** (`Count`/`Sum` con `filter=Q(...)`).
- **Catálogo de categorías para empleados (`catalogo_categorias_empleado`)**: Se eliminó un problema **N+1** donde `c.productos.filter(activo=True).count()` ejecutaba una consulta por cada categoría; ahora se resuelve en **1 sola consulta** con `Count("productos", filter=Q(productos__activo=True))`.
- **Panel de empleado (`empleado_panel`)**: Los conteos de pedidos pendientes y preparados pasaron de 2 consultas `.count()` a 1 sola llamada `.aggregate()`.
- **Detalle de pedido de empleado (`_pedido_sucursal` y plantilla)**: Se agregó `"id_repartidor__id_repartidor"` a `select_related` y se reemplazó `pedido.detalles.count` por `lista_detalles|length` sobre los detalles ya precargados en memoria (`prefetch_related`).
- **Formularios y Django Admin**: Se añadió `select_related("id_repartidor")` en `PedidoPortalForm` y `list_select_related` en todas las clases `ModelAdmin` con llaves foráneas.
- **Búsqueda en denuncias (`denuncias_admin`)**: Se reemplazó `id_pedido__id_pedido__icontains` (conversión de `BIGINT` a texto con `JOIN`) por comparación exacta indexada `id_pedido_id=int(busqueda)` cuando el término es numérico.

---

## 5. Adaptación de HTML + Tailwind CSS (Manteniendo la Identidad Actual)

- **Plantilla base unificada ([`templates/tienda/base_portal.html`](./templates/tienda/base_portal.html))**:
  - Antes `admin/base.html` y `empleado/base.html` duplicaban todo el documento HTML. Ahora ambas extienden `base_portal.html` en 5 líneas cada una.
  - Se integró **Tailwind CSS CDN** configurado con la paleta exacta de MiniMarket (`mm-green: #9caa17`, `mm-green-dark: #707d0c`, `mm-green-soft: #f3f5df`, `mm-orange: #f67300`, `mm-orange-dark: #cf5e00`, `mm-ink: #172b25`, `mm-canvas: #f4f8f5`, `mm-line: #e2eae6`).
  - Se mejoró la barra superior (`topbar`) con indicador de estado e inicial en avatar del usuario autenticado.
- **Limpieza y mejoras en [`tienda/static/tienda/empleado.css`](./tienda/static/tienda/empleado.css)**:
  - Se conservaron las tarjetas, botones, tablas y menú lateral característicos del proyecto para no perder su estética original.
  - Se eliminaron reglas CSS huérfanas de versiones antiguas (`.brand`, `.nav`, `.account`, `#nav-sidebar`, `body #header`).
  - Se añadieron las variantes de `badge` que faltaban en el CSS (`aprobado`, `pagado`, `en_ruta`, `entregado`, `investigando`, `resuelto`, `desestimada`), estilos para `<textarea>` y campos checkbox en línea (`.field-checkbox`).
- **Modernización de plantillas HTML con utilidades de Tailwind y mejoras de UX**:
  - **Espaciado lateral equilibrado (`28px`)**: Se desacopló el contenedor principal de la clase `.container` de Tailwind usando `.portal-container` con `width: 100%; padding: 24px 28px 56px;` (más clases utilitarias `px-4 sm:px-7 pt-6 pb-14` y versionado `?v=20261104` en [`empleado.css`](./tienda/static/tienda/empleado.css) para invalidar la caché del navegador), dejando un pequeño margen limpio y constante de `28px` entre la barra lateral izquierda y el contenido derecho en cualquier tamaño de monitor.
  - **Eliminación de Accesos Rápidos redundantes**: Se retiró la sección `ACCESO DIRECTO / Gestión del sistema` y los botones redundantes del encabezado en [`dashboard.html`](./templates/tienda/admin/dashboard.html) y [`empleado/panel.html`](./templates/tienda/empleado/panel.html), delegando la navegación al menú lateral izquierdo.
  - **Unificación visual de íconos y nombres en el menú lateral ([`sidebar.html`](./templates/tienda/partials/sidebar.html))**:
    - Se reemplazó el emoji multicolor `📊` por el ícono geométrico monocromático `▥` en **`Métricas`**, igualando el estilo y color naranja (`var(--orange-dark)`) del resto de apartados.
    - Se renombró el apartado de incidencias exclusivamente a **`Reporte`** (diferenciándolo de **`Métricas`** en [`reportes.html`](./templates/tienda/admin/reportes.html)).
  - **Módulo `Reporte` simplificado y armonizado en tarjetas limpias ([`denuncias.html`](./templates/tienda/admin/denuncias.html), [`denuncia_detalle.html`](./templates/tienda/admin/denuncia_detalle.html), [`forms.py`](./tienda/forms.py), [`services.py`](./tienda/services.py))**:
    - Diseño multi-cuadro alineado con la estética de [`pedido_detalle.html`](./templates/tienda/admin/pedido_detalle.html) (tarjetas separadas para *Incidencia reportada*, *Pedido vinculado*, *Involucrados*, *Resolución del caso* y *Horario de atención*), sin sub-cajas saturadas ni desplegables de ancho excesivo.
    - Registro automático de **Hora de entrada** al abrir el caso y **Hora de salida** dinámica con cálculo de duración, prioridad, acción aplicada y solución técnica compatible con `resolucion_admin` (`VARCHAR(500)`).
  - **Integración de `codigo_pedido` en Django Admin y Portal Web**:
    - En [`tienda/admin.py`](./tienda/admin.py) se configuró `codigo_pedido` en `PedidoAdmin` (`list_display`, `search_fields`, `readonly_fields`) y `get_codigo_pedido` (`@admin.display(description="Código de Pedido")`) más búsqueda `id_pedido__codigo_pedido` en `DenunciaAdmin`.
    - En [`tienda/views.py`](./tienda/views.py) y plantillas de pedidos y `Reporte` se habilitó la búsqueda inmediata por `codigo_pedido` (ej. `PED-A7K92M`, `PED-000002`) y su visualización prioritaria en tablas, encabezados y reportes en lugar del `id_pedido` numérico interno.
  - **Retiro del módulo de Tracking GPS del portal Django**:
    - Se retiraron las vistas, rutas, plantilla (`pedido_seguimiento.html`), botones de acceso y dependencia `pyjwt` en `minimarket-web`, delegando el seguimiento GPS en tiempo real al módulo de **FastAPI**.
  - **Paginación SQL uniforme de 25 registros por página ([`views.py`](./tienda/views.py), [`portal_list.html`](./templates/tienda/admin/portal_list.html), [`pedidos.html`](./templates/tienda/empleado/pedidos.html), [`inventario.html`](./templates/tienda/empleado/inventario.html), [`denuncias.html`](./templates/tienda/admin/denuncias.html))**:
    - Se implementó `PAGE_SIZE = 25` con paginación directa en base de datos (`Paginator`) en **Pedidos** (admin y empleado), **Productos** (admin y consulta empleado), **Categorías** (admin y consulta empleado), **Inventario** (admin y empleado), **Sucursales**, **Usuarios** y **Reporte**, mostrando rango actual (`Mostrando X–Y de Z · 25 por página`) y preservando filtros de búsqueda.
  - **Preparación lista para despliegue en VPS**:
    - Se agregaron `gunicorn` y `whitenoise` en [`requirements.txt`](./requirements.txt) con activación automática en [`core/settings.py`](./core/settings.py), soporte de cabeceras proxy HTTPS (`SECURE_PROXY_SSL_HEADER`) y orígenes CSRF para `64.236.138.169` y `minimarket-api.duckdns.org`.
    - Se incluyeron [`deploy_vps.sh`](./deploy_vps.sh), [`Dockerfile`](./Dockerfile), [`docker-compose.yml`](./docker-compose.yml) y el paquete listo para subir `minimarket-web-vps.zip` dentro de `minimarket-web/`.
  - Se eliminaron los atributos `style="..."` en línea en [`reportes.html`](./templates/tienda/admin/reportes.html), [`denuncia_detalle.html`](./templates/tienda/admin/denuncia_detalle.html) y [`pedido_detalle.html`](./templates/tienda/empleado/pedido_detalle.html), reemplazándolos por grillas responsivas de Tailwind, estado activo completo en subrutas de [`sidebar.html`](./templates/tienda/partials/sidebar.html), paginación en [`denuncias.html`](./templates/tienda/admin/denuncias.html) y *badges* de estado en [`portal_list.html`](./templates/tienda/admin/portal_list.html).

---

## 6. Resumen de Archivos Modificados y Creados

| Archivo | Acción | Propósito principal |
| :--- | :--- | :--- |
| `requirements.txt` | Modificado | Eliminación de paquetes no utilizados (`djangorestframework`, `Pillow`, `pyjwt`) y adición de `gunicorn`/`whitenoise` |
| `core/settings.py` | Modificado | Persistencia `CONN_MAX_AGE`, `MinimarketAuthBackend`, `WhiteNoiseMiddleware` y cabeceras VPS |
| `core/urls.py` | Modificado | Eliminación de configuración duplicada del Admin |
| `tienda/services.py` | **Nuevo** | Servicios transaccionales, bitácora de Soporte Técnico y backend de autenticación |
| `tienda/models.py` | Modificado | Permisos consistentes para rol `ADMIN` y limpieza de propiedades sin uso |
| `tienda/admin_site.py` | Modificado | Uso de `es_admin()` centralizado |
| `tienda/admin.py` | Modificado | Fusión de `save_model` duplicado, `list_select_related` y retiro de código muerto |
| `tienda/forms.py` | Modificado | Prevención de consultas N+1, `DenunciaResolucionForm` y ordenamiento en selectores |
| `tienda/views.py` | Modificado | Optimización SQL, paginación de 25 registros, helpers DRY y retiro de vistas de Tracking |
| `tienda/urls.py` | Modificado | Retiro de rutas de seguimiento GPS |
| `tienda/tests.py` | Modificado | Pruebas unitarias de paginación, Soporte Técnico, base unificada y Tailwind |
| `tienda/static/tienda/empleado.css` | Modificado | Limpieza de CSS muerto, `.portal-container` (`28px`), nuevos badges y soporte `textarea` |
| `templates/tienda/base_portal.html` | **Nuevo** | Layout base unificado con configuración de tema Tailwind MiniMarket |
| `templates/tienda/admin/*.html` | Modificados | Adaptación responsiva con Tailwind CSS, paginación de 25 y Soporte Técnico en tarjetas |
| `templates/tienda/empleado/*.html` | Modificados | Adaptación con Tailwind CSS, paginación de 25, login con marca y retiro de Tracking |
| `templates/tienda/partials/sidebar.html` | Modificado | Estado activo en subrutas, ícono unificado de `Métricas` y nombre `Reporte` |
| `templates/tienda/pedido_seguimiento.html` | **Eliminado** | Seguimiento GPS delegado al servicio de FastAPI |

