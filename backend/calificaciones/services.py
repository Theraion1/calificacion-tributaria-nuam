import os
import re
from decimal import Decimal

import pandas as pd
import pdfplumber
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import ArchivoCarga, CalificacionTributaria, Pais


class DetectorPaisTributario(object):
    """
    Detección simple de país a partir de los datos de una fila.
    Usa patrones de identificadores (RUT/NIT/RUC) y palabras clave.
    Devuelve un código ISO3 (CHL, COL, PER) y un score de confianza.
    """

    PATRONES = {
        "CHL": {
            "regex": [
                r"\d{1,2}\.\d{3}\.\d{3}-[0-9kK]",  # RUT Chile 12.345.678-9
            ],
            "keywords": ["CHILE", "SANTIAGO", "RUT"],
            "score_regex": 0.7,
            "score_keyword": 0.2,
        },
        "COL": {
            "regex": [
                r"\d{3}\.\d{3}\.\d{3}-\d",  # NIT 123.456.789-0
            ],
            "keywords": ["COLOMBIA", "BOGOTA", "NIT"],
            "score_regex": 0.7,
            "score_keyword": 0.2,
        },
        "PER": {
            "regex": [
                r"2\d{10}",  # RUC Perú, ejemplo simple
            ],
            "keywords": ["PERU", "LIMA", "RUC"],
            "score_regex": 0.7,
            "score_keyword": 0.2,
        },
    }

    def detectar(self, row):
        """
        Recibe una fila (dict) y devuelve (codigo_iso3, score).
        Si no detecta nada, devuelve (None, 0.0).
        """
        textos = []
        for v in row.values():
            if v is None:
                continue
            textos.append(str(v))
        texto = " ".join(textos)
        texto_upper = texto.upper()

        mejor_codigo = None
        mejor_score = 0.0

        for codigo, cfg in self.PATRONES.items():
            score = 0.0

            for patron in cfg.get("regex", []):
                if re.search(patron, texto):
                    score += cfg.get("score_regex", 0.0)

            for kw in cfg.get("keywords", []):
                if kw in texto_upper:
                    score += cfg.get("score_keyword", 0.0)

            if score > mejor_score:
                mejor_score = score
                mejor_codigo = codigo

        return mejor_codigo, mejor_score


def _to_decimal(value):
    """
    Convierte distintos formatos a Decimal.
    Acepta: "", None -> None; "0.1", "0,1", 0.1, 1, etc.
    """
    if value is None:
        return None

    if isinstance(value, Decimal):
        return value

    if isinstance(value, (int, float)):
        return Decimal(str(value))

    s = str(value).strip()
    if s == "":
        return None

    s = s.replace("%", "").strip()
    s = s.replace(",", ".")

    try:
        return Decimal(s)
    except Exception:
        raise ValidationError("Valor decimal inválido: %r" % value)


def _normalizar_header(nombre):
    """
    Normaliza el texto del encabezado a nombres de campo
    que usamos en la carga masiva.
    """
    base = nombre.strip().lower()
    base = re.sub(r"\s+", " ", base)
    base = (
        base.replace("ó", "o")
        .replace("í", "i")
        .replace("á", "a")
        .replace("é", "e")
        .replace("ú", "u")
    )

    if "identificador" in base and "cliente" in base:
        return "identificador_cliente"
    if "instrumento" in base:
        return "instrumento"
    if base.strip() == "pais":
        return "pais"
    if "observacion" in base:
        return "observaciones"

    m = re.search(r"factor[_\s]*(\d+)", base)
    if m:
        return "factor_%s" % m.group(1)

    base = re.sub(r"[^a-z0-9]+", "_", base)
    base = base.strip("_")
    return base


def _normalizar_row_claves(row):
    """
    Toma un dict con claves cualquiera (nombres de columnas originales)
    y devuelve un dict con claves normalizadas usando _normalizar_header.
    """
    nuevo = {}
    for k, v in row.items():
        if k is None:
            continue
        key = _normalizar_header(str(k))
        nuevo[key] = v
    return nuevo


def _parse_pdf_text_to_rows(file_obj):
    """
    Lee el PDF como texto y arma filas en base a líneas con '|'.
    Busca específicamente la fila que contiene 'identificador_cliente'
    para usarla como encabezado real.
    """
    lineas_tabla = []

    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            texto = page.extract_text() or ""
            for raw in texto.splitlines():
                linea = raw.strip()
                if not linea:
                    continue
                if "|" in linea:
                    lineas_tabla.append(linea)

    if not lineas_tabla:
        return []

    idx_header = None
    for idx, linea in enumerate(lineas_tabla):
        if "identificador_cliente" in linea.lower() and "instrumento" in linea.lower():
            idx_header = idx
            break

    if idx_header is None:
        for idx, linea in enumerate(lineas_tabla):
            partes = [p.strip() for p in linea.split("|")]
            if len([p for p in partes if p]) >= 3:
                idx_header = idx
                break

    if idx_header is None:
        return []

    encabezado_bruto = [c.strip() for c in lineas_tabla[idx_header].split("|")]
    headers = [_normalizar_header(c) for c in encabezado_bruto]

    filas = []

    for linea in lineas_tabla[idx_header + 1 :]:
        partes = [p.strip() for p in linea.split("|")]

        if not any(partes):
            continue

        if len(partes) < len(headers):
            partes += [""] * (len(headers) - len(partes))
        elif len(partes) > len(headers):
            partes = partes[: len(headers)]

        fila_dict = dict(zip(headers, partes))

        texto_fila = " ".join(partes).lower()
        if "identificador_cliente" in texto_fila and "instrumento" in texto_fila:
            continue

        if not any(v for v in fila_dict.values()):
            continue

        filas.append(fila_dict)

    return filas


