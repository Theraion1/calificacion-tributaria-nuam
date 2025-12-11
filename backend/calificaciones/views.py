from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.core.exceptions import ValidationError

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser

from .models import UsuarioPerfil
from .serializers import (
    RegistroCorredorSerializer,
    UsuarioPerfilSerializer,
    CambiarRolSerializer,
)
from .services import generar_vista_previa_archivo, convertir_archivo_generico

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
        }

        if perfil:
            data["perfil"] = UsuarioPerfilSerializer(perfil).data

        return Response(data, status=status.HTTP_200_OK)


class CambiarRolView(APIView):
    """
    Permite que un usuario staff cambie el rol de un UsuarioPerfil.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, usuario_id):
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


class ConversionArchivoView(APIView):
    """
    Endpoint para conversión de archivos.
    - POST /api/conversion/archivo/?accion=preview
      -> JSON con columns, rows, total_rows
    - POST /api/conversion/archivo/?accion=convertir
      -> descarga del archivo convertido
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        accion = request.query_params.get("accion", "preview").lower()
        file_obj = request.FILES.get("archivo")
        formato_destino = request.data.get("formato_destino")

        # Blindaje extra en la vista:
        # si viene vacío o con más de 1 carácter, lo forzamos a ","
        delimitador = (request.data.get("delimitador") or ",").strip()
        if len(delimitador) != 1:
            delimitador = ","

        if not file_obj:
            return Response(
                {"detail": "No se recibió ningún archivo."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            if accion == "preview":
                data = generar_vista_previa_archivo(
                    file_obj,
                    file_obj.name,
                    delimiter=delimitador,
                )
                return Response(data, status=status.HTTP_200_OK)

            elif accion == "convertir":
                buffer, out_name, mimetype = convertir_archivo_generico(
                    file_obj,
                    file_obj.name,
                    formato_destino=formato_destino,
                    delimiter=delimitador,
                )

                response = HttpResponse(buffer.getvalue(), content_type=mimetype)
                response["Content-Disposition"] = f'attachment; filename=\"{out_name}\"'
                return response

            else:
                return Response(
                    {"detail": "Acción no válida. Use 'preview' o 'convertir'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            return Response(
                {"detail": f"Error inesperado: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
