import os
import re
from decimal import Decimal

import pandas as pd
import pdfplumber
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import ArchivoCarga, CalificacionTributaria, Pais


class DetectorPaisTributario:
    """
    Detección simple de país a partir de los datos de una fila.
    Usa patrones de identificadores (RUT/NIT/RUC) y palabras clave.
    Devuelve un código ISO3 (CHL, COL, PER) y un score de confianza.
    """

    PATRONES = {
        "CHL": {
            "regex": [
                r"\d{1,2}\.\d{3}\.\d{3}-[0-9kK]",   # RUT Chile 12.345.678-9
            ],
            "keywords": ["CHILE", "SANTIAGO", "RUT"],
            "score_regex": 0.7,
            "score_keyword": 0.2,
        },
        "COL": {
            "regex": [
                r"\d{3}\.\d{3}\.\d{3}-\d",          # NIT Colombia 123.456.789-0
            ],
            "keywords": ["COLOMBIA", "BOGOTA", "NIT"],
            "score_regex": 0.7,
            "score_keyword": 0.2,
        },
        "PER": {
            "regex": [
                r"2\d{10}",                         # RUC Perú: 11 dígitos empezando con 2
            ],
            "keywords": ["PERU", "LIMA", "RUC"],
            "score_regex": 0.7,
            "score_keyword": 0.2,
        },
    }

    @classmethod
    def _armar_texto(cls, row: dict) -> str:
        """
        Construye un texto base concatenando campos relevantes de la fila.
        Así el detector puede buscar patrones dentro de ese texto.
        """
        partes = []
        for key in (
            "identificador_cliente",
            "instrumento",
            "observaciones",
            "pais",   # por si viene como texto
            "PAIS",
        ):
            val = row.get(key)
            if val is not None:
                partes.append(str(val))
        return " ".join(partes)

    @classmethod
    def detectar_desde_row(cls, row: dict):
        """
        A partir de una fila (dict), intenta detectar el país.
        Devuelve:
        - codigo_iso3 (str o None, ej: 'CHL', 'COL', 'PER')
        - confianza (float 0.0–1.0)
        - detalle_scores (dict con score por país)
        """
        texto = cls._armar_texto(row)
        if not texto:
            return None, 0.0, {}

        texto_upper = texto.upper()
        resultados = {}

        for iso3, reglas in cls.PATRONES.items():
            score = 0.0

            # 1) Regex (RUT/NIT/RUC)
            for patron in reglas["regex"]:
                if re.search(patron, texto):
                    score += reglas["score_regex"]

            # 2) Palabras clave
            for kw in reglas["keywords"]:
                if kw in texto_upper:
                    score += reglas["score_keyword"]

            resultados[iso3] = round(score, 2)

        if not resultados:
            return None, 0.0, {}

        iso3_detectado = max(resultados, key=resultados.get)
        confianza = resultados[iso3_detectado]

        if confianza == 0:
            return None, 0.0, resultados

        return iso3_detectado, confianza, resultados


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
                # 1) País manual desde la columna 'pais' si viene
                pais = None
                pais_codigo = str(row.get("pais") or "").strip()
                if pais_codigo:
                    pais = Pais.objects.filter(codigo_iso3=pais_codigo).first()

                # 2) Detección automática de país desde la fila
                iso3_detectado, confianza, _detalle = DetectorPaisTributario.detectar_desde_row(row)
                pais_detectado = None
                if iso3_detectado:
                    pais_detectado = Pais.objects.filter(
                        codigo_iso3__iexact=iso3_detectado
                    ).first()

                # 3) Crear o recuperar la calificación
                calif, created = CalificacionTributaria.objects.get_or_create(
                    corredor=corredor,
                    identificador_cliente=str(row.get("identificador_cliente") or "").strip(),
                    instrumento=str(row.get("instrumento") or "").strip(),
                    defaults={
                        "moneda": str(row.get("moneda") or "CLP"),
                        # Prioridad: país del archivo; si no hay, país detectado.
                        "pais": pais or pais_detectado,
                        "pais_detectado": pais_detectado,
                    },
                )

                # 4) Actualizar campos base
                calif.moneda = str(row.get("moneda") or calif.moneda or "CLP")
                calif.pais = pais or pais_detectado or calif.pais
                calif.pais_detectado = pais_detectado or calif.pais_detectado
                calif.archivo_origen = archivo_carga
                calif.observaciones = row.get("observaciones") or ""

                # 5) Factores 8–19
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
