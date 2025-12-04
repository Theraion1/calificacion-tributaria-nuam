"""
URL configuration for nuam_backend project.
"""

from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from calificaciones.jwt_views import LoginAPI
from calificaciones.api import (
    PaisViewSet,
    CorredorViewSet,
    CalificacionTributariaViewSet,
    ArchivoCargaViewSet,
    HistorialCalificacionViewSet,
)
from calificaciones.views import (
    RegistroCorredorView,
    WhoAmIView,
    CambiarRolView,
)

router = DefaultRouter()
router.register(r"paises", PaisViewSet)
router.register(r"corredores", CorredorViewSet)
router.register(r"calificaciones", CalificacionTributariaViewSet)
router.register(r"archivos-carga", ArchivoCargaViewSet, basename="archivos-carga")
router.register(
    r"historial-calificaciones",
    HistorialCalificacionViewSet,
    basename="historial-calificaciones",
)

urlpatterns = [
    path("admin/", admin.site.urls),

    # Autenticación JWT
    path("api/auth/login/", LoginAPI.as_view(), name="jwt_login"),
    path("api/auth/refresh/", TokenRefreshView.as_view(), name="jwt_refresh"),

    # Usuarios / perfiles
    path("api/registro-corredor/", RegistroCorredorView.as_view(), name="registro-corredor"),
    path("api/whoami/", WhoAmIView.as_view(), name="whoami"),
    path(
        "api/usuarios/<int:usuario_id>/cambiar-rol/",
        CambiarRolView.as_view(),
        name="cambiar-rol",
    ),

    # API principal
    path("api/", include(router.urls)),
]
