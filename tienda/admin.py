"""
Django Admin para Minimarket.
Todos los modelos registrados leen/escriben la misma BD que FastAPI.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html

from .models import (
    Rol, Municipio, Usuario, Sucursal, Direccion,
    Empleado, PerfilRepartidor,
    Categoria, Producto, Inventario,
    Pedido, DetallePedido,
)


# ─── Rol ─────────────────────────────────────────────────────────────────────
@admin.register(Rol)
class RolAdmin(admin.ModelAdmin):
    list_display  = ("id_rol", "nombre")
    search_fields = ("nombre",)


# ─── Municipio ───────────────────────────────────────────────────────────────
@admin.register(Municipio)
class MunicipioAdmin(admin.ModelAdmin):
    list_display  = ("id_municipio", "nombre")
    search_fields = ("nombre",)


# ─── Usuario ──────────────────────────────────────────────────────────────────
@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    model = Usuario

    list_display  = ("email", "nombre_completo", "id_rol", "activo", "email_verificado", "is_staff")
    list_filter   = ("id_rol", "activo", "is_staff", "is_superuser", "email_verificado")
    search_fields = ("email", "nombre_completo")
    ordering      = ("email",)
    filter_horizontal = ()

    # Campos al EDITAR un usuario existente
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Informacion personal", {"fields": ("nombre_completo", "telefono", "id_rol")}),
        ("Estado", {"fields": ("activo", "email_verificado")}),
        ("Permisos", {"fields": ("is_staff", "is_superuser")}),
        ("Actividad", {"fields": ("last_login",)}),
    )

    # Campos al CREAR un usuario nuevo
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "nombre_completo", "id_rol", "password1", "password2"),
        }),
        ("Opciones", {
            "classes": ("collapse",),
            "fields": ("telefono", "activo", "email_verificado", "is_staff", "is_superuser"),
        }),
    )

    readonly_fields = ("last_login",)


# ─── Sucursal ─────────────────────────────────────────────────────────────────
@admin.register(Sucursal)
class SucursalAdmin(admin.ModelAdmin):
    list_display  = ("id_sucursal", "nombre", "id_municipio", "activa", "telefono")
    list_filter   = ("activa", "id_municipio")
    search_fields = ("nombre", "direccion")
    list_editable = ("activa",)


# ─── Direccion ───────────────────────────────────────────────────────────────
@admin.register(Direccion)
class DireccionAdmin(admin.ModelAdmin):
    list_display  = ("id_direccion", "id_cliente", "id_municipio", "detalle_direccion")
    search_fields = ("detalle_direccion", "id_cliente__email")
    raw_id_fields = ("id_cliente", "id_municipio")


# ─── Empleado ─────────────────────────────────────────────────────────────────
@admin.register(Empleado)
class EmpleadoAdmin(admin.ModelAdmin):
    list_display  = ("id_empleado", "cargo", "id_sucursal")
    list_filter   = ("id_sucursal", "cargo")
    raw_id_fields = ("id_empleado", "id_sucursal")


# ─── PerfilRepartidor ─────────────────────────────────────────────────────────
@admin.register(PerfilRepartidor)
class PerfilRepartidorAdmin(admin.ModelAdmin):
    list_display  = ("id_repartidor", "estado_operativo", "placa_vehiculo", "ultima_actualizacion")
    list_filter   = ("estado_operativo",)
    raw_id_fields = ("id_repartidor",)


# ─── Categoria ───────────────────────────────────────────────────────────────
@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display  = ("id_categoria", "nombre", "descripcion")
    search_fields = ("nombre",)


# ─── Producto ─────────────────────────────────────────────────────────────────
class InventarioInline(admin.TabularInline):
    model       = Inventario
    extra       = 0
    fields      = ("id_sucursal", "stock_disponible", "fecha_vencimiento")
    raw_id_fields = ("id_sucursal",)


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display  = ("id_producto", "nombre", "id_categoria", "precio_base", "activo", "imagen_preview")
    list_filter   = ("activo", "id_categoria")
    search_fields = ("nombre", "codigo_barras")
    list_editable = ("activo", "precio_base")
    inlines       = [InventarioInline]

    def imagen_preview(self, obj):
        if obj.imagen_url:
            return format_html('<img src="{}" height="40" style="border-radius:4px"/>', obj.imagen_url)
        return "—"
    imagen_preview.short_description = "Imagen"


# ─── Inventario ───────────────────────────────────────────────────────────────
@admin.register(Inventario)
class InventarioAdmin(admin.ModelAdmin):
    list_display  = ("id_inventario", "id_producto", "id_sucursal", "stock_disponible", "fecha_vencimiento")
    list_filter   = ("id_sucursal",)
    search_fields = ("id_producto__nombre",)
    raw_id_fields = ("id_sucursal", "id_producto")


# ─── Pedidos ──────────────────────────────────────────────────────────────────
class DetallePedidoInline(admin.TabularInline):
    model         = DetallePedido
    extra         = 0
    readonly_fields = ("subtotal",)
    fields        = ("id_producto", "cantidad", "precio_unitario", "subtotal")
    raw_id_fields = ("id_producto",)


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display  = (
        "id_pedido", "id_cliente", "id_sucursal", "estado",
        "tipo_entrega", "metodo_pago", "estado_pago", "total", "fecha_creacion",
    )
    list_filter   = ("estado", "estado_pago", "tipo_entrega", "canal", "metodo_pago")
    search_fields = ("id_cliente__email", "id_cliente__nombre_completo")
    readonly_fields = ("fecha_creacion", "subtotal_calculado")
    raw_id_fields = ("id_cliente", "id_sucursal", "id_repartidor")
    inlines       = [DetallePedidoInline]
    date_hierarchy = "fecha_creacion"

    def subtotal_calculado(self, obj):
        return obj.total
    subtotal_calculado.short_description = "Total"


@admin.register(DetallePedido)
class DetallePedidoAdmin(admin.ModelAdmin):
    list_display  = ("id_detalle", "id_pedido", "id_producto", "cantidad", "precio_unitario", "subtotal")
    raw_id_fields = ("id_pedido", "id_producto")
    readonly_fields = ("subtotal",)
