from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from rest_framework.views import APIView

from django.core.files.storage import default_storage
from django.db.models import Q
from decimal import Decimal
from django.forms.models import model_to_dict
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse

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
from .services import (
    procesar_archivo_carga,
    procesar_archivo_carga_monto,
    DetectorPaisTributario,
    generar_vista_previa_archivo,
    convertir_archivo_generico,
)


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
        qs = CalificacionTributaria.objects.select_related("corredor", "pais").all()

        if not user.is_authenticated:
            return qs.none()

        if not (user.is_superuser or user.is_staff):
            perfil = getattr(user, "perfil", None)
            if not perfil:
                return qs.none()

            if perfil.rol == "corredor":
                qs = qs.filter(corredor=perfil.corredor)
            elif perfil.rol != "auditor":
                return qs.none()

        params = self.request.query_params

        mercado = params.get("mercado")
        ejercicio = params.get("ejercicio")
        estado = params.get("estado")

        if mercado:
            qs = qs.filter(mercado__iexact=mercado)
        if ejercicio:
            qs = qs.filter(ejercicio=ejercicio)
        if estado:
            qs = qs.filter(estado__iexact=estado)

        pais_id = params.get("pais_id")
        instrumento = params.get("instrumento")
        cliente = params.get("cliente")
        moneda = params.get("moneda")
        search = params.get("search")
        creado_desde = params.get("creado_desde")
        creado_hasta = params.get("creado_hasta")

        if pais_id:
            qs = qs.filter(pais_id=pais_id)

        corredor_id = params.get("corredor_id")
        if corredor_id and (user.is_superuser or user.is_staff):
            qs = qs.filter(corredor_id=corredor_id)

        if instrumento:
            qs = qs.filter(instrumento__icontains=instrumento)

        if cliente:
            qs = qs.filter(identificador_cliente__icontains=cliente)

        if moneda:
            qs = qs.filter(moneda__iexact=moneda)

        if search:
            qs = qs.filter(
                Q(instrumento__icontains=search)
                | Q(identificador_cliente__icontains=search)
                | Q(observaciones__icontains=search)
            )

        if creado_desde:
            qs = qs.filter(creado_en__date__gte=creado_desde)

        if creado_hasta:
            qs = qs.filter(creado_en__date__lte=creado_hasta)

        return qs.order_by("-creado_en")

    def perform_create(self, serializer):
        user = self.request.user
        if user.is_superuser or user.is_staff:
            calif = serializer.save(creado_por=user, actualizado_por=user)
        else:
            perfil = getattr(user, "perfil", None)
            if not perfil or perfil.rol != "corredor":
                raise PermissionDenied("Solo corredores pueden crear calificaciones.")
            calif = serializer.save(
                corredor=perfil.corredor,
                creado_por=user,
                actualizado_por=user,
            )

    def perform_update(self, serializer):
        user = self.request.user
        serializer.save(actualizado_por=user)

    @action(detail=True, methods=["post"], url_path="aprobar")
    def aprobar(self, request, pk=None):
        calif = self.get_object()
        user = request.user
        perfil = getattr(user, "perfil", None)

        if not (user.is_staff or user.is_superuser or (perfil and perfil.rol == "admin")):
            raise PermissionDenied("Solo admin/staff puede aprobar calificaciones.")

        calif.estado = "aprobada"
        calif.actualizado_por = user
        calif.save(update_fields=["estado", "actualizado_por", "actualizado_en"])

        HistorialCalificacion.objects.create(
            calificacion=calif,
            usuario=user,
            accion="aprobar",
            descripcion_cambio="Calificación aprobada.",
        )

        return Response(self.get_serializer(calif).data)

    @action(detail=True, methods=["post"], url_path="copiar")
    def copiar(self, request, pk=None):
        calif = self.get_object()
        user = request.user

        data = model_to_dict(
            calif,
            exclude=[
                "id", "creado_en", "actualizado_en", "archivo_origen",
                "creado_por", "actualizado_por"
            ],
        )

        if request.data.get("ejercicio"):
            data["ejercicio"] = request.data["ejercicio"]
        if request.data.get("mercado"):
            data["mercado"] = request.data["mercado"]

        data["estado"] = "pendiente"
        data["creado_por"] = user
        data["actualizado_por"] = user

        nueva = CalificacionTributaria.objects.create(**data)

        HistorialCalificacion.objects.create(
            calificacion=nueva,
            usuario=user,
            accion="copiar",
            descripcion_cambio=f"Copia de calificación {calif.id}.",
        )

        return Response(self.get_serializer(nueva).data, status=201)

    @action(detail=True, methods=["get"], url_path="historial")
    def historial(self, request, pk=None):
        eventos = self.get_object().historial.select_related("usuario")
        return Response(HistorialCalificacionSerializer(eventos, many=True).data)


class ArchivoCargaViewSet(viewsets.ModelViewSet):
    queryset = ArchivoCarga.objects.select_related("corredor").all()
    serializer_class = ArchivoCargaSerializer
    permission_classes = [ArchivoCargaPermission]
    parser_classes = [MultiPartParser, FormParser]

    def perform_create(self, serializer):
        user = self.request.user

        if user.is_superuser or user.is_staff:
            serializer.save(submitted_by=user)
            return

        perfil = getattr(user, "perfil", None)
        if not perfil or perfil.rol != "corredor":
            raise PermissionDenied("Solo corredores pueden crear cargas.")

        serializer.save(corredor=perfil.corredor, submitted_by=user)

    @action(detail=False, methods=["post"], url_path="subir")
    def subir_archivo(self, request):
        user = request.user
        if not user.is_authenticated:
            raise PermissionDenied("Autenticación requerida.")

        perfil = getattr(user, "perfil", None)
        if not (user.is_superuser or user.is_staff or (perfil and perfil.rol == "corredor")):
            raise PermissionDenied("No autorizado.")

        upload = request.FILES.get("archivo")
        if not upload:
            return Response({"detail": "Debe adjuntar archivo."}, status=400)

        tipo_carga = (request.data.get("tipo_carga") or "FACTOR").upper()
        if tipo_carga not in ("FACTOR", "MONTO"):
            tipo_carga = "FACTOR"

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
        ruta = default_storage.path(filename)

        archivo_carga = ArchivoCarga.objects.create(
            corredor=corredor,
            nombre_original=upload.name,
            ruta_almacenamiento=ruta,
            tipo_mime=upload.content_type or "",
            tamano_bytes=upload.size,
            estado_proceso="pendiente",
            submitted_by=user,
            tipo_carga=tipo_carga,
        )

        try:
            procesar_archivo_carga(archivo_carga)
        except Exception:
            archivo_carga.refresh_from_db()

        return Response(self.get_serializer(archivo_carga).data, status=201)

    @action(detail=True, methods=["get"], url_path="resumen")
    def resumen(self, request, pk=None):
        job = self.get_object()
        return Response(
            {
                "estado_proceso": job.estado_proceso,
                "resumen_proceso": job.resumen_proceso,
                "errores_por_fila": job.errores_por_fila,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
                "tiempo_procesamiento_seg": job.tiempo_procesamiento_seg,
            }
        )
