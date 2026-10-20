"""
Modelos Django que mapean las tablas existentes creadas por FastAPI/SQLAlchemy.

REGLA: managed = False en todos los modelos de negocio.
  -> Django NUNCA hace CREATE TABLE / ALTER TABLE en estas tablas.
  -> FastAPI es el unico dueno del DDL (schema).
  -> Django solo lee y escribe datos (DML).

Las tablas propias de Django (auth, sessions, admin, migrations) SI son
gestionadas por Django mediante sus propias migraciones.
"""

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


# ─────────────────────────────────────────────────────────────────────────────
# Manager personalizado para el modelo Usuario
# ─────────────────────────────────────────────────────────────────────────────
class UsuarioManager(BaseUserManager):
    """Manager que usa email en lugar de username."""

    def _crear_usuario(self, email, password, nombre_completo, id_rol_id=2, **extra):
        if not email:
            raise ValueError("El correo es obligatorio.")
        email = self.normalize_email(email)
        user = self.model(
            email=email,
            nombre_completo=nombre_completo,
            id_rol_id=id_rol_id,
            **extra,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, nombre_completo="", **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._crear_usuario(email, password, nombre_completo, **extra)

    def create_superuser(self, email, password, nombre_completo="Admin", **extra):
        extra["is_staff"]      = True
        extra["is_superuser"]  = True
        # Los superusuarios de Django usan rol Empleado (id=3) por convencion
        return self._crear_usuario(email, password, nombre_completo, id_rol_id=3, **extra)


# ─────────────────────────────────────────────────────────────────────────────
# Tablas de catalogo / soporte
# ─────────────────────────────────────────────────────────────────────────────
class Rol(models.Model):
    id_rol  = models.BigAutoField(primary_key=True)
    nombre  = models.CharField(max_length=50, unique=True)

    class Meta:
        managed    = False      # FastAPI es dueno de esta tabla
        db_table   = "rol"
        verbose_name        = "Rol"
        verbose_name_plural = "Roles"

    def __str__(self):
        return self.nombre


class Municipio(models.Model):
    id_municipio = models.BigAutoField(primary_key=True)
    nombre       = models.CharField(max_length=100, unique=True)

    class Meta:
        managed    = False
        db_table   = "municipio"
        verbose_name        = "Municipio"
        verbose_name_plural = "Municipios"

    def __str__(self):
        return self.nombre


# ─────────────────────────────────────────────────────────────────────────────
# Usuario (AUTH_USER_MODEL)
# Mapea la tabla "usuario" que ya existe en PostgreSQL creada por FastAPI.
# ─────────────────────────────────────────────────────────────────────────────
class Usuario(AbstractBaseUser, PermissionsMixin):
    id_usuario       = models.BigAutoField(primary_key=True)
    id_rol           = models.ForeignKey(
        Rol, on_delete=models.RESTRICT,
        db_column="id_rol", related_name="usuarios",
    )
    nombre_completo  = models.CharField(max_length=150)
    email            = models.CharField(max_length=254, unique=True)
    # 'password' ya existe en AbstractBaseUser pero apuntamos a la columna real
    password         = models.CharField(max_length=128)
    telefono         = models.CharField(max_length=20, blank=True, null=True)
    activo           = models.BooleanField(default=True)
    email_verificado = models.BooleanField(default=False)
    is_staff         = models.BooleanField(default=False)
    is_superuser     = models.BooleanField(default=False)
    last_login       = models.DateTimeField(null=True, blank=True)

    objects   = UsuarioManager()

    USERNAME_FIELD  = "email"
    REQUIRED_FIELDS = ["nombre_completo"]

    # Propiedades que Django Admin / PermissionsMixin necesitan
    @property
    def is_active(self):
        return self.activo

    @is_active.setter
    def is_active(self, value):
        self.activo = value

    class Meta:
        managed    = False
        db_table   = "usuario"
        verbose_name        = "Usuario"
        verbose_name_plural = "Usuarios"

    def __str__(self):
        return f"{self.nombre_completo} <{self.email}>"


# ─────────────────────────────────────────────────────────────────────────────
# Sucursal
# ─────────────────────────────────────────────────────────────────────────────
class Sucursal(models.Model):
    id_sucursal = models.BigAutoField(primary_key=True)
    id_municipio = models.ForeignKey(
        Municipio, on_delete=models.RESTRICT,
        db_column="id_municipio", related_name="sucursales",
    )
    nombre    = models.CharField(max_length=100)
    direccion = models.CharField(max_length=300)
    telefono  = models.CharField(max_length=20, blank=True, null=True)
    latitud   = models.DecimalField(max_digits=9, decimal_places=6)
    longitud  = models.DecimalField(max_digits=9, decimal_places=6)
    activa    = models.BooleanField(default=True)

    class Meta:
        managed    = False
        db_table   = "sucursal"
        verbose_name        = "Sucursal"
        verbose_name_plural = "Sucursales"

    def __str__(self):
        return self.nombre


# ─────────────────────────────────────────────────────────────────────────────
# Direccion
# ─────────────────────────────────────────────────────────────────────────────
class Direccion(models.Model):
    id_direccion      = models.BigAutoField(primary_key=True)
    id_cliente        = models.ForeignKey(
        Usuario, on_delete=models.RESTRICT,
        db_column="id_cliente", related_name="direcciones",
    )
    id_municipio      = models.ForeignKey(
        Municipio, on_delete=models.RESTRICT,
        db_column="id_municipio", related_name="direcciones",
    )
    detalle_direccion = models.CharField(max_length=300)
    punto_referencia  = models.CharField(max_length=300, blank=True, null=True)

    class Meta:
        managed    = False
        db_table   = "direccion"
        verbose_name        = "Direccion"
        verbose_name_plural = "Direcciones"

    def __str__(self):
        return f"{self.detalle_direccion} ({self.id_municipio})"


# ─────────────────────────────────────────────────────────────────────────────
# Empleado
# ─────────────────────────────────────────────────────────────────────────────
class Empleado(models.Model):
    id_empleado  = models.OneToOneField(
        Usuario, on_delete=models.CASCADE,
        db_column="id_empleado", primary_key=True, related_name="perfil_empleado",
    )
    id_sucursal  = models.ForeignKey(
        Sucursal, on_delete=models.RESTRICT,
        db_column="id_sucursal", related_name="empleados",
    )
    cargo        = models.CharField(max_length=50)

    class Meta:
        managed    = False
        db_table   = "empleado"
        verbose_name        = "Empleado"
        verbose_name_plural = "Empleados"

    def __str__(self):
        return f"{self.id_empleado} — {self.cargo}"


# ─────────────────────────────────────────────────────────────────────────────
# PerfilRepartidor
# ─────────────────────────────────────────────────────────────────────────────
class PerfilRepartidor(models.Model):
    ESTADO_CHOICES = [("LIBRE", "Libre"), ("PENDIENTE", "Pendiente")]

    id_repartidor        = models.OneToOneField(
        Usuario, on_delete=models.CASCADE,
        db_column="id_repartidor", primary_key=True, related_name="perfil_repartidor",
    )
    estado_operativo     = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="LIBRE")
    placa_vehiculo       = models.CharField(max_length=20, blank=True, null=True)
    latitud_actual       = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitud_actual      = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    ultima_actualizacion = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed    = False
        db_table   = "perfil_repartidor"
        verbose_name        = "Perfil Repartidor"
        verbose_name_plural = "Perfiles Repartidor"

    def __str__(self):
        return f"Repartidor {self.id_repartidor} [{self.estado_operativo}]"


