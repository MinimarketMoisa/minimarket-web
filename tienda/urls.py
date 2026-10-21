"""URLs de la app tienda (rutas API DRF)."""

from django.urls import path
from . import views

app_name = "tienda"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("empleados/login/", views.empleado_login, name="empleado-login"),
    path("empleados/logout/", views.empleado_logout, name="empleado-logout"),
    path("administracion/", views.admin_panel, name="admin-panel"),
    path("administracion/pedidos/", views.pedidos_admin, name="admin-pedidos"),
    path(
        "administracion/pedidos/<int:id_pedido>/",
        views.pedido_admin_detalle,
        name="admin-pedido-detalle",
    ),
    path("administracion/sucursales/", views.sucursales_admin, name="admin-sucursales"),
    path(
        "administracion/sucursales/crear/",
        views.sucursal_admin_form,
        name="admin-sucursal-crear",
    ),
    path(
        "administracion/sucursales/<int:id_sucursal>/editar/",
        views.sucursal_admin_form,
        name="admin-sucursal-editar",
    ),
    path(
        "administracion/sucursales/<int:id_sucursal>/estado/",
        views.sucursal_admin_estado,
        name="admin-sucursal-estado",
    ),
    path("administracion/productos/", views.productos_admin, name="admin-productos"),
    path(
        "administracion/productos/crear/",
        views.producto_admin_form,
        name="admin-producto-crear",
    ),
    path(
        "administracion/productos/<int:id_producto>/editar/",
        views.producto_admin_form,
        name="admin-producto-editar",
    ),
    path(
        "administracion/productos/<int:id_producto>/estado/",
        views.producto_admin_estado,
        name="admin-producto-estado",
    ),
    path("administracion/categorias/", views.categorias_admin, name="admin-categorias"),
    path(
        "administracion/categorias/crear/",
        views.categoria_admin_form,
        name="admin-categoria-crear",
    ),
    path(
        "administracion/categorias/<int:id_categoria>/editar/",
        views.categoria_admin_form,
        name="admin-categoria-editar",
    ),
    path(
        "administracion/categorias/<int:id_categoria>/eliminar/",
        views.categoria_admin_eliminar,
        name="admin-categoria-eliminar",
    ),
    path("administracion/inventario/", views.inventario_admin, name="admin-inventario"),
    path(
        "administracion/inventario/crear/",
        views.inventario_admin_form,
        name="admin-inventario-crear",
    ),
    path(
        "administracion/inventario/<int:id_inventario>/editar/",
        views.inventario_admin_form,
        name="admin-inventario-editar",
    ),
    path(
        "administracion/inventario/<int:id_inventario>/eliminar/",
        views.inventario_admin_eliminar,
        name="admin-inventario-eliminar",
    ),
    path("administracion/usuarios/", views.usuarios_admin, name="admin-usuarios"),
    path(
        "administracion/usuarios/crear/",
        views.usuario_admin_form,
        name="admin-usuario-crear",
    ),
    path(
        "administracion/usuarios/<int:id_usuario>/editar/",
        views.usuario_admin_form,
        name="admin-usuario-editar",
    ),
    path(
        "administracion/usuarios/<int:id_usuario>/estado/",
        views.usuario_admin_estado,
        name="admin-usuario-estado",
    ),
    path("empleados/", views.empleado_panel, name="empleado-panel"),
    path(
        "empleados/catalogo/productos/",
        views.catalogo_productos_empleado,
        name="empleado-productos",
    ),
    path(
        "empleados/catalogo/categorias/",
        views.catalogo_categorias_empleado,
        name="empleado-categorias",
    ),
    path("empleados/pedidos/", views.pedidos_empleado, name="pedidos"),
    path(
        "empleados/pedidos/<int:id_pedido>/",
        views.detalle_pedido_empleado,
        name="pedido-detalle",
    ),
    path(
        "empleados/pedidos/<int:id_pedido>/accion/",
        views.accion_pedido,
        name="pedido-accion",
    ),
    path("empleados/inventario/", views.inventario_empleado, name="inventario"),
    path(
        "empleados/inventario/<int:id_producto>/",
        views.editar_inventario,
        name="inventario-editar",
    ),
]
