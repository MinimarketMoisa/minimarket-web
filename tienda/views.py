from functools import wraps

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, DateField, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    CategoriaPortalForm,
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
    DetallePedido,
    Direccion,
    Empleado,
    Inventario,
    Pedido,
    PerfilRepartidor,
    Producto,
    Sucursal,
    Usuario,
)

ESTADOS_GESTIONABLES = ("PENDIENTE", "PREPARADO")


def _es_empleado(usuario: Usuario) -> bool:
    return (
        usuario.is_authenticated
        and usuario.is_active
        and not usuario.is_superuser
        and usuario.id_rol.nombre == "EMPLEADO"
    )


def _es_admin(usuario: Usuario) -> bool:
    return (
        usuario.is_authenticated
        and usuario.is_active
        and (usuario.is_superuser or usuario.id_rol.nombre == "ADMIN")
    )


def _es_personal(usuario: Usuario) -> bool:
    return _es_admin(usuario) or _es_empleado(usuario)


def empleado_required(view_func):
    @wraps(view_func)
    @login_required(login_url="tienda:empleado-login")
    def wrapper(request: HttpRequest, *args, **kwargs):
        if not _es_empleado(request.user):
            return HttpResponseForbidden("Esta sección está disponible solo para empleados.")
        empleado = (
            Empleado.objects.select_related("id_sucursal")
            .filter(id_empleado=request.user)
            .first()
        )
        if empleado is None:
            return HttpResponseForbidden("Tu cuenta no tiene una sucursal asignada.")
        request.sucursal_empleado = empleado.id_sucursal
        return view_func(request, *args, **kwargs)

    return wrapper


def admin_required(view_func):
    @wraps(view_func)
    @login_required(login_url="tienda:empleado-login")
    def wrapper(request: HttpRequest, *args, **kwargs):
        if not _es_admin(request.user):
            return HttpResponseForbidden("Esta sección está disponible solo para administradores.")
        return view_func(request, *args, **kwargs)

    return wrapper