# ─────────────────────────────────────────────────────────────────────────────
# Catalogo
# ─────────────────────────────────────────────────────────────────────────────
class Categoria(models.Model):
    id_categoria = models.BigAutoField(primary_key=True)
    nombre       = models.CharField(max_length=100, unique=True)
    descripcion  = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        managed    = False
        db_table   = "categoria"
        verbose_name        = "Categoria"
        verbose_name_plural = "Categorias"

    def __str__(self):
        return self.nombre


class Producto(models.Model):
    id_producto   = models.BigAutoField(primary_key=True)
    id_categoria  = models.ForeignKey(
        Categoria, on_delete=models.RESTRICT,
        db_column="id_categoria", related_name="productos",
    )
    codigo_barras = models.CharField(max_length=50, unique=True, blank=True, null=True)
    nombre        = models.CharField(max_length=200)
    descripcion   = models.CharField(max_length=1000, blank=True, null=True)
    precio_base   = models.DecimalField(max_digits=10, decimal_places=2)
    imagen_url    = models.CharField(max_length=500, blank=True, null=True)
    activo        = models.BooleanField(default=True)

    class Meta:
        managed    = False
        db_table   = "producto"
        verbose_name        = "Producto"
        verbose_name_plural = "Productos"

    def __str__(self):
        return self.nombre


