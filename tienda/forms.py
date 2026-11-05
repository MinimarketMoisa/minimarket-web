from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.db.models import Q
from django.utils import timezone

from .models import (
    Categoria,
    Denuncia,
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
from .services import (
    SOPORTE_ACCION_CHOICES,
    SOPORTE_PRIORIDAD_CHOICES,
    formatear_resolucion_soporte,
    parsear_resolucion_soporte,
)


class EmpleadoLoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="Correo electrónico",
        widget=forms.EmailInput(
            attrs={"autocomplete": "email", "autofocus": True, "placeholder": "correo@minimarket.com"}
        ),
    )
    password = forms.CharField(
        label="Contraseña",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password", "placeholder": "••••••••"}
        ),
    )


class InventarioForm(forms.Form):
    stock_disponible = forms.IntegerField(label="Existencias", min_value=0)
    fecha_vencimiento = forms.DateField(
        label="Fecha de vencimiento",
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}),
    )


class ProductoPortalForm(forms.ModelForm):
    precio_base = forms.DecimalField(
        label="Precio base",
        min_value=0,
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
    )

    class Meta:
        model = Producto
        fields = (
            "nombre",
            "id_categoria",
            "codigo_barras",
            "descripcion",
            "precio_base",
            "imagen_url",
            "activo",
        )
        widgets = {
            "descripcion": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["id_categoria"].queryset = Categoria.objects.order_by("nombre")

    def clean_codigo_barras(self):
        return self.cleaned_data["codigo_barras"] or None


class CategoriaPortalForm(forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ("nombre", "descripcion")
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 3})}


