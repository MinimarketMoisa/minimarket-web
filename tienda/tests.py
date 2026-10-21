from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase
from django.template.loader import get_template
from django.template.loader import render_to_string
from django.urls import reverse

from .admin_site import MinimarketAdminSite
from .forms import EmpleadoLoginForm, InventarioForm
from .views import admin_panel, empleado_login, empleado_panel


def make_user(role, *, is_staff=False, is_superuser=False):
    return SimpleNamespace(
        is_authenticated=True,
        is_active=True,
        is_staff=is_staff,
        is_superuser=is_superuser,
        nombre_completo="Personal",
        id_rol=SimpleNamespace(nombre=role),
    )


class PortalEmpleadoFormTests(SimpleTestCase):
    def test_login_uses_email_and_password(self):
        form = EmpleadoLoginForm()

        self.assertEqual(form.fields["username"].label, "Correo electrónico")
        self.assertEqual(form.fields["password"].label, "Contraseña")

    def test_inventory_rejects_negative_stock(self):
        form = InventarioForm(data={"stock_disponible": -1, "fecha_vencimiento": ""})

        self.assertFalse(form.is_valid())
        self.assertIn("stock_disponible", form.errors)

    def test_inventory_accepts_zero_stock_and_optional_expiration(self):
        form = InventarioForm(data={"stock_disponible": 0, "fecha_vencimiento": ""})

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["stock_disponible"], 0)
        self.assertIsNone(form.cleaned_data["fecha_vencimiento"])

    def test_portal_routes_are_registered(self):
        self.assertEqual(reverse("tienda:inicio"), "/")
        self.assertEqual(reverse("tienda:empleado-login"), "/empleados/login/")
        self.assertEqual(reverse("tienda:empleado-panel"), "/empleados/")
        self.assertEqual(reverse("tienda:admin-panel"), "/administracion/")
        self.assertEqual(reverse("tienda:pedidos"), "/empleados/pedidos/")
        self.assertEqual(reverse("tienda:inventario"), "/empleados/inventario/")

    def test_django_admin_routes_open_from_portal_navigation(self):
        for route_name in (
            "admin:tienda_pedido_changelist",
            "admin:tienda_inventario_changelist",
            "admin:tienda_producto_changelist",
            "admin:tienda_categoria_changelist",
            "admin:tienda_usuario_changelist",
            "admin:tienda_sucursal_changelist",
            "admin:tienda_empleado_changelist",
        ):
            with self.subTest(route=route_name):
                self.assertTrue(reverse(route_name))

    def test_admin_sidebar_uses_portal_sections_instead_of_django_changelists(self):
        sidebar = render_to_string(
            "tienda/partials/sidebar.html",
            {"portal": "admin", "user": make_user("ADMIN")},
        )

        for route_name in (
            "tienda:admin-pedidos",
            "tienda:admin-inventario",
            "tienda:admin-sucursales",
            "tienda:admin-productos",
            "tienda:admin-categorias",
            "tienda:admin-usuarios",
        ):
            self.assertIn(reverse(route_name), sidebar)
        self.assertNotIn("/admin/", sidebar)
        self.assertNotIn("Abrir consola Django", sidebar)

    def test_employee_sidebar_links_to_branch_operations_and_read_only_catalog(self):
        sidebar = render_to_string(
            "tienda/partials/sidebar.html",
            {"portal": "empleado", "user": make_user("EMPLEADO")},
        )

        self.assertIn(reverse("tienda:pedidos"), sidebar)
        self.assertIn(reverse("tienda:inventario"), sidebar)
        self.assertIn(reverse("tienda:empleado-productos"), sidebar)
        self.assertIn(reverse("tienda:empleado-categorias"), sidebar)
        self.assertNotIn("/admin/", sidebar)

    def test_custom_admin_site_allows_admin_role_without_staff_flag(self):
        site = MinimarketAdminSite()
        request = SimpleNamespace(user=make_user("ADMIN", is_staff=False))

        self.assertTrue(site.has_permission(request))

    def test_custom_admin_site_rejects_employee_role(self):
        site = MinimarketAdminSite()
        request = SimpleNamespace(user=make_user("EMPLEADO", is_staff=False))

        self.assertFalse(site.has_permission(request))

    def test_custom_admin_site_rejects_inactive_accounts(self):
        site = MinimarketAdminSite()
        user = make_user("ADMIN", is_staff=False)
        user.is_active = False
        request = SimpleNamespace(user=user)

        self.assertFalse(site.has_permission(request))

    def test_employee_templates_load(self):
        for template_name in (
            "tienda/empleado/base.html",
            "tienda/empleado/login.html",
            "tienda/empleado/panel.html",
            "tienda/empleado/pedidos.html",
            "tienda/empleado/pedido_detalle.html",
            "tienda/empleado/inventario.html",
            "tienda/empleado/inventario_editar.html",
            "tienda/admin/base.html",
            "tienda/admin/dashboard.html",
            "tienda/admin/portal_list.html",
            "tienda/admin/portal_form.html",
            "tienda/admin/pedido_detalle.html",
            "admin/base_site.html",
        ):
            with self.subTest(template=template_name):
                context = {}
                if template_name.endswith("pedido_detalle.html"):
                    context["pedido"] = SimpleNamespace(id_pedido=1)
                if template_name.endswith("dashboard.html"):
                    context.update(
                        {
                            "user": make_user("ADMIN", is_staff=True),
                            "sucursales": [],
                            "hoy": None,
                            "ventas_entregadas": 0,
                            "pedidos_hoy": 0,
                            "total_pedidos": 0,
                            "pendientes": 0,
                            "preparados": 0,
                            "en_ruta": 0,
                            "pagos_pendientes": 0,
                            "inventario_bajo": 0,
                            "repartidores_libres": 0,
                        }
                    )
                get_template(template_name).render(context)

    def test_brand_assets_are_available(self):
        static_directory = Path(__file__).parent / "static" / "tienda"

        self.assertTrue((static_directory / "logo-minimarket.png").is_file())
        self.assertTrue((static_directory / "empleado.css").is_file())
        self.assertTrue((static_directory / "admin-brand.css").is_file())

    def test_non_employee_cannot_open_employee_panel(self):
        request = RequestFactory().get("/empleados/")
        request.user = make_user("CLIENTE")

        response = empleado_panel(request)

        self.assertEqual(response.status_code, 403)

    def test_non_admin_cannot_open_admin_panel(self):
        request = RequestFactory().get("/administracion/")
        request.user = make_user("EMPLEADO")

        response = admin_panel(request)

        self.assertEqual(response.status_code, 403)

    def test_admin_login_redirects_to_global_dashboard(self):
        admin = make_user("ADMIN")
        request = RequestFactory().post(
            "/empleados/login/",
            {"username": "admin@example.com", "password": "password"},
        )
        request.user = AnonymousUser()
        form = SimpleNamespace(is_valid=lambda: True, get_user=lambda: admin)

        with patch("tienda.views.EmpleadoLoginForm", return_value=form), patch(
            "tienda.views.login"
        ):
            response = empleado_login(request)

        self.assertRedirects(
            response,
            reverse("tienda:admin-panel"),
            fetch_redirect_response=False,
        )

    def test_employee_login_redirects_to_branch_dashboard(self):
        employee = make_user("EMPLEADO")
        request = RequestFactory().post(
            "/empleados/login/",
            {"username": "employee@example.com", "password": "password"},
        )
        request.user = AnonymousUser()
        form = SimpleNamespace(is_valid=lambda: True, get_user=lambda: employee)

        with patch("tienda.views.EmpleadoLoginForm", return_value=form), patch(
            "tienda.views.login"
        ):
            response = empleado_login(request)

        self.assertRedirects(
            response,
            reverse("tienda:empleado-panel"),
            fetch_redirect_response=False,
        )

    def test_anonymous_user_is_redirected_to_login(self):
        request = RequestFactory().get("/empleados/")
        request.user = AnonymousUser()

        response = empleado_panel(request)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("tienda:empleado-login"), response.url)
