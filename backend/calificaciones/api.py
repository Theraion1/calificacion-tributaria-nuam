from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from rest_framework.views import APIView
from django.db.models import Q

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
    ArchivoCargaHistorialSerializer,
    HistorialPagination,
)
from .services import (
    procesar_archivo_carga,
    generar_vista_previa_archivo,
    convertir_archivo_generico,
)



# ============================================================
# PERMISOS
# ============================================================

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


# ============================================================
# Identificador si es admin o auditor
# ============================================================

class IsAdminOrAuditor(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if user.is_superuser or user.is_staff:
            return True

        perfil = getattr(user, "perfil", None)
        return perfil and perfil.rol in ["admin", "auditor"]


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
        return perfil and perfil.rol == "corredor"

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
        return perfil and perfil.rol == "corredor" and obj.corredor_id == perfil.corredor_id



# ============================================================
# VIEWSETS PRINCIPALES
# ============================================================

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

    # ----------------------------------------
    # FILTROS
    # ----------------------------------------
    def get_queryset(self):
        user = self.request.user
        qs = CalificacionTributaria.objects.select_related("corredor", "pais").all()

        if not user.is_authenticated:
            return qs.none()

        perfil = getattr(user, "perfil", None)

        if not (user.is_superuser or user.is_staff):
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

        # Filtros adicionales
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

    # ----------------------------------------
    # FILTROS DINÁMICOS
    # ----------------------------------------
    @action(detail=False, methods=["get"], url_path="filtros")
    def filtros(self, request):
        """
        Devuelve mercados y períodos comerciales reales
        según los datos existentes y permisos del usuario.
        """
        user = request.user
        qs = CalificacionTributaria.objects.all()

        if not (user.is_superuser or user.is_staff):
            perfil = getattr(user, "perfil", None)

        if not perfil:
            return Response({"mercados": [], "periodos": []})

        if perfil.rol == "corredor":
            qs = qs.filter(corredor=perfil.corredor)
        elif perfil.rol != "auditor":
            return Response({"mercados": [], "periodos": []})

        mercados = (
            qs.values_list("mercado", flat=True)
            .exclude(mercado__isnull=True)
            .exclude(mercado__exact="")
            .distinct()
            .order_by("mercado"))

        periodos = (
            qs.values_list("ejercicio", flat=True)
            .exclude(ejercicio__isnull=True)
            .distinct()
            .order_by("-ejercicio"))

        return Response({
            "mercados": list(mercados),
            "periodos": list(periodos),
        })


    # ----------------------------------------
    # CREATE / UPDATE
    # ----------------------------------------
    def perform_create(self, serializer):
        user = self.request.user

        if user.is_superuser or user.is_staff:
            serializer.save(identificador_cliente=user.username, creado_por=user, actualizado_por=user)
            return

        perfil = getattr(user, "perfil", None)
        if not perfil or perfil.rol != "corredor":
            raise PermissionDenied("Solo corredores pueden crear calificaciones.")

        pais = serializer.validated_data.get("pais") or perfil.corredor.pais
            serializer.save(
                corredor=perfil.corredor,
                pais=pais,
                identificador_cliente=user.username,
                creado_por=user,
                actualizado_por=user,
            )

    def perform_update(self, serializer):
        user = self.request.user
        serializer.save(actualizado_por=user)

    # ----------------------------------------
    # APROBAR
    # ----------------------------------------
    @action(detail=True, methods=["post"], url_path="aprobar")
    def aprobar(self, request, pk=None):
        calif = self.get_object()
        user = request.user
        perfil = getattr(user, "perfil", None)

        if not (user.is_staff or user.is_superuser or (perfil and perfil.rol == "admin")):
            raise PermissionDenied("No autorizado.")

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

    # ----------------------------------------
    # COPIAR
    # ----------------------------------------
    @action(detail=True, methods=["post"], url_path="copiar")
    def copiar(self, request, pk=None):
        calif = self.get_object()
        user = request.user

        data = model_to_dict(
            calif,
            exclude=[
                "id", "creado_en", "actualizado_en",
                "archivo_origen", "creado_por", "actualizado_por"
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

    # ----------------------------------------
    # HISTORIAL POR CALIFICACIÓN
    # ----------------------------------------
    @action(detail=True, methods=["get"], url_path="historial")
    def historial(self, request, pk=None):
        eventos = self.get_object().historial.select_related("usuario")
        return Response(HistorialCalificacionSerializer(eventos, many=True).data)
    @action(detail=False, methods=["post"], url_path="eliminar-masivo")
    def eliminar_masivo(self, request):
        """
        Elimina múltiples calificaciones en una sola operación.
        Requiere permisos de admin, staff o corredor (solo sus propias calificaciones).
        """
        ids = request.data.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return Response({"detail": "Debe enviar una lista de IDs."}, status=400)

        user = request.user
        perfil = getattr(user, "perfil", None)

        qs = CalificacionTributaria.objects.filter(id__in=ids)

        # Seguridad
        if not (user.is_superuser or user.is_staff):
            if not perfil or perfil.rol != "corredor":
                return Response({"detail": "No autorizado."}, status=403)

            qs = qs.filter(corredor=perfil.corredor)

        eliminados = qs.count()
        qs.delete()

        return Response(
            {"eliminados": eliminados, "ids": ids},
            status=200
        )




# ============================================================
# ARCHIVO CARGA (INCLUYE /subir)
# ============================================================

class ArchivoCargaViewSet(viewsets.ModelViewSet):
    queryset = ArchivoCarga.objects.select_related("corredor").all()
    serializer_class = ArchivoCargaSerializer
    permission_classes = [ArchivoCargaPermission]
    parser_classes = [MultiPartParser, FormParser]

    def perform_create(self, serializer):
        user = self.request.user
        perfil = getattr(user, "perfil", None)

        if user.is_superuser or user.is_staff:
            serializer.save(submitted_by=user)
        else:
            if not perfil or perfil.rol != "corredor":
                raise PermissionDenied("Solo corredores pueden crear cargas.")
            serializer.save(corredor=perfil.corredor, submitted_by=user)

    # ----------------------------------------
    # SUBIR ARCHIVO
    # ----------------------------------------
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

        # ADMIN puede subir para cualquier corredor
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

        procesar_archivo_carga(archivo_carga)

        return Response(self.get_serializer(archivo_carga).data, status=201)

    # ----------------------------------------
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



# ============================================================
# HISTORIAL GLOBAL (ViewSet Independiente)
# ============================================================

class HistorialArchivosViewSet(viewsets.ReadOnlyModelViewSet):

    serializer_class = ArchivoCargaHistorialSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = HistorialPagination

    def get_queryset(self):
        qs = ArchivoCarga.objects.select_related(
            "submitted_by",
            "corredor",
        ).order_by("-creado_en")

        user = self.request.user
        perfil = getattr(user, "perfil", None)

        # Admin / Auditor
        if user.is_staff or (perfil and perfil.rol in ["Administrador", "Auditor"]):
            return qs

        # Usuario normal
        return qs.filter(submitted_by=user)



# ============================================================
# CONVERSIÓN DE ARCHIVOS (PDF, XLSX, CSV)
# ============================================================

class ConversionArchivoView(APIView):
    """
    Endpoint para conversión de archivos:
    - preview
    - convertir
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        accion = request.query_params.get("accion", "preview").lower()
        file_obj = request.FILES.get("archivo")
        formato_destino = request.data.get("formato_destino")

        delimitador = (request.data.get("delimitador") or ",").strip()
        if len(delimitador) != 1:
            delimitador = ","

        if not file_obj:
            return Response({"detail": "No se recibió ningún archivo."}, status=400)

        try:
            if accion == "preview":
                data = generar_vista_previa_archivo(
                    file_obj, file_obj.name, delimiter=delimitador
                )
                return Response(data, status=200)

            elif accion == "convertir":
                buffer, out_name, mimetype = convertir_archivo_generico(
                    file_obj, file_obj.name, formato_destino, delimitador
                )

                response = HttpResponse(buffer.getvalue(), content_type=mimetype)
                response["Content-Disposition"] = f'attachment; filename="{out_name}"'
                return response

            else:
                return Response({"detail": "Acción no válida."}, status=400)

        except DjangoValidationError as e:
            return Response({"detail": str(e)}, status=400)

        except Exception as e:
            return Response({"detail": f"Error inesperado: {e}"}, status=500)
 
