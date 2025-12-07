from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied

from django.core.files.storage import default_storage
from django.db.models import Q
from decimal import Decimal
from django.forms.models import model_to_dict
from django.core.exceptions import ValidationError as DjangoValidationError


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
        qs = CalificacionTributaria.objects.select_related("corredor", "pais").all()

        if not user.is_authenticated:
            return qs.none()

        if user.is_superuser or user.is_staff:
            pass
        else:
            perfil = getattr(user, "perfil", None)
            if not perfil:
                return qs.none()

            if perfil.rol == "corredor":
                qs = qs.filter(corredor=perfil.corredor)
            elif perfil.rol == "auditor":
                pass
            else:
                return qs.none()

        params = self.request.query_params

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

    # --------- helper para detectar país y setear pais_detectado ----------
    def _detectar_y_setear_pais_detectado(self, calif: CalificacionTributaria):
        texto_obs = (calif.observaciones or "").strip()
        if not texto_obs:
            return

        detector = DetectorPaisTributario()
        row = {
            "identificador_cliente": calif.identificador_cliente,
            "instrumento": calif.instrumento,
            "observaciones": texto_obs,
        }

        iso3_detectado, confianza = detector.detectar(row)
        if not iso3_detectado:
            return

        pais_detectado = Pais.objects.filter(
            codigo_iso3__iexact=iso3_detectado
        ).first()

        if pais_detectado and calif.pais_detectado_id != pais_detectado.id:
            calif.pais_detectado = pais_detectado
            calif.save(update_fields=["pais_detectado"])

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

        self._detectar_y_setear_pais_detectado(calif)

    def perform_update(self, serializer):
        user = self.request.user
        calif = serializer.save(actualizado_por=user)
        self._detectar_y_setear_pais_detectado(calif)

    # ======================================================
    # 4.2 Recalcular país (detalle)
    # ======================================================
    @action(detail=True, methods=["post"], url_path="recalcular-pais")
    def recalcular_pais(self, request, pk=None):
        calif = self.get_object()

        texto = request.data.get("texto")
        if not texto:
            texto = calif.observaciones or ""
        if not texto.strip():
            return Response(
                {"detail": "No hay texto disponible para detección."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        row = {
            "identificador_cliente": calif.identificador_cliente,
            "instrumento": calif.instrumento,
            "observaciones": texto,
        }

        detector = DetectorPaisTributario()
        iso3_detectado, confianza = detector.detectar(row)

        pais_detectado = None
        if iso3_detectado:
            pais_detectado = Pais.objects.filter(
                codigo_iso3__iexact=iso3_detectado
            ).first()

        if pais_detectado:
            calif.pais_detectado = pais_detectado
            calif.save(update_fields=["pais_detectado"])

        return Response(
            {
                "id": calif.id,
                "pais_detectado": str(pais_detectado) if pais_detectado else None,
                "iso3_detectado": iso3_detectado,
                "confianza": confianza,
            }
        )

    # ======================================================
    # 4.3 Recalcular país detectado masivo (detail=False)
    # ======================================================
    @action(detail=False, methods=["post"], url_path="recalcular-pais-detectado")
    def recalcular_pais_detectado(self, request):
        user = request.user
        if not (user.is_staff or user.is_superuser):
            raise PermissionDenied(
                "Solo staff puede recalcular pais_detectado masivamente."
            )

        qs = self.get_queryset().filter(
            pais_detectado__isnull=True
        ).exclude(
            observaciones__isnull=True
        ).exclude(
            observaciones__exact=""
        )

        total = qs.count()
        actualizados = 0
        sin_detectar = 0

        detector = DetectorPaisTributario()

        for calif in qs:
            row = {
                "identificador_cliente": calif.identificador_cliente,
                "instrumento": calif.instrumento,
                "observaciones": calif.observaciones or "",
            }
            iso3_detectado, confianza = detector.detectar(row)
            if not iso3_detectado:
                sin_detectar += 1
                continue

            pais_detectado = Pais.objects.filter(
                codigo_iso3__iexact=iso3_detectado
            ).first()

            if not pais_detectado:
                sin_detectar += 1
                continue

            if calif.pais_detectado_id != pais_detectado.id:
                calif.pais_detectado = pais_detectado
                calif.save(update_fields=["pais_detectado"])
                actualizados += 1

        return Response(
            {
                "total": total,
                "actualizados": actualizados,
                "sin_detectar": sin_detectar,
            }
        )

    # ======================================================
    # 4.2 Calcular factores (normaliza para que sumen 1)
    # ======================================================
    @action(detail=True, methods=["post"], url_path="calcular-factores")
    def calcular_factores(self, request, pk=None):
        calif = self.get_object()

        montos = [
            calif.factor_8, calif.factor_9, calif.factor_10, calif.factor_11,
            calif.factor_12, calif.factor_13, calif.factor_14, calif.factor_15,
            calif.factor_16, calif.factor_17, calif.factor_18, calif.factor_19,
        ]
        total = sum((m or Decimal("0") for m in montos), Decimal("0"))

        if total <= Decimal("0"):
            return Response(
                {"detail": "La suma actual de factores es 0; no se puede normalizar."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        nombres = [
            "factor_8", "factor_9", "factor_10", "factor_11",
            "factor_12", "factor_13", "factor_14", "factor_15",
            "factor_16", "factor_17", "factor_18", "factor_19",
        ]

        # Usaremos 4 decimales máximo (max_digits=5, decimal_places=4)
        escala = Decimal("0.0001")

        # 1) calculamos valores normalizados y redondeados
        valores = []
        for monto in montos:
            bruto = (monto or Decimal("0")) / total
            valor = bruto.quantize(escala)  # 4 decimales máximo
            valores.append(valor)

        # 2) asignamos a los campos
        for nombre, valor in zip(nombres, valores):
            setattr(calif, nombre, valor)

        # 3) intentamos guardar capturando errores de validación del modelo
        try:
            calif.save()
        except DjangoValidationError as e:
            return Response(
                {"detail": e.messages},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 4) calculamos la suma final para devolverla
        suma = sum(valores, Decimal("0"))

        return Response(
            {
                "id": calif.id,
                "suma_factores": str(suma),
            }
        )


    # ======================================================
    # 4.2 Aprobar calificación
    # ======================================================
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
            descripcion_cambio="Calificación marcada como aprobada.",
        )

        serializer = self.get_serializer(calif)
        return Response(serializer.data)

    # ======================================================
    # 4.2 Copiar calificación
    # ======================================================
    @action(detail=True, methods=["post"], url_path="copiar")
    def copiar(self, request, pk=None):
        calif = self.get_object()
        user = request.user

        data = model_to_dict(
            calif,
            exclude=[
                "id",
                "creado_en",
                "actualizado_en",
                "archivo_origen",
                "creado_por",
                "actualizado_por",
            ],
        )

        nuevo_ejercicio = request.data.get("ejercicio")
        nuevo_mercado = request.data.get("mercado")

        if nuevo_ejercicio is not None:
            data["ejercicio"] = nuevo_ejercicio
        if nuevo_mercado is not None:
            data["mercado"] = nuevo_mercado

        data["estado"] = "pendiente"
        data["creado_por"] = user
        data["actualizado_por"] = user

        nueva_calif = CalificacionTributaria.objects.create(**data)

        HistorialCalificacion.objects.create(
            calificacion=nueva_calif,
            usuario=user,
            accion="copiar",
            descripcion_cambio=f"Copia de calificación {calif.id}.",
        )

        serializer = self.get_serializer(nueva_calif)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    # ======================================================
    # 6. Historial de una calificación específica
    # ======================================================
    @action(detail=True, methods=["get"], url_path="historial")
    def historial(self, request, pk=None):
        calif = self.get_object()
        eventos = calif.historial.select_related("usuario").all()
        serializer = HistorialCalificacionSerializer(eventos, many=True)
        return Response(serializer.data)



class ArchivoCargaViewSet(viewsets.ModelViewSet):
    queryset = ArchivoCarga.objects.select_related("corredor").all()
    serializer_class = ArchivoCargaSerializer
    permission_classes = [ArchivoCargaPermission]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        user = self.request.user
        qs = ArchivoCarga.objects.select_related("corredor").all()
        if not user.is_authenticated:
            return qs.none()
        if user.is_superuser or user.is_staff:
            return qs
        perfil = getattr(user, "perfil", None)
        if not perfil:
            return qs.none()
        if perfil.rol == "corredor":
            return qs.filter(corredor=perfil.corredor)
        if perfil.rol == "auditor":
            return qs
        return qs.none()

    def perform_create(self, serializer):
        user = self.request.user
        if user.is_superuser or user.is_staff:
            serializer.save(submitted_by=user)
            return
        perfil = getattr(user, "perfil", None)
        if not perfil or perfil.rol != "corredor":
            raise PermissionDenied("Solo corredores pueden crear cargas.")
        serializer.save(
            corredor=perfil.corredor,
            submitted_by=user,
        )

    @action(detail=False, methods=["post"], url_path="subir")
    def subir_archivo(self, request):
        user = request.user
        if not user.is_authenticated:
            raise PermissionDenied("Autenticación requerida.")
        perfil = getattr(user, "perfil", None)
        if not (user.is_superuser or user.is_staff) and not (
            perfil and perfil.rol == "corredor"
        ):
            raise PermissionDenied("No autorizado.")

        upload = request.FILES.get("archivo")
        if not upload:
            return Response(
                {"detail": "Debe adjuntar archivo."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if user.is_superuser or user.is_staff:
            corredor_id = request.data.get("corredor")
            if not corredor_id:
                return Response(
                    {"detail": "Debe indicar corredor."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                corredor = Corredor.objects.get(pk=corredor_id)
            except Corredor.DoesNotExist:
                return Response(
                    {"detail": "Corredor no encontrado."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
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
        )

        try:
            procesar_archivo_carga(archivo_carga)
        except Exception:
            archivo_carga.refresh_from_db()

        archivo_carga.refresh_from_db()
        serializer = self.get_serializer(archivo_carga)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

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

    # ======================================================
    # 5.2 Procesar job (general / FACTOR)
    # ======================================================
    @action(detail=True, methods=["post"], url_path="procesar")
    def procesar(self, request, pk=None):
        job = self.get_object()

        user = request.user
        if not user.is_authenticated:
            raise PermissionDenied("Autenticación requerida.")
        if not (user.is_superuser or user.is_staff):
            perfil = getattr(user, "perfil", None)
            if not (perfil and perfil.rol == "corredor" and job.corredor_id == perfil.corredor_id):
                raise PermissionDenied("No autorizado para procesar este job.")

        procesar_archivo_carga(job)

        job.refresh_from_db()
        serializer = self.get_serializer(job)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="procesar-factor")
    def procesar_factor(self, request, pk=None):
        job = self.get_object()
        if job.tipo_carga != "FACTOR":
            return Response(
                {"detail": "Este job no es de tipo FACTOR."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return self.procesar(request, pk)

    # ======================================================
    # 5.2 MONTO / CALCULAR FACTORES (stubs por ahora)
    # ======================================================
    @action(detail=True, methods=["post"], url_path="procesar-monto")
    def procesar_monto(self, request, pk=None):
        job = self.get_object()
        if job.tipo_carga != "MONTO":
            return Response(
                {"detail": "Este job no es de tipo MONTO."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"detail": "Procesar carga por MONTO todavía no está implementado en el backend."},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    @action(detail=True, methods=["post"], url_path="calcular-factores")
    def calcular_factores_desde_job(self, request, pk=None):
        job = self.get_object()
        if job.tipo_carga != "MONTO":
            return Response(
                {"detail": "Este job no es de tipo MONTO."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"detail": "Calcular factores para cargas por MONTO todavía no está implementado en el backend."},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )



class HistorialCalificacionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = HistorialCalificacion.objects.select_related(
        "calificacion", "usuario"
    ).all()
    serializer_class = HistorialCalificacionSerializer
    permission_classes = [permissions.IsAuthenticated]
