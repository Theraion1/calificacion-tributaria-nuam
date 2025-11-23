from rest_framework import viewsets, permissions

from .models import (
    Pais,
    Corredor,
    CalificacionTributaria,
    ArchivoCarga,
    HistorialCalificacion,
)

from .serializers import (
    PaisSerializer,
    CorredorSerializer,
    CalificacionTributariaSerializer,
    ArchivoCargaSerializer,
    HistorialCalificacionSerializer,
)


class PaisViewSet(viewsets.ModelViewSet):
    queryset = Pais.objects.all()
    serializer_class = PaisSerializer


class CorredorViewSet(viewsets.ModelViewSet):
    queryset = Corredor.objects.all()
    serializer_class = CorredorSerializer


class CalificacionTributariaViewSet(viewsets.ModelViewSet):
    queryset = CalificacionTributaria.objects.all()
    serializer_class = CalificacionTributariaSerializer


class ArchivoCargaViewSet(viewsets.ModelViewSet):
    queryset = ArchivoCarga.objects.all()
    serializer_class = ArchivoCargaSerializer


class HistorialCalificacionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = HistorialCalificacion.objects.all()
    serializer_class = HistorialCalificacionSerializer
