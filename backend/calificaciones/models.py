from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Max, IntegerField
from django.db.models.functions import Cast


# =====================================================================
# BASE
# =====================================================================
class TimeStampedModel(models.Model):
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    activo = models.BooleanField(default=True)

    class Meta:
        abstract = True


# =====================================================================
# PAIS
# =====================================================================
class Pais(TimeStampedModel):
    nombre = models.CharField(max_length=100)
    codigo_iso3 = models.CharField(max_length=10)
    reglas_tributarias = models.JSONField(null=True, blank=True)

    class Meta:
        unique_together = ("nombre", "codigo_iso3")

    def __str__(self):
        return f"{self.nombre} ({self.codigo_iso3})"


# =====================================================================
# CORREDOR
# =====================================================================
class Corredor(TimeStampedModel):
    nombre = models.CharField(max_length=150)
    codigo_interno = models.CharField(max_length=50, unique=True)
    pais = models.ForeignKey(Pais, on_delete=models.PROTECT, related_name="corredores")

    config = models.JSONField(null=True, blank=True)
    email_contacto = models.EmailField(null=True, blank=True)

    def __str__(self):
        return f"{self.nombre} [{self.codigo_interno}]"

    def delete(self, *args, **kwargs):
        User = get_user_model()
        usuarios_ids = list(self.usuarios.values_list("user_id", flat=True))
        User.objects.filter(id__in=usuarios_ids).delete()
        super().delete(*args, **kwargs)


