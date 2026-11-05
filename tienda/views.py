import calendar
from decimal import Decimal
from functools import wraps
import json

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import (
    Count,
    DateField,
    DecimalField,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, TruncDate
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    CategoriaPortalForm,
    DenunciaResolucionForm,
    EmpleadoLoginForm,
    InventarioForm,
    InventarioPortalForm,
    PedidoPortalForm,
    ProductoPortalForm,
    SucursalPortalForm,
    UsuarioPortalForm,
)
from .models import (
    Categoria,
    Denuncia,
    DetallePedido,
    Inventario,
    Pedido,
    PerfilRepartidor,
    Producto,
    Sucursal,
    Usuario,
)
from .services import (
    ESTADOS_GESTIONABLES,
    actualizar_repartidor_pedido,
    cancelar_pedido,
    direccion_pedido,
    es_admin,
    es_empleado,
    es_personal,
    liberar_repartidor_si_sin_pedidos,
    obtener_sucursal_empleado,
    parsear_resolucion_soporte,
)

MESES_ES = (
    "",
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
)


# ─── Decoradores y helpers comunes ───────────────────────────────────────────

def empleado_required(view_func):
    @wraps(view_func)
    @login_required(login_url="tienda:empleado-login")
    def wrapper(request: HttpRequest, *args, **kwargs):
        if not es_empleado(request.user):
            return HttpResponseForbidden("Esta sección está disponible solo para empleados.")
        sucursal = obtener_sucursal_empleado(request.user)
        if sucursal is None:
            return HttpResponseForbidden("Tu cuenta no tiene una sucursal asignada.")
        request.sucursal_empleado = sucursal
        return view_func(request, *args, **kwargs)

    return wrapper


def admin_required(view_func):
    @wraps(view_func)
    @login_required(login_url="tienda:empleado-login")
    def wrapper(request: HttpRequest, *args, **kwargs):
        if not es_admin(request.user):
            return HttpResponseForbidden("Esta sección está disponible solo para administradores.")
        return view_func(request, *args, **kwargs)

    return wrapper


def _badge(texto: str, variante: str = ""):
    clase = f"badge {variante}".strip()
    return format_html('<span class="{}">{}</span>', clase, texto)


def _productos_con_inventario(sucursal):
    existencia = Inventario.objects.filter(
        id_sucursal=sucursal,
        id_producto=OuterRef("pk"),
    )
    return Producto.objects.filter(activo=True).annotate(
        stock_disponible=Coalesce(
            Subquery(existencia.values("stock_disponible")[:1]),
            Value(0),
            output_field=IntegerField(),
        ),
        fecha_vencimiento=Subquery(
            existencia.values("fecha_vencimiento")[:1],
            output_field=DateField(),
        ),
    )


def _filtro_texto(queryset, request: HttpRequest, *fields: str):
    busqueda = request.GET.get("q", "").strip()
    if busqueda:
        filtros = Q()
        for field in fields:
            filtros |= Q(**{f"{field}__icontains": busqueda})
        queryset = queryset.filter(filtros)
    return queryset


PAGE_SIZE = 25


def _portal_list(
    request: HttpRequest,
    *,
    title,
    description,
    columns,
    items=None,
    queryset=None,
    row_builder=None,
    create_url=None,
    per_page: int = PAGE_SIZE,
):
    if queryset is not None and row_builder is not None:
        page_obj = Paginator(queryset, per_page).get_page(request.GET.get("page"))
        page_items = [row_builder(obj) for obj in page_obj]
    else:
        page_obj = Paginator(items or [], per_page).get_page(request.GET.get("page"))
        page_items = list(page_obj.object_list)

    params = request.GET.copy()
    params.pop("page", None)

    return render(
        request,
        "tienda/admin/portal_list.html",
        {
            "title": title,
            "description": description,
            "columns": columns,
            "items": page_items,
            "page_obj": page_obj,
            "total_registros": page_obj.paginator.count,
            "query_base": params.urlencode(),
            "create_url": create_url,
            "busqueda": request.GET.get("q", "").strip(),
            "base_template": (
                "tienda/admin/base.html"
                if es_admin(request.user)
                else "tienda/empleado/base.html"
            ),
        },
    )


def _portal_form(request: HttpRequest, *, title, description, form, cancel_url, submit_label):
    return render(
        request,
        "tienda/admin/portal_form.html",
        {
            "title": title,
            "description": description,
            "form": form,
            "cancel_url": cancel_url,
            "submit_label": submit_label,
            "base_template": "tienda/admin/base.html",
        },
    )


def _guardar_portal_model_form(
    request: HttpRequest,
    *,
    model,
    pk,
    form_class,
    redirect_name: str,
    success_msg: str,
    create_title: str,
    edit_title: str,
    submit_label: str,
    description: str = "",
) -> HttpResponse:
    instance = get_object_or_404(model, pk=pk) if pk else None
    form = form_class(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, success_msg)
        return redirect(redirect_name)
    return _portal_form(
        request,
        title=edit_title if instance else create_title,
        description=description,
        form=form,
        cancel_url=reverse(redirect_name),
        submit_label=submit_label,
    )


# ─── Autenticación y redirección inicial ─────────────────────────────────────

