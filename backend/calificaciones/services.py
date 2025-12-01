import os
from decimal import Decimal

import pandas as pd
import pdfplumber
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import ArchivoCarga, CalificacionTributaria, Pais


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

    ext = os.path.splitext(archivo_carga.nombre_original)[1].lower()
    rows = []

    # 1) CSV
    if ext == ".csv":
        df = pd.read_csv(file_obj)
        rows = df.to_dict(orient="records")

    # 2) Excel
    elif ext in [".xlsx", ".xls"]:
        df = pd.read_excel(file_obj)
        rows = df.to_dict(orient="records")

    # 3) PDF: leemos la primera tabla utilizable
    elif ext == ".pdf":
        file_obj.seek(0)
        try:
            extracted_rows = []

            with pdfplumber.open(file_obj) as pdf:
                for page in pdf.pages:
                    table = page.extract_table()
                    if not table:
                        continue

                    # Primera fila = cabecera
                    header, *data_rows = table
                    if not header:
                        continue

                    # Normalizar nombres de columnas
                    normalized_headers = []
                    for h in header:
                        if h is None:
                            normalized_headers.append("")
                        else:
                            normalized_headers.append(
                                str(h).strip().lower().replace(" ", "_")
                            )

                    # Construir diccionarios fila por fila
                    for data in data_rows:
                        if data is None:
                            continue
                        # saltar filas completamente vacías
                        if all(
                            cell is None or str(cell).strip() == ""
                            for cell in data
                        ):
                            continue

                        row_dict = {}
                        for i, col_name in enumerate(normalized_headers):
                            if not col_name:
                                continue
                            value = data[i] if i < len(data) else ""
                            row_dict[col_name] = value
                        extracted_rows.append(row_dict)

                    if extracted_rows:
                        # usamos solo la primera tabla con datos
                        break

            if not extracted_rows:
                finished = timezone.now()
                elapsed = (finished - started).total_seconds()
                archivo_carga.finished_at = finished
                archivo_carga.tiempo_procesamiento_seg = elapsed
                archivo_carga.estado_proceso = "error"
                archivo_carga.resumen_proceso = {
                    "total_registros": 0,
                    "nuevos": 0,
                    "actualizados": 0,
                    "rechazados": 0,
                    "detalle": "No se encontraron tablas utilizables en el PDF.",
                }
                archivo_carga.errores_por_fila = []
                archivo_carga.save(
                    update_fields=[
                        "finished_at",
                        "tiempo_procesamiento_seg",
                        "estado_proceso",
                        "resumen_proceso",
                        "errores_por_fila",
                    ]
                )
                return

            rows = extracted_rows

        except Exception as e:
            finished = timezone.now()
            elapsed = (finished - started).total_seconds()
            archivo_carga.finished_at = finished
            archivo_carga.tiempo_procesamiento_seg = elapsed
            archivo_carga.estado_proceso = "error"
            archivo_carga.resumen_proceso = {
                "total_registros": 0,
                "nuevos": 0,
                "actualizados": 0,
                "rechazados": 0,
                "detalle": f"Error al leer PDF: {str(e)}",
            }
            archivo_carga.errores_por_fila = []
            archivo_carga.save(
                update_fields=[
                    "finished_at",
                    "tiempo_procesamiento_seg",
                    "estado_proceso",
                    "resumen_proceso",
                    "errores_por_fila",
                ]
            )
            return

    # 4) Otros formatos siguen siendo no soportados
    else:
        finished = timezone.now()
        elapsed = (finished - started).total_seconds()
        archivo_carga.finished_at = finished
        archivo_carga.tiempo_procesamiento_seg = elapsed
        archivo_carga.estado_proceso = "error"
        archivo_carga.resumen_proceso = {
            "total_registros": 0,
            "nuevos": 0,
            "actualizados": 0,
            "rechazados": 0,
            "detalle": f"Formato no soportado para carga masiva: {ext}",
        }
        archivo_carga.errores_por_fila = []
        archivo_carga.save(
            update_fields=[
                "finished_at",
                "tiempo_procesamiento_seg",
                "estado_proceso",
                "resumen_proceso",
                "errores_por_fila",
            ]
        )
        return

    # Si por alguna razón no hay filas, marcar como ok sin cambios
    if not rows:
        finished = timezone.now()
        elapsed = (finished - started).total_seconds()
        archivo_carga.finished_at = finished
        archivo_carga.tiempo_procesamiento_seg = elapsed
        archivo_carga.estado_proceso = "ok"
        archivo_carga.resumen_proceso = {
            "total_registros": 0,
            "nuevos": 0,
            "actualizados": 0,
            "rechazados": 0,
            "detalle": "Archivo leído correctamente pero no se encontraron filas.",
        }
        archivo_carga.errores_por_fila = []
        archivo_carga.save(
            update_fields=[
                "finished_at",
                "tiempo_procesamiento_seg",
                "estado_proceso",
                "resumen_proceso",
                "errores_por_fila",
            ]
        )
        return

    # Lógica común para CSV, Excel y PDF 
    for idx, row in enumerate(rows, start=2):
        procesados += 1
        try:
            with transaction.atomic():
                pais = None
                pais_codigo = str(row.get("pais") or "").strip()
                if pais_codigo:
                    pais = Pais.objects.filter(codigo_iso3=pais_codigo).first()

                calif, created = CalificacionTributaria.objects.get_or_create(
                    corredor=corredor,
                    identificador_cliente=str(row.get("identificador_cliente") or "").strip(),
                    instrumento=str(row.get("instrumento") or "").strip(),
                    defaults={
                        "moneda": str(row.get("moneda") or "CLP"),
                        "pais": pais,
                    },
                )

                calif.moneda = str(row.get("moneda") or calif.moneda or "CLP")
                calif.pais = pais or calif.pais
                calif.archivo_origen = archivo_carga
                calif.observaciones = row.get("observaciones") or ""

                for n in range(8, 20):
                    field_name = f"factor_{n}"
                    raw = row.get(field_name, "")
                    if raw is None or raw == "":
                        value = Decimal("0")
                    else:
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
