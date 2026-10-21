from django.contrib.admin import AdminSite


class MinimarketAdminSite(AdminSite):
    site_header = "Minimarket · Gestión"
    site_title = "Minimarket"
    index_title = "Gestión del minimarket"

    def has_permission(self, request):
        usuario = request.user
        if not usuario.is_active or not usuario.is_authenticated:
            return False
        rol = getattr(usuario, "id_rol", None)
        return usuario.is_superuser or (rol is not None and rol.nombre == "ADMIN")
