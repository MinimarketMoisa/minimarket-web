"""URLs raíz de minimarket-web."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("tienda.urls")),
    path("admin/", admin.site.urls),
]
