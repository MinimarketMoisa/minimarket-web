"""
Servicios de dominio, autorización y backend de autenticación de Minimarket.
Centraliza reglas compartidas entre las vistas del portal y Django Admin.
"""

from datetime import time as dt_time
import re

from django.contrib.auth.backends import ModelBackend

from .models import (
    DetallePedido,
    Direccion,
    Empleado,
    Inventario,
    Pedido,
    PerfilRepartidor,
    Usuario,
)

# Estados que el empleado de sucursal gestiona activamente
ESTADOS_GESTIONABLES = ("PENDIENTE", "PREPARADO")

# Estados donde un repartidor se considera ocupado con una entrega en curso
ESTADOS_ACTIVOS_REPARTIDOR = ("PENDIENTE", "PREPARADO", "EN_RUTA")


def es_admin(user) -> bool:
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    rol = getattr(user, "id_rol", None)
    return bool(getattr(user, "is_superuser", False) or (rol is not None and rol.nombre == "ADMIN"))


def es_empleado(user) -> bool:
    if (
        not getattr(user, "is_authenticated", False)
        or not getattr(user, "is_active", False)
        or getattr(user, "is_superuser", False)
    ):
        return False
    rol = getattr(user, "id_rol", None)
    return bool(rol is not None and rol.nombre == "EMPLEADO")


def es_personal(user) -> bool:
    return es_admin(user) or es_empleado(user)


def obtener_sucursal_empleado(user):
    """Obtiene la sucursal asignada al empleado reutilizando relaciones precargadas."""
    if not getattr(user, "is_authenticated", False):
        return None
    state = getattr(user, "_state", None)
    if state is not None and "perfil_empleado" in state.fields_cache:
        perfil = state.fields_cache["perfil_empleado"]
        return perfil.id_sucursal if perfil is not None else None
    empleado = (
        Empleado.objects.select_related("id_sucursal")
        .filter(id_empleado=user)
        .first()
    )
    return empleado.id_sucursal if empleado is not None else None


class MinimarketAuthBackend(ModelBackend):
    """
    Precarga rol y perfil_empleado/sucursal en una sola consulta SQL por petición,
    evitando consultas N+1 hacia PostgreSQL en cada vista autenticada.
    """

    def _queryset(self):
        return Usuario.objects.select_related("id_rol", "perfil_empleado__id_sucursal")

    def authenticate(self, request, username=None, password=None, **kwargs):
        email = username or kwargs.get(Usuario.USERNAME_FIELD)
        if not email or password is None:
            return None
        usuario = self._queryset().filter(email=email).first()
        if usuario is None:
            Usuario().set_password(password)
            return None
        if usuario.check_password(password) and self.user_can_authenticate(usuario):
            return usuario
        return None

    def get_user(self, user_id):
        usuario = self._queryset().filter(pk=user_id).first()
        return usuario if usuario and self.user_can_authenticate(usuario) else None


def direccion_pedido(pedido: Pedido):
    if not pedido.id_direccion:
        return None
    return (
        Direccion.objects.select_related("id_municipio")
        .filter(id_direccion=pedido.id_direccion, id_cliente=pedido.id_cliente_id)
        .first()
    )


def liberar_repartidor_si_sin_pedidos(id_repartidor, pedido_excluido_pk=None) -> None:
    """Marca al repartidor como LIBRE si no tiene otras entregas activas."""
    if id_repartidor is None:
        return
    perfil = PerfilRepartidor.objects.select_for_update().filter(pk=id_repartidor).first()
    if perfil is None:
        return
    otros_activos = Pedido.objects.filter(
        id_repartidor_id=id_repartidor,
        estado__in=ESTADOS_ACTIVOS_REPARTIDOR,
    )
    if pedido_excluido_pk is not None:
        otros_activos = otros_activos.exclude(pk=pedido_excluido_pk)
    if not otros_activos.exists() and perfil.estado_operativo != "LIBRE":
        perfil.estado_operativo = "LIBRE"
        perfil.save(update_fields=["estado_operativo"])


