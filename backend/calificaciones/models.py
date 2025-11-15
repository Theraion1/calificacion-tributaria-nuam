from django.db import models
from django.conf import settings


class TimeStampedModel(models.Model):
    """
    Modelo base con fechas de creación/actualización y flag de activo.
    Se hereda en casi todas las entidades del dominio.
    """
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    activo = models.BooleanField(default=True)

    class Meta:
        abstract = True


class Pais(TimeStampedModel):
    """
    País donde aplica la calificación tributaria.
    Corresponde a la clase 'Pais' del diagrama.
    Ej: Chile, Perú, Colombia.
    """
    nombre = models.CharField(max_length=100)
    # En el diagrama es 'codigo'; aquí puedes dejar 'codigo_iso3' o renombrar a 'codigo' si quieres 1:1
    codigo_iso3 = models.CharField(max_length=10, help_text="Ej: CHL, PER, COL")
    reglas_tributarias = models.JSONField(null=True, blank=True)

    class Meta:
        verbose_name = "País"
        verbose_name_plural = "Países"
        unique_together = ("nombre", "codigo_iso3")

    def __str__(self):
        return f"{self.nombre} ({self.codigo_iso3})"


class Corredor(TimeStampedModel):
    """
    Corredor / broker miembro de NUAM.
    Corresponde a la clase 'Corredor' del diagrama.
    """
    nombre = models.CharField(max_length=150)
    codigo_interno = models.CharField(max_length=50, unique=True)
    pais = models.ForeignKey(Pais, on_delete=models.PROTECT, related_name="corredores")
    # Campo config del diagrama, como JSON flexible
    config = models.JSONField(null=True, blank=True, help_text="Configuración específica del corredor")

    class Meta:
        verbose_name = "Corredor"
        verbose_name_plural = "Corredores"

    def __str__(self):
        return f"{self.nombre} [{self.codigo_interno}]"


class UsuarioPerfil(TimeStampedModel):
    """
    Corresponde a la clase 'Usuario' del diagrama.
    Extiende el usuario de Django con rol, metadata y corredor.
    """
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
        on_delete=models.PROTECT,
        related_name="usuarios",
    )

    class Meta:
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"

    def __str__(self):
        return f"{self.nombre} ({self.rol})"


class ArchivoCarga(TimeStampedModel):
    """
    Archivo subido para carga masiva de calificaciones.
    Corresponde a la entidad 'JobCarga' del diagrama (nombreArchivo, tipoArchivo, rutaObjeto, estado, etc.).
    Solo guardamos metadatos; el archivo físico puede vivir en disco, S3, etc.
    """
    ESTADO_PROCESO_CHOICES = [
        ("pendiente", "Pendiente"),
        ("procesando", "Procesando"),
        ("ok", "Procesado OK"),
        ("error", "Procesado con errores"),
    ]

    corredor = models.ForeignKey(
        Corredor,
        on_delete=models.CASCADE,
        related_name="archivos",
    )
    # nombreArchivo en el diagrama
    nombre_original = models.CharField(max_length=255)
    # rutaObjeto en el diagrama
    ruta_almacenamiento = models.CharField(
        max_length=500,
        help_text="Path o URL donde se guardó el archivo",
    )
    # tipoArchivo en el diagrama
    tipo_mime = models.CharField(max_length=100, null=True, blank=True)
    tamano_bytes = models.BigIntegerField(null=True, blank=True)
    # estado en el diagrama
    estado_proceso = models.CharField(
        max_length=20,
        choices=ESTADO_PROCESO_CHOICES,
        default="pendiente",
    )
    # resultadoResumen en el diagrama
    resumen_proceso = models.JSONField(
        null=True,
        blank=True,
        help_text="Resumen de registros OK, rechazados, etc.",
    )
    # erroresPorFila en el diagrama
    errores_por_fila = models.JSONField(
        null=True,
        blank=True,
        help_text="Detalle de errores por fila",
    )
    # startedAt / finishedAt / tiempoProcesamiento
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    tiempo_procesamiento_seg = models.FloatField(null=True, blank=True)
    # submittedByUsuarioId
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs_carga_enviados",
    )

    class Meta:
        verbose_name = "Job de Carga"
        verbose_name_plural = "Jobs de Carga"

    def __str__(self):
        return f"{self.nombre_original} ({self.estado_proceso})"


class CalificacionTributaria(TimeStampedModel):
    """
    Registro de calificación tributaria para un instrumento/cliente específico.
    Corresponde a la clase 'CalificacionTributaria' del diagrama.
    Más adelante se pueden agregar más campos según el Excel real.
    """
    corredor = models.ForeignKey(
        Corredor,
        on_delete=models.CASCADE,
        related_name="calificaciones",
    )
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
        help_text="País detectado automáticamente a partir del archivo/origen",
    )

    archivo_origen = models.ForeignKey(
        ArchivoCarga,
        on_delete=models.SET_NULL,
        related_name="calificaciones_generadas",
        null=True,
        blank=True,
    )

    identificador_cliente = models.CharField(
        max_length=100,
        help_text="RUT, documento, ID cliente, etc.",
    )
    instrumento = models.CharField(
        max_length=150,
        help_text="Acción, bono, fondo, etc.",
    )
    moneda = models.CharField(max_length=10, default="CLP")

    # Ejemplo de factores 8-19 (puede ajustarse después)
    factor_8 = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    factor_9 = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    factor_10 = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    factor_11 = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    factor_12 = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    factor_13 = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    factor_14 = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    factor_15 = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    factor_16 = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    factor_17 = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    factor_18 = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    factor_19 = models.DecimalField(max_digits=5, decimal_places=4, default=0)

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
        verbose_name = "Calificación Tributaria"
        verbose_name_plural = "Calificaciones Tributarias"
        indexes = [
            models.Index(fields=["corredor", "identificador_cliente"]),
            models.Index(fields=["pais"]),
        ]

    def __str__(self):
        return f"{self.identificador_cliente} - {self.instrumento} ({self.pais})"


class HistorialCalificacion(TimeStampedModel):
    """
    Registro histórico de cambios sobre una calificación tributaria.
    Corresponde a 'RegistroHistorico' del diagrama.
    Permite trazabilidad (quién cambió qué y cuándo).
    """
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
    # accion del diagrama (crear, actualizar, eliminar, etc.)
    accion = models.CharField(max_length=100, default="actualizacion")
    descripcion_cambio = models.TextField()
    datos_previos = models.JSONField(null=True, blank=True)
    datos_nuevos = models.JSONField(null=True, blank=True)
    # cambiosResumen, ip, userAgent del diagrama
    cambios_resumen = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        verbose_name = "Historial de Calificación"
        verbose_name_plural = "Historial de Calificaciones"
        ordering = ["-creado_en"]

    def __str__(self):
        return f"Historial #{self.id} de calificación {self.calificacion_id}"
