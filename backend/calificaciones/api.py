from rest_framework import viewsets, permissions
from rest_framework.exceptions import PermissionDenied

from .models import (
    Pais,
    Corredor,
    CalificacionTributaria,
    ArchivoCarga,
    HistorialCalificacion,
)
from .serializers import (
    PaisSerializer,
    CorredorSerializer,
    CalificacionTributariaSerializer,
    ArchivoCargaSerializer,
    HistorialCalificacionSerializer,
)


# PERMISOS ===============================

class IsStaffOrReadOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        # Lectura para todos los autenticados
        if request.method in permissions.SAFE_METHODS:
            return request.user.is_authenticated
        # Escritura solo staff / admin
        return request.user.is_staff or request.user.is_superuser


class CalificacionPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False

        # GET permitido para todos los autenticados
        if request.method in permissions.SAFE_METHODS:
            return True

        # Admin o staff → permitido
        if user.is_superuser or user.is_staff:
            return True

        perfil = getattr(user, "perfil", None)

        # Corredor → puede crear/editar
        if perfil and perfil.rol == "corredor":
            return True

        # Auditor → solo lectura
        return False

    def has_object_permission(self, request, view, obj):
        user = request.user

        # lectura
        if request.method in permissions.SAFE_METHODS:
            if user.is_superuser or user.is_staff:
                return True
            perfil = getattr(user, "perfil", None)
            if not perfil:
                return False
            if perfil.rol == "corredor":
                return obj.corredor_id == perfil.corredor_id
            if perfil.rol == "auditor":
                return True
            return False

        # DELETE → solo admin o staff
        if request.method == "DELETE":
            return user.is_superuser or user.is_staff

        # UPDATE → corredor solo su propio corredor
        perfil = getattr(user, "perfil", None)
        if perfil and perfil.rol == "corredor":
            return obj.corredor_id == perfil.corredor_id

        return user.is_superuser or user.is_staff


# VIEWSETS ===============================

class PaisViewSet(viewsets.ModelViewSet):
    queryset = Pais.objects.all()
    serializer_class = PaisSerializer
    permission_classes = [IsStaffOrReadOnly]


class CorredorViewSet(viewsets.ModelViewSet):
    queryset = Corredor.objects.all()
    serializer_class = CorredorSerializer
    permission_classes = [IsStaffOrReadOnly]


class CalificacionTributariaViewSet(viewsets.ModelViewSet):
    serializer_class = CalificacionTributariaSerializer
    permission_classes = [CalificacionPermission]

    def get_queryset(self):
        user = self.request.user
        qs = CalificacionTributaria.objects.all()

        if user.is_superuser or user.is_staff:
            return qs

        perfil = getattr(user, "perfil", None)
        if not perfil:
            return qs.none()

        # corredor ve solo lo suyo
        if perfil.rol == "corredor":
            return qs.filter(corredor=perfil.corredor)

        # auditor ve todo
        if perfil.rol == "auditor":
            return qs

        return qs.none()

    def perform_create(self, serializer):
        user = self.request.user

        if user.is_superuser or user.is_staff:
            serializer.save(creado_por=user)
            return

        perfil = getattr(user, "perfil", None)
        if not perfil or perfil.rol != "corredor":
            raise PermissionDenied("Solo los corredores pueden crear calificaciones.")

        serializer.save(
            corredor=perfil.corredor,
            creado_por=user,
            actualizado_por=user,
        )

    def perform_update(self, serializer):
        user = self.request.user

        if user.is_superuser or user.is_staff:
            serializer.save(actualizado_por=user)
            return

        perfil = getattr(user, "perfil", None)
        if not perfil or perfil.rol != "corredor":
            raise PermissionDenied("Solo los corredores pueden editar calificaciones.")

        serializer.save(
            corredor=perfil.corredor,
            actualizado_por=user,
        )

    def perform_destroy(self, instance):
        user = self.request.user
        if not (user.is_superuser or user.is_staff):
            raise PermissionDenied("Solo administradores pueden eliminar calificaciones.")
        instance.delete()


class ArchivoCargaViewSet(viewsets.ModelViewSet):
    queryset = ArchivoCarga.objects.all()
    serializer_class = ArchivoCargaSerializer
    permission_classes = [IsStaffOrReadOnly]


class HistorialCalificacionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = HistorialCalificacion.objects.all()
    serializer_class = HistorialCalificacionSerializer
    permission_classes = [permissions.IsAuthenticated]
