from django.contrib.auth import authenticate, get_user_model
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import UsuarioPerfil
from .serializers import (
    RegistroCorredorSerializer,
    LoginSerializer,
    UsuarioPerfilSerializer,
    CambiarRolSerializer,
)

User = get_user_model()


class RegistroCorredorView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegistroCorredorSerializer(data=request.data)
        if serializer.is_valid():
            data = serializer.save()
            user = User.objects.get(id=data["user_id"])
            token, _ = Token.objects.get_or_create(user=user)
            data["token"] = token.key
            return Response(data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        username = serializer.validated_data["username"]
        password = serializer.validated_data["password"]

        user = authenticate(request, username=username, password=password)
        if not user:
            return Response(
                {"detail": "Credenciales inválidas"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token, _ = Token.objects.get_or_create(user=user)
        perfil = getattr(user, "perfil", None)

        return Response(
            {
                "token": token.key,
                "user_id": user.id,
                "username": user.username,
                "rol": getattr(perfil, "rol", None),
            }
        )


class WhoAmIView(APIView):
    def get(self, request):
        perfil = getattr(request.user, "perfil", None)
        if not perfil:
            return Response(
                {"detail": "El usuario no tiene perfil asociado"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = UsuarioPerfilSerializer(perfil).data
        return Response(data)


class CambiarRolView(APIView):
    def patch(self, request, usuario_id):
        user = request.user
        es_admin = user.is_superuser or user.is_staff
        perfil_actual = getattr(user, "perfil", None)
        if perfil_actual and perfil_actual.rol == "admin":
            es_admin = True

        if not es_admin:
            return Response(
                {"detail": "No tienes permisos para cambiar roles."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            perfil = UsuarioPerfil.objects.get(id=usuario_id)
        except UsuarioPerfil.DoesNotExist:
            return Response(
                {"detail": "UsuarioPerfil no encontrado"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = CambiarRolSerializer(data=request.data)
        if serializer.is_valid():
            nuevo_rol = serializer.validated_data["rol"]
            perfil.rol = nuevo_rol
            perfil.save()
            return Response(
                {
                    "detail": "Rol actualizado correctamente",
                    "usuario_id": perfil.id,
                    "rol": perfil.rol,
                }
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
