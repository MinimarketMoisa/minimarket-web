from django.contrib.admin import AdminSite

from .services import es_admin


class MinimarketAdminSite(AdminSite):
    site_header = "Minimarket · Gestión"
    site_title = "Minimarket"
    index_title = "Gestión del minimarket"

    def has_permission(self, request):
        return es_admin(request.user)