class SucursalPortalForm(forms.ModelForm):
    class Meta:
        model = Sucursal
        fields = (
            "nombre",
            "id_municipio",
            "direccion",
            "telefono",
            "latitud",
            "longitud",
            "activa",
        )
        widgets = {
            "latitud": forms.NumberInput(attrs={"step": "0.000001"}),
            "longitud": forms.NumberInput(attrs={"step": "0.000001"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["id_municipio"].queryset = Municipio.objects.order_by("nombre")

    def clean_latitud(self):
        latitud = self.cleaned_data["latitud"]
        if not -90 <= latitud <= 90:
            raise forms.ValidationError("La latitud debe estar entre -90 y 90.")
        return latitud

    def clean_longitud(self):
        longitud = self.cleaned_data["longitud"]
        if not -180 <= longitud <= 180:
            raise forms.ValidationError("La longitud debe estar entre -180 y 180.")
        return longitud


class InventarioPortalForm(forms.ModelForm):
    stock_disponible = forms.IntegerField(
        label="Existencias",
        min_value=0,
        widget=forms.NumberInput(attrs={"min": 0}),
    )

    class Meta:
        model = Inventario
        fields = ("id_sucursal", "id_producto", "stock_disponible", "fecha_vencimiento")
        widgets = {
            "fecha_vencimiento": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["id_sucursal"].queryset = Sucursal.objects.order_by("nombre")
        self.fields["id_producto"].queryset = Producto.objects.order_by("nombre")

    def clean(self):
        cleaned_data = super().clean()
        sucursal = cleaned_data.get("id_sucursal")
        producto = cleaned_data.get("id_producto")
        if sucursal and producto:
            duplicados = Inventario.objects.filter(
                id_sucursal=sucursal,
                id_producto=producto,
            )
            if self.instance.pk:
                duplicados = duplicados.exclude(pk=self.instance.pk)
            if duplicados.exists():
                raise forms.ValidationError(
                    "Ya existe un registro de inventario para ese producto y sucursal."
                )
        return cleaned_data


class PedidoPortalForm(forms.Form):
    estado = forms.ChoiceField(label="Estado del pedido", choices=Pedido.ESTADO_CHOICES)
    estado_pago = forms.ChoiceField(
        label="Estado del pago",
        choices=Pedido.ESTADO_PAGO_CHOICES,
    )
    id_repartidor = forms.ModelChoiceField(
        label="Repartidor asignado",
        queryset=PerfilRepartidor.objects.select_related("id_repartidor")
        .filter(
            id_repartidor__activo=True,
            id_repartidor__id_rol__nombre="REPARTIDOR",
        )
        .order_by("id_repartidor__nombre_completo"),
        required=False,
        empty_label="Sin repartidor",
    )

    def __init__(self, *args, pedido, **kwargs):
        self.pedido = pedido
        super().__init__(*args, **kwargs)
        perfiles = self.fields["id_repartidor"].queryset
        if pedido.id_repartidor_id:
            perfiles = perfiles.filter(
                Q(estado_operativo="LIBRE") | Q(pk=pedido.id_repartidor_id)
            )
        else:
            perfiles = perfiles.filter(estado_operativo="LIBRE")
        self.fields["id_repartidor"].queryset = perfiles
        self.initial.setdefault("estado", pedido.estado)
        self.initial.setdefault("estado_pago", pedido.estado_pago)
        self.initial.setdefault("id_repartidor", pedido.id_repartidor_id)

    def clean(self):
        cleaned_data = super().clean()
        estado = cleaned_data.get("estado")
        repartidor = cleaned_data.get("id_repartidor")
        if self.pedido.tipo_entrega == "RETIRO" and repartidor:
            self.add_error("id_repartidor", "Los pedidos para retiro no llevan repartidor.")
        if self.pedido.estado == "CANCELADO" and estado != "CANCELADO":
            self.add_error("estado", "Los pedidos cancelados se conservan en el historial.")
        return cleaned_data


class UsuarioPortalForm(forms.Form):
    nombre_completo = forms.CharField(label="Nombre completo", max_length=150)
    email = forms.EmailField(label="Correo electrónico", max_length=254)
    telefono = forms.CharField(label="Teléfono", max_length=20, required=False)
    rol = forms.ModelChoiceField(
        label="Rol",
        queryset=Rol.objects.filter(
            nombre__in=("ADMIN", "EMPLEADO", "REPARTIDOR", "CLIENTE")
        ).order_by("nombre"),
    )
    activo = forms.BooleanField(label="Cuenta activa", required=False, initial=True)
    email_verificado = forms.BooleanField(label="Correo verificado", required=False)
    password = forms.CharField(
        label="Contraseña",
        min_length=8,
        required=False,
        widget=forms.PasswordInput,
    )
    sucursal = forms.ModelChoiceField(
        label="Sucursal asignada",
        queryset=Sucursal.objects.order_by("nombre"),
        required=False,
    )
    cargo = forms.CharField(label="Cargo", max_length=50, required=False)

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        super().__init__(*args, **kwargs)
        if instance is not None:
            perfil = Empleado.objects.filter(pk=instance.pk).first()
            self.initial.update(
                {
                    "nombre_completo": instance.nombre_completo,
                    "email": instance.email,
                    "telefono": instance.telefono,
                    "rol": instance.id_rol_id,
                    "activo": instance.activo,
                    "email_verificado": instance.email_verificado,
                    "sucursal": perfil.id_sucursal_id if perfil else None,
                    "cargo": perfil.cargo if perfil else "",
                }
            )
        else:
            self.fields["password"].required = True

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        duplicados = Usuario.objects.filter(email__iexact=email)
        if self.instance is not None:
            duplicados = duplicados.exclude(pk=self.instance.pk)
        if duplicados.exists():
            raise forms.ValidationError("Ya existe una cuenta con ese correo.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        rol = cleaned_data.get("rol")
        if rol and rol.nombre == "EMPLEADO":
            if not cleaned_data.get("sucursal"):
                self.add_error("sucursal", "Selecciona la sucursal asignada al empleado.")
            if not cleaned_data.get("cargo"):
                self.add_error("cargo", "Indica el cargo del empleado.")
        elif rol:
            cleaned_data["sucursal"] = None
            cleaned_data["cargo"] = ""
        return cleaned_data

    def save(self):
        usuario = self.instance if self.instance is not None else Usuario()
        data = self.cleaned_data
        rol_nombre = data["rol"].nombre

        estaba_activo = bool(self.instance and self.instance.activo)
        usuario.nombre_completo = data["nombre_completo"]
        usuario.email = data["email"]
        usuario.telefono = data["telefono"] or None
        usuario.id_rol = data["rol"]
        usuario.activo = data["activo"]
        usuario.email_verificado = data["email_verificado"]
        usuario.is_staff = rol_nombre in {"ADMIN", "EMPLEADO"}
        usuario.is_superuser = rol_nombre == "ADMIN"

        if estaba_activo and not data["activo"] and not data["password"]:
            usuario.token_version = (usuario.token_version or 0) + 1
        if data["password"]:
            usuario.set_password(data["password"])
        usuario.save()

        if rol_nombre == "EMPLEADO":
            Empleado.objects.update_or_create(
                id_empleado=usuario,
                defaults={
                    "id_sucursal": data["sucursal"],
                    "cargo": data["cargo"],
                },
            )
        elif rol_nombre == "REPARTIDOR":
            PerfilRepartidor.objects.get_or_create(
                id_repartidor=usuario,
                defaults={"estado_operativo": "LIBRE"},
            )
        return usuario


class DenunciaResolucionForm(forms.Form):
    estado = forms.ChoiceField(
        label="Estado del caso",
        choices=Denuncia.ESTADO_CHOICES,
    )
    prioridad = forms.ChoiceField(
        label="Prioridad",
        choices=SOPORTE_PRIORIDAD_CHOICES,
        initial="MEDIA",
    )
    accion_tomada = forms.ChoiceField(
        label="Acción aplicada",
        choices=SOPORTE_ACCION_CHOICES,
        initial="SEGUIMIENTO_CLIENTE",
    )
    hora_entrada = forms.TimeField(
        label="Hora de entrada",
        input_formats=["%H:%M", "%H:%M:%S"],
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
    )
    hora_salida = forms.TimeField(
        label="Hora de salida",
        required=False,
        input_formats=["%H:%M", "%H:%M:%S"],
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
    )
    detalle_solucion = forms.CharField(
        label="Solución brindada",
        max_length=280,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "maxlength": "280",
                "placeholder": "Describe de forma clara la revisión realizada y la solución enviada...",
            }
        ),
    )

    def __init__(
        self,
        *args,
        instance: Denuncia | None = None,
        admin_user=None,
        hora_entrada_inicial: str | None = None,
        **kwargs,
    ):
        self.instance = instance
        self.admin_user = admin_user
        super().__init__(*args, **kwargs)

        ahora = timezone.localtime()
        previo = parsear_resolucion_soporte(getattr(instance, "resolucion_admin", None))

        if instance is not None:
            self.initial.setdefault("estado", instance.estado)
        self.initial.setdefault(
            "prioridad", previo["prioridad_codigo"] if previo else "MEDIA"
        )
        self.initial.setdefault(
            "accion_tomada",
            previo["accion_codigo"] if previo else "SEGUIMIENTO_CLIENTE",
        )
        self.initial.setdefault(
            "hora_entrada", hora_entrada_inicial or ahora.strftime("%H:%M")
        )
        self.initial.setdefault("hora_salida", ahora.strftime("%H:%M"))
        if previo and previo["detalle"]:
            self.initial.setdefault("detalle_solucion", previo["detalle"])

    def clean(self):
        cleaned_data = super().clean()
        hora_entrada = cleaned_data.get("hora_entrada")
        hora_salida = cleaned_data.get("hora_salida")
        if hora_salida is None:
            ahora = timezone.localtime()
            hora_salida = ahora.time().replace(second=0, microsecond=0)
            cleaned_data["hora_salida"] = hora_salida

        if hora_entrada and hora_salida:
            if (hora_salida.hour, hora_salida.minute) < (hora_entrada.hour, hora_entrada.minute):
                self.add_error(
                    "hora_salida",
                    "La hora de salida debe ser igual o posterior a la hora de entrada.",
                )

        estado = cleaned_data.get("estado")
        detalle = (cleaned_data.get("detalle_solucion") or "").strip()
        if estado in {"RESUELTO", "DESESTIMADA"} and len(detalle) < 10:
            self.add_error(
                "detalle_solucion",
                "Detalla la verificación y la solución brindada (mínimo 10 caracteres).",
            )
        cleaned_data["detalle_solucion"] = detalle
        return cleaned_data

    def save(self) -> Denuncia:
        if self.instance is None:
            raise ValueError("DenunciaResolucionForm requiere una instancia de Denuncia.")
        ahora = timezone.localtime()
        admin_nombre = (
            getattr(self.admin_user, "nombre_completo", "")
            or getattr(self.admin_user, "email", "")
            or "Administrador"
        )
        self.instance.estado = self.cleaned_data["estado"]
        self.instance.resolucion_admin = formatear_resolucion_soporte(
            admin_nombre=admin_nombre,
            fecha_str=ahora.strftime("%d/%m/%Y"),
            hora_entrada=self.cleaned_data["hora_entrada"],
            hora_salida=self.cleaned_data["hora_salida"],
            prioridad=self.cleaned_data["prioridad"],
            accion_tomada=self.cleaned_data["accion_tomada"],
            detalle_solucion=self.cleaned_data["detalle_solucion"],
        )
        self.instance.save(update_fields=["estado", "resolucion_admin"])
        return self.instance
