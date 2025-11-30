import csv
import io
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import ArchivoCarga, CalificacionTributaria, Corredor, Pais


def procesar_archivo_carga(archivo_carga: ArchivoCarga, file_obj) -> None:
    archivo_carga.started_at = timezone.now()
    archivo_carga.estado_proceso = "procesando"
    archivo_carga.save(update_fields=["started_at", "estado_proceso"])

    corredor = archivo_carga.corredor

    procesados = 0
    nuevos = 0
    actualizados = 0
    rechazados = 0
    errores_por_fila = []

    started = timezone.now()

    file_data = file_obj.read().decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(file_data))

    for idx, row in enumerate(reader, start=2):
        procesados += 1
        try:
            with transaction.atomic():
                pais = None
                pais_codigo = (row.get("pais") or "").strip()
                if pais_codigo:
                    pais = Pais.objects.filter(codigo_iso3=pais_codigo).first()

                calif, created = CalificacionTributaria.objects.get_or_create(
                    corredor=corredor,
                    identificador_cliente=row.get("identificador_cliente", "").strip(),
                    instrumento=row.get("instrumento", "").strip(),
                    defaults={
                        "moneda": row.get("moneda", "CLP"),
                        "pais": pais,
                    },
                )

                calif.moneda = row.get("moneda", calif.moneda or "CLP")
                calif.pais = pais or calif.pais
                calif.archivo_origen = archivo_carga
                calif.observaciones = row.get("observaciones", "")

                for n in range(8, 20):
                    field_name = f"factor_{n}"
                    raw = row.get(field_name, "") or "0"
                    try:
                        value = Decimal(str(raw).replace(",", "."))
                    except Exception:
                        value = Decimal("0")
                    setattr(calif, field_name, value)

                calif.save()

                if created:
                    nuevos += 1
                else:
                    actualizados += 1

        except ValidationError as ve:
            rechazados += 1
            errores_por_fila.append(
                {
                    "fila": idx,
                    "error": ve.message_dict if hasattr(ve, "message_dict") else str(ve),
                    "data": row,
                }
            )
        except Exception as exc:
            rechazados += 1
            errores_por_fila.append(
                {
                    "fila": idx,
                    "error": str(exc),
                    "data": row,
                }
            )

    finished = timezone.now()
    elapsed = (finished - started).total_seconds()

    archivo_carga.finished_at = finished
    archivo_carga.tiempo_procesamiento_seg = elapsed
    archivo_carga.estado_proceso = "ok" if rechazados == 0 else "error"
    archivo_carga.resumen_proceso = {
        "total_registros": procesados,
        "nuevos": nuevos,
        "actualizados": actualizados,
        "rechazados": rechazados,
    }
    archivo_carga.errores_por_fila = errores_por_fila
    archivo_carga.save(
        update_fields=[
            "finished_at",
            "tiempo_procesamiento_seg",
            "estado_proceso",
            "resumen_proceso",
            "errores_por_fila",
        ]
    )
