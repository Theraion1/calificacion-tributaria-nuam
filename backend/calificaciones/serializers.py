from django.contrib.auth import get_user_model
from rest_framework import serializers
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
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    nombre_corredor = serializers.CharField(max_length=150)
    codigo_interno = serializers.CharField(max_length=50)
    pais_id = serializers.IntegerField()
    nombre_usuario = serializers.CharField(max_length=150)

    def validate(self, attrs):
        if User.objects.filter(username=attrs["username"]).exists():
            raise serializers.ValidationError("Ese nombre de usuario ya existe.")
        if Corredor.objects.filter(codigo_interno=attrs["codigo_interno"]).exists():
            raise serializers.ValidationError("Ese código interno de corredor ya existe.")
        return attrs

    def create(self, validated_data):
        username = validated_data["username"]
        email = validated_data["email"]
        password = validated_data["password"]
        nombre_corredor = validated_data["nombre_corredor"]
        codigo_interno = validated_data["codigo_interno"]
        pais_id = validated_data["pais_id"]
        nombre_usuario = validated_data["nombre_usuario"]

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
        )

        pais = Pais.objects.get(id=pais_id)

        corredor = Corredor.objects.create(
            nombre=nombre_corredor,
            codigo_interno=codigo_interno,
            pais=pais,
        )

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
