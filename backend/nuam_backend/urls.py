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
    ConversionArchivoView,
)

router = DefaultRouter()
router.register("paises", PaisViewSet, basename="pais")
router.register("corredores", CorredorViewSet, basename="corredor")
router.register(
    "calificaciones",
    CalificacionTributariaViewSet,
    basename="calificacion-tributaria",
)
router.register("jobs-carga", ArchivoCargaViewSet, basename="archivo-carga")
router.register(
    "historial-calificaciones",
    HistorialCalificacionViewSet,
    basename="historial-calificacion",
)

urlpatterns = [
    path("admin/", admin.site.urls),

    # Auth JWT
    path("api/auth/login/", LoginAPI.as_view(), name="jwt_login"),
    path("api/auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),

    # Endpoints auxiliares
    path("api/registro-corredor/", RegistroCorredorView.as_view(), name="registro-corredor"),
    path("api/whoami/", WhoAmIView.as_view(), name="whoami"),
    path("api/usuarios/<int:usuario_id>/cambiar-rol/", CambiarRolView.as_view(), name="cambiar-rol"),

    # Conversión de archivos (PDF, CSV, XLSX)
    path("api/conversion/archivo/", ConversionArchivoView.as_view(), name="conversion-archivo"),

    # API principal
    path("api/", include(router.urls)),
]
