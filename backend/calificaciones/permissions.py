from rest_framework.permissions import BasePermission, SAFE_METHODS

class IsAdminOrAuditor(BasePermission):
    """
    Permite acceso solo a usuarios con rol admin o auditor
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        perfil = getattr(user, "perfil", None)
        if not perfil:
            return False

        return perfil.rol in ["admin", "auditor"]

class CalificacionPermission(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        if request.method in SAFE_METHODS:
            return True

        if user.is_superuser or user.is_staff:
            return True

        perfil = getattr(user, "perfil", None)
        if not perfil:
            return False

        return perfil.rol == "corredor"

    def has_object_permission(self, request, view, obj):
        user = request.user

        if request.method in SAFE_METHODS:
            if user.is_superuser or user.is_staff:
                return True

            perfil = getattr(user, "perfil", None)
            if not perfil:
                return False

            if perfil.rol == "auditor":
                return True

            if perfil.rol == "corredor":
                return obj.corredor_id == perfil.corredor_id

            return False

        if user.is_superuser or user.is_staff:
            return True

        perfil = getattr(user, "perfil", None)
        if perfil and perfil.rol == "corredor":
            return obj.corredor_id == perfil.corredor_id

        return False