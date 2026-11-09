import calendar
from decimal import Decimal
from functools import wraps
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import (
    Count,
    DateField,
    DecimalField,
    Exists,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, TruncDate
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
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
    ESTADOS_VISIBLES_SUCURSAL,
    UMBRAL_BAJO_STOCK,
    actualizar_repartidor_pedido,
    cancelar_pedido,
    direccion_pedido,
    es_admin,
    es_empleado,
    es_personal,
    generar_jwt_seguimiento,
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

METODOS_PAGO_FILTRO_CHOICES = (
    ("TARJETA", "Tarjeta"),
    ("TRANSFERENCIA", "Transferencia"),
    ("CONTRA_ENTREGA", "Contra entrega"),
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


def _nombre_sucursal_corto(nombre: str) -> str:
    limpio = (nombre or "").strip()
    if limpio.lower().startswith("minimarket "):
        return limpio[11:].strip() or limpio
    return limpio


def _badge_stock(stock: int):
    if stock <= 0:
        return format_html(
            '<div class="flex items-center gap-1.5"><span class="stock low">{} uds</span>'
            '<span class="badge cancelado">{}</span></div>',
            0,
            "Agotado",
        )
    if stock <= UMBRAL_BAJO_STOCK:
        return format_html(
            '<div class="flex items-center gap-1.5"><span class="stock low">{} uds</span>'
            '<span class="badge pendiente">Bajo stock</span></div>',
            stock,
        )
    return format_html(
        '<div class="flex items-center gap-1.5"><span class="stock">{} uds</span>'
        '<span class="badge aprobado">Disponible</span></div>',
        stock,
    )


def _resumen_ubicaciones_html(producto: Producto, sucursal_id_filtro: int | None = None):
    inventarios = list(getattr(producto, "inventarios_prefetched", None) or producto.inventarios.all())
    if sucursal_id_filtro is not None:
        inventarios = [inv for inv in inventarios if inv.id_sucursal_id == sucursal_id_filtro]
    if not inventarios:
        return format_html(
            '<span class="text-xs text-mm-muted">{}</span>',
            "Sin stock asignado en sucursales",
        )

    items = []
    for inv in inventarios:
        stock = int(inv.stock_disponible or 0)
        nombre_corto = _nombre_sucursal_corto(inv.id_sucursal.nombre)
        if stock == 0:
            clases_caja = "border-red-200 bg-red-50/70 text-red-900"
            clases_punto = "bg-red-500"
            clases_num = "text-red-700"
        elif stock <= UMBRAL_BAJO_STOCK:
            clases_caja = "border-amber-200 bg-amber-50/70 text-amber-900"
            clases_punto = "bg-amber-500"
            clases_num = "text-amber-800"
        else:
            clases_caja = "border-mm-line bg-white text-mm-ink"
            clases_punto = "bg-emerald-500"
            clases_num = "text-emerald-800"
        items.append((inv.id_sucursal.nombre, clases_caja, clases_punto, nombre_corto, clases_num, stock))

    return format_html(
        '<div class="grid grid-cols-2 sm:grid-cols-3 gap-1.5 min-w-[260px]">{}</div>',
        format_html_join(
            "",
            '<div title="{}" class="flex items-center justify-between gap-2 rounded-md border px-2 py-1 text-xs {}">'
            '<span class="inline-flex items-center gap-1.5 truncate font-medium">'
            '<span class="h-1.5 w-1.5 rounded-full shrink-0 {}"></span>{}</span>'
            '<strong class="font-bold tabular-nums {}">{}</strong></div>',
            items,
        ),
    )


def _serializar_seguimiento_pedido(pedido: Pedido, direccion) -> dict:
    sucursal = pedido.id_sucursal
    perfil_rep = pedido.id_repartidor
    usuario_rep = getattr(perfil_rep, "id_repartidor", None) if perfil_rep else None

    # Si el repartidor ya aceptó el pedido o está transmitiendo pulso GPS activo para este pedido,
    # reflejar automáticamente el estado EN_RUTA en la base de datos y en el portal web.
    if perfil_rep is not None and pedido.estado in {"PENDIENTE", "PREPARADO"}:
        pulso_activo = (
            perfil_rep.ultima_actualizacion is not None
            and pedido.fecha_creacion is not None
            and perfil_rep.ultima_actualizacion >= pedido.fecha_creacion
        )
        if getattr(pedido, "estado_repartidor", None) == "ACEPTADO" or pulso_activo:
            pedido.estado = "EN_RUTA"
            pedido.estado_repartidor = "ACEPTADO"
            Pedido.objects.filter(pk=pedido.pk).update(
                estado="EN_RUTA",
                estado_repartidor="ACEPTADO",
            )

    suc_lat = float(sucursal.latitud) if sucursal and sucursal.latitud is not None else 13.4834
    suc_lng = float(sucursal.longitud) if sucursal and sucursal.longitud is not None else -88.1833

    dir_lat = (
        float(direccion.latitud)
        if direccion and direccion.latitud is not None
        else round(suc_lat + 0.0055, 6)
    )
    dir_lng = (
        float(direccion.longitud)
        if direccion and direccion.longitud is not None
        else round(suc_lng + 0.0045, 6)
    )

    rep_lat = (
        float(perfil_rep.latitud_actual)
        if perfil_rep and perfil_rep.latitud_actual is not None
        else suc_lat
    )
    rep_lng = (
        float(perfil_rep.longitud_actual)
        if perfil_rep and perfil_rep.longitud_actual is not None
        else suc_lng
    )

    datos_direccion = (
        {
            "id_direccion": direccion.id_direccion,
            "detalle_direccion": direccion.detalle_direccion,
            "punto_referencia": direccion.punto_referencia,
            "latitud": dir_lat,
            "longitud": dir_lng,
        }
        if direccion
        else {
            "id_direccion": None,
            "detalle_direccion": "Dirección de entrega a domicilio",
            "punto_referencia": None,
            "latitud": dir_lat,
            "longitud": dir_lng,
        }
    )

    return {
        "id_pedido": pedido.id_pedido,
        "codigo_pedido": pedido.codigo_pedido,
        "estado": pedido.estado,
        "estado_display": pedido.get_estado_display(),
        "estado_pago": pedido.estado_pago,
        "estado_pago_display": pedido.get_estado_pago_display(),
        "metodo_pago": pedido.metodo_pago,
        "metodo_pago_display": pedido.get_metodo_pago_display(),
        "tipo_entrega": pedido.tipo_entrega,
        "sucursal": {
            "id_sucursal": sucursal.id_sucursal,
            "nombre": sucursal.nombre,
            "direccion": sucursal.direccion,
            "latitud": suc_lat,
            "longitud": suc_lng,
        }
        if sucursal
        else None,
        "direccion_entrega": datos_direccion,
        "destino": datos_direccion,
        "repartidor": {
            "id_repartidor": perfil_rep.id_repartidor_id,
            "nombre_completo": usuario_rep.nombre_completo if usuario_rep else "Repartidor",
            "nombre": usuario_rep.nombre_completo if usuario_rep else "Repartidor",
            "telefono": usuario_rep.telefono if usuario_rep else None,
            "estado_operativo": perfil_rep.estado_operativo,
            "latitud": rep_lat,
            "longitud": rep_lng,
            "ultima_actualizacion": perfil_rep.ultima_actualizacion.isoformat()
            if perfil_rep.ultima_actualizacion is not None
            else None,
        }
        if perfil_rep
        else None,
    }


def _contexto_seguimiento_pedido(request: HttpRequest, pedido: Pedido, direccion) -> dict:
    estado_inicial = _serializar_seguimiento_pedido(pedido, direccion)
    return {
        "fastapi_base_url": getattr(settings, "FASTAPI_BASE_URL", "https://minimarket-api.duckdns.org"),
        "fastapi_ws_url": getattr(settings, "FASTAPI_WS_URL", "wss://minimarket-api.duckdns.org"),
        "jwt_seguimiento": generar_jwt_seguimiento(request.user),
        "telemetria_url": reverse("tienda:pedido-telemetria", args=[pedido.id_pedido]),
        "seguimiento_inicial_json": json.dumps(estado_inicial),
    }


@login_required(login_url="tienda:empleado-login")
def pedido_telemetria_json(request: HttpRequest, id_pedido: int) -> HttpResponse:
    if not (es_admin(request.user) or es_empleado(request.user)):
        return HttpResponseForbidden("Sin permisos.")

    filtros = {"id_pedido": id_pedido}
    if not es_admin(request.user):
        sucursal_emp = obtener_sucursal_empleado(request.user)
        if sucursal_emp is None:
            return HttpResponseForbidden("Sin sucursal asignada.")
        filtros["id_sucursal"] = sucursal_emp

    pedido = get_object_or_404(
        Pedido.objects.select_related(
            "id_cliente", "id_sucursal", "id_repartidor__id_repartidor"
        ),
        **filtros,
    )
    direccion = direccion_pedido(pedido)
    return JsonResponse(_serializar_seguimiento_pedido(pedido, direccion))


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
    sucursales=None,
    sucursal_filtro: str = "",
    categorias=None,
    categoria_filtro: str = "",
    estados_opciones=None,
    estado_filtro: str = "",
    metodos_pago_opciones=None,
    metodo_pago_filtro: str = "",
    mostrar_filtro_stock: bool = False,
    stock_filtro: str = "",
    extra_links=None,
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
            "sucursales": sucursales,
            "sucursal_filtro": sucursal_filtro,
            "categorias": categorias,
            "categoria_filtro": categoria_filtro,
            "estados_opciones": estados_opciones,
            "estado_filtro": estado_filtro,
            "metodos_pago_opciones": metodos_pago_opciones,
            "metodo_pago_filtro": metodo_pago_filtro,
            "mostrar_filtro_stock": mostrar_filtro_stock,
            "stock_filtro": stock_filtro,
            "umbral_bajo_stock": UMBRAL_BAJO_STOCK,
            "extra_links": extra_links,
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
    id_sucursal = request.GET.get("id_sucursal", "").strip()
    sucursales_todas = list(Sucursal.objects.order_by("nombre"))
    sucursal_seleccionada = None

    pedidos_qs = Pedido.objects.all()
    inventario_qs = Inventario.objects.filter(id_producto__activo=True)
    if id_sucursal.isdecimal():
        suc_id = int(id_sucursal)
        pedidos_qs = pedidos_qs.filter(id_sucursal_id=suc_id)
        inventario_qs = inventario_qs.filter(id_sucursal_id=suc_id)
        sucursal_seleccionada = next((s for s in sucursales_todas if s.pk == suc_id), None)

    venta_confirmada_q = ~Q(estado="CANCELADO") & (Q(estado="ENTREGADO") | Q(estado_pago="PAGADO"))
    resumen = pedidos_qs.aggregate(
        total_pedidos=Count("id_pedido"),
        pedidos_hoy=Count("id_pedido", filter=Q(fecha_creacion__date=hoy)),
        pendientes=Count("id_pedido", filter=Q(estado="PENDIENTE")),
        preparados=Count("id_pedido", filter=Q(estado="PREPARADO")),
        en_ruta=Count("id_pedido", filter=Q(estado="EN_RUTA")),
        pagos_pendientes=Count(
            "id_pedido",
            filter=Q(estado_pago="PENDIENTE") & ~Q(estado="CANCELADO"),
        ),
        ventas_entregadas=Coalesce(
            Sum("total", filter=venta_confirmada_q),
            Decimal("0.00"),
            output_field=DecimalField(),
        ),
    )

    bajo_stock_qs = (
        inventario_qs.filter(stock_disponible__lte=UMBRAL_BAJO_STOCK)
        .select_related("id_producto__id_categoria", "id_sucursal")
        .order_by("stock_disponible", "id_producto__nombre", "id_sucursal__nombre")
    )
    inventario_bajo_count = bajo_stock_qs.count()
    inventario_agotado_count = inventario_qs.filter(stock_disponible=0).count()

    bajo_stock_subq = (
        Inventario.objects.filter(
            id_sucursal=OuterRef("pk"),
            id_producto__activo=True,
            stock_disponible__lte=UMBRAL_BAJO_STOCK,
        )
        .values("id_sucursal")
        .annotate(cnt=Count("id_inventario"))
        .values("cnt")[:1]
    )

    sucursales_tabla_qs = Sucursal.objects.select_related("id_municipio")
    if sucursal_seleccionada:
        sucursales_tabla_qs = sucursales_tabla_qs.filter(pk=sucursal_seleccionada.pk)

    sucursales = list(
        sucursales_tabla_qs.annotate(
            total_pedidos=Count("pedidos"),
            pedidos_pendientes=Count("pedidos", filter=Q(pedidos__estado="PENDIENTE")),
            pedidos_preparados=Count("pedidos", filter=Q(pedidos__estado="PREPARADO")),
            pedidos_en_ruta=Count("pedidos", filter=Q(pedidos__estado="EN_RUTA")),
            ventas_entregadas=Coalesce(
                Sum(
                    "pedidos__total",
                    filter=~Q(pedidos__estado="CANCELADO")
                    & (Q(pedidos__estado="ENTREGADO") | Q(pedidos__estado_pago="PAGADO")),
                ),
                Decimal("0.00"),
                output_field=DecimalField(),
            ),
            bajo_stock_count=Coalesce(
                Subquery(bajo_stock_subq, output_field=IntegerField()),
                Value(0),
            ),
        ).order_by("nombre")
    )

    return render(
        request,
        "tienda/admin/dashboard.html",
        {
            **resumen,
            "sucursales": sucursales,
            "sucursales_todas": sucursales_todas,
            "sucursal_filtro": id_sucursal,
            "sucursal_seleccionada": sucursal_seleccionada,
            "inventario_bajo": inventario_bajo_count,
            "inventario_agotado": inventario_agotado_count,
            "productos_bajo_stock": list(bajo_stock_qs[:15]),
            "umbral_bajo_stock": UMBRAL_BAJO_STOCK,
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
    id_sucursal = request.GET.get("id_sucursal", "").strip()
    if id_sucursal.isdecimal():
        consulta = consulta.filter(id_sucursal_id=int(id_sucursal))

    estado = request.GET.get("estado", "").strip()
    estados_validos = {choice[0] for choice in Pedido.ESTADO_CHOICES}
    if estado in estados_validos:
        consulta = consulta.filter(estado=estado)

    metodo_pago = request.GET.get("metodo_pago", "").strip()
    if metodo_pago in {"WOMPI", "TARJETA"}:
        consulta = consulta.filter(metodo_pago__in=["WOMPI", "TARJETA"])
        metodo_pago = "TARJETA"
    elif metodo_pago in {"TRANSFERENCIA", "CONTRA_ENTREGA"}:
        consulta = consulta.filter(metodo_pago=metodo_pago)

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
        description="Historial completo. Filtra por sucursal, estado, método de pago o código de pedido.",
        columns=("Código de Pedido", "Cliente", "Sucursal", "Método / Pago", "Estado", "Total", "Creado"),
        queryset=consulta.order_by("-fecha_creacion", "-id_pedido"),
        sucursales=Sucursal.objects.order_by("nombre"),
        sucursal_filtro=id_sucursal,
        estados_opciones=Pedido.ESTADO_CHOICES,
        estado_filtro=estado,
        metodos_pago_opciones=METODOS_PAGO_FILTRO_CHOICES,
        metodo_pago_filtro=metodo_pago,
        row_builder=lambda pedido: {
            "cells": (
                pedido.codigo_pedido or f"#{pedido.id_pedido}",
                pedido.id_cliente.nombre_completo,
                pedido.id_sucursal.nombre,
                format_html(
                    '<div class="flex flex-wrap items-center gap-1.5">'
                    '<span class="text-xs font-medium text-mm-ink">{}</span>'
                    '<span class="badge {}">{}</span></div>',
                    pedido.get_metodo_pago_display(),
                    "aprobado" if pedido.estado_pago == "PAGADO" else "pendiente",
                    pedido.get_estado_pago_display(),
                ),
                _badge(pedido.get_estado_display(), pedido.estado.lower()),
                f"$ {pedido.total:.2f}",
                pedido.fecha_creacion,
            ),
            "url": reverse("tienda:admin-pedido-detalle", args=[pedido.pk]),
            "label": "Ver / seguir GPS" if pedido.tipo_entrega == "DOMICILIO" else "Ver / editar",
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
                    pedido_lock.save(update_fields=["estado", "estado_pago", "id_repartidor", "estado_repartidor"])
                else:
                    actualizar_repartidor_pedido(pedido_lock, nuevo_repartidor)
                    pedido_lock.estado = nuevo_estado
                    pedido_lock.estado_pago = nuevo_estado_pago
                    if nuevo_estado == "EN_RUTA" and pedido_lock.id_repartidor_id:
                        pedido_lock.estado_repartidor = "ACEPTADO"
                    elif nuevo_estado == "ENTREGADO":
                        pedido_lock.estado_repartidor = None
                    pedido_lock.save(update_fields=["estado", "estado_pago", "id_repartidor", "estado_repartidor"])
                    if nuevo_estado == "ENTREGADO" and pedido_lock.id_repartidor_id:
                        liberar_repartidor_si_sin_pedidos(pedido_lock.id_repartidor_id, pedido_lock.pk)
            except ValueError as error:
                messages.error(request, str(error))
                return redirect("tienda:admin-pedido-detalle", id_pedido=pedido_lock.pk)

        codigo_ref = pedido.codigo_pedido or f"#{pedido.id_pedido}"
        messages.success(request, f"El pedido {codigo_ref} fue actualizado.")
        return redirect("tienda:admin-pedido-detalle", id_pedido=pedido.pk)

    direccion = direccion_pedido(pedido)
    return render(
        request,
        "tienda/admin/pedido_detalle.html",
        {
            "pedido": pedido,
            "form": form,
            "detalles": list(pedido.detalles.all()),
            "direccion": direccion,
            **_contexto_seguimiento_pedido(request, pedido, direccion),
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
    id_sucursal = request.GET.get("id_sucursal", "").strip()
    id_categoria = request.GET.get("id_categoria", "").strip()
    stock_filtro = request.GET.get("bajo", "").strip()
    suc_id_int = int(id_sucursal) if id_sucursal.isdecimal() else None

    inv_filter = Q(inventarios__id_sucursal_id=suc_id_int) if suc_id_int else Q()
    productos = (
        _filtro_texto(
            Producto.objects.select_related("id_categoria").prefetch_related(
                Prefetch(
                    "inventarios",
                    queryset=Inventario.objects.select_related("id_sucursal").order_by("id_sucursal__nombre"),
                    to_attr="inventarios_prefetched",
                )
            ),
            request,
            "nombre",
            "codigo_barras",
        )
        .annotate(
            stock_total=Coalesce(
                Sum("inventarios__stock_disponible", filter=inv_filter),
                Value(0),
                output_field=IntegerField(),
            )
        )
    )

    if id_categoria.isdecimal():
        productos = productos.filter(id_categoria_id=int(id_categoria))

    if stock_filtro == "agotado":
        if suc_id_int:
            productos = productos.filter(
                ~Exists(
                    Inventario.objects.filter(
                        id_producto=OuterRef("pk"),
                        id_sucursal_id=suc_id_int,
                        stock_disponible__gt=0,
                    )
                )
            )
        else:
            productos = productos.filter(stock_total=0)
    elif stock_filtro == "1":
        bajo_subq = Inventario.objects.filter(
            id_producto=OuterRef("pk"),
            stock_disponible__lte=UMBRAL_BAJO_STOCK,
        )
        if suc_id_int:
            bajo_subq = bajo_subq.filter(id_sucursal_id=suc_id_int)
        productos = productos.filter(Exists(bajo_subq) | Q(stock_total__lte=UMBRAL_BAJO_STOCK))

    return _portal_list(
        request,
        title="Productos",
        description="Catálogo global con stock total y desglose de existencias por sucursal.",
        columns=("Producto", "Categoría", "Precio", "Stock Total", "Dónde están (Sucursales)", "Estado"),
        queryset=productos.order_by("nombre"),
        sucursales=Sucursal.objects.order_by("nombre"),
        sucursal_filtro=id_sucursal,
        categorias=Categoria.objects.order_by("nombre"),
        categoria_filtro=id_categoria,
        mostrar_filtro_stock=True,
        stock_filtro=stock_filtro,
        extra_links=[{"url": reverse("tienda:admin-stock-general"), "label": "Vista Stock General"}],
        row_builder=lambda p: {
            "cells": (
                p.nombre,
                p.id_categoria.nombre,
                f"$ {p.precio_base:.2f}",
                _badge_stock(int(p.stock_total or 0)),
                _resumen_ubicaciones_html(p, suc_id_int),
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
        Inventario.objects.select_related("id_sucursal", "id_producto__id_categoria"),
        request,
        "id_producto__nombre",
        "id_producto__codigo_barras",
        "id_sucursal__nombre",
    )

    id_sucursal = request.GET.get("id_sucursal", "").strip()
    if id_sucursal.isdecimal():
        inventarios = inventarios.filter(id_sucursal_id=int(id_sucursal))

    id_categoria = request.GET.get("id_categoria", "").strip()
    if id_categoria.isdecimal():
        inventarios = inventarios.filter(id_producto__id_categoria_id=int(id_categoria))

    stock_filtro = request.GET.get("bajo", "").strip()
    if stock_filtro == "agotado":
        inventarios = inventarios.filter(stock_disponible=0)
    elif stock_filtro == "1":
        inventarios = inventarios.filter(stock_disponible__lte=UMBRAL_BAJO_STOCK)

    orden = (
        ("stock_disponible", "id_producto__nombre", "id_sucursal__nombre")
        if stock_filtro in {"1", "agotado"}
        else ("id_sucursal__nombre", "id_producto__nombre")
    )

    return _portal_list(
        request,
        title="Inventario por sucursal",
        description=f"Filtra por sucursal, categoría o productos en bajo stock (≤ {UMBRAL_BAJO_STOCK} unidades).",
        columns=("Sucursal", "Producto", "Categoría", "Existencias / Estado", "Vencimiento"),
        queryset=inventarios.order_by(*orden),
        sucursales=Sucursal.objects.order_by("nombre"),
        sucursal_filtro=id_sucursal,
        categorias=Categoria.objects.order_by("nombre"),
        categoria_filtro=id_categoria,
        mostrar_filtro_stock=True,
        stock_filtro=stock_filtro,
        extra_links=[
            {"url": reverse("tienda:admin-stock-general"), "label": "Ver stock general por producto"},
            {
                "url": f"{reverse('tienda:admin-inventario-documento')}{'?' + request.GET.urlencode() if request.GET else ''}",
                "label": "🖨️ Imprimir hoja de control",
            },
        ],
        row_builder=lambda i: {
            "cells": (
                i.id_sucursal.nombre,
                i.id_producto.nombre,
                i.id_producto.id_categoria.nombre,
                _badge_stock(int(i.stock_disponible or 0)),
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
def stock_general_admin(request: HttpRequest) -> HttpResponse:
    busqueda = request.GET.get("q", "").strip()
    id_sucursal = request.GET.get("id_sucursal", "").strip()
    id_categoria = request.GET.get("id_categoria", "").strip()
    stock_filtro = request.GET.get("bajo", "").strip()

    sucursales = list(Sucursal.objects.filter(activa=True).order_by("nombre"))
    sucursal_seleccionada = None
    sucursales_evaluadas = sucursales
    if id_sucursal.isdecimal():
        suc_id = int(id_sucursal)
        sucursal_seleccionada = next((s for s in sucursales if s.pk == suc_id), None)
        if sucursal_seleccionada:
            sucursales_evaluadas = [sucursal_seleccionada]

    productos_qs = Producto.objects.filter(activo=True).select_related("id_categoria").prefetch_related(
        Prefetch(
            "inventarios",
            queryset=Inventario.objects.select_related("id_sucursal").order_by("id_sucursal__nombre"),
            to_attr="inventarios_prefetched",
        )
    )
    if busqueda:
        productos_qs = productos_qs.filter(
            Q(nombre__icontains=busqueda) | Q(codigo_barras__icontains=busqueda)
        )
    if id_categoria.isdecimal():
        productos_qs = productos_qs.filter(id_categoria_id=int(id_categoria))

    inv_resumen_qs = Inventario.objects.filter(id_producto__activo=True)
    if sucursal_seleccionada:
        inv_resumen_qs = inv_resumen_qs.filter(id_sucursal=sucursal_seleccionada)
    if id_categoria.isdecimal():
        inv_resumen_qs = inv_resumen_qs.filter(id_producto__id_categoria_id=int(id_categoria))

    resumen_stock = inv_resumen_qs.aggregate(
        unidades_totales=Coalesce(Sum("stock_disponible"), Value(0), output_field=IntegerField()),
        registros_bajo_stock=Count("id_inventario", filter=Q(stock_disponible__lte=UMBRAL_BAJO_STOCK)),
        registros_agotados=Count("id_inventario", filter=Q(stock_disponible=0)),
    )
    resumen_stock["total_productos"] = productos_qs.count()

    filas = []
    for prod in productos_qs.order_by("nombre"):
        mapa_inv = {inv.id_sucursal_id: inv for inv in getattr(prod, "inventarios_prefetched", [])}
        ubicaciones = []
        stock_total = 0
        tiene_bajo = False
        tiene_agotado = False

        for suc in sucursales_evaluadas:
            inv = mapa_inv.get(suc.pk)
            stock_suc = int(inv.stock_disponible) if inv is not None else 0
            stock_total += stock_suc
            es_bajo = stock_suc <= UMBRAL_BAJO_STOCK
            if es_bajo:
                tiene_bajo = True
            if stock_suc == 0:
                tiene_agotado = True
            ubicaciones.append(
                {
                    "id_sucursal": suc.pk,
                    "sucursal": suc.nombre,
                    "sucursal_corta": _nombre_sucursal_corto(suc.nombre),
                    "stock": stock_suc,
                    "es_bajo": es_bajo,
                    "id_inventario": inv.pk if inv is not None else None,
                }
            )

        if stock_filtro == "agotado" and not tiene_agotado:
            continue
        if stock_filtro == "1" and not tiene_bajo:
            continue

        filas.append(
            {
                "id_producto": prod.pk,
                "nombre": prod.nombre,
                "codigo_barras": prod.codigo_barras,
                "categoria": prod.id_categoria.nombre,
                "precio_base": prod.precio_base,
                "stock_total": stock_total,
                "tiene_bajo_stock": tiene_bajo,
                "ubicaciones": ubicaciones,
            }
        )

    if stock_filtro in {"1", "agotado"}:
        filas.sort(key=lambda item: (item["stock_total"], item["nombre"]))

    page_obj = Paginator(filas, PAGE_SIZE).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)

    return render(
        request,
        "tienda/admin/stock_general.html",
        {
            "productos": list(page_obj.object_list),
            "page_obj": page_obj,
            "query_base": params.urlencode(),
            "busqueda": busqueda,
            "sucursales": sucursales,
            "sucursal_filtro": id_sucursal,
            "sucursal_seleccionada": sucursal_seleccionada,
            "categorias": Categoria.objects.order_by("nombre"),
            "categoria_filtro": id_categoria,
            "stock_filtro": stock_filtro,
            "umbral_bajo_stock": UMBRAL_BAJO_STOCK,
            "resumen_stock": resumen_stock,
            "base_template": "tienda/admin/base.html",
        },
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
    id_sucursal = request.GET.get("id_sucursal", "").strip()
    id_categoria = request.GET.get("id_categoria", "").strip()
    stock_filtro = request.GET.get("bajo", "").strip()
    suc_id_int = int(id_sucursal) if id_sucursal.isdecimal() else None

    inv_filter = Q(inventarios__id_sucursal_id=suc_id_int) if suc_id_int else Q()
    productos = (
        _filtro_texto(
            Producto.objects.filter(activo=True)
            .select_related("id_categoria")
            .prefetch_related(
                Prefetch(
                    "inventarios",
                    queryset=Inventario.objects.select_related("id_sucursal").order_by("id_sucursal__nombre"),
                    to_attr="inventarios_prefetched",
                )
            ),
            request,
            "nombre",
            "codigo_barras",
        )
        .annotate(
            stock_total=Coalesce(
                Sum("inventarios__stock_disponible", filter=inv_filter),
                Value(0),
                output_field=IntegerField(),
            )
        )
    )
    if id_categoria.isdecimal():
        productos = productos.filter(id_categoria_id=int(id_categoria))
    if stock_filtro == "agotado":
        productos = productos.filter(stock_total=0)
    elif stock_filtro == "1":
        productos = productos.filter(stock_total__lte=UMBRAL_BAJO_STOCK)

    return _portal_list(
        request,
        title="Consultar productos y stock general",
        description="Catálogo de consulta con existencias totales y ubicación por sucursal.",
        columns=("Producto", "Categoría", "Precio", "Stock Total", "Dónde están (Sucursales)"),
        queryset=productos.order_by("nombre"),
        sucursales=Sucursal.objects.filter(activa=True).order_by("nombre"),
        sucursal_filtro=id_sucursal,
        categorias=Categoria.objects.order_by("nombre"),
        categoria_filtro=id_categoria,
        mostrar_filtro_stock=True,
        stock_filtro=stock_filtro,
        row_builder=lambda p: {
            "cells": (
                p.nombre,
                p.id_categoria.nombre,
                f"$ {p.precio_base:.2f}",
                _badge_stock(int(p.stock_total or 0)),
                _resumen_ubicaciones_html(p, suc_id_int),
            )
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
        estado__in=ESTADOS_VISIBLES_SUCURSAL,
    ).aggregate(
        pedidos_pendientes=Count("id_pedido", filter=Q(estado="PENDIENTE")),
        pedidos_preparados=Count("id_pedido", filter=Q(estado="PREPARADO")),
        pedidos_en_ruta=Count("id_pedido", filter=Q(estado="EN_RUTA")),
    )
    productos_bajos = (
        _productos_con_inventario(sucursal)
        .select_related("id_categoria")
        .filter(stock_disponible__lte=UMBRAL_BAJO_STOCK)
        .order_by("stock_disponible", "nombre")
    )
    return render(
        request,
        "tienda/empleado/panel.html",
        {
            "sucursal": sucursal,
            **conteos,
            "productos_bajos": list(productos_bajos[:10]),
            "total_productos_bajos": productos_bajos.count(),
            "umbral_bajo_stock": UMBRAL_BAJO_STOCK,
        },
    )


@empleado_required
def pedidos_empleado(request: HttpRequest) -> HttpResponse:
    consulta = Pedido.objects.filter(
        id_sucursal=request.sucursal_empleado,
        estado__in=ESTADOS_VISIBLES_SUCURSAL,
    ).select_related("id_cliente", "id_repartidor__id_repartidor")

    estado = request.GET.get("estado", "")
    if estado in ESTADOS_VISIBLES_SUCURSAL:
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
        estado__in=ESTADOS_VISIBLES_SUCURSAL,
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
    direccion = direccion_pedido(pedido)
    return render(
        request,
        "tienda/empleado/pedido_detalle.html",
        {
            "pedido": pedido,
            "direccion": direccion,
            "repartidores": repartidores,
            **_contexto_seguimiento_pedido(request, pedido, direccion),
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
                    pedido.save(update_fields=["id_repartidor", "estado_repartidor"])
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
    id_categoria = request.GET.get("id_categoria", "").strip()
    stock_filtro = request.GET.get("bajo", "").strip()

    if busqueda:
        consulta = consulta.filter(
            Q(nombre__icontains=busqueda) | Q(codigo_barras__icontains=busqueda)
        )
    if id_categoria.isdecimal():
        consulta = consulta.filter(id_categoria_id=int(id_categoria))
    if stock_filtro == "agotado":
        consulta = consulta.filter(stock_disponible=0)
    elif stock_filtro == "1":
        consulta = consulta.filter(stock_disponible__lte=UMBRAL_BAJO_STOCK)

    orden = ("stock_disponible", "nombre") if stock_filtro in {"1", "agotado"} else ("nombre",)
    pagina = Paginator(consulta.order_by(*orden), 25).get_page(request.GET.get("page"))
    return render(
        request,
        "tienda/empleado/inventario.html",
        {
            "sucursal": request.sucursal_empleado,
            "inventario": pagina,
            "busqueda": busqueda,
            "categorias": Categoria.objects.order_by("nombre"),
            "categoria_filtro": id_categoria,
            "stock_filtro": stock_filtro,
            "solo_bajo": stock_filtro == "1",
            "umbral_bajo_stock": UMBRAL_BAJO_STOCK,
        },
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
    valido_q = ~Q(estado="CANCELADO")
    confirmado_q = valido_q & (Q(estado="ENTREGADO") | Q(estado_pago="PAGADO"))
    en_proceso_q = Q(estado__in=["PENDIENTE", "PREPARADO", "EN_RUTA"])
    wompi_q = Q(metodo_pago__in=["WOMPI", "TARJETA"])
    cero = Decimal("0.00")

    agregados = pedidos_periodo.aggregate(
        total_pedidos_periodo=Count("id_pedido"),
        pedidos_entregados_count=Count("id_pedido", filter=entregado_q),
        pedidos_confirmados_count=Count("id_pedido", filter=confirmado_q),
        pedidos_validos_count=Count("id_pedido", filter=valido_q),
        pedidos_en_proceso_count=Count("id_pedido", filter=en_proceso_q),
        pedidos_cancelados_count=Count("id_pedido", filter=Q(estado="CANCELADO")),
        ventas_confirmadas=Coalesce(Sum("total", filter=confirmado_q), cero, output_field=DecimalField()),
        ventas_validas=Coalesce(Sum("total", filter=valido_q), cero, output_field=DecimalField()),
        ventas_en_proceso=Coalesce(
            Sum("total", filter=en_proceso_q & Q(estado_pago="PENDIENTE")),
            cero,
            output_field=DecimalField(),
        ),
        ventas_wompi=Coalesce(
            Sum("total", filter=valido_q & wompi_q),
            cero,
            output_field=DecimalField(),
        ),
        ventas_transferencia=Coalesce(
            Sum("total", filter=valido_q & Q(metodo_pago="TRANSFERENCIA")),
            cero,
            output_field=DecimalField(),
        ),
        ventas_efectivo=Coalesce(
            Sum("total", filter=valido_q & Q(metodo_pago="CONTRA_ENTREGA")),
            cero,
            output_field=DecimalField(),
        ),
        wompi_count=Count("id_pedido", filter=valido_q & wompi_q),
        transferencia_count=Count("id_pedido", filter=valido_q & Q(metodo_pago="TRANSFERENCIA")),
        efectivo_count=Count("id_pedido", filter=valido_q & Q(metodo_pago="CONTRA_ENTREGA")),
    )

    total_pedidos = agregados["total_pedidos_periodo"] or 0
    entregados_count = agregados["pedidos_entregados_count"] or 0
    confirmados_count = agregados["pedidos_confirmados_count"] or 0
    validos_count = agregados["pedidos_validos_count"] or 0
    cancelados_count = agregados["pedidos_cancelados_count"] or 0

    ventas_confirmadas = agregados["ventas_confirmadas"] or cero
    ventas_validas = agregados["ventas_validas"] or cero
    total_ventas = ventas_confirmadas if ventas_confirmadas > cero else ventas_validas

    # Efectividad: pedidos no cancelados que fueron entregados o pagados exitosamente sobre el total
    if total_pedidos > 0:
        exitosos = max(entregados_count, confirmados_count, validos_count - cancelados_count if entregados_count == 0 else entregados_count)
        tasa_efectividad = round(((total_pedidos - cancelados_count) / total_pedidos) * 100, 1)
    else:
        tasa_efectividad = 0.0

    ticket_promedio = (
        round(float(ventas_validas) / validos_count, 2) if validos_count > 0 else 0.0
    )

    pedidos_grafica_qs = pedidos_periodo.exclude(estado="CANCELADO")
    num_dias = calendar.monthrange(anio, mes)[1]
    dias_dict = {d: 0.0 for d in range(1, num_dias + 1)}

    ventas_por_dia = (
        pedidos_grafica_qs.annotate(
            dia=TruncDate("fecha_creacion", tzinfo=timezone.get_current_timezone())
        )
        .values("dia")
        .annotate(total_dia=Sum("total"))
    )
    for row in ventas_por_dia:
        if row["dia"] and row["dia"].month == mes:
            dias_dict[row["dia"].day] = float(row["total_dia"] or 0)

    top_productos = [
        {
            "nombre": p["id_producto__nombre"],
            "unidades": p["unidades"],
            "total_recaudado": float(p["total_recaudado"] or 0),
        }
        for p in (
            DetallePedido.objects.filter(id_pedido__in=pedidos_grafica_qs)
            .values("id_producto__nombre")
            .annotate(unidades=Sum("cantidad"), total_recaudado=Sum("subtotal"))
            .order_by("-unidades", "-total_recaudado")[:8]
        )
    ]

    rendimiento_repartidores = [
        {
            "nombre": r["id_repartidor__id_repartidor__nombre_completo"],
            "estado": r["id_repartidor__estado_operativo"],
            "entregas": r["entregas"],
        }
        for r in (
            pedidos_grafica_qs.filter(id_repartidor__isnull=False)
            .values(
                "id_repartidor__id_repartidor__nombre_completo",
                "id_repartidor__estado_operativo",
            )
            .annotate(entregas=Count("id_pedido"))
            .order_by("-entregas")[:8]
        )
    ]

    v_wompi = float(agregados["ventas_wompi"] or 0)
    v_transf = float(agregados["ventas_transferencia"] or 0)
    v_efectivo = float(agregados["ventas_efectivo"] or 0)

    return {
        "total_ventas": total_ventas,
        "ventas_en_proceso": float(agregados["ventas_en_proceso"] or 0),
        "pedidos_entregados_count": entregados_count,
        "pedidos_confirmados_count": confirmados_count,
        "pedidos_validos_count": validos_count,
        "pedidos_en_proceso_count": agregados["pedidos_en_proceso_count"] or 0,
        "pedidos_cancelados_count": cancelados_count,
        "total_pedidos_periodo": total_pedidos,
        "tasa_efectividad": tasa_efectividad,
        "ticket_promedio": ticket_promedio,
        "ventas_wompi": v_wompi,
        "ventas_transferencia": v_transf,
        "ventas_efectivo": v_efectivo,
        "wompi_count": agregados["wompi_count"] or 0,
        "transferencia_count": agregados["transferencia_count"] or 0,
        "efectivo_count": agregados["efectivo_count"] or 0,
        "tiene_datos_metodos": (v_wompi + v_transf + v_efectivo) > 0,
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
    sucursales = list(Sucursal.objects.filter(activa=True).order_by("nombre"))
    sucursal_seleccionada = None
    if id_sucursal.isdecimal():
        suc_id = int(id_sucursal)
        pedidos_qs = pedidos_qs.filter(id_sucursal_id=suc_id)
        sucursal_seleccionada = next((s for s in sucursales if s.pk == suc_id), None)

    metricas = _calcular_metricas_reportes(pedidos_qs, anio, mes)
    return render(
        request,
        "tienda/admin/reportes.html",
        {
            **metricas,
            "portal": "admin",
            "base_template": "tienda/admin/base.html",
            "sucursales": sucursales,
            "sucursal_filtro": id_sucursal,
            "sucursal_seleccionada": sucursal_seleccionada,
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
    id_sucursal = request.GET.get("id_sucursal", "").strip()
    base_qs = Denuncia.objects.all()
    if id_sucursal.isdecimal():
        base_qs = base_qs.filter(id_pedido__id_sucursal_id=int(id_sucursal))

    resumen = base_qs.aggregate(
        total_reportes=Count("id_denuncia"),
        pendientes_count=Count("id_denuncia", filter=Q(estado="PENDIENTE")),
        investigando_count=Count("id_denuncia", filter=Q(estado="INVESTIGANDO")),
        resueltos_count=Count("id_denuncia", filter=Q(estado="RESUELTO")),
    )

    queryset = base_qs.select_related(
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
            "sucursal_filtro": id_sucursal,
            "sucursales": Sucursal.objects.order_by("nombre"),
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


# ─── Generador de Documentos Imprimibles (Pedidos, Inventario y Métricas) ────

def _render_pedido_imprimible(request: HttpRequest, pedido: Pedido, volver_url: str) -> HttpResponse:
    detalles = list(pedido.detalles.select_related("id_producto").all())
    subtotal_productos = sum((d.subtotal for d in detalles), Decimal("0.00"))
    total_unidades = sum((int(d.cantidad or 0) for d in detalles), 0)
    generado_por = (
        getattr(request.user, "nombre_completo", "")
        or getattr(request.user, "email", "")
        or "Usuario"
    )
    return render(
        request,
        "tienda/documentos/pedido_imprimible.html",
        {
            "pedido": pedido,
            "detalles": detalles,
            "direccion": direccion_pedido(pedido),
            "subtotal_productos": subtotal_productos,
            "total_unidades": total_unidades,
            "fecha_impresion": timezone.localtime(),
            "generado_por": generado_por,
            "volver_url": volver_url,
        },
    )


@admin_required
def pedido_admin_documento(request: HttpRequest, id_pedido: int) -> HttpResponse:
    pedido = get_object_or_404(
        Pedido.objects.select_related(
            "id_cliente", "id_sucursal", "id_repartidor__id_repartidor"
        ).prefetch_related("detalles__id_producto"),
        id_pedido=id_pedido,
    )
    return _render_pedido_imprimible(
        request,
        pedido,
        reverse("tienda:admin-pedido-detalle", args=[pedido.id_pedido]),
    )


@empleado_required
def pedido_empleado_documento(request: HttpRequest, id_pedido: int) -> HttpResponse:
    pedido = get_object_or_404(
        Pedido.objects.select_related(
            "id_cliente", "id_sucursal", "id_repartidor__id_repartidor"
        ).prefetch_related("detalles__id_producto"),
        id_pedido=id_pedido,
        id_sucursal=request.sucursal_empleado,
    )
    return _render_pedido_imprimible(
        request,
        pedido,
        reverse("tienda:pedido-detalle", args=[pedido.id_pedido]),
    )


@admin_required
def inventario_admin_documento(request: HttpRequest) -> HttpResponse:
    inventarios = _filtro_texto(
        Inventario.objects.select_related("id_sucursal", "id_producto__id_categoria"),
        request,
        "id_producto__nombre",
        "id_producto__codigo_barras",
        "id_sucursal__nombre",
    )
    id_sucursal = request.GET.get("id_sucursal", "").strip()
    sucursal_obj = None
    if id_sucursal.isdecimal():
        sucursal_obj = Sucursal.objects.filter(pk=int(id_sucursal)).first()
        inventarios = inventarios.filter(id_sucursal_id=int(id_sucursal))

    id_categoria = request.GET.get("id_categoria", "").strip()
    categoria_obj = None
    if id_categoria.isdecimal():
        categoria_obj = Categoria.objects.filter(pk=int(id_categoria)).first()
        inventarios = inventarios.filter(id_producto__id_categoria_id=int(id_categoria))

    stock_filtro = request.GET.get("bajo", "").strip()
    if stock_filtro == "agotado":
        inventarios = inventarios.filter(stock_disponible=0)
        titulo_doc = "Reporte de Productos Agotados"
    elif stock_filtro == "1":
        inventarios = inventarios.filter(stock_disponible__lte=UMBRAL_BAJO_STOCK)
        titulo_doc = "Orden de Reabastecimiento (Bajo Stock)"
    else:
        titulo_doc = "Hoja de Control de Inventario Físico"

    filas = []
    total_unidades = 0
    total_bajo = 0
    total_agotados = 0
    for inv in inventarios.order_by("id_sucursal__nombre", "id_producto__nombre"):
        st = int(inv.stock_disponible or 0)
        total_unidades += st
        if st == 0:
            total_agotados += 1
            total_bajo += 1
            estado_txt = "Agotado"
        elif st <= UMBRAL_BAJO_STOCK:
            total_bajo += 1
            estado_txt = "Bajo stock"
        else:
            estado_txt = "Disponible"
        filas.append(
            {
                "sucursal": inv.id_sucursal.nombre,
                "codigo_barras": inv.id_producto.codigo_barras,
                "producto": inv.id_producto.nombre,
                "categoria": inv.id_producto.id_categoria.nombre,
                "stock": st,
                "estado": estado_txt,
                "vencimiento": inv.fecha_vencimiento,
            }
        )

    filtros_txt = []
    if categoria_obj:
        filtros_txt.append(f"Categoría: {categoria_obj.nombre}")
    if stock_filtro == "1":
        filtros_txt.append(f"Bajo stock (≤ {UMBRAL_BAJO_STOCK} uds)")
    elif stock_filtro == "agotado":
        filtros_txt.append("Solo agotados (0 uds)")
    if request.GET.get("q"):
        filtros_txt.append(f"Búsqueda: '{request.GET.get('q')}'")

    volver_base = reverse("tienda:admin-inventario")
    qs_str = request.GET.urlencode()
    return render(
        request,
        "tienda/documentos/inventario_imprimible.html",
        {
            "titulo_documento": titulo_doc,
            "ambito_nombre": sucursal_obj.nombre if sucursal_obj else "Todas las sucursales",
            "filtro_descripcion": " · ".join(filtros_txt),
            "filas": filas,
            "total_registros": len(filas),
            "total_unidades": total_unidades,
            "total_bajo_stock": total_bajo,
            "total_agotados": total_agotados,
            "umbral_bajo_stock": UMBRAL_BAJO_STOCK,
            "fecha_impresion": timezone.localtime(),
            "generado_por": getattr(request.user, "nombre_completo", "") or request.user.email,
            "volver_url": f"{volver_base}?{qs_str}" if qs_str else volver_base,
        },
    )


@empleado_required
def inventario_empleado_documento(request: HttpRequest) -> HttpResponse:
    sucursal = request.sucursal_empleado
    consulta = _productos_con_inventario(sucursal).select_related("id_categoria")
    busqueda = request.GET.get("q", "").strip()
    id_categoria = request.GET.get("id_categoria", "").strip()
    stock_filtro = request.GET.get("bajo", "").strip()

    if busqueda:
        consulta = consulta.filter(
            Q(nombre__icontains=busqueda) | Q(codigo_barras__icontains=busqueda)
        )
    if id_categoria.isdecimal():
        consulta = consulta.filter(id_categoria_id=int(id_categoria))
    if stock_filtro == "agotado":
        consulta = consulta.filter(stock_disponible=0)
        titulo_doc = "Reporte de Productos Agotados en Sucursal"
    elif stock_filtro == "1":
        consulta = consulta.filter(stock_disponible__lte=UMBRAL_BAJO_STOCK)
        titulo_doc = "Orden de Reabastecimiento de Sucursal"
    else:
        titulo_doc = "Hoja de Control de Inventario de Sucursal"

    filas = []
    total_unidades = 0
    total_bajo = 0
    total_agotados = 0
    for prod in consulta.order_by("nombre"):
        st = int(prod.stock_disponible or 0)
        total_unidades += st
        if st == 0:
            total_agotados += 1
            total_bajo += 1
            estado_txt = "Agotado"
        elif st <= UMBRAL_BAJO_STOCK:
            total_bajo += 1
            estado_txt = "Bajo stock"
        else:
            estado_txt = "Disponible"
        filas.append(
            {
                "sucursal": sucursal.nombre,
                "codigo_barras": prod.codigo_barras,
                "producto": prod.nombre,
                "categoria": prod.id_categoria.nombre,
                "stock": st,
                "estado": estado_txt,
                "vencimiento": prod.fecha_vencimiento,
            }
        )

    volver_base = reverse("tienda:inventario")
    qs_str = request.GET.urlencode()
    return render(
        request,
        "tienda/documentos/inventario_imprimible.html",
        {
            "titulo_documento": titulo_doc,
            "ambito_nombre": sucursal.nombre,
            "filtro_descripcion": "",
            "filas": filas,
            "total_registros": len(filas),
            "total_unidades": total_unidades,
            "total_bajo_stock": total_bajo,
            "total_agotados": total_agotados,
            "umbral_bajo_stock": UMBRAL_BAJO_STOCK,
            "fecha_impresion": timezone.localtime(),
            "generado_por": getattr(request.user, "nombre_completo", "") or request.user.email,
            "volver_url": f"{volver_base}?{qs_str}" if qs_str else volver_base,
        },
    )


@admin_required
def reportes_admin_documento(request: HttpRequest) -> HttpResponse:
    anio, mes = _parse_mes_anio(request)
    pedidos_qs = Pedido.objects.all()
    id_sucursal = request.GET.get("id_sucursal", "").strip()
    sucursal_seleccionada = None
    if id_sucursal.isdecimal():
        suc_id = int(id_sucursal)
        pedidos_qs = pedidos_qs.filter(id_sucursal_id=suc_id)
        sucursal_seleccionada = Sucursal.objects.filter(pk=suc_id).first()

    metricas = _calcular_metricas_reportes(pedidos_qs, anio, mes)
    volver_base = reverse("tienda:admin-reportes")
    qs_str = request.GET.urlencode()
    return render(
        request,
        "tienda/documentos/reporte_imprimible.html",
        {
            **metricas,
            "ambito_nombre": (
                sucursal_seleccionada.nombre
                if sucursal_seleccionada
                else "Consolidado Global (Todas las sucursales)"
            ),
            "fecha_impresion": timezone.localtime(),
            "generado_por": getattr(request.user, "nombre_completo", "") or request.user.email,
            "volver_url": f"{volver_base}?{qs_str}" if qs_str else volver_base,
        },
    )


@empleado_required
def reportes_empleado_documento(request: HttpRequest) -> HttpResponse:
    anio, mes = _parse_mes_anio(request)
    sucursal = request.sucursal_empleado
    pedidos_qs = Pedido.objects.filter(id_sucursal=sucursal)
    metricas = _calcular_metricas_reportes(pedidos_qs, anio, mes)
    volver_base = reverse("tienda:empleado-reportes")
    qs_str = request.GET.urlencode()
    return render(
        request,
        "tienda/documentos/reporte_imprimible.html",
        {
            **metricas,
            "ambito_nombre": sucursal.nombre,
            "fecha_impresion": timezone.localtime(),
            "generado_por": getattr(request.user, "nombre_completo", "") or request.user.email,
            "volver_url": f"{volver_base}?{qs_str}" if qs_str else volver_base,
        },
    )

