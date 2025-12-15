from rest_framework.permissions import BasePermission

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