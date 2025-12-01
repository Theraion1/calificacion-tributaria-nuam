from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied

from django.core.files.storage import default_storage
from django.db.models import Q

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
from .services import procesar_archivo_carga, DetectorPaisTributario


class IsStaffOrReadOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user and request.user.is_staff


class CalificacionPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        if user.is_superuser or user.is_staff:
            return True
        perfil = getattr(user, "perfil", None)
        if perfil and perfil.rol in ["corredor", "admin"]:
            return True
        return False

    def has_object_permission(self, request, view, obj):
        user = request.user
        if request.method in permissions.SAFE_METHODS:
            if user.is_superuser or user.is_staff:
                return True
            perfil = getattr(user, "perfil", None)
            if perfil and perfil.rol == "corredor":
                return obj.corredor_id == perfil.corredor_id
            if perfil and perfil.rol == "auditor":
                return True
            return False
        if user.is_superuser or user.is_staff:
            return True
        perfil = getattr(user, "perfil", None)
        if perfil and perfil.rol == "corredor":
            return obj.corredor_id == perfil.corredor_id
        return False


class ArchivoCargaPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        if user.is_superuser or user.is_staff:
            return True
        perfil = getattr(user, "perfil", None)
        if perfil and perfil.rol == "corredor":
            return True
        return False

    def has_object_permission(self, request, view, obj):
        user = request.user
        if request.method in permissions.SAFE_METHODS:
            if user.is_superuser or user.is_staff:
                return True
            perfil = getattr(user, "perfil", None)
            if perfil and perfil.rol == "corredor":
                return obj.corredor_id == perfil.corredor_id
            if perfil and perfil.rol == "auditor":
                return True
            return False
        if user.is_superuser or user.is_staff:
            return True
        perfil = getattr(user, "perfil", None)
        if perfil and perfil.rol == "corredor":
            return obj.corredor_id == perfil.corredor_id
        return False


class PaisViewSet(viewsets.ModelViewSet):
    queryset = Pais.objects.all()
    serializer_class = PaisSerializer
    permission_classes = [IsStaffOrReadOnly]


class CorredorViewSet(viewsets.ModelViewSet):
    queryset = Corredor.objects.select_related("pais").all()
    serializer_class = CorredorSerializer
    permission_classes = [IsStaffOrReadOnly]


class CalificacionTributariaViewSet(viewsets.ModelViewSet):
    queryset = CalificacionTributaria.objects.select_related("corredor", "pais").all()
    serializer_class = CalificacionTributariaSerializer
    permission_classes = [CalificacionPermission]

    def get_queryset(self):
        user = self.request.user
        qs = CalificacionTributaria.objects.select_related("corredor", "pais")

        if not user.is_authenticated:
            return qs.none()

        perfil = getattr(user, "perfil", None)

        if user.is_superuser or user.is_staff:
            pass
        elif perfil and perfil.rol == "corredor":
            qs = qs.filter(corredor=perfil.corredor)
        elif perfil and perfil.rol == "auditor":
            pass
        else:
            return qs.none()

        params = self.request.query_params

        if params.get("pais_id"):
            qs = qs.filter(pais_id=params["pais_id"])

        corredor_id = params.get("corredor_id")
        if corredor_id and (user.is_superuser or user.is_staff):
            qs = qs.filter(corredor_id=corredor_id)

        if params.get("instrumento"):
            qs = qs.filter(instrumento__icontains=params["instrumento"])

        if params.get("cliente"):
            qs = qs.filter(identificador_cliente__icontains=params["cliente"])

        if params.get("moneda"):
            qs = qs.filter(moneda__iexact=params["moneda"])

        if params.get("search"):
            s = params["search"]
            qs = qs.filter(
                Q(instrumento__icontains=s)
                | Q(identificador_cliente__icontains=s)
                | Q(observaciones__icontains=s)
            )

        if params.get("creado_desde"):
            qs = qs.filter(creado_en__date__gte=params["creado_desde"])

        if params.get("creado_hasta"):
            qs = qs.filter(creado_en__date__lte=params["creado_hasta"])

        return qs.order_by("-creado_en")

    def perform_create(self, serializer):
        user = self.request.user
        perfil = getattr(user, "perfil", None)

        if user.is_superuser or user.is_staff:
            serializer.save(creado_por=user, actualizado_por=user)
        elif perfil and perfil.rol == "corredor":
            serializer.save(
                corredor=perfil.corredor,
                creado_por=user,
                actualizado_por=user,
            )
        else:
            raise PermissionDenied("No autorizado")

    def perform_update(self, serializer):
        serializer.save(actualizado_por=self.request.user)

    @action(detail=True, methods=["post"])
    def detectar_pais(self, request, pk=None):
        calif = self.get_object()
        texto = request.data.get("texto")

        if not texto:
            return Response({"detail": "Debe enviar texto."}, status=400)

        row = {
            "identificador_cliente": calif.identificador_cliente,
            "instrumento": calif.instrumento,
            "observaciones": texto,
        }

        detector = DetectorPaisTributario()
        iso3, score = detector.detectar(row)

        pais = None
        if iso3:
            pais = Pais.objects.filter(codigo_iso3__iexact=iso3).first()

        if pais:
            calif.pais_detectado = pais
            calif.save(update_fields=["pais_detectado"])

        return Response({
            "iso3_detectado": iso3,
            "confianza": score,
            "pais_detectado": str(pais) if pais else None
        })


