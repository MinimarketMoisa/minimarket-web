"""
Configuración del panel de administración nativo de Django para Minimarket.
El acceso al sitio está restringido globalmente a administradores mediante MinimarketAdminSite.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.db import transaction
from django.utils.html import format_html

from .models import (
    Categoria,
    Denuncia,
    DetallePedido,
    Direccion,
    Empleado,
    Inventario,
    Municipio,
    Pedido,
    PerfilRepartidor,
    Producto,
    Rol,
    Sucursal,
    Usuario,
)
from .services import cancelar_pedido

# Ocultar el modelo de grupos nativo de Django (los permisos se gestionan por Rol)
try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass


@admin.register(Rol)
class RolAdmin(admin.ModelAdmin):
    list_display = ("id_rol", "nombre")
    search_fields = ("nombre",)


@admin.register(Municipio)
class MunicipioAdmin(admin.ModelAdmin):
    list_display = ("id_municipio", "nombre")
    search_fields = ("nombre",)


@admin.register(Sucursal)
class SucursalAdmin(admin.ModelAdmin):
    list_display = ("id_sucursal", "nombre", "id_municipio", "activa", "telefono")
    list_select_related = ("id_municipio",)
    list_filter = ("activa", "id_municipio")
    search_fields = ("nombre", "direccion")
    list_editable = ("activa",)


@admin.register(Direccion)
class DireccionAdmin(admin.ModelAdmin):
    list_display = ("id_direccion", "id_cliente", "id_municipio", "detalle_direccion", "latitud", "longitud")
    list_select_related = ("id_cliente", "id_municipio")
    search_fields = ("detalle_direccion", "id_cliente__email")
    raw_id_fields = ("id_cliente", "id_municipio")


@admin.register(Empleado)
class EmpleadoAdmin(admin.ModelAdmin):
    list_display = ("id_empleado", "cargo", "id_sucursal")
    list_select_related = ("id_empleado", "id_sucursal")
    list_filter = ("id_sucursal", "cargo")
    raw_id_fields = ("id_empleado", "id_sucursal")


@admin.register(PerfilRepartidor)
class PerfilRepartidorAdmin(admin.ModelAdmin):
    list_display = ("id_repartidor", "estado_operativo", "placa_vehiculo", "ultima_actualizacion")
    list_select_related = ("id_repartidor",)
    list_filter = ("estado_operativo",)
    raw_id_fields = ("id_repartidor",)


@admin.register(Denuncia)
class DenunciaAdmin(admin.ModelAdmin):
    list_display = (
        "id_denuncia",
        "get_codigo_pedido",
        "id_cliente",
        "id_repartidor",
        "motivo",
        "estado",
        "fecha_creacion",
    )
    list_select_related = ("id_pedido", "id_cliente", "id_repartidor")
    list_filter = ("estado", "fecha_creacion")
    search_fields = (
        "id_pedido__codigo_pedido",
        "motivo",
        "descripcion",
        "resolucion_admin",
        "id_cliente__nombre_completo",
        "id_repartidor__nombre_completo",
    )
    readonly_fields = ("fecha_creacion",)

    @admin.display(description="Código de Pedido", ordering="id_pedido__codigo_pedido")
    def get_codigo_pedido(self, obj):
        if obj.id_pedido_id and obj.id_pedido and obj.id_pedido.codigo_pedido:
            return obj.id_pedido.codigo_pedido
        return f"#{obj.id_pedido_id}"


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    model = Usuario

    list_display = (
        "email",
        "nombre_completo",
        "id_rol",
        "activo",
        "email_verificado",
        "token_version",
        "intentos_fallidos",
        "is_staff",
    )
    list_select_related = ("id_rol",)
    list_filter = ("id_rol", "activo", "is_staff", "is_superuser", "email_verificado")
    search_fields = ("email", "nombre_completo")
    ordering = ("email",)
    filter_horizontal = ()
    readonly_fields = ("last_login",)

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if obj and request.user and obj.pk == request.user.pk:
            ro.append("id_rol")
        return tuple(ro)

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Información personal", {"fields": ("nombre_completo", "telefono", "id_rol")}),
        ("Estado de cuenta", {"fields": ("activo", "email_verificado")}),
        (
            "Seguridad de tokens y sesiones",
            {"fields": ("token_version", "intentos_fallidos", "bloqueado_hasta", "ultimo_correo_en")},
        ),
        ("Permisos de sistema", {"fields": ("is_staff", "is_superuser")}),
        ("Registro de actividad", {"fields": ("last_login",)}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "nombre_completo", "id_rol", "password1", "password2"),
            },
        ),
        (
            "Campos opcionales",
            {
                "classes": ("collapse",),
                "fields": ("telefono", "activo", "email_verificado", "is_staff", "is_superuser"),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        # Un administrador no puede cambiar su propio rol
        if change and request.user and obj.pk == request.user.pk and "id_rol" in form.changed_data:
            obj.id_rol = Usuario.objects.get(pk=obj.pk).id_rol

        # Sincronizar permisos internos de Django con el rol de negocio
        if obj.id_rol:
            rol_nombre = obj.id_rol.nombre
            if rol_nombre == "ADMIN":
                obj.is_staff = True
                obj.is_superuser = True
            elif rol_nombre == "EMPLEADO":
                obj.is_staff = True
                obj.is_superuser = False
            elif rol_nombre in ("CLIENTE", "REPARTIDOR"):
                obj.is_staff = False
                obj.is_superuser = False

        # Invalidar JWT activos al desactivar cuenta (set_password ya incrementa en cambio de clave)
        if change and "activo" in form.changed_data and not obj.activo and "password" not in form.changed_data:
            obj.token_version = (obj.token_version or 0) + 1

        super().save_model(request, obj, form, change)


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ("id_categoria", "nombre", "descripcion")
    search_fields = ("nombre",)


class InventarioInline(admin.TabularInline):
    model = Inventario
    extra = 0
    fields = ("id_sucursal", "stock_disponible", "fecha_vencimiento")
    raw_id_fields = ("id_sucursal",)


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ("id_producto", "nombre", "id_categoria", "precio_base", "activo", "imagen_preview")
    list_select_related = ("id_categoria",)
    list_filter = ("activo", "id_categoria")
    search_fields = ("nombre", "codigo_barras")
    list_editable = ("activo", "precio_base")
    inlines = [InventarioInline]

    @admin.display(description="Vista previa")
    def imagen_preview(self, obj):
        if obj.imagen_url:
            return format_html('<img src="{}" height="40" style="border-radius:4px"/>', obj.imagen_url)
        return "-"


@admin.register(Inventario)
class InventarioAdmin(admin.ModelAdmin):
    list_display = ("id_inventario", "id_producto", "id_sucursal", "stock_disponible", "fecha_vencimiento")
    list_select_related = ("id_producto", "id_sucursal")
    list_filter = ("id_sucursal",)
    search_fields = ("id_producto__nombre",)
    raw_id_fields = ("id_sucursal", "id_producto")


class DetallePedidoInline(admin.TabularInline):
    model = DetallePedido
    extra = 0
    readonly_fields = ("subtotal",)
    fields = ("id_producto", "cantidad", "precio_unitario", "subtotal")
    raw_id_fields = ("id_producto",)


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = (
        "codigo_pedido",
        "id_cliente",
        "id_sucursal",
        "estado",
        "tipo_entrega",
        "metodo_pago",
        "estado_pago",
        "total",
        "fecha_creacion",
    )
    list_select_related = ("id_cliente", "id_sucursal", "id_repartidor__id_repartidor")
    list_filter = ("estado", "tipo_entrega", "metodo_pago", "estado_pago", "canal")
    search_fields = (
        "codigo_pedido",
        "id_pedido",
        "id_cliente__email",
        "id_cliente__nombre_completo",
    )
    readonly_fields = ("codigo_pedido", "fecha_creacion", "subtotal_calculado")
    raw_id_fields = ("id_cliente", "id_sucursal", "id_repartidor")
    inlines = [DetallePedidoInline]
    date_hierarchy = "fecha_creacion"

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if obj and obj.estado == "EN_RUTA":
            ro.extend(["estado", "id_repartidor"])
        return tuple(ro)

    @admin.display(description="Total")
    def subtotal_calculado(self, obj):
        return obj.total

    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            pedido_anterior = (
                Pedido.objects.select_for_update().filter(pk=obj.pk).first()
                if change
                else None
            )
            if change and pedido_anterior and pedido_anterior.estado == "EN_RUTA":
                if obj.estado != "EN_RUTA":
                    obj.estado = "EN_RUTA"
                if obj.id_repartidor != pedido_anterior.id_repartidor:
                    obj.id_repartidor = pedido_anterior.id_repartidor
            se_cancela = (
                pedido_anterior is not None
                and pedido_anterior.estado != "CANCELADO"
                and obj.estado == "CANCELADO"
            )
            if se_cancela:
                cancelar_pedido(obj, guardar_pedido=False)
            super().save_model(request, obj, form, change)


@admin.register(DetallePedido)
class DetallePedidoAdmin(admin.ModelAdmin):
    list_display = ("id_detalle", "id_pedido", "id_producto", "cantidad", "precio_unitario", "subtotal")
    list_select_related = ("id_pedido", "id_producto")
    raw_id_fields = ("id_pedido", "id_producto")
    readonly_fields = ("subtotal",)
