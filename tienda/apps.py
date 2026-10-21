from django.apps import AppConfig
from django.contrib.admin.apps import AdminConfig


class TiendaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name               = "tienda"
    verbose_name       = "Minimarket - Tienda"


class MinimarketAdminConfig(AdminConfig):
    default_site = "tienda.admin_site.MinimarketAdminSite"
