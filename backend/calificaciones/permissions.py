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
     """
    Permisos para CalificacionTributaria

    - Admin / staff: acceso total
    - Corredor: puede ver, crear y editar SOLO sus calificaciones
    - Auditor: solo lectura
    """
    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        # Lectura libre para roles válidos
        if request.method in SAFE_METHODS:
            return True

        # Admin / staff
        if user.is_superuser or user.is_staff:
            return True

        perfil = getattr(user, "perfil", None)
        if not perfil:
            return False

        # Corredor puede modificar
        if perfil.rol == "corredor":
            return True

        # Auditor NO puede modificar
        return False

    def has_object_permission(self, request, view, obj):
        user = request.user

        # Lectura
        if request.method in SAFE_METHODS:
            if user.is_superuser or user.is_staff:
                return True

            perfil = getattr(user, "perfil", None)
            if not perfil:
                return False

            # Auditor ve todo
            if perfil.rol == "auditor":
                return True

            # Corredor ve solo las suyas
            if perfil.rol == "corredor":
                return obj.corredor_id == perfil.corredor_id

            return False

        # Escritura
        if user.is_superuser or user.is_staff:
            return True

        perfil = getattr(user, "perfil", None)
        if perfil and perfil.rol == "corredor":
            return obj.corredor_id == perfil.corredor_id

        return False