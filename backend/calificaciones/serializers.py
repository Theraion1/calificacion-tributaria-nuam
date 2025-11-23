from rest_framework import serializers
from .models import (
    Pais,
    Corredor,
    CalificacionTributaria,
    ArchivoCarga,
    HistorialCalificacion,
)


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