def _resolver_ruta_archivo(archivo_carga):
    """
    Devuelve la ruta física del archivo a partir del modelo.
    Usa primero ruta_almacenamiento, y si no, archivo.path (FileField).
    """
    path = getattr(archivo_carga, "ruta_almacenamiento", None)
    if not path:
        archivo_field = getattr(archivo_carga, "archivo", None)
        if archivo_field:
            try:
                path = archivo_field.path
            except Exception:
                path = None
    return path


def _leer_archivo_a_rows(archivo_carga):
    """
    Abre el archivo físico y lo convierte a una lista de dicts (rows).
    Soporta CSV, XLSX/XLS y PDF (siempre por texto).
    """
    path = _resolver_ruta_archivo(archivo_carga)
    if not path:
        raise FileNotFoundError(
            "ArchivoCarga no tiene ruta_almacenamiento ni archivo.path definido."
        )

    ext = os.path.splitext(path)[1].lower()

    if not os.path.exists(path):
        raise FileNotFoundError(
            "No se encontró el archivo en ruta_almacenamiento: %s" % path
        )

    rows = []

    with open(path, "rb") as file_obj:
        if ext == ".csv":
            df = pd.read_csv(file_obj)
            rows = df.to_dict(orient="records")

        elif ext in (".xls", ".xlsx"):
            df = pd.read_excel(file_obj)
            rows = df.to_dict(orient="records")

        elif ext == ".pdf":
            rows_from_text = _parse_pdf_text_to_rows(file_obj)
            if rows_from_text:
                rows = rows_from_text
            else:
                return []
        else:
            raise ValidationError("Extensión de archivo no soportada: %s" % ext)

    rows_normalizadas = []
    for row in rows:
        rows_normalizadas.append(_normalizar_row_claves(row))

    return rows_normalizadas


@transaction.atomic
def procesar_archivo_carga(archivo_carga):
    """
    Procesa un ArchivoCarga:
      - Lee el archivo físico
      - Interpreta filas
      - Detecta país si no viene explícito
      - Crea/actualiza CalificacionTributaria
      - Actualiza resumen y errores en ArchivoCarga

    Acepta tanto una instancia de ArchivoCarga como un ID (pk).
    """

    if not isinstance(archivo_carga, ArchivoCarga):
        archivo_carga = ArchivoCarga.objects.get(pk=archivo_carga)

    started = timezone.now()
    archivo_carga.started_at = started
    archivo_carga.estado_proceso = "procesando"
    archivo_carga.save(update_fields=["started_at", "estado_proceso"])

    detector = DetectorPaisTributario()

    try:
        rows = _leer_archivo_a_rows(archivo_carga)

        if not rows:
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
                "detalle": "No se encontraron filas utilizables en el archivo.",
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

        total_registros = len(rows)
        nuevos = 0
        actualizados = 0
        rechazados = 0
        errores_por_fila = []

        factor_fields = ["factor_%d" % i for i in range(8, 20)]

        for index, row in enumerate(rows, start=2):
            errores = {}
            data_fila = dict(row)
            fila_numero = index

            identificador_cliente = (row.get("identificador_cliente") or "").strip()
            instrumento = (row.get("instrumento") or "").strip()
            observaciones = (row.get("observaciones") or "").strip()

            if not identificador_cliente:
                errores.setdefault("identificador_cliente", []).append(
                    "This field cannot be blank."
                )
            if not instrumento:
                errores.setdefault("instrumento", []).append(
                    "This field cannot be blank."
                )

            factores = {}
            for fname in factor_fields:
                valor_bruto = row.get(fname, "")
                try:
                    factores[fname] = _to_decimal(valor_bruto)
                except ValidationError as e:
                    errores.setdefault(fname, []).append(str(e))

            pais_obj = None
            pais_valor = (row.get("pais") or "").strip()

            if pais_valor:
                pais_obj = (
                    Pais.objects.filter(codigo_iso3__iexact=pais_valor).first()
                    or Pais.objects.filter(nombre__iexact=pais_valor).first()
                )
                if not pais_obj:
                    errores.setdefault("pais", []).append(
                        "País '%s' no encontrado en tabla Pais." % pais_valor
                    )
            else:
                codigo_iso3, score = detector.detectar(row)
                if codigo_iso3:
                    pais_obj = Pais.objects.filter(
                        codigo_iso3__iexact=codigo_iso3
                    ).first()
                if not pais_obj:
                    errores.setdefault("pais", []).append(
                        "No se pudo determinar el país a partir de los datos."
                    )

            if errores:
                rechazados += 1
                errores_por_fila.append(
                    {
                        "fila": fila_numero,
                        "error": errores,
                        "data": data_fila,
                    }
                )
                continue

            calif, created = CalificacionTributaria.objects.get_or_create(
                corredor=archivo_carga.corredor,
                identificador_cliente=identificador_cliente,
                instrumento=instrumento,
                defaults=dict(
                    pais=pais_obj,
                    observaciones=observaciones,
                    archivo_origen=archivo_carga,
                    **factores,
                ),
            )

            if created:
                nuevos += 1
            else:
                calif.pais = pais_obj
                calif.observaciones = observaciones
                calif.archivo_origen = archivo_carga
                for k, v in factores.items():
                    setattr(calif, k, v)
                calif.save()
                actualizados += 1

        finished = timezone.now()
        elapsed = (finished - started).total_seconds()

        archivo_carga.finished_at = finished
        archivo_carga.tiempo_procesamiento_seg = elapsed
        archivo_carga.estado_proceso = "ok" if rechazados == 0 else "error"
        archivo_carga.resumen_proceso = {
            "total_registros": total_registros,
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
            "detalle": "Error inesperado en procesamiento: %s" % str(e),
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
        raise
