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
    identificador_cliente = serializers.CharField(read_only=True)
    class Meta:
        model = CalificacionTributaria
        fields = "__all__"
        read_only_fields = [
            "corredor",
            "creado_por",
            "actualizado_por",
            "pais_detectado",
            "archivo_origen",
            "creado_en",
            "actualizado_en",
        ]
        
    def create(self, validated_data):
        request = self.context.get("request")
        perfil = getattr(request.user, "perfil", None) if request else None
        corredor = getattr(perfil, "corredor", None)
        
        if not validated_data.get("pais") and corredor and corredor.pais:
            validated_data["pais"] = corredor.pais

        return super().create(validated_data)


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
    nombre_usuario = serializers.CharField(max_length=150)

    corredor_id = serializers.IntegerField(required=False)

    nombre_corredor = serializers.CharField(max_length=150, required=False)
    codigo_interno = serializers.CharField(max_length=50, required=False)
    pais_id = serializers.IntegerField(required=False)

    def validate(self, attrs):
        username = attrs.get("username")
        corredor_id = attrs.get("corredor_id")
        codigo_interno = attrs.get("codigo_interno")
        nombre_corredor = attrs.get("nombre_corredor")
        pais_id = attrs.get("pais_id")

        if User.objects.filter(username=username).exists():
            raise serializers.ValidationError("Ese nombre de usuario ya existe.")

        if corredor_id:
            if not Corredor.objects.filter(id=corredor_id).exists():
                raise serializers.ValidationError(
                    {"corredor_id": "No existe un corredor con ese ID."}
                )
        else:
            if not (nombre_corredor and codigo_interno and pais_id):
                raise serializers.ValidationError(
                    "Debes enviar 'corredor_id' o bien 'nombre_corredor', 'codigo_interno' y 'pais_id' para crear un corredor nuevo."
                )

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

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
        )

        if corredor_id:
            corredor = Corredor.objects.get(id=corredor_id)
        else:
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


class CustomTokenSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        token["username"] = user.username
        token["email"] = user.email

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

class ArchivoCargaHistorialSerializer(serializers.ModelSerializer):
    usuario = serializers.CharField(
        source="submitted_by.username",
        read_only=True
    )
    corredor_nombre = serializers.CharField(
        source="corredor.nombre",
        read_only=True
    )

    class Meta:
        model = ArchivoCarga
        fields = (
            "id",
            "creado_en",
            "usuario",
            "corredor_nombre",
            "nombre_original",
            "tipo_carga",
            "estado_proceso",
            "periodo",
            "mercado",
        )
