"""
Configuracion del panel de administracion para Minimarket.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.db import transaction
from django.utils.html import format_html

from .models import (
    Rol, Municipio, Usuario, Sucursal, Direccion,
    Empleado, PerfilRepartidor,
    Categoria, Producto, Inventario,
    Pedido, DetallePedido,
)

# Ocultar el modelo de grupos nativo de Django
try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass


# Funciones auxiliares de autorizacion
def es_admin(user):
    if not user.is_authenticated:
        return False
    return bool(user.is_superuser or (user.id_rol and user.id_rol.nombre == "ADMIN"))


def es_empleado(user):
    if not user.is_authenticated:
        return False
    return bool(not user.is_superuser and user.id_rol and user.id_rol.nombre == "EMPLEADO")


def get_sucursal_empleado(user):
    if not user.is_authenticated:
        return None
    try:
        return user.perfil_empleado.id_sucursal
    except Exception:
        emp = Empleado.objects.filter(id_empleado=user.id_usuario).select_related("id_sucursal").first()
        return emp.id_sucursal if emp else None


# Mixins de permisos
class SoloAdminMixin:
    def has_module_permission(self, request):
        return es_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return es_admin(request.user)

    def has_add_permission(self, request):
        return es_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return es_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return es_admin(request.user)


class CatalogoAdminMixin:
    def has_module_permission(self, request):
        return es_admin(request.user) or es_empleado(request.user)

    def has_view_permission(self, request, obj=None):
        return es_admin(request.user) or es_empleado(request.user)

    def has_add_permission(self, request):
        return es_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return es_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return es_admin(request.user)


# Modelos administrativos
@admin.register(Rol)
class RolAdmin(SoloAdminMixin, admin.ModelAdmin):
    list_display = ("id_rol", "nombre")
    search_fields = ("nombre",)


@admin.register(Municipio)
class MunicipioAdmin(SoloAdminMixin, admin.ModelAdmin):
    list_display = ("id_municipio", "nombre")
    search_fields = ("nombre",)


@admin.register(Sucursal)
class SucursalAdmin(SoloAdminMixin, admin.ModelAdmin):
    list_display = ("id_sucursal", "nombre", "id_municipio", "activa", "telefono")
    list_filter = ("activa", "id_municipio")
    search_fields = ("nombre", "direccion")
    list_editable = ("activa",)


@admin.register(Direccion)
class DireccionAdmin(SoloAdminMixin, admin.ModelAdmin):
    list_display = ("id_direccion", "id_cliente", "id_municipio", "detalle_direccion")
    search_fields = ("detalle_direccion", "id_cliente__email")
    raw_id_fields = ("id_cliente", "id_municipio")


@admin.register(Empleado)
class EmpleadoAdmin(SoloAdminMixin, admin.ModelAdmin):
    list_display = ("id_empleado", "cargo", "id_sucursal")
    list_filter = ("id_sucursal", "cargo")
    raw_id_fields = ("id_empleado", "id_sucursal")


@admin.register(PerfilRepartidor)
class PerfilRepartidorAdmin(SoloAdminMixin, admin.ModelAdmin):
    list_display = ("id_repartidor", "estado_operativo", "placa_vehiculo", "ultima_actualizacion")
    list_filter = ("estado_operativo",)
    raw_id_fields = ("id_repartidor",)


@admin.register(Usuario)
class UsuarioAdmin(SoloAdminMixin, UserAdmin):
    model = Usuario

    list_display = ("email", "nombre_completo", "id_rol", "activo", "email_verificado", "is_staff")
    list_filter = ("id_rol", "activo", "is_staff", "is_superuser", "email_verificado")
    search_fields = ("email", "nombre_completo")
    ordering = ("email",)
    filter_horizontal = ()

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Informacion personal", {"fields": ("nombre_completo", "telefono", "id_rol")}),
        ("Estado de cuenta", {"fields": ("activo", "email_verificado")}),
        ("Permisos de sistema", {"fields": ("is_staff", "is_superuser")}),
        ("Registro de actividad", {"fields": ("last_login",)}),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "nombre_completo", "id_rol", "password1", "password2"),
        }),
        ("Campos opcionales", {
            "classes": ("collapse",),
            "fields": ("telefono", "activo", "email_verificado", "is_staff", "is_superuser"),
        }),
    )

    readonly_fields = ("last_login",)

    def save_model(self, request, obj, form, change):
        if obj.id_rol and obj.id_rol.nombre == "ADMIN":
            obj.is_staff = True
            obj.is_superuser = True
        elif obj.id_rol and obj.id_rol.nombre == "EMPLEADO":
            obj.is_staff = True
            obj.is_superuser = False
        elif obj.id_rol and obj.id_rol.nombre in ("CLIENTE", "REPARTIDOR"):
            obj.is_staff = False
            obj.is_superuser = False
        super().save_model(request, obj, form, change)


# Catalogo
@admin.register(Categoria)
class CategoriaAdmin(CatalogoAdminMixin, admin.ModelAdmin):
    list_display = ("id_categoria", "nombre", "descripcion")
    search_fields = ("nombre",)


class InventarioInline(admin.TabularInline):
    model = Inventario
    extra = 0
    fields = ("id_sucursal", "stock_disponible", "fecha_vencimiento")
    raw_id_fields = ("id_sucursal",)

    def has_add_permission(self, request, obj=None):
        return es_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return es_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return es_admin(request.user)


@admin.register(Producto)
class ProductoAdmin(CatalogoAdminMixin, admin.ModelAdmin):
    list_display = ("id_producto", "nombre", "id_categoria", "precio_base", "activo", "imagen_preview")
    list_filter = ("activo", "id_categoria")
    search_fields = ("nombre", "codigo_barras")
    list_editable = ("activo", "precio_base")
    inlines = [InventarioInline]

    def imagen_preview(self, obj):
        if obj.imagen_url:
            return format_html('<img src="{}" height="40" style="border-radius:4px"/>', obj.imagen_url)
        return "-"
    imagen_preview.short_description = "Vista previa"

    def get_list_editable(self, request):
        if es_empleado(request.user):
            return ()
        return super().get_list_editable(request)


# Inventario
@admin.register(Inventario)
class InventarioAdmin(admin.ModelAdmin):
    list_display = ("id_inventario", "id_producto", "id_sucursal", "stock_disponible", "fecha_vencimiento")
    list_filter = ("id_sucursal",)
    search_fields = ("id_producto__nombre",)
    raw_id_fields = ("id_sucursal", "id_producto")

    def has_module_permission(self, request):
        return es_admin(request.user) or es_empleado(request.user)

    def has_view_permission(self, request, obj=None):
        return es_admin(request.user) or es_empleado(request.user)

    def has_add_permission(self, request):
        return es_admin(request.user) or (es_empleado(request.user) and get_sucursal_empleado(request.user) is not None)

    def has_change_permission(self, request, obj=None):
        return es_admin(request.user) or (es_empleado(request.user) and get_sucursal_empleado(request.user) is not None)

    def has_delete_permission(self, request, obj=None):
        return es_admin(request.user)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if es_admin(request.user):
            return qs
        if es_empleado(request.user):
            sucursal = get_sucursal_empleado(request.user)
            if sucursal:
                return qs.filter(id_sucursal=sucursal)
            return qs.none()
        return qs.none()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "id_sucursal" and es_empleado(request.user):
            sucursal = get_sucursal_empleado(request.user)
            kwargs["queryset"] = Sucursal.objects.filter(id_sucursal=sucursal.id_sucursal if sucursal else 0)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if es_empleado(request.user):
            sucursal = get_sucursal_empleado(request.user)
            if sucursal:
                obj.id_sucursal = sucursal
        super().save_model(request, obj, form, change)


# Pedidos
class DetallePedidoInline(admin.TabularInline):
    model = DetallePedido
    extra = 0
    readonly_fields = ("subtotal",)
    fields = ("id_producto", "cantidad", "precio_unitario", "subtotal")
    raw_id_fields = ("id_producto",)

    def has_add_permission(self, request, obj=None):
        return es_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return es_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return es_admin(request.user)


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = (
        "id_pedido", "id_cliente", "id_sucursal", "estado",
        "tipo_entrega", "metodo_pago", "estado_pago", "total", "fecha_creacion",
    )
    list_filter = ("estado", "estado_pago", "tipo_entrega", "canal", "metodo_pago")
    search_fields = ("id_cliente__email", "id_cliente__nombre_completo")
    readonly_fields = ("fecha_creacion", "subtotal_calculado")
    raw_id_fields = ("id_cliente", "id_sucursal", "id_repartidor")
    inlines = [DetallePedidoInline]
    date_hierarchy = "fecha_creacion"

    def subtotal_calculado(self, obj):
        return obj.total
    subtotal_calculado.short_description = "Total"

    def has_module_permission(self, request):
        return es_admin(request.user) or es_empleado(request.user)

    def has_view_permission(self, request, obj=None):
        return es_admin(request.user) or es_empleado(request.user)

    def has_add_permission(self, request):
        return es_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return es_admin(request.user) or es_empleado(request.user)

    def has_delete_permission(self, request, obj=None):
        return es_admin(request.user)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if es_admin(request.user):
            return qs
        if es_empleado(request.user):
            sucursal = get_sucursal_empleado(request.user)
            if sucursal:
                return qs.filter(
                    id_sucursal=sucursal,
                    estado__in=("PENDIENTE", "PREPARADO"),
                )
            return qs.none()
        return qs.none()

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if es_empleado(request.user):
            readonly.extend(["id_cliente", "id_sucursal", "tipo_entrega", "metodo_pago", "total", "canal"])
            if obj is None or obj.metodo_pago != "TRANSFERENCIA" or obj.estado_pago == "PAGADO":
                readonly.append("estado_pago")
        return readonly

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if es_empleado(request.user) and obj is not None:
            estados_permitidos = {
                "PENDIENTE": {"PENDIENTE", "PREPARADO", "CANCELADO"},
                "PREPARADO": {"PREPARADO", "CANCELADO"},
            }.get(obj.estado, {obj.estado})
            todas_las_etiquetas = dict(Pedido.ESTADO_CHOICES)
            form.base_fields["estado"].choices = [
                (estado, todas_las_etiquetas[estado])
                for estado in estados_permitidos
            ]
            if obj.metodo_pago == "TRANSFERENCIA" and obj.estado_pago == "PENDIENTE":
                form.base_fields["estado_pago"].choices = [
                    ("PENDIENTE", "Pendiente"),
                    ("PAGADO", "Pagado"),
                ]
        return form

    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            pedido_anterior = None
            if change:
                pedido_anterior = (
                    Pedido.objects.select_for_update()
                    .filter(pk=obj.pk)
                    .first()
                )
            cancelar = (
                pedido_anterior is not None
                and pedido_anterior.estado != "CANCELADO"
                and obj.estado == "CANCELADO"
            )
            super().save_model(request, obj, form, change)
            if cancelar:
                detalles = DetallePedido.objects.filter(
                    id_pedido=obj
                ).values_list("id_producto_id", "cantidad")
                for id_producto, cantidad in detalles:
                    inventario = (
                        Inventario.objects.select_for_update()
                        .filter(
                            id_sucursal=pedido_anterior.id_sucursal_id,
                            id_producto=id_producto,
                        )
                        .first()
                    )
                    if inventario is not None:
                        inventario.stock_disponible += cantidad
                        inventario.save(update_fields=["stock_disponible"])


@admin.register(DetallePedido)
class DetallePedidoAdmin(admin.ModelAdmin):
    list_display = ("id_detalle", "id_pedido", "id_producto", "cantidad", "precio_unitario", "subtotal")
    raw_id_fields = ("id_pedido", "id_producto")
    readonly_fields = ("subtotal",)

    def has_module_permission(self, request):
        return es_admin(request.user)
