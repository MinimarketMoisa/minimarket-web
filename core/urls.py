"""URLs raiz de minimarket-django."""

from django.contrib import admin
from django.urls import path, include

admin.site.site_header = "Minimarket Admin"
admin.site.site_title  = "Minimarket"
admin.site.index_title = "Panel de administracion"

urlpatterns = [
    path("", include("tienda.urls")),
    path("admin/", admin.site.urls),
]
