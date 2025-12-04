from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import UsuarioPerfil
from .serializers import (
    RegistroCorredorSerializer,
    UsuarioPerfilSerializer,
    CambiarRolSerializer,
)

User = get_user_model()


class RegistroCorredorView(APIView):
    """
    Registro de usuarios tipo corredor.
    No genera token aquí; el login se hace con SimpleJWT en /api/auth/login/.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegistroCorredorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.save()

        return Response(data, status=status.HTTP_201_CREATED)


class WhoAmIView(APIView):
    """
    Devuelve información del usuario autenticado, incluyendo rol y corredor.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        perfil = getattr(user, "perfil", None)

        data = {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_staff": user.is_staff,
            "perfil": {
                "id": getattr(perfil, "id", None),
                "rol": getattr(perfil, "rol", None),
                "corredor_id": getattr(perfil, "corredor_id", None),
            }
            if perfil
            else None,
        }

        return Response(data, status=status.HTTP_200_OK)


class CambiarRolView(APIView):
    """
    Permite que un usuario staff cambie el rol de otro UsuarioPerfil.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, usuario_id):
        # Solo staff puede cambiar roles
        if not request.user.is_staff:
            return Response(
                {"detail": "No tienes permiso para cambiar roles."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            perfil = UsuarioPerfil.objects.get(id=usuario_id)
        except UsuarioPerfil.DoesNotExist:
            return Response(
                {"detail": "UsuarioPerfil no encontrado."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = CambiarRolSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        perfil.rol = serializer.validated_data["rol"]
        perfil.save()

        return Response(
            {
                "detail": "Rol actualizado correctamente.",
                "usuario_id": perfil.id,
                "rol": perfil.rol,
            },
            status=status.HTTP_200_OK,
        )
