"""
URL configuration for nuam_backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from calificaciones.api import (
    PaisViewSet,
    CorredorViewSet,
    CalificacionTributariaViewSet,
    ArchivoCargaViewSet,
    HistorialCalificacionViewSet,
)

from calificaciones.views import (
    RegistroCorredorView,
    LoginView,
    WhoAmIView,
    CambiarRolView,
)

router = DefaultRouter()
router.register(r"paises", PaisViewSet)
router.register(r"corredores", CorredorViewSet)
router.register(r"calificaciones", CalificacionTributariaViewSet)
router.register(r"jobs-carga", ArchivoCargaViewSet)
router.register(r"historial", HistorialCalificacionViewSet, basename="historial")

urlpatterns = [
    path("admin/", admin.site.urls),

    # Endpoints JWT para el front
    path("api/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),

    # Tus endpoints actuales
    path("api/registro-corredor/", RegistroCorredorView.as_view(), name="registro-corredor"),
    path("api/login/", LoginView.as_view(), name="login"),
    path("api/whoami/", WhoAmIView.as_view(), name="whoami"),
    path(
        "api/usuarios/<int:usuario_id>/cambiar-rol/",
        CambiarRolView.as_view(),
        name="cambiar-rol",
    ),

    path("api/", include(router.urls)),
]