# ─────────────────────────────────────────────────────────────────────────────
# Inventario
# ─────────────────────────────────────────────────────────────────────────────
class Inventario(models.Model):
    id_inventario      = models.BigAutoField(primary_key=True)
    id_sucursal        = models.ForeignKey(
        Sucursal, on_delete=models.RESTRICT,
        db_column="id_sucursal", related_name="inventarios",
    )
    id_producto        = models.ForeignKey(
        Producto, on_delete=models.RESTRICT,
        db_column="id_producto", related_name="inventarios",
    )
    stock_disponible   = models.IntegerField(default=0)
    fecha_vencimiento  = models.DateField(null=True, blank=True)

    class Meta:
        managed    = False
        db_table   = "inventario"
        verbose_name        = "Inventario"
        verbose_name_plural = "Inventarios"
        unique_together = [("id_sucursal", "id_producto")]

    def __str__(self):
        return f"{self.id_producto} en {self.id_sucursal}: {self.stock_disponible}"


# ─────────────────────────────────────────────────────────────────────────────
# Pedidos
# ─────────────────────────────────────────────────────────────────────────────
class Pedido(models.Model):
    CANAL_CHOICES        = [("APP", "App movil"), ("WEB", "Web")]
    ESTADO_CHOICES       = [
        ("PENDIENTE", "Pendiente"), ("PREPARADO", "Preparado"),
        ("EN_RUTA", "En ruta"), ("ENTREGADO", "Entregado"), ("CANCELADO", "Cancelado"),
    ]
    TIPO_ENTREGA_CHOICES = [("DOMICILIO", "Domicilio"), ("RETIRO", "Retiro en sucursal")]
    METODO_PAGO_CHOICES  = [("CONTRA_ENTREGA", "Contra entrega"), ("TRANSFERENCIA", "Transferencia")]
    ESTADO_PAGO_CHOICES  = [("PENDIENTE", "Pendiente"), ("PAGADO", "Pagado")]

    id_pedido    = models.BigAutoField(primary_key=True)
    id_cliente   = models.ForeignKey(
        Usuario, on_delete=models.RESTRICT,
        db_column="id_cliente", related_name="pedidos",
    )
    id_sucursal  = models.ForeignKey(
        Sucursal, on_delete=models.RESTRICT,
        db_column="id_sucursal", related_name="pedidos",
    )
    id_repartidor = models.ForeignKey(
        PerfilRepartidor, on_delete=models.SET_NULL,
        db_column="id_repartidor", related_name="pedidos",
        null=True, blank=True,
    )
    id_direccion  = models.BigIntegerField(null=True, blank=True)
    canal         = models.CharField(max_length=10, choices=CANAL_CHOICES)
    fecha_creacion = models.DateTimeField(auto_now_add=False)
    estado        = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="PENDIENTE")
    tipo_entrega  = models.CharField(max_length=20, choices=TIPO_ENTREGA_CHOICES)
    metodo_pago   = models.CharField(max_length=20, choices=METODO_PAGO_CHOICES)
    estado_pago   = models.CharField(max_length=20, choices=ESTADO_PAGO_CHOICES, default="PENDIENTE")
    costo_envio   = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total         = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        managed    = False
        db_table   = "pedido"
        verbose_name        = "Pedido"
        verbose_name_plural = "Pedidos"
        ordering = ["-fecha_creacion"]

    def __str__(self):
        return f"Pedido #{self.id_pedido} — {self.estado}"


class DetallePedido(models.Model):
    id_detalle      = models.BigAutoField(primary_key=True)
    id_pedido       = models.ForeignKey(
        Pedido, on_delete=models.CASCADE,
        db_column="id_pedido", related_name="detalles",
    )
    id_producto     = models.ForeignKey(
        Producto, on_delete=models.RESTRICT,
        db_column="id_producto", related_name="detalles_pedido",
    )
    cantidad        = models.IntegerField()
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    # subtotal es una columna COMPUTED en PG (no se escribe desde Django)
    subtotal        = models.DecimalField(max_digits=12, decimal_places=2, editable=False)

    class Meta:
        managed    = False
        db_table   = "detalle_pedido"
        verbose_name        = "Detalle de pedido"
        verbose_name_plural = "Detalles de pedido"
        unique_together = [("id_pedido", "id_producto")]

    def __str__(self):
        return f"{self.id_producto} x{self.cantidad}"