def actualizar_repartidor_pedido(pedido: Pedido, nuevo_perfil) -> PerfilRepartidor | None:
    """
    Valida y actualiza el repartidor de un pedido dentro de una transacción activa.
    Libera automáticamente al repartidor anterior si fue reemplazado o removido.
    """
    if pedido.tipo_entrega != "DOMICILIO" and nuevo_perfil is not None:
        raise ValueError("Los pedidos para retiro no requieren repartidor.")
    if pedido.estado == "CANCELADO" and nuevo_perfil is not None:
        raise ValueError("No se puede asignar repartidor a un pedido cancelado.")

    repartidor_anterior_id = pedido.id_repartidor_id

    if nuevo_perfil is None:
        pedido.id_repartidor = None
        if repartidor_anterior_id:
            liberar_repartidor_si_sin_pedidos(repartidor_anterior_id, pedido.pk)
        return None

    nuevo_pk = nuevo_perfil.pk if hasattr(nuevo_perfil, "pk") else int(nuevo_perfil)
    perfil = (
        PerfilRepartidor.objects.select_for_update()
        .select_related("id_repartidor")
        .filter(
            pk=nuevo_pk,
            id_repartidor__activo=True,
            id_repartidor__id_rol__nombre="REPARTIDOR",
        )
        .first()
    )
    if perfil is None:
        raise ValueError("El repartidor seleccionado ya no está activo o disponible.")

    if perfil.pk != repartidor_anterior_id:
        tiene_entrega_activa = (
            perfil.estado_operativo != "LIBRE"
            or Pedido.objects.filter(
                id_repartidor_id=perfil.pk,
                estado__in=ESTADOS_ACTIVOS_REPARTIDOR,
            )
            .exclude(pk=pedido.pk)
            .exists()
        )
        if tiene_entrega_activa:
            raise ValueError("El repartidor seleccionado ya tiene una entrega activa.")

    perfil.estado_operativo = "PENDIENTE"
    perfil.save(update_fields=["estado_operativo"])
    pedido.id_repartidor = perfil

    if repartidor_anterior_id and repartidor_anterior_id != perfil.pk:
        liberar_repartidor_si_sin_pedidos(repartidor_anterior_id, pedido.pk)

    return perfil