def inicio(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        if _es_admin(request.user):
            return redirect("tienda:admin-panel")
        if _es_empleado(request.user):
            return redirect("tienda:empleado-panel")
    return redirect("tienda:empleado-login")


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


@require_http_methods(["GET", "POST"])
def empleado_login(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        if _es_admin(request.user):
            return redirect("tienda:admin-panel")
        if _es_empleado(request.user):
            return redirect("tienda:empleado-panel")
        return HttpResponseForbidden("Este acceso es exclusivo para empleados y administradores.")

    form = EmpleadoLoginForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        usuario = form.get_user()
        if not _es_personal(usuario):
            form.add_error(None, "Esta cuenta no tiene acceso al portal de personal.")
        else:
            login(request, usuario)
            if _es_admin(usuario):
                return redirect("tienda:admin-panel")
            return redirect("tienda:empleado-panel")
    return render(request, "tienda/empleado/login.html", {"form": form})


@require_POST
def empleado_logout(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect("tienda:empleado-login")
    if not _es_personal(request.user):
        return HttpResponseForbidden("Esta acción está disponible solo para empleados y administradores.")
    logout(request)
    return redirect("tienda:empleado-login")


@admin_required
def admin_panel(request: HttpRequest) -> HttpResponse:
    hoy = timezone.localdate()
    pedidos = Pedido.objects.all()
    resumen = pedidos.aggregate(
        total_pedidos=Count("id_pedido"),
        pedidos_hoy=Count("id_pedido", filter=Q(fecha_creacion__date=hoy)),
        pendientes=Count("id_pedido", filter=Q(estado="PENDIENTE")),
        preparados=Count("id_pedido", filter=Q(estado="PREPARADO")),
        en_ruta=Count("id_pedido", filter=Q(estado="EN_RUTA")),
        pagos_pendientes=Count("id_pedido", filter=Q(estado_pago="PENDIENTE")),
        ventas_entregadas=Sum("total", filter=Q(estado="ENTREGADO")),
    )
    sucursales = Sucursal.objects.annotate(
        total_pedidos=Count("pedidos"),
        pedidos_pendientes=Count("pedidos", filter=Q(pedidos__estado="PENDIENTE")),
        pedidos_preparados=Count("pedidos", filter=Q(pedidos__estado="PREPARADO")),
        pedidos_en_ruta=Count("pedidos", filter=Q(pedidos__estado="EN_RUTA")),
        ventas_entregadas=Sum("pedidos__total", filter=Q(pedidos__estado="ENTREGADO")),
    ).order_by("nombre")
    inventario_bajo = Inventario.objects.filter(stock_disponible__lte=5).count()
    repartidores_libres = PerfilRepartidor.objects.filter(
        estado_operativo="LIBRE",
        id_repartidor__activo=True,
    ).count()
    return render(
        request,
        "tienda/admin/dashboard.html",
        {
            **resumen,
            "ventas_entregadas": resumen["ventas_entregadas"] or 0,
            "sucursales": sucursales,
            "inventario_bajo": inventario_bajo,
            "repartidores_libres": repartidores_libres,
            "productos_activos": Producto.objects.filter(activo=True).count(),
            "empleados_activos": Usuario.objects.filter(
                id_rol__nombre="EMPLEADO",
                activo=True,
            ).count(),
            "hoy": hoy,
        },
    )


def _portal_list(request, *, title, description, columns, items, create_url=None):
    return render(
        request,
        "tienda/admin/portal_list.html",
        {
            "title": title,
            "description": description,
            "columns": columns,
            "items": items,
            "create_url": create_url,
            "busqueda": request.GET.get("q", "").strip(),
            "base_template": (
                "tienda/admin/base.html"
                if _es_admin(request.user)
                else "tienda/empleado/base.html"
            ),
        },
    )


def _portal_form(request, *, title, description, form, cancel_url, submit_label):
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


def _filtro_texto(queryset, request, *fields):
    busqueda = request.GET.get("q", "").strip()
    if busqueda:
        filtros = Q()
        for field in fields:
            filtros |= Q(**{f"{field}__icontains": busqueda})
        queryset = queryset.filter(filtros)
    return queryset


@admin_required
def pedidos_admin(request: HttpRequest) -> HttpResponse:
    consulta = Pedido.objects.select_related(
        "id_cliente", "id_sucursal", "id_repartidor__id_repartidor"
    ).all()
    estado = request.GET.get("estado", "")
    if estado in {choice[0] for choice in Pedido.ESTADO_CHOICES}:
        consulta = consulta.filter(estado=estado)
    busqueda = request.GET.get("q", "").strip()
    if busqueda:
        filtros = Q(id_cliente__nombre_completo__icontains=busqueda)
        if busqueda.isdecimal():
            filtros |= Q(id_pedido=int(busqueda))
        consulta = consulta.filter(filtros)
    items = [
        {
            "cells": (
                f"#{pedido.id_pedido}",
                pedido.id_cliente.nombre_completo,
                pedido.id_sucursal.nombre,
                pedido.get_estado_display(),
                f"$ {pedido.total:.2f}",
                pedido.fecha_creacion,
            ),
            "url": reverse("tienda:admin-pedido-detalle", args=[pedido.pk]),
            "label": "Ver / editar",
        }
        for pedido in consulta.order_by("-fecha_creacion")[:200]
    ]
    return _portal_list(
        request,
        title="Pedidos",
        description="Historial completo. Los pedidos se cancelan; nunca se eliminan.",
        columns=("Pedido", "Cliente", "Sucursal", "Estado", "Total", "Creado"),
        items=items,
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
            pedido = Pedido.objects.select_for_update().get(pk=id_pedido)
            estado_anterior = pedido.estado
            repartidor_anterior_id = pedido.id_repartidor_id
            nuevo_estado = form.cleaned_data["estado"]
            nuevo_repartidor = form.cleaned_data["id_repartidor"]

            if nuevo_estado == "CANCELADO" and estado_anterior == "ENTREGADO":
                messages.error(request, "Un pedido entregado no se puede cancelar desde el portal.")
                return redirect("tienda:admin-pedido-detalle", id_pedido=pedido.pk)
            try:
                if nuevo_estado == "CANCELADO" and estado_anterior != "CANCELADO":
                    _cancelar_pedido(pedido)
                elif nuevo_estado != "CANCELADO":
                    _actualizar_repartidor_pedido(pedido, nuevo_repartidor)
                    pedido.estado = nuevo_estado
                    pedido.estado_pago = form.cleaned_data["estado_pago"]
                    pedido.save(update_fields=["estado", "estado_pago", "id_repartidor"])
                    if nuevo_estado == "ENTREGADO" and pedido.id_repartidor_id:
                        _liberar_repartidor_si_sin_pedidos(pedido.id_repartidor_id, pedido.pk)
                if nuevo_estado == "CANCELADO":
                    pedido.estado_pago = form.cleaned_data["estado_pago"]
                    pedido.save(update_fields=["estado_pago"])
                if repartidor_anterior_id != pedido.id_repartidor_id:
                    _liberar_repartidor_si_sin_pedidos(repartidor_anterior_id, pedido.pk)
            except ValueError as error:
                messages.error(request, str(error))
                return redirect("tienda:admin-pedido-detalle", id_pedido=pedido.pk)
        messages.success(request, f"El pedido #{pedido.id_pedido} fue actualizado.")
        return redirect("tienda:admin-pedido-detalle", id_pedido=pedido.pk)
    return render(
        request,
        "tienda/admin/pedido_detalle.html",
        {
            "pedido": pedido,
            "form": form,
            "detalles": pedido.detalles.all(),
            "direccion": _direccion_pedido(pedido),
        },
    )


def _direccion_pedido(pedido):
    if not pedido.id_direccion:
        return None
    return Direccion.objects.select_related("id_municipio").filter(
        id_direccion=pedido.id_direccion,
        id_cliente=pedido.id_cliente_id,
    ).first()


def _actualizar_repartidor_pedido(pedido, nuevo_perfil):
    if pedido.tipo_entrega == "RETIRO" and nuevo_perfil:
        raise ValueError("Los pedidos para retiro no llevan repartidor.")
    if pedido.estado == "CANCELADO" and nuevo_perfil:
        raise ValueError("No se puede asignar repartidor a un pedido cancelado.")
    if nuevo_perfil is None:
        pedido.id_repartidor = None
        return
    perfil = (
        PerfilRepartidor.objects.select_for_update()
        .filter(pk=nuevo_perfil.pk, id_repartidor__activo=True)
        .first()
    )
    if perfil is None:
        raise ValueError("El repartidor seleccionado ya no está activo.")
    if perfil.pk != pedido.id_repartidor_id and (
        perfil.estado_operativo != "LIBRE"
        or Pedido.objects.filter(id_repartidor=perfil, estado__in=ESTADOS_GESTIONABLES)
        .exclude(pk=pedido.pk)
        .exists()
    ):
        raise ValueError("El repartidor seleccionado ya tiene una entrega activa.")
    perfil.estado_operativo = "PENDIENTE"
    perfil.save(update_fields=["estado_operativo"])
    pedido.id_repartidor = perfil


def _liberar_repartidor_si_sin_pedidos(id_repartidor, pedido_actual):
    if id_repartidor is None:
        return
    perfil = PerfilRepartidor.objects.select_for_update().filter(pk=id_repartidor).first()
    if perfil and not Pedido.objects.filter(
        id_repartidor_id=id_repartidor,
    ).exclude(pk=pedido_actual).exclude(estado__in=("ENTREGADO", "CANCELADO")).exists():
        perfil.estado_operativo = "LIBRE"
        perfil.save(update_fields=["estado_operativo"])


@admin_required
def sucursales_admin(request: HttpRequest) -> HttpResponse:
    sucursales = _filtro_texto(Sucursal.objects.select_related("id_municipio"), request, "nombre")
    items = [
        {
            "cells": (s.nombre, s.id_municipio.nombre, s.telefono or "—", "Activa" if s.activa else "Inactiva"),
            "url": reverse("tienda:admin-sucursal-editar", args=[s.pk]),
            "label": "Editar",
            "action_url": reverse("tienda:admin-sucursal-estado", args=[s.pk]),
            "action_label": "Desactivar" if s.activa else "Activar",
        }
        for s in sucursales.order_by("nombre")
    ]
    return _portal_list(
        request,
        title="Sucursales",
        description="Gestiona los locales sin borrar sucursales que tienen historial.",
        columns=("Sucursal", "Municipio", "Teléfono", "Estado"),
        items=items,
        create_url=reverse("tienda:admin-sucursal-crear"),
    )


@admin_required
@require_http_methods(["GET", "POST"])
def sucursal_admin_form(request: HttpRequest, id_sucursal=None) -> HttpResponse:
    instance = get_object_or_404(Sucursal, pk=id_sucursal) if id_sucursal else None
    form = SucursalPortalForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "La sucursal fue guardada.")
        return redirect("tienda:admin-sucursales")
    return _portal_form(
        request,
        title="Editar sucursal" if instance else "Agregar sucursal",
        description="Los registros con pedidos o empleados se desactivan en lugar de eliminarse.",
        form=form,
        cancel_url=reverse("tienda:admin-sucursales"),
        submit_label="Guardar sucursal",
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
    items = [
        {
            "cells": (p.nombre, p.id_categoria.nombre, f"$ {p.precio_base:.2f}", "Activo" if p.activo else "Inactivo"),
            "url": reverse("tienda:admin-producto-editar", args=[p.pk]),
            "label": "Editar",
            "action_url": reverse("tienda:admin-producto-estado", args=[p.pk]),
            "action_label": "Desactivar" if p.activo else "Activar",
        }
        for p in productos.order_by("nombre")
    ]
    return _portal_list(
        request,
        title="Productos",
        description="Administra el catálogo global. Los productos con historial se desactivan.",
        columns=("Producto", "Categoría", "Precio", "Estado"),
        items=items,
        create_url=reverse("tienda:admin-producto-crear"),
    )


@admin_required
@require_http_methods(["GET", "POST"])
def producto_admin_form(request: HttpRequest, id_producto=None) -> HttpResponse:
    instance = get_object_or_404(Producto, pk=id_producto) if id_producto else None
    form = ProductoPortalForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "El producto fue guardado.")
        return redirect("tienda:admin-productos")
    return _portal_form(
        request,
        title="Editar producto" if instance else "Agregar producto",
        description="Los cambios se reflejan en el catálogo compartido de Django.",
        form=form,
        cancel_url=reverse("tienda:admin-productos"),
        submit_label="Guardar producto",
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
    categorias = _filtro_texto(Categoria.objects.annotate(productos_count=Count("productos")), request, "nombre")
    items = [
        {
            "cells": (c.nombre, c.productos_count, c.descripcion or "—"),
            "url": reverse("tienda:admin-categoria-editar", args=[c.pk]),
            "label": "Editar",
            "delete_url": reverse("tienda:admin-categoria-eliminar", args=[c.pk]) if not c.productos_count else None,
        }
        for c in categorias.order_by("nombre")
    ]
    return _portal_list(
        request,
        title="Categorías",
        description="Una categoría solo se elimina si no tiene productos asociados.",
        columns=("Categoría", "Productos", "Descripción"),
        items=items,
        create_url=reverse("tienda:admin-categoria-crear"),
    )


@admin_required
@require_http_methods(["GET", "POST"])
def categoria_admin_form(request: HttpRequest, id_categoria=None) -> HttpResponse:
    instance = get_object_or_404(Categoria, pk=id_categoria) if id_categoria else None
    form = CategoriaPortalForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "La categoría fue guardada.")
        return redirect("tienda:admin-categorias")
    return _portal_form(
        request,
        title="Editar categoría" if instance else "Agregar categoría",
        description="",
        form=form,
        cancel_url=reverse("tienda:admin-categorias"),
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
    items = [
        {
            "cells": (
                i.id_sucursal.nombre,
                i.id_producto.nombre,
                i.stock_disponible,
                i.fecha_vencimiento or "—",
            ),
            "url": reverse("tienda:admin-inventario-editar", args=[i.pk]),
            "label": "Editar",
            "delete_url": reverse("tienda:admin-inventario-eliminar", args=[i.pk]) if i.stock_disponible == 0 else None,
        }
        for i in inventarios.order_by("id_sucursal__nombre", "id_producto__nombre")
    ]
    return _portal_list(
        request,
        title="Inventario",
        description="Administra existencias por sucursal; solo se puede retirar un registro si su stock es cero.",
        columns=("Sucursal", "Producto", "Existencias", "Vencimiento"),
        items=items,
        create_url=reverse("tienda:admin-inventario-crear"),
    )


@admin_required
@require_http_methods(["GET", "POST"])
def inventario_admin_form(request: HttpRequest, id_inventario=None) -> HttpResponse:
    instance = get_object_or_404(Inventario, pk=id_inventario) if id_inventario else None
    form = InventarioPortalForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "El inventario fue guardado.")
        return redirect("tienda:admin-inventario")
    return _portal_form(
        request,
        title="Editar inventario" if instance else "Agregar inventario",
        description="Cada combinación de sucursal y producto debe ser única.",
        form=form,
        cancel_url=reverse("tienda:admin-inventario"),
        submit_label="Guardar inventario",
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
    items = [
        {
            "cells": (u.nombre_completo, u.email, u.id_rol.nombre, "Activo" if u.activo else "Inactivo"),
            "url": reverse("tienda:admin-usuario-editar", args=[u.pk]),
            "label": "Editar",
            "action_url": reverse("tienda:admin-usuario-estado", args=[u.pk]),
            "action_label": "Desactivar" if u.activo else "Reactivar",
        }
        for u in usuarios.order_by("nombre_completo")
    ]
    return _portal_list(
        request,
        title="Usuarios",
        description="Gestiona cuentas y asignaciones. Las cuentas se desactivan para conservar el historial.",
        columns=("Nombre", "Correo", "Rol", "Estado"),
        items=items,
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
        usuario.save(update_fields=["activo"])
        messages.success(request, f"Cuenta {'reactivada' if usuario.activo else 'desactivada'}.")
    return redirect("tienda:admin-usuarios")


@empleado_required
def catalogo_productos_empleado(request: HttpRequest) -> HttpResponse:
    productos = _filtro_texto(
        Producto.objects.filter(activo=True).select_related("id_categoria"),
        request,
        "nombre",
        "codigo_barras",
    )
    items = [
        {"cells": (p.nombre, p.id_categoria.nombre, f"$ {p.precio_base:.2f}", p.codigo_barras or "—")}
        for p in productos.order_by("nombre")[:300]
    ]
    return _portal_list(
        request,
        title="Consultar productos",
        description="Catálogo de consulta; los empleados no modifican precios ni productos globales.",
        columns=("Producto", "Categoría", "Precio", "Código"),
        items=items,
    )


@empleado_required
def catalogo_categorias_empleado(request: HttpRequest) -> HttpResponse:
    categorias = _filtro_texto(Categoria.objects.all(), request, "nombre")
    items = [
        {"cells": (c.nombre, c.descripcion or "—", c.productos.filter(activo=True).count())}
        for c in categorias.order_by("nombre")[:300]
    ]
    return _portal_list(
        request,
        title="Consultar categorías",
        description="Categorías del catálogo global en modo de solo lectura.",
        columns=("Categoría", "Descripción", "Productos activos"),
        items=items,
    )


@empleado_required
def empleado_panel(request: HttpRequest) -> HttpResponse:
    sucursal = request.sucursal_empleado
    pedidos = Pedido.objects.filter(
        id_sucursal=sucursal,
        estado__in=ESTADOS_GESTIONABLES,
    )
    productos_bajos = _productos_con_inventario(sucursal).filter(
        stock_disponible__lte=5
    ).order_by("stock_disponible", "nombre")
    return render(
        request,
        "tienda/empleado/panel.html",
        {
            "sucursal": sucursal,
            "pedidos_pendientes": pedidos.filter(estado="PENDIENTE").count(),
            "pedidos_preparados": pedidos.filter(estado="PREPARADO").count(),
            "productos_bajos": productos_bajos[:8],
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
        filtros = Q(id_cliente__nombre_completo__icontains=busqueda)
        if busqueda.isdecimal():
            filtros |= Q(id_pedido=int(busqueda))
        consulta = consulta.filter(filtros)

    pagina = Paginator(consulta.order_by("-fecha_creacion", "-id_pedido"), 20).get_page(
        request.GET.get("page")
    )
    return render(
        request,
        "tienda/empleado/pedidos.html",
        {"pedidos": pagina, "estado_filtro": estado, "busqueda": busqueda},
    )


def _pedido_sucursal(request: HttpRequest, id_pedido: int) -> Pedido:
    return get_object_or_404(
        Pedido.objects.select_related("id_cliente", "id_sucursal").prefetch_related(
            "detalles__id_producto"
        ),
        id_pedido=id_pedido,
        id_sucursal=request.sucursal_empleado,
        estado__in=ESTADOS_GESTIONABLES,
    )


@empleado_required
def detalle_pedido_empleado(request: HttpRequest, id_pedido: int) -> HttpResponse:
    pedido = _pedido_sucursal(request, id_pedido)
    direccion = None
    if pedido.id_direccion:
        direccion = (
            Direccion.objects.select_related("id_municipio")
            .filter(
                id_direccion=pedido.id_direccion,
                id_cliente=pedido.id_cliente_id,
            )
            .first()
        )
    repartidores = PerfilRepartidor.objects.filter(
        estado_operativo="LIBRE",
        id_repartidor__activo=True,
        id_repartidor__id_rol__nombre="REPARTIDOR",
    ).select_related("id_repartidor").order_by("id_repartidor__nombre_completo")
    return render(
        request,
        "tienda/empleado/pedido_detalle.html",
        {
            "pedido": pedido,
            "direccion": direccion,
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

        if accion == "preparar":
            if pedido.estado != "PENDIENTE":
                messages.error(request, "Solo puedes preparar pedidos pendientes.")
            else:
                pedido.estado = "PREPARADO"
                pedido.save(update_fields=["estado"])
                messages.success(request, f"El pedido #{pedido.id_pedido} está preparado.")

        elif accion == "pagar":
            if pedido.metodo_pago != "TRANSFERENCIA" or pedido.estado_pago != "PENDIENTE":
                messages.error(request, "Solo se pueden confirmar transferencias pendientes.")
            else:
                pedido.estado_pago = "PAGADO"
                pedido.save(update_fields=["estado_pago"])
                messages.success(request, f"El pago del pedido #{pedido.id_pedido} fue confirmado.")

        elif accion == "cancelar":
            _cancelar_pedido(pedido)
            messages.success(
                request,
                f"El pedido #{pedido.id_pedido} fue cancelado y el inventario fue actualizado.",
            )
            return redirect("tienda:pedidos")

        elif accion == "asignar_repartidor":
            _asignar_repartidor(request, pedido)

        else:
            messages.error(request, "La acción solicitada no es válida.")

    return redirect("tienda:pedido-detalle", id_pedido=id_pedido)


def _cancelar_pedido(pedido: Pedido) -> None:
    detalles = list(
        DetallePedido.objects.filter(id_pedido=pedido).values_list(
            "id_producto_id", "cantidad"
        )
    )
    for id_producto, cantidad in detalles:
        existencia = (
            Inventario.objects.select_for_update()
            .filter(id_sucursal=pedido.id_sucursal_id, id_producto=id_producto)
            .first()
        )
        if existencia is None:
            Inventario.objects.create(
                id_sucursal_id=pedido.id_sucursal_id,
                id_producto_id=id_producto,
                stock_disponible=cantidad,
            )
        else:
            existencia.stock_disponible += cantidad
            existencia.save(update_fields=["stock_disponible"])

    if pedido.id_repartidor_id:
        perfil = PerfilRepartidor.objects.select_for_update().filter(
            pk=pedido.id_repartidor_id
        ).first()
        if perfil is not None:
            _liberar_repartidor_si_sin_pedidos(perfil.pk, pedido.pk)
        pedido.id_repartidor = None

    pedido.estado = "CANCELADO"
    pedido.save(update_fields=["estado", "id_repartidor"])


def _asignar_repartidor(request: HttpRequest, pedido: Pedido) -> None:
    if pedido.tipo_entrega != "DOMICILIO":
        messages.error(request, "Los pedidos para retiro no requieren repartidor.")
        return

    nuevo_id = request.POST.get("id_repartidor")
    if not nuevo_id:
        messages.error(request, "Selecciona un repartidor disponible.")
        return
    try:
        nuevo_id = int(nuevo_id)
    except ValueError:
        messages.error(request, "El repartidor seleccionado no es válido.")
        return
    nuevo_perfil = (
        PerfilRepartidor.objects.select_for_update()
        .filter(
            pk=nuevo_id,
            estado_operativo="LIBRE",
            id_repartidor__activo=True,
            id_repartidor__id_rol__nombre="REPARTIDOR",
        )
        .first()
    )
    if nuevo_perfil is None:
        messages.error(request, "Ese repartidor ya no está disponible.")
        return
    if Pedido.objects.filter(
        id_repartidor_id=nuevo_perfil.pk,
        estado__in=ESTADOS_GESTIONABLES,
    ).exists():
        messages.error(request, "El repartidor seleccionado ya tiene un pedido asignado.")
        return

    if pedido.id_repartidor_id and pedido.id_repartidor_id != nuevo_perfil.pk:
        anterior = PerfilRepartidor.objects.select_for_update().filter(
            pk=pedido.id_repartidor_id
        ).first()
        if anterior is not None:
            otro_pedido_activo = Pedido.objects.filter(
                id_repartidor_id=anterior.pk,
                estado__in=ESTADOS_GESTIONABLES,
            ).exclude(pk=pedido.pk).exists()
            if not otro_pedido_activo:
                anterior.estado_operativo = "LIBRE"
                anterior.save(update_fields=["estado_operativo"])

    nuevo_perfil.estado_operativo = "PENDIENTE"
    nuevo_perfil.save(update_fields=["estado_operativo"])
    pedido.id_repartidor_id = nuevo_perfil.pk
    pedido.save(update_fields=["id_repartidor"])
    messages.success(request, f"El repartidor {nuevo_perfil.id_repartidor.nombre_completo} fue asignado.")


@empleado_required
def inventario_empleado(request: HttpRequest) -> HttpResponse:
    consulta = _productos_con_inventario(request.sucursal_empleado).select_related(
        "id_categoria"
    )
    busqueda = request.GET.get("q", "").strip()
    if busqueda:
        consulta = consulta.filter(
            Q(nombre__icontains=busqueda)
            | Q(codigo_barras__icontains=busqueda)
        )
    if request.GET.get("bajo") == "1":
        consulta = consulta.filter(stock_disponible__lte=5)
    pagina = Paginator(consulta.order_by("nombre"), 25).get_page(
        request.GET.get("page")
    )
    return render(
        request,
        "tienda/empleado/inventario.html",
        {"inventario": pagina, "busqueda": busqueda, "solo_bajo": request.GET.get("bajo") == "1"},
    )


@require_http_methods(["GET", "POST"])
@empleado_required
def editar_inventario(request: HttpRequest, id_producto: int) -> HttpResponse:
    producto = get_object_or_404(Producto, pk=id_producto, activo=True)
    sucursal = request.sucursal_empleado
    existencia = Inventario.objects.filter(
        id_sucursal=sucursal,
        id_producto=producto,
    ).first()
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
            if existencia is None:
                Inventario.objects.create(
                    id_sucursal=sucursal,
                    id_producto=producto,
                    stock_disponible=form.cleaned_data["stock_disponible"],
                    fecha_vencimiento=form.cleaned_data["fecha_vencimiento"],
                )
            else:
                existencia.stock_disponible = form.cleaned_data["stock_disponible"]
                existencia.fecha_vencimiento = form.cleaned_data["fecha_vencimiento"]
                existencia.save(update_fields=["stock_disponible", "fecha_vencimiento"])
        messages.success(request, f"Inventario actualizado para {producto.nombre}.")
        return redirect("tienda:inventario")

    return render(
        request,
        "tienda/empleado/inventario_editar.html",
        {"producto": producto, "form": form},
    )