# =====================================================================
# USUARIO PERFIL
# =====================================================================
class UsuarioPerfil(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil",
    )

    nombre = models.CharField(max_length=150)
    rol = models.CharField(max_length=50)
    metadata = models.JSONField(null=True, blank=True)

    corredor = models.ForeignKey(
        Corredor,
        on_delete=models.CASCADE,
        related_name="usuarios",
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"{self.nombre} ({self.rol})"

    def clean(self):
        super().clean()
        if self.rol in ("corredor", "auditor") and not self.corredor:
            raise ValidationError(
                {"corredor": "Este campo es obligatorio para rol corredor/auditor."}
            )


# =====================================================================
# ARCHIVO DE CARGA
# =====================================================================
class ArchivoCarga(TimeStampedModel):
    ESTADO_PROCESO_CHOICES = [
        ("pendiente", "Pendiente"),
        ("procesando", "Procesando"),
        ("ok", "Ok"),
        ("error", "Error"),
    ]

    TIPO_CARGA_CHOICES = [
        ("FACTOR", "Carga por factor"),
        ("MONTO", "Carga por monto"),
    ]

    corredor = models.ForeignKey(
        Corredor,
        on_delete=models.CASCADE,
        related_name="archivos",
    )

    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs_carga_enviados",
    )

    tipo_carga = models.CharField(max_length=10, choices=TIPO_CARGA_CHOICES, default="FACTOR")
    periodo = models.PositiveIntegerField(null=True, blank=True)
    mercado = models.CharField(max_length=10, null=True, blank=True)

    nombre_original = models.CharField(max_length=255)
    ruta_almacenamiento = models.CharField(max_length=500)
    tipo_mime = models.CharField(max_length=100, null=True, blank=True)
    tamano_bytes = models.BigIntegerField(null=True, blank=True)

    estado_proceso = models.CharField(max_length=20, choices=ESTADO_PROCESO_CHOICES, default="pendiente")

    resumen_proceso = models.JSONField(null=True, blank=True)
    errores_por_fila = models.JSONField(null=True, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    tiempo_procesamiento_seg = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f"{self.nombre_original} ({self.estado_proceso})"


# =====================================================================
# CALIFICACIÓN TRIBUTARIA
# =====================================================================
class CalificacionTributaria(TimeStampedModel):
    ejercicio = models.PositiveIntegerField(null=True, blank=True)
    mercado = models.CharField(max_length=10, null=True, blank=True)

    fecha_pago = models.DateField(null=True, blank=True)
    descripcion = models.CharField(max_length=255, null=True, blank=True)
    secuencia_evento = models.CharField(max_length=50, null=True, blank=True)

    dividendo = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    valor_historico = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)

    # Campo necesario para cargas masivas y cálculo
    valor_actualizado = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        default=0
    )

    factor_actualizacion = models.DecimalField(max_digits=10, decimal_places=5, null=True, blank=True)

    isfut = models.BooleanField(default=False)

    ESTADO_CHOICES = [
        ("pendiente", "Pendiente"),
        ("aprobada", "Aprobada"),
        ("rechazada", "Rechazada"),
    ]
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="pendiente")

    corredor = models.ForeignKey(Corredor, on_delete=models.CASCADE, related_name="calificaciones")

    pais = models.ForeignKey(
        Pais,
        on_delete=models.PROTECT,
        related_name="calificaciones",
        null=True,
        blank=True,
    )

    pais_detectado = models.ForeignKey(
        Pais,
        on_delete=models.SET_NULL,
        related_name="calificaciones_detectadas",
        null=True,
        blank=True,
    )

    archivo_origen = models.ForeignKey(
        ArchivoCarga,
        on_delete=models.CASCADE,
        related_name="calificaciones_generadas",
        null=True,
        blank=True,
    )

    identificador_cliente = models.CharField(max_length=100)
    instrumento = models.CharField(max_length=150)
    moneda = models.CharField(max_length=10, default="CLP")

    factor_8  = models.DecimalField(max_digits=7, decimal_places=5, default=0)
    factor_9  = models.DecimalField(max_digits=7, decimal_places=5, default=0)
    factor_10 = models.DecimalField(max_digits=7, decimal_places=5, default=0)
    factor_11 = models.DecimalField(max_digits=7, decimal_places=5, default=0)
    factor_12 = models.DecimalField(max_digits=7, decimal_places=5, default=0)
    factor_13 = models.DecimalField(max_digits=7, decimal_places=5, default=0)
    factor_14 = models.DecimalField(max_digits=7, decimal_places=5, default=0)
    factor_15 = models.DecimalField(max_digits=7, decimal_places=5, default=0)
    factor_16 = models.DecimalField(max_digits=7, decimal_places=5, default=0)
    factor_17 = models.DecimalField(max_digits=7, decimal_places=5, default=0)
    factor_18 = models.DecimalField(max_digits=7, decimal_places=5, default=0)
    factor_19 = models.DecimalField(max_digits=7, decimal_places=5, default=0)

    observaciones = models.TextField(null=True, blank=True)

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="calificaciones_creadas",
        null=True,
        blank=True,
    )
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="calificaciones_actualizadas",
        null=True,
        blank=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["corredor", "identificador_cliente"]),
            models.Index(fields=["pais"]),
        ]

    def __str__(self):
        return f"{self.identificador_cliente} - {self.instrumento}"

    def suma_factores(self) -> Decimal:
        valores = [
            self.factor_8,
            self.factor_9,
            self.factor_10,
            self.factor_11,
            self.factor_12,
            self.factor_13,
            self.factor_14,
            self.factor_15,
            self.factor_16,
            self.factor_17,
            self.factor_18,
            self.factor_19,
        ]
        return sum((v or Decimal("0") for v in valores), Decimal("0"))

    def clean(self):
        super().clean()

        if self.suma_factores() > Decimal("1"):
            raise ValidationError("La suma de factores no puede ser mayor a 1.")

        if self.secuencia_evento:
            if not self.secuencia_evento.isdigit():
                raise ValidationError({"secuencia_evento": "Debe ser numérica."})
            if int(self.secuencia_evento) < 10000:
                raise ValidationError(
                    {"secuencia_evento": "Debe ser mayor o igual a 10000."}
                )

    def save(self, *args, **kwargs):
        if not self.secuencia_evento:
            qs = (
                self.__class__
                .objects.exclude(secuencia_evento__isnull=True)
                .exclude(secuencia_evento__exact="")
                .annotate(seq_int=Cast("secuencia_evento", IntegerField()))
            )
            max_seq = qs.aggregate(max_seq=Max("seq_int"))["max_seq"] or 9999
            self.secuencia_evento = str(max_seq + 1)

        self.full_clean()
        return super().save(*args, **kwargs)


# =====================================================================
# HISTORIAL
# =====================================================================
class HistorialCalificacion(TimeStampedModel):
    calificacion = models.ForeignKey(
        CalificacionTributaria,
        on_delete=models.CASCADE,
        related_name="historial",
    )

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eventos_calificacion",
    )

    accion = models.CharField(max_length=100, default="actualizacion")
    descripcion_cambio = models.TextField()

    datos_previos = models.JSONField(null=True, blank=True)
    datos_nuevos = models.JSONField(null=True, blank=True)
    cambios_resumen = models.JSONField(null=True, blank=True)

    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        ordering = ["-creado_en"]

    def __str__(self):
        return f"Historial #{self.id} de calificación {self.calificacion_id}"