class ArchivoCargaViewSet(viewsets.ModelViewSet):
    queryset = ArchivoCarga.objects.select_related("corredor")
    serializer_class = ArchivoCargaSerializer
    permission_classes = [ArchivoCargaPermission]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        user = self.request.user
        qs = ArchivoCarga.objects.select_related("corredor")

        if not user.is_authenticated:
            return qs.none()

        perfil = getattr(user, "perfil", None)

        if user.is_superuser or user.is_staff:
            return qs
        if perfil and perfil.rol == "corredor":
            return qs.filter(corredor=perfil.corredor)
        if perfil and perfil.rol == "auditor":
            return qs

        return qs.none()

    @action(detail=False, methods=["post"])
    def subir(self, request):
        user = request.user
        perfil = getattr(user, "perfil", None)

        if not user.is_authenticated:
            raise PermissionDenied("Autenticación requerida.")

        if not (user.is_superuser or user.is_staff or (perfil and perfil.rol == "corredor")):
            raise PermissionDenied("No autorizado.")

        upload = request.FILES.get("archivo")
        if not upload:
            return Response({"detail": "Debe subir un archivo."}, status=400)

        if user.is_superuser or user.is_staff:
            corredor_id = request.data.get("corredor")
            if not corredor_id:
                return Response({"detail": "Debe indicar corredor."}, status=400)
            try:
                corredor = Corredor.objects.get(pk=corredor_id)
            except Corredor.DoesNotExist:
                return Response({"detail": "Corredor no encontrado."}, status=400)
        else:
            corredor = perfil.corredor

        filename = default_storage.save(f"cargas/{upload.name}", upload)
        ruta_fisica = default_storage.path(filename)

        obj = ArchivoCarga.objects.create(
            corredor=corredor,
            nombre_original=upload.name,
            ruta_almacenamiento=ruta_fisica,
            tipo_mime=upload.content_type,
            tamano_bytes=upload.size,
            estado_proceso="pendiente",
            submitted_by=user,
        )

        try:
            procesar_archivo_carga(obj)
        except Exception:
            obj.refresh_from_db()

        return Response(ArchivoCargaSerializer(obj).data)

    @action(detail=True, methods=["get"])
    def resumen(self, request, pk=None):
        obj = self.get_object()
        return Response({
            "estado_proceso": obj.estado_proceso,
            "resumen_proceso": obj.resumen_proceso,
            "errores_por_fila": obj.errores_por_fila,
            "started_at": obj.started_at,
            "finished_at": obj.finished_at,
            "tiempo_procesamiento_seg": obj.tiempo_procesamiento_seg,
        })


class HistorialCalificacionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = HistorialCalificacion.objects.select_related("calificacion", "usuario")
    serializer_class = HistorialCalificacionSerializer
    permission_classes = [permissions.IsAuthenticated]
