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


# =========================
# Permisos genéricos
# =========================

class IsStaffOrReadOnly(permissions.BasePermission):
    """
    Solo staff/admin pueden escribir.
    Cualquier usuario autenticado puede leer (GET, HEAD, OPTIONS).
    """

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return request.user.is_authenticated
        return request.user.is_staff or request.user.is_superuser


class CalificacionPermission(permissions.BasePermission):
    """
    Reglas:
    - Todos los autenticados pueden leer (filtramos en get_queryset).
    - corredor: puede crear/editar SOLO de su corredor.
    - auditor: solo lectura.
    - admin/staff: puede todo.
    - SOLO admin/staff pueden borrar (DELETE).
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        # Lectura permitida para todos los autenticados
        if request.method in permissions.SAFE_METHODS:
            return True

        # Escritura:
        if user.is_superuser or user.is_staff:
            return True

        perfil = getattr(user, "perfil", None)

        # corredores pueden crear/editar, auditor no
        if perfil and perfil.rol == "corredor":
            return True

        return False

    def has_object_permission(self, request, view, obj):
        user = request.user

        # Lectura
        if request.method in permissions.SAFE_METHODS:
            if user.is_superuser or user.is_staff:
                return True

            perfil = getattr(user, "perfil", None)
            if not perfil:
                return False

            if perfil.rol == "corredor":
                # corredor solo ve sus propias calificaciones
                return obj.corredor_id == perfil.corredor_id

            if perfil.rol == "auditor":
                # auditor puede ver todas
                return True

            return False

        # DELETE: solo admin/staff
        if request.method == "DELETE":
            return user.is_superuser or user.is_staff

        # UPDATE / PARTIAL_UPDATE:
        if user.is_superuser or user.is_staff:
            return True

        perfil = getattr(user, "perfil", None)
        if perfil and perfil.rol == "corredor":
            return obj.corredor_id == perfil.corredor_id

        return False


# =========================
# ViewSets
# =========================

class PaisViewSet(viewsets.ModelViewSet):
    queryset = Pais.objects.all().order_by("nombre")
    serializer_class = PaisSerializer
    permission_classes = [IsStaffOrReadOnly]


class CorredorViewSet(viewsets.ModelViewSet):
    queryset = Corredor.objects.all()
    serializer_class = CorredorSerializer
    permission_classes = [IsStaffOrReadOnly]


class CalificacionTributariaViewSet(viewsets.ModelViewSet):
    # IMPORTANTE: queryset definido para que el router pueda obtener el basename
    queryset = CalificacionTributaria.objects.all()
    serializer_class = CalificacionTributariaSerializer
    permission_classes = [CalificacionPermission]

    def get_queryset(self):
        user = self.request.user
        qs = CalificacionTributaria.objects.all()

        if not user.is_authenticated:
            return qs.none()

        # Admin / staff ven todo
        if user.is_superuser or user.is_staff:
            return qs

        perfil = getattr(user, "perfil", None)
        if not perfil:
            return qs.none()

        if perfil.rol == "corredor":
            # corredor solo ve su propio corredor
            return qs.filter(corredor=perfil.corredor)

        if perfil.rol == "auditor":
            # auditor puede ver todas
            return qs

        return qs.none()

    def perform_create(self, serializer):
        user = self.request.user

        # Admin/staff pueden crear para cualquier corredor
        if user.is_superuser or user.is_staff:
            serializer.save(creado_por=user, actualizado_por=user)
            return

        perfil = getattr(user, "perfil", None)
        if not perfil or perfil.rol != "corredor":
            raise PermissionDenied("Solo usuarios con rol 'corredor' pueden crear calificaciones.")

        # Forzamos a que la calificación quede asociada a SU corredor
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
            raise PermissionDenied("Solo usuarios con rol 'corredor' pueden editar calificaciones.")

        # Igual que en create: no dejamos que cambie el corredor
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
    queryset = ArchivoCarga.objects.select_related("corredor").all()
    serializer_class = ArchivoCargaSerializer
    permission_classes = [IsStaffOrReadOnly]


class HistorialCalificacionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = HistorialCalificacion.objects.select_related(
        "calificacion", "usuario"
    ).all()
    serializer_class = HistorialCalificacionSerializer
    permission_classes = [permissions.IsAuthenticated]