def inicio(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        if es_admin(request.user):
            return redirect("tienda:admin-panel")
        if es_empleado(request.user):
            return redirect("tienda:empleado-panel")
    return redirect("tienda:empleado-login")


@require_http_methods(["GET", "POST"])
def empleado_login(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        if es_admin(request.user):
            return redirect("tienda:admin-panel")
        if es_empleado(request.user):
            return redirect("tienda:empleado-panel")
        return HttpResponseForbidden("Este acceso es exclusivo para empleados y administradores.")

    form = EmpleadoLoginForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        usuario = form.get_user()
        if not es_personal(usuario):
            form.add_error(None, "Esta cuenta no tiene acceso al portal de personal.")
        else:
            login(request, usuario)
            return redirect("tienda:admin-panel" if es_admin(usuario) else "tienda:empleado-panel")
    return render(request, "tienda/empleado/login.html", {"form": form})


@require_POST
def empleado_logout(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect("tienda:empleado-login")
    if not es_personal(request.user):
        return HttpResponseForbidden("Esta acción está disponible solo para empleados y administradores.")
    logout(request)
    return redirect("tienda:empleado-login")


# ─── Administración Global ───────────────────────────────────────────────────

@admin_required
def admin_panel(request: HttpRequest) -> HttpResponse:
    hoy = timezone.localdate()
    resumen = Pedido.objects.aggregate(
        total_pedidos=Count("id_pedido"),
        pedidos_hoy=Count("id_pedido", filter=Q(fecha_creacion__date=hoy)),
        pendientes=Count("id_pedido", filter=Q(estado="PENDIENTE")),
        preparados=Count("id_pedido", filter=Q(estado="PREPARADO")),
        en_ruta=Count("id_pedido", filter=Q(estado="EN_RUTA")),
        pagos_pendientes=Count("id_pedido", filter=Q(estado_pago="PENDIENTE")),
        ventas_entregadas=Coalesce(
            Sum("total", filter=Q(estado="ENTREGADO")),
            Decimal("0.00"),
            output_field=DecimalField(),
        ),
    )
    sucursales = list(
        Sucursal.objects.select_related("id_municipio")
        .annotate(
            total_pedidos=Count("pedidos"),
            pedidos_pendientes=Count("pedidos", filter=Q(pedidos__estado="PENDIENTE")),
            pedidos_preparados=Count("pedidos", filter=Q(pedidos__estado="PREPARADO")),
            pedidos_en_ruta=Count("pedidos", filter=Q(pedidos__estado="EN_RUTA")),
            ventas_entregadas=Coalesce(
                Sum("pedidos__total", filter=Q(pedidos__estado="ENTREGADO")),
                Decimal("0.00"),
                output_field=DecimalField(),
            ),
        )
        .order_by("nombre")
    )
    return render(
        request,
        "tienda/admin/dashboard.html",
        {
            **resumen,
            "sucursales": sucursales,
            "inventario_bajo": Inventario.objects.filter(stock_disponible__lte=5).count(),
            "repartidores_libres": PerfilRepartidor.objects.filter(
                estado_operativo="LIBRE",
                id_repartidor__activo=True,
            ).count(),
            "hoy": hoy,
        },
    )


@admin_required
def pedidos_admin(request: HttpRequest) -> HttpResponse:
    consulta = Pedido.objects.select_related("id_cliente", "id_sucursal")
    estado = request.GET.get("estado", "")
    estados_validos = {choice[0] for choice in Pedido.ESTADO_CHOICES}
    if estado in estados_validos:
        consulta = consulta.filter(estado=estado)
    busqueda = request.GET.get("q", "").strip()
    if busqueda:
        filtros = (
            Q(codigo_pedido__icontains=busqueda)
            | Q(id_cliente__nombre_completo__icontains=busqueda)
        )
        if busqueda.isdecimal():
            filtros |= Q(id_pedido=int(busqueda))
        consulta = consulta.filter(filtros)

    return _portal_list(
        request,
        title="Pedidos",
        description="Historial completo. Busca por código de pedido (ej. PED-000002) o nombre del cliente.",
        columns=("Código de Pedido", "Cliente", "Sucursal", "Estado", "Total", "Creado"),
        queryset=consulta.order_by("-fecha_creacion", "-id_pedido"),
        row_builder=lambda pedido: {
            "cells": (
                pedido.codigo_pedido or f"#{pedido.id_pedido}",
                pedido.id_cliente.nombre_completo,
                pedido.id_sucursal.nombre,
                _badge(pedido.get_estado_display(), pedido.estado.lower()),
                f"$ {pedido.total:.2f}",
                pedido.fecha_creacion,
            ),
            "url": reverse("tienda:admin-pedido-detalle", args=[pedido.pk]),
            "label": "Ver / editar",
        },
    )


@admin_required
@require_http_methods(["GET", "POST"])
def pedido_admin_detalle(request: HttpRequest, id_pedido: int) -> HttpResponse:
    pedido = get_object_or_404(
        Pedido.objects.select_related(
            "id_cliente", "id_sucursal", "id_repartidor__id_repartidor"
        ).prefetch_related("detalles__id_producto"),
        pk=id_pedido,
    )
    form = PedidoPortalForm(request.POST or None, pedido=pedido)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            pedido_lock = Pedido.objects.select_for_update().get(pk=id_pedido)
            estado_anterior = pedido_lock.estado
            nuevo_estado = form.cleaned_data["estado"]
            nuevo_estado_pago = form.cleaned_data["estado_pago"]
            nuevo_repartidor = form.cleaned_data["id_repartidor"]

            if nuevo_estado == "CANCELADO" and estado_anterior == "ENTREGADO":
                messages.error(request, "Un pedido entregado no se puede cancelar desde el portal.")
                return redirect("tienda:admin-pedido-detalle", id_pedido=pedido_lock.pk)

            try:
                if nuevo_estado == "CANCELADO":
                    if estado_anterior != "CANCELADO":
                        cancelar_pedido(pedido_lock, guardar_pedido=False)
                    pedido_lock.estado_pago = nuevo_estado_pago
                    pedido_lock.save(update_fields=["estado", "estado_pago", "id_repartidor"])
                else:
                    actualizar_repartidor_pedido(pedido_lock, nuevo_repartidor)
                    pedido_lock.estado = nuevo_estado
                    pedido_lock.estado_pago = nuevo_estado_pago
                    pedido_lock.save(update_fields=["estado", "estado_pago", "id_repartidor"])
                    if nuevo_estado == "ENTREGADO" and pedido_lock.id_repartidor_id:
                        liberar_repartidor_si_sin_pedidos(pedido_lock.id_repartidor_id, pedido_lock.pk)
            except ValueError as error:
                messages.error(request, str(error))
                return redirect("tienda:admin-pedido-detalle", id_pedido=pedido_lock.pk)

        codigo_ref = pedido.codigo_pedido or f"#{pedido.id_pedido}"
        messages.success(request, f"El pedido {codigo_ref} fue actualizado.")
        return redirect("tienda:admin-pedido-detalle", id_pedido=pedido.pk)

    return render(
        request,
        "tienda/admin/pedido_detalle.html",
        {
            "pedido": pedido,
            "form": form,
            "detalles": list(pedido.detalles.all()),
            "direccion": direccion_pedido(pedido),
        },
    )


@admin_required
def sucursales_admin(request: HttpRequest) -> HttpResponse:
    sucursales = _filtro_texto(Sucursal.objects.select_related("id_municipio"), request, "nombre")
    return _portal_list(
        request,
        title="Sucursales",
        description="Gestiona los locales sin borrar sucursales que tienen historial.",
        columns=("Sucursal", "Municipio", "Teléfono", "Estado"),
        queryset=sucursales.order_by("nombre"),
        row_builder=lambda s: {
            "cells": (
                s.nombre,
                s.id_municipio.nombre,
                s.telefono or "—",
                _badge("Activa", "aprobado") if s.activa else _badge("Inactiva", "cancelado"),
            ),
            "url": reverse("tienda:admin-sucursal-editar", args=[s.pk]),
            "label": "Editar",
            "action_url": reverse("tienda:admin-sucursal-estado", args=[s.pk]),
            "action_label": "Desactivar" if s.activa else "Activar",
        },
        create_url=reverse("tienda:admin-sucursal-crear"),
    )


@admin_required
@require_http_methods(["GET", "POST"])
def sucursal_admin_form(request: HttpRequest, id_sucursal=None) -> HttpResponse:
    return _guardar_portal_model_form(
        request,
        model=Sucursal,
        pk=id_sucursal,
        form_class=SucursalPortalForm,
        redirect_name="tienda:admin-sucursales",
        success_msg="La sucursal fue guardada.",
        create_title="Agregar sucursal",
        edit_title="Editar sucursal",
        submit_label="Guardar sucursal",
        description="Los registros con pedidos o empleados se desactivan en lugar de eliminarse.",
    )


@admin_required
@require_POST
def sucursal_admin_estado(request: HttpRequest, id_sucursal: int) -> HttpResponse:
    sucursal = get_object_or_404(Sucursal, pk=id_sucursal)
    sucursal.activa = not sucursal.activa
    sucursal.save(update_fields=["activa"])
    messages.success(request, f"Sucursal {'activada' if sucursal.activa else 'desactivada'}.")
    return redirect("tienda:admin-sucursales")


@admin_required
def productos_admin(request: HttpRequest) -> HttpResponse:
    productos = _filtro_texto(
        Producto.objects.select_related("id_categoria"), request, "nombre", "codigo_barras"
    )
    return _portal_list(
        request,
        title="Productos",
        description="Administra el catálogo global. Los productos con historial se desactivan.",
        columns=("Producto", "Categoría", "Precio", "Estado"),
        queryset=productos.order_by("nombre"),
        row_builder=lambda p: {
            "cells": (
                p.nombre,
                p.id_categoria.nombre,
                f"$ {p.precio_base:.2f}",
                _badge("Activo", "aprobado") if p.activo else _badge("Inactivo", "cancelado"),
            ),
            "url": reverse("tienda:admin-producto-editar", args=[p.pk]),
            "label": "Editar",
            "action_url": reverse("tienda:admin-producto-estado", args=[p.pk]),
            "action_label": "Desactivar" if p.activo else "Activar",
        },
        create_url=reverse("tienda:admin-producto-crear"),
    )


@admin_required
@require_http_methods(["GET", "POST"])
def producto_admin_form(request: HttpRequest, id_producto=None) -> HttpResponse:
    return _guardar_portal_model_form(
        request,
        model=Producto,
        pk=id_producto,
        form_class=ProductoPortalForm,
        redirect_name="tienda:admin-productos",
        success_msg="El producto fue guardado.",
        create_title="Agregar producto",
        edit_title="Editar producto",
        submit_label="Guardar producto",
        description="Los cambios se reflejan en el catálogo compartido de Django.",
    )


@admin_required
@require_POST
def producto_admin_estado(request: HttpRequest, id_producto: int) -> HttpResponse:
    producto = get_object_or_404(Producto, pk=id_producto)
    producto.activo = not producto.activo
    producto.save(update_fields=["activo"])
    messages.success(request, f"Producto {'activado' if producto.activo else 'desactivado'}.")
    return redirect("tienda:admin-productos")


@admin_required
def categorias_admin(request: HttpRequest) -> HttpResponse:
    categorias = _filtro_texto(
        Categoria.objects.annotate(productos_count=Count("productos")), request, "nombre"
    )
    return _portal_list(
        request,
        title="Categorías",
        description="Una categoría solo se elimina si no tiene productos asociados.",
        columns=("Categoría", "Productos", "Descripción"),
        queryset=categorias.order_by("nombre"),
        row_builder=lambda c: {
            "cells": (c.nombre, c.productos_count, c.descripcion or "—"),
            "url": reverse("tienda:admin-categoria-editar", args=[c.pk]),
            "label": "Editar",
            "delete_url": (
                reverse("tienda:admin-categoria-eliminar", args=[c.pk])
                if not c.productos_count
                else None
            ),
        },
        create_url=reverse("tienda:admin-categoria-crear"),
    )


@admin_required
@require_http_methods(["GET", "POST"])
def categoria_admin_form(request: HttpRequest, id_categoria=None) -> HttpResponse:
    return _guardar_portal_model_form(
        request,
        model=Categoria,
        pk=id_categoria,
        form_class=CategoriaPortalForm,
        redirect_name="tienda:admin-categorias",
        success_msg="La categoría fue guardada.",
        create_title="Agregar categoría",
        edit_title="Editar categoría",
        submit_label="Guardar categoría",
    )


@admin_required
@require_POST
def categoria_admin_eliminar(request: HttpRequest, id_categoria: int) -> HttpResponse:
    categoria = get_object_or_404(Categoria, pk=id_categoria)
    if categoria.productos.exists():
        messages.error(request, "No se puede eliminar una categoría que contiene productos.")
    else:
        categoria.delete()
        messages.success(request, "La categoría fue eliminada.")
    return redirect("tienda:admin-categorias")


@admin_required
def inventario_admin(request: HttpRequest) -> HttpResponse:
    inventarios = _filtro_texto(
        Inventario.objects.select_related("id_sucursal", "id_producto"),
        request,
        "id_producto__nombre",
        "id_sucursal__nombre",
    )
    return _portal_list(
        request,
        title="Inventario",
        description="Administra existencias por sucursal; solo se puede retirar un registro si su stock es cero.",
        columns=("Sucursal", "Producto", "Existencias", "Vencimiento"),
        queryset=inventarios.order_by("id_sucursal__nombre", "id_producto__nombre"),
        row_builder=lambda i: {
            "cells": (
                i.id_sucursal.nombre,
                i.id_producto.nombre,
                format_html(
                    '<span class="stock {}">{}</span>',
                    "low" if i.stock_disponible <= 5 else "",
                    i.stock_disponible,
                ),
                i.fecha_vencimiento or "—",
            ),
            "url": reverse("tienda:admin-inventario-editar", args=[i.pk]),
            "label": "Editar",
            "delete_url": (
                reverse("tienda:admin-inventario-eliminar", args=[i.pk])
                if i.stock_disponible == 0
                else None
            ),
        },
        create_url=reverse("tienda:admin-inventario-crear"),
    )


@admin_required
@require_http_methods(["GET", "POST"])
def inventario_admin_form(request: HttpRequest, id_inventario=None) -> HttpResponse:
    return _guardar_portal_model_form(
        request,
        model=Inventario,
        pk=id_inventario,
        form_class=InventarioPortalForm,
        redirect_name="tienda:admin-inventario",
        success_msg="El inventario fue guardado.",
        create_title="Agregar inventario",
        edit_title="Editar inventario",
        submit_label="Guardar inventario",
        description="Cada combinación de sucursal y producto debe ser única.",
    )


@admin_required
@require_POST
def inventario_admin_eliminar(request: HttpRequest, id_inventario: int) -> HttpResponse:
    inventario = get_object_or_404(Inventario, pk=id_inventario)
    if inventario.stock_disponible != 0:
        messages.error(request, "Solo se pueden eliminar registros de inventario con stock cero.")
    else:
        inventario.delete()
        messages.success(request, "El registro de inventario fue eliminado.")
    return redirect("tienda:admin-inventario")


@admin_required
def usuarios_admin(request: HttpRequest) -> HttpResponse:
    usuarios = _filtro_texto(
        Usuario.objects.select_related("id_rol"), request, "nombre_completo", "email"
    )
    return _portal_list(
        request,
        title="Usuarios",
        description="Gestiona cuentas y asignaciones. Las cuentas se desactivan para conservar el historial.",
        columns=("Nombre", "Correo", "Rol", "Estado"),
        queryset=usuarios.order_by("nombre_completo"),
        row_builder=lambda u: {
            "cells": (
                u.nombre_completo,
                u.email,
                _badge(u.id_rol.nombre),
                _badge("Activo", "aprobado") if u.activo else _badge("Inactivo", "cancelado"),
            ),
            "url": reverse("tienda:admin-usuario-editar", args=[u.pk]),
            "label": "Editar",
            "action_url": reverse("tienda:admin-usuario-estado", args=[u.pk]),
            "action_label": "Desactivar" if u.activo else "Reactivar",
        },
        create_url=reverse("tienda:admin-usuario-crear"),
    )


@admin_required
@require_http_methods(["GET", "POST"])
def usuario_admin_form(request: HttpRequest, id_usuario=None) -> HttpResponse:
    instance = get_object_or_404(Usuario, pk=id_usuario) if id_usuario else None
    form = UsuarioPortalForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
        messages.success(request, "La cuenta fue guardada.")
        return redirect("tienda:admin-usuarios")
    return _portal_form(
        request,
        title="Editar usuario" if instance else "Agregar usuario",
        description="Para usuarios nuevos se requiere contraseña. Para editar, déjala vacía si no deseas cambiarla.",
        form=form,
        cancel_url=reverse("tienda:admin-usuarios"),
        submit_label="Guardar usuario",
    )


@admin_required
@require_POST
def usuario_admin_estado(request: HttpRequest, id_usuario: int) -> HttpResponse:
    usuario = get_object_or_404(Usuario, pk=id_usuario)
    if usuario.pk == request.user.pk and usuario.activo:
        messages.error(request, "No puedes desactivar tu propia cuenta.")
    else:
        usuario.activo = not usuario.activo
        campos = ["activo"]
        if not usuario.activo:
            # Invalidar sesiones JWT activas de inmediato al desactivar la cuenta
            usuario.token_version = (usuario.token_version or 0) + 1
            campos.append("token_version")
        usuario.save(update_fields=campos)
        messages.success(request, f"Cuenta {'reactivada' if usuario.activo else 'desactivada'}.")
    return redirect("tienda:admin-usuarios")


# ─── Portal de Sucursal (Empleados) ──────────────────────────────────────────

@empleado_required
def catalogo_productos_empleado(request: HttpRequest) -> HttpResponse:
    productos = _filtro_texto(
        Producto.objects.filter(activo=True).select_related("id_categoria"),
        request,
        "nombre",
        "codigo_barras",
    )
    return _portal_list(
        request,
        title="Consultar productos",
        description="Catálogo de consulta; los empleados no modifican precios ni productos globales.",
        columns=("Producto", "Categoría", "Precio", "Código"),
        queryset=productos.order_by("nombre"),
        row_builder=lambda p: {
            "cells": (p.nombre, p.id_categoria.nombre, f"$ {p.precio_base:.2f}", p.codigo_barras or "—")
        },
    )


@empleado_required
def catalogo_categorias_empleado(request: HttpRequest) -> HttpResponse:
    categorias = _filtro_texto(
        Categoria.objects.annotate(
            productos_activos=Count("productos", filter=Q(productos__activo=True))
        ),
        request,
        "nombre",
    )
    return _portal_list(
        request,
        title="Consultar categorías",
        description="Categorías del catálogo global en modo de solo lectura.",
        columns=("Categoría", "Descripción", "Productos activos"),
        queryset=categorias.order_by("nombre"),
        row_builder=lambda c: {"cells": (c.nombre, c.descripcion or "—", c.productos_activos)},
    )


@empleado_required
def empleado_panel(request: HttpRequest) -> HttpResponse:
    sucursal = request.sucursal_empleado
    conteos = Pedido.objects.filter(
        id_sucursal=sucursal,
        estado__in=ESTADOS_GESTIONABLES,
    ).aggregate(
        pedidos_pendientes=Count("id_pedido", filter=Q(estado="PENDIENTE")),
        pedidos_preparados=Count("id_pedido", filter=Q(estado="PREPARADO")),
    )
    productos_bajos = (
        _productos_con_inventario(sucursal)
        .filter(stock_disponible__lte=5)
        .order_by("stock_disponible", "nombre")
    )
    return render(
        request,
        "tienda/empleado/panel.html",
        {
            "sucursal": sucursal,
            **conteos,
            "productos_bajos": list(productos_bajos[:8]),
            "total_productos_bajos": productos_bajos.count(),
        },
    )


@empleado_required
def pedidos_empleado(request: HttpRequest) -> HttpResponse:
    consulta = Pedido.objects.filter(
        id_sucursal=request.sucursal_empleado,
        estado__in=ESTADOS_GESTIONABLES,
    ).select_related("id_cliente", "id_repartidor__id_repartidor")

    estado = request.GET.get("estado", "")
    if estado in ESTADOS_GESTIONABLES:
        consulta = consulta.filter(estado=estado)
    busqueda = request.GET.get("q", "").strip()
    if busqueda:
        filtros = (
            Q(codigo_pedido__icontains=busqueda)
            | Q(id_cliente__nombre_completo__icontains=busqueda)
        )
        if busqueda.isdecimal():
            filtros |= Q(id_pedido=int(busqueda))
        consulta = consulta.filter(filtros)

    pagina = Paginator(consulta.order_by("-fecha_creacion", "-id_pedido"), PAGE_SIZE).get_page(
        request.GET.get("page")
    )
    return render(
        request,
        "tienda/empleado/pedidos.html",
        {"pedidos": pagina, "estado_filtro": estado, "busqueda": busqueda},
    )


def _pedido_sucursal(request: HttpRequest, id_pedido: int) -> Pedido:
    return get_object_or_404(
        Pedido.objects.select_related(
            "id_cliente", "id_sucursal", "id_repartidor__id_repartidor"
        ).prefetch_related("detalles__id_producto"),
        id_pedido=id_pedido,
        id_sucursal=request.sucursal_empleado,
        estado__in=ESTADOS_GESTIONABLES,
    )


@empleado_required
def detalle_pedido_empleado(request: HttpRequest, id_pedido: int) -> HttpResponse:
    pedido = _pedido_sucursal(request, id_pedido)
    repartidores = (
        PerfilRepartidor.objects.filter(
            estado_operativo="LIBRE",
            id_repartidor__activo=True,
            id_repartidor__id_rol__nombre="REPARTIDOR",
        )
        .select_related("id_repartidor")
        .order_by("id_repartidor__nombre_completo")
    )
    return render(
        request,
        "tienda/empleado/pedido_detalle.html",
        {
            "pedido": pedido,
            "direccion": direccion_pedido(pedido),
            "repartidores": repartidores,
        },
    )


@require_POST
@empleado_required
def accion_pedido(request: HttpRequest, id_pedido: int) -> HttpResponse:
    accion = request.POST.get("accion")
    with transaction.atomic():
        pedido = get_object_or_404(
            Pedido.objects.select_for_update(),
            id_pedido=id_pedido,
            id_sucursal=request.sucursal_empleado,
            estado__in=ESTADOS_GESTIONABLES,
        )

        codigo_ref = pedido.codigo_pedido or f"#{pedido.id_pedido}"
        if accion == "preparar":
            if pedido.estado != "PENDIENTE":
                messages.error(request, "Solo puedes preparar pedidos pendientes.")
            else:
                pedido.estado = "PREPARADO"
                pedido.save(update_fields=["estado"])
                messages.success(request, f"El pedido {codigo_ref} está preparado.")

        elif accion == "pagar":
            if pedido.metodo_pago != "TRANSFERENCIA" or pedido.estado_pago != "PENDIENTE":
                messages.error(request, "Solo se pueden confirmar transferencias pendientes.")
            else:
                pedido.estado_pago = "PAGADO"
                pedido.save(update_fields=["estado_pago"])
                messages.success(request, f"El pago del pedido {codigo_ref} fue confirmado.")

        elif accion == "cancelar":
            cancelar_pedido(pedido)
            messages.success(
                request,
                f"El pedido {codigo_ref} fue cancelado y el inventario fue actualizado.",
            )
            return redirect("tienda:pedidos")

        elif accion == "asignar_repartidor":
            nuevo_id = request.POST.get("id_repartidor", "").strip()
            if not nuevo_id or not nuevo_id.isdecimal():
                messages.error(request, "Selecciona un repartidor disponible.")
            else:
                try:
                    perfil = actualizar_repartidor_pedido(pedido, int(nuevo_id))
                    pedido.save(update_fields=["id_repartidor"])
                    messages.success(
                        request,
                        f"El repartidor {perfil.id_repartidor.nombre_completo} fue asignado.",
                    )
                except ValueError as error:
                    messages.error(request, str(error))

        else:
            messages.error(request, "La acción solicitada no es válida.")

    return redirect("tienda:pedido-detalle", id_pedido=id_pedido)


@empleado_required
def inventario_empleado(request: HttpRequest) -> HttpResponse:
    consulta = _productos_con_inventario(request.sucursal_empleado).select_related("id_categoria")
    busqueda = request.GET.get("q", "").strip()
    solo_bajo = request.GET.get("bajo") == "1"

    if busqueda:
        consulta = consulta.filter(
            Q(nombre__icontains=busqueda) | Q(codigo_barras__icontains=busqueda)
        )
    if solo_bajo:
        consulta = consulta.filter(stock_disponible__lte=5)

    pagina = Paginator(consulta.order_by("nombre"), 25).get_page(request.GET.get("page"))
    return render(
        request,
        "tienda/empleado/inventario.html",
        {"inventario": pagina, "busqueda": busqueda, "solo_bajo": solo_bajo},
    )


@require_http_methods(["GET", "POST"])
@empleado_required
def editar_inventario(request: HttpRequest, id_producto: int) -> HttpResponse:
    producto = get_object_or_404(Producto, pk=id_producto, activo=True)
    sucursal = request.sucursal_empleado
    existencia = Inventario.objects.filter(id_sucursal=sucursal, id_producto=producto).first()
    form = InventarioForm(
        request.POST or None,
        initial={
            "stock_disponible": existencia.stock_disponible if existencia else 0,
            "fecha_vencimiento": existencia.fecha_vencimiento if existencia else None,
        },
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            existencia = (
                Inventario.objects.select_for_update()
                .filter(id_sucursal=sucursal, id_producto=producto)
                .first()
            )
            datos = {
                "stock_disponible": form.cleaned_data["stock_disponible"],
                "fecha_vencimiento": form.cleaned_data["fecha_vencimiento"],
            }
            if existencia is None:
                Inventario.objects.create(id_sucursal=sucursal, id_producto=producto, **datos)
            else:
                existencia.stock_disponible = datos["stock_disponible"]
                existencia.fecha_vencimiento = datos["fecha_vencimiento"]
                existencia.save(update_fields=["stock_disponible", "fecha_vencimiento"])
        messages.success(request, f"Inventario actualizado para {producto.nombre}.")
        return redirect("tienda:inventario")

    return render(
        request,
        "tienda/empleado/inventario_editar.html",
        {"producto": producto, "form": form},
    )


# ─── Reportes y Métricas (Chart.js) ──────────────────────────────────────────

def _parse_mes_anio(request: HttpRequest) -> tuple[int, int]:
    ahora = timezone.localtime()
    valor = request.GET.get("mes_anio", "")
    try:
        anio, mes = map(int, valor.split("-"))
        if 2000 <= anio <= 2100 and 1 <= mes <= 12:
            return anio, mes
    except ValueError:
        pass
    return ahora.year, ahora.month


def _calcular_metricas_reportes(pedidos_qs, anio: int, mes: int) -> dict:
    pedidos_periodo = pedidos_qs.filter(
        fecha_creacion__year=anio,
        fecha_creacion__month=mes,
    )
    entregado_q = Q(estado="ENTREGADO")
    cero = Decimal("0.00")

    # Consolidamos 7 consultas individuales en una sola agregación condicional SQL
    agregados = pedidos_periodo.aggregate(
        total_pedidos_periodo=Count("id_pedido"),
        pedidos_entregados_count=Count("id_pedido", filter=entregado_q),
        pedidos_cancelados_count=Count("id_pedido", filter=Q(estado="CANCELADO")),
        total_ventas=Coalesce(Sum("total", filter=entregado_q), cero, output_field=DecimalField()),
        ventas_wompi=Coalesce(
            Sum("total", filter=entregado_q & Q(metodo_pago__in=["WOMPI", "TARJETA"])),
            cero,
            output_field=DecimalField(),
        ),
        ventas_transferencia=Coalesce(
            Sum("total", filter=entregado_q & Q(metodo_pago="TRANSFERENCIA")),
            cero,
            output_field=DecimalField(),
        ),
        ventas_efectivo=Coalesce(
            Sum("total", filter=entregado_q & Q(metodo_pago="CONTRA_ENTREGA")),
            cero,
            output_field=DecimalField(),
        ),
    )

    total_pedidos = agregados["total_pedidos_periodo"]
    entregados_count = agregados["pedidos_entregados_count"]
    total_ventas = agregados["total_ventas"] or cero

    tasa_efectividad = (
        round((entregados_count / total_pedidos) * 100, 1) if total_pedidos > 0 else 100.0
    )
    ticket_promedio = (
        round(float(total_ventas) / entregados_count, 2) if entregados_count > 0 else 0.0
    )

    entregados_qs = pedidos_periodo.filter(estado="ENTREGADO")
    num_dias = calendar.monthrange(anio, mes)[1]
    dias_dict = {d: 0.0 for d in range(1, num_dias + 1)}

    ventas_por_dia = (
        entregados_qs.annotate(dia=TruncDate("fecha_creacion"))
        .values("dia")
        .annotate(total_dia=Sum("total"))
    )
    for row in ventas_por_dia:
        if row["dia"]:
            dias_dict[row["dia"].day] = float(row["total_dia"] or 0)

    top_productos = [
        {
            "nombre": p["id_producto__nombre"],
            "unidades": p["unidades"],
            "total_recaudado": float(p["total_recaudado"] or 0),
        }
        for p in (
            DetallePedido.objects.filter(id_pedido__in=entregados_qs)
            .values("id_producto__nombre")
            .annotate(unidades=Sum("cantidad"), total_recaudado=Sum("subtotal"))
            .order_by("-unidades")[:8]
        )
    ]

    rendimiento_repartidores = [
        {
            "nombre": r["id_repartidor__id_repartidor__nombre_completo"],
            "estado": r["id_repartidor__estado_operativo"],
            "entregas": r["entregas"],
        }
        for r in (
            entregados_qs.filter(id_repartidor__isnull=False)
            .values(
                "id_repartidor__id_repartidor__nombre_completo",
                "id_repartidor__estado_operativo",
            )
            .annotate(entregas=Count("id_pedido"))
            .order_by("-entregas")[:8]
        )
    ]

    return {
        "total_ventas": total_ventas,
        "pedidos_entregados_count": entregados_count,
        "pedidos_cancelados_count": agregados["pedidos_cancelados_count"],
        "total_pedidos_periodo": total_pedidos,
        "tasa_efectividad": tasa_efectividad,
        "ticket_promedio": ticket_promedio,
        "ventas_wompi": float(agregados["ventas_wompi"]),
        "ventas_transferencia": float(agregados["ventas_transferencia"]),
        "ventas_efectivo": float(agregados["ventas_efectivo"]),
        "chart_dias_labels": json.dumps([f"Día {d}" for d in range(1, num_dias + 1)]),
        "chart_dias_valores": json.dumps([dias_dict[d] for d in range(1, num_dias + 1)]),
        "top_productos": top_productos,
        "rendimiento_repartidores": rendimiento_repartidores,
        "mes_nombre": f"{MESES_ES[mes]} {anio}",
        "mes_seleccionado": f"{anio:04d}-{mes:02d}",
    }


@admin_required
def reportes_admin(request: HttpRequest) -> HttpResponse:
    anio, mes = _parse_mes_anio(request)
    pedidos_qs = Pedido.objects.all()
    id_sucursal = request.GET.get("id_sucursal", "").strip()
    if id_sucursal.isdecimal():
        pedidos_qs = pedidos_qs.filter(id_sucursal_id=int(id_sucursal))

    metricas = _calcular_metricas_reportes(pedidos_qs, anio, mes)
    return render(
        request,
        "tienda/admin/reportes.html",
        {
            **metricas,
            "portal": "admin",
            "base_template": "tienda/admin/base.html",
            "sucursales": Sucursal.objects.filter(activa=True).order_by("nombre"),
            "sucursal_filtro": id_sucursal,
        },
    )


@empleado_required
def reportes_empleado(request: HttpRequest) -> HttpResponse:
    anio, mes = _parse_mes_anio(request)
    pedidos_qs = Pedido.objects.filter(id_sucursal=request.sucursal_empleado)
    metricas = _calcular_metricas_reportes(pedidos_qs, anio, mes)
    return render(
        request,
        "tienda/admin/reportes.html",
        {
            **metricas,
            "portal": "empleado",
            "base_template": "tienda/empleado/base.html",
            "sucursal_actual": request.sucursal_empleado,
        },
    )


# ─── Reporte (Soporte Técnico e Incidencias) ─────────────────────────────────

@admin_required
def denuncias_admin(request: HttpRequest) -> HttpResponse:
    resumen = Denuncia.objects.aggregate(
        total_reportes=Count("id_denuncia"),
        pendientes_count=Count("id_denuncia", filter=Q(estado="PENDIENTE")),
        investigando_count=Count("id_denuncia", filter=Q(estado="INVESTIGANDO")),
        resueltos_count=Count("id_denuncia", filter=Q(estado="RESUELTO")),
    )

    queryset = Denuncia.objects.select_related(
        "id_pedido", "id_cliente", "id_repartidor"
    ).order_by("-fecha_creacion")

    estado = request.GET.get("estado", "").strip()
    estados_validos = {choice[0] for choice in Denuncia.ESTADO_CHOICES}
    if estado in estados_validos:
        queryset = queryset.filter(estado=estado)

    busqueda = request.GET.get("q", "").strip()
    if busqueda:
        filtros = (
            Q(id_pedido__codigo_pedido__icontains=busqueda)
            | Q(motivo__icontains=busqueda)
            | Q(descripcion__icontains=busqueda)
            | Q(id_cliente__email__icontains=busqueda)
            | Q(id_cliente__nombre_completo__icontains=busqueda)
            | Q(id_repartidor__email__icontains=busqueda)
            | Q(id_repartidor__nombre_completo__icontains=busqueda)
        )
        if busqueda.isdecimal():
            filtros |= Q(id_pedido_id=int(busqueda)) | Q(id_denuncia=int(busqueda))
        queryset = queryset.filter(filtros)

    page_obj = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get("page"))
    for item in page_obj:
        item.soporte = parsear_resolucion_soporte(item.resolucion_admin)

    return render(
        request,
        "tienda/admin/denuncias.html",
        {
            "items": page_obj,
            "estado_filtro": estado,
            "busqueda": busqueda,
            "page_obj": page_obj,
            **resumen,
        },
    )


@admin_required
@require_http_methods(["GET", "POST"])
def denuncia_detalle_admin(request: HttpRequest, id_denuncia: int) -> HttpResponse:
    denuncia = get_object_or_404(
        Denuncia.objects.select_related(
            "id_pedido__id_sucursal",
            "id_cliente",
            "id_repartidor__perfil_repartidor",
        ),
        id_denuncia=id_denuncia,
    )

    ahora_local = timezone.localtime()
    session_key = f"soporte_entrada_{denuncia.id_denuncia}"
    if request.method == "GET" and session_key not in request.session:
        request.session[session_key] = ahora_local.strftime("%H:%M")
    hora_entrada_sesion = request.session.get(session_key) or ahora_local.strftime("%H:%M")

    form = DenunciaResolucionForm(
        request.POST or None,
        instance=denuncia,
        admin_user=request.user,
        hora_entrada_inicial=hora_entrada_sesion,
    )
    if request.method == "POST" and form.is_valid():
        denuncia = form.save()
        request.session.pop(session_key, None)
        entrada_txt = form.cleaned_data["hora_entrada"].strftime("%H:%M")
        salida_txt = form.cleaned_data["hora_salida"].strftime("%H:%M")
        messages.success(
            request,
            (
                f"El reporte #{denuncia.id_denuncia} fue actualizado por soporte técnico "
                f"({denuncia.get_estado_display()} · Atención {entrada_txt}–{salida_txt})."
            ),
        )
        return redirect("tienda:admin-denuncias")

    direccion = direccion_pedido(denuncia.id_pedido) if denuncia.id_pedido_id else None
    perfil_repartidor = (
        getattr(denuncia.id_repartidor, "perfil_repartidor", None)
        if denuncia.id_repartidor_id
        else None
    )
    stats_repartidor = (
        Denuncia.objects.filter(id_repartidor_id=denuncia.id_repartidor_id).aggregate(
            total=Count("id_denuncia"),
            abiertos=Count(
                "id_denuncia", filter=Q(estado__in=["PENDIENTE", "INVESTIGANDO"])
            ),
        )
        if denuncia.id_repartidor_id
        else {"total": 0, "abiertos": 0}
    )
    total_pedidos_cliente = (
        Pedido.objects.filter(id_cliente_id=denuncia.id_cliente_id).count()
        if denuncia.id_cliente_id
        else 0
    )

    return render(
        request,
        "tienda/admin/denuncia_detalle.html",
        {
            "denuncia": denuncia,
            "form": form,
            "soporte_previo": parsear_resolucion_soporte(denuncia.resolucion_admin),
            "direccion": direccion,
            "perfil_repartidor": perfil_repartidor,
            "stats_repartidor": stats_repartidor,
            "total_pedidos_cliente": total_pedidos_cliente,
            "fecha_hoy": ahora_local,
        },
    )
