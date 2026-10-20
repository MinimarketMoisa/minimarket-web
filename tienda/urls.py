"""URLs de la app tienda (rutas API DRF)."""

from django.urls import path
from . import views

app_name = "tienda"

urlpatterns = [
    # Aqui iran los endpoints REST de Django cuando los desarrollemos
    # Ejemplo: path("productos/", views.ProductoListView.as_view(), name="producto-list"),
]
