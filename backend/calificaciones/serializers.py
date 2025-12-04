from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import (
    Pais,
    Corredor,
    CalificacionTributaria,
    ArchivoCarga,
    HistorialCalificacion,
    UsuarioPerfil,
)

User = get_user_model()


class PaisSerializer(serializers.ModelSerializer):
    class Meta:
        model = Pais
        fields = "__all__"


class CorredorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Corredor
        fields = "__all__"


class CalificacionTributariaSerializer(serializers.ModelSerializer):
    class Meta:
        model = CalificacionTributaria
        fields = "__all__"


class ArchivoCargaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ArchivoCarga
        fields = "__all__"


class HistorialCalificacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = HistorialCalificacion
        fields = "__all__"


class RegistroCorredorSerializer(serializers.Serializer):
    # Datos del usuario
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    nombre_usuario = serializers.CharField(max_length=150)

    # Opción 1: usar corredor existente
    corredor_id = serializers.IntegerField(required=False)

    # Opción 2: crear corredor nuevo
    nombre_corredor = serializers.CharField(max_length=150, required=False)
    codigo_interno = serializers.CharField(max_length=50, required=False)
    pais_id = serializers.IntegerField(required=False)

    def validate(self, attrs):
        username = attrs.get("username")
        corredor_id = attrs.get("corredor_id")
        codigo_interno = attrs.get("codigo_interno")
        nombre_corredor = attrs.get("nombre_corredor")
        pais_id = attrs.get("pais_id")

        # Validar username único
        if User.objects.filter(username=username).exists():
            raise serializers.ValidationError("Ese nombre de usuario ya existe.")

        # Si viene corredor_id → usar corredor existente
        if corredor_id:
            if not Corredor.objects.filter(id=corredor_id).exists():
                raise serializers.ValidationError(
                    {"corredor_id": "No existe un corredor con ese ID."}
                )
        else:
            # Si no viene corredor_id → obligamos a mandar datos del corredor nuevo
            if not (nombre_corredor and codigo_interno and pais_id):
                raise serializers.ValidationError(
                    "Debes enviar 'corredor_id' o bien "
                    "'nombre_corredor', 'codigo_interno' y 'pais_id' para crear un corredor nuevo."
                )

            # Validar código interno único solo cuando se crea corredor
            if Corredor.objects.filter(codigo_interno=codigo_interno).exists():
                raise serializers.ValidationError(
                    "Ese código interno de corredor ya existe."
                )

        return attrs

    def create(self, validated_data):
        username = validated_data["username"]
        email = validated_data["email"]
        password = validated_data["password"]
        nombre_usuario = validated_data["nombre_usuario"]

        corredor_id = validated_data.get("corredor_id")
        nombre_corredor = validated_data.get("nombre_corredor")
        codigo_interno = validated_data.get("codigo_interno")
        pais_id = validated_data.get("pais_id")

        # 1) Crear usuario Django
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
        )

        # 2) Obtener o crear corredor
        if corredor_id:
            corredor = Corredor.objects.get(id=corredor_id)
        else:
            pais = Pais.objects.get(id=pais_id)
            corredor = Corredor.objects.create(
                nombre=nombre_corredor,
                codigo_interno=codigo_interno,
                pais=pais,
            )

        # 3) Crear perfil
        perfil = UsuarioPerfil.objects.create(
            user=user,
            nombre=nombre_usuario,
            rol="corredor",
            corredor=corredor,
        )

        return {
            "user_id": user.id,
            "perfil_id": perfil.id,
            "corredor_id": corredor.id,
            "username": user.username,
            "rol": perfil.rol,
        }


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


class UsuarioPerfilSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = UsuarioPerfil
        fields = ("id", "username", "email", "nombre", "rol", "corredor")


class CambiarRolSerializer(serializers.Serializer):
    rol = serializers.ChoiceField(choices=["admin", "corredor", "auditor"])


class CustomTokenSerializer(TokenObtainPairSerializer):
    """
    Serializer para SimpleJWT que agrega datos del perfil al token.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        token["username"] = user.username
        token["email"] = user.email

        # IMPORTANTE: el related_name del perfil es "perfil"
        perfil = getattr(user, "perfil", None)
        if perfil:
            token["rol"] = perfil.rol
            token["corredor_id"] = perfil.corredor_id

        return token

    def validate(self, attrs):
        data = super().validate(attrs)

        user = self.user
        perfil = getattr(user, "perfil", None)

        data["user"] = {
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

        return data