def cancelar_pedido(pedido: Pedido, *, guardar_pedido: bool = True) -> None:
    """
    Restituye las existencias del pedido en la sucursal correspondiente,
    libera al repartidor asignado (si existía) y marca el pedido como CANCELADO.
    """
    detalles = list(
        DetallePedido.objects.filter(id_pedido=pedido).values_list("id_producto_id", "cantidad")
    )
    for id_producto, cantidad in detalles:
        existencia = (
            Inventario.objects.select_for_update()
            .filter(id_sucursal_id=pedido.id_sucursal_id, id_producto_id=id_producto)
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
        repartidor_id = pedido.id_repartidor_id
        pedido.id_repartidor = None
        liberar_repartidor_si_sin_pedidos(repartidor_id, pedido.pk)

    pedido.estado = "CANCELADO"
    if guardar_pedido:
        pedido.save(update_fields=["estado", "id_repartidor"])


# ─── Soporte Técnico para Reportes (Denuncia) ────────────────────────────────

SOPORTE_PRIORIDAD_CHOICES = [
    ("BAJA", "Baja"),
    ("MEDIA", "Media"),
    ("ALTA", "Alta"),
    ("CRITICA", "Crítica"),
]

SOPORTE_PRIORIDAD_LABELS = dict(SOPORTE_PRIORIDAD_CHOICES)

SOPORTE_ACCION_CHOICES = [
    ("SEGUIMIENTO_CLIENTE", "Seguimiento con el cliente"),
    ("LLAMADO_REPARTIDOR", "Llamado de atención al repartidor"),
    ("REASIGNACION_PEDIDO", "Reasignación de entrega"),
    ("COMPENSACION_PEDIDO", "Reposición o ajuste de pedido"),
    ("SUSPENSION_REPARTIDOR", "Suspensión de repartidor"),
    ("SIN_IRREGULARIDAD", "Sin irregularidades"),
    ("OTRA_GESTION", "Otra gestión de soporte"),
]

SOPORTE_ACCION_LABELS = dict(SOPORTE_ACCION_CHOICES)

_PRIORIDAD_INVERSA = {v.lower(): k for k, v in SOPORTE_PRIORIDAD_LABELS.items()}
_ACCION_INVERSA = {
    **{v.lower(): k for k, v in SOPORTE_ACCION_LABELS.items()},
    "contacto y seguimiento con el cliente": "SEGUIMIENTO_CLIENTE",
    "reasignación o ajuste de entrega": "REASIGNACION_PEDIDO",
    "reposición o ajuste en el pedido": "COMPENSACION_PEDIDO",
    "suspensión preventiva del repartidor": "SUSPENSION_REPARTIDOR",
    "verificación sin irregularidades": "SIN_IRREGULARIDAD",
    "otra gestión de soporte técnico": "OTRA_GESTION",
}

_SOPORTE_HEADER_RE = re.compile(
    r"^\[Soporte Técnico \| Admin: (?P<admin>[^|]+) \| Fecha: (?P<fecha>[^|]+) \| "
    r"Entrada: (?P<entrada>\d{2}:\d{2}) - Salida: (?P<salida>\d{2}:\d{2}) \((?P<duracion>[^)]+)\) \| "
    r"Prioridad: (?P<prioridad>[^|]+) \| Acción: (?P<accion>[^\]]+)\]\n?(?P<detalle>[\s\S]*)$"
)


def calcular_duracion_soporte(hora_entrada: dt_time, hora_salida: dt_time) -> str:
    """Calcula el tiempo legible empleado por el administrador en atender el caso."""
    min_entrada = hora_entrada.hour * 60 + hora_entrada.minute
    min_salida = hora_salida.hour * 60 + hora_salida.minute
    diferencia = max(0, min_salida - min_entrada)
    if diferencia == 0:
        return "< 1 min"
    if diferencia < 60:
        return f"{diferencia} min"
    horas, minutos = divmod(diferencia, 60)
    return f"{horas} h {minutos:02d} min" if minutos else f"{horas} h"


def formatear_resolucion_soporte(
    *,
    admin_nombre: str,
    fecha_str: str,
    hora_entrada: dt_time,
    hora_salida: dt_time,
    prioridad: str,
    accion_tomada: str,
    detalle_solucion: str,
) -> str:
    """
    Serializa la bitácora de soporte técnico dentro de `resolucion_admin` (VARCHAR(500)),
    manteniendo compatibilidad total con PostgreSQL y lectura desde FastAPI / App móvil.
    """
    admin_limpio = (
        (admin_nombre or "Administrador").replace("|", "/").replace("]", "").strip()[:35]
    )
    prioridad_label = SOPORTE_PRIORIDAD_LABELS.get(prioridad, prioridad)
    accion_label = SOPORTE_ACCION_LABELS.get(accion_tomada, accion_tomada)
    entrada_str = hora_entrada.strftime("%H:%M")
    salida_str = hora_salida.strftime("%H:%M")
    duracion_str = calcular_duracion_soporte(hora_entrada, hora_salida)

    encabezado = (
        f"[Soporte Técnico | Admin: {admin_limpio} | Fecha: {fecha_str} | "
        f"Entrada: {entrada_str} - Salida: {salida_str} ({duracion_str}) | "
        f"Prioridad: {prioridad_label} | Acción: {accion_label}]"
    )
    detalle_limpio = detalle_solucion.strip()
    espacio_disponible = max(0, 500 - len(encabezado) - 1)
    return f"{encabezado}\n{detalle_limpio[:espacio_disponible]}"[:500]


def parsear_resolucion_soporte(texto: str | None) -> dict | None:
    """Extrae los metadatos estructurados de soporte o degrada limpiamente si es texto plano."""
    if not texto or not texto.strip():
        return None
    limpio = texto.strip()
    coincidencia = _SOPORTE_HEADER_RE.match(limpio)
    if not coincidencia:
        return {
            "es_estructurado": False,
            "admin": None,
            "fecha": None,
            "entrada": None,
            "salida": None,
            "duracion": None,
            "prioridad": None,
            "prioridad_codigo": "MEDIA",
            "accion": None,
            "accion_codigo": "SEGUIMIENTO_CLIENTE",
            "detalle": limpio,
        }

    datos = coincidencia.groupdict()
    prioridad_txt = datos["prioridad"].strip()
    accion_txt = datos["accion"].strip()
    return {
        "es_estructurado": True,
        "admin": datos["admin"].strip(),
        "fecha": datos["fecha"].strip(),
        "entrada": datos["entrada"].strip(),
        "salida": datos["salida"].strip(),
        "duracion": datos["duracion"].strip(),
        "prioridad": prioridad_txt,
        "prioridad_codigo": _PRIORIDAD_INVERSA.get(prioridad_txt.lower(), "MEDIA"),
        "accion": accion_txt,
        "accion_codigo": _ACCION_INVERSA.get(accion_txt.lower(), "OTRA_GESTION"),
        "detalle": datos["detalle"].strip(),
    }


