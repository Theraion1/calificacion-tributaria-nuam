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
            "keywords": ["COLOMBIA", "COLOMBIA", "BOGOTA", "NIT"],
            "score_regex": 0.7,
            "score_keyword": 0.2,
        },
        "PER": {
            "regex": [
                r"2\d{10}",  # RUC Perú: 11 dígitos y suele empezar con 10, 20, 2x...
            ],
            "keywords": ["PERU", "LIMA", "RUC"],
            "score_regex": 0.7,
            "score_keyword": 0.2,
        },
    }

    def detectar(self, row: dict) -> tuple[str | None, float]:
        """
        Recibe una fila (dict) y devuelve (codigo_iso3, score).
        Si no detecta nada, devuelve (None, 0.0).
        """
        # Unimos todos los valores de la fila como texto grande
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

            # regex
            for patron in cfg.get("regex", []):
                if re.search(patron, texto):
                    score += cfg.get("score_regex", 0.0)

            # keywords
            for kw in cfg.get("keywords", []):
                if kw in texto_upper:
                    score += cfg.get("score_keyword", 0.0)

            if score > mejor_score:
                mejor_score = score
                mejor_codigo = codigo

        return mejor_codigo, mejor_score


def _to_decimal(value) -> Decimal | None:
    """
    Convierte distintos formatos a Decimal.
    Acepta:
      - "", None -> None
      - "0.1", "0,1", "0,10", 0.1, 1, etc.
    Lanza ValidationError si no se puede interpretar.
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

    # permitir porcentajes tipo "0,1%" -> "0,1"
    s = s.replace("%", "").strip()
    # cambiar coma por punto
    s = s.replace(",", ".")

    try:
        return Decimal(s)
    except Exception:
        raise ValidationError(f"Valor decimal inválido: {value!r}")


def _normalizar_header(nombre: str) -> str:
    """
    Normaliza el texto del encabezado a nombres de campo
    que usamos en la carga masiva.
    """
    base = nombre.strip().lower()

    # Limpieza básica
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

    # factores: "factor_8", "factor 8", etc.
    m = re.search(r"factor[_\s]*(\d+)", base)
    if m:
        return f"factor_{m.group(1)}"

    # fallback: versión con underscore
    base = re.sub(r"[^a-z0-9]+", "_", base)
    base = base.strip("_")
    return base


def _normalizar_row_claves(row: dict) -> dict:
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


def _parse_pdf_text_to_rows(file_obj) -> list[dict]:
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
                # Nos interesan sólo líneas estilo tabla con pipes
                if "|" in linea:
                    lineas_tabla.append(linea)

    if not lineas_tabla:
        return []

    # 1) Buscar la línea de encabezados REAL (la que contiene identificador_cliente)
    idx_header = None
    for idx, linea in enumerate(lineas_tabla):
        if "identificador_cliente" in linea.lower() and "instrumento" in linea.lower():
            idx_header = idx
            break

    # Si no encontramos ese encabezado, usamos el primero que tenga varias columnas
    if idx_header is None:
        for idx, linea in enumerate(lineas_tabla):
            partes = [p.strip() for p in linea.split("|")]
            if len([p for p in partes if p]) >= 3:
                idx_header = idx
                break

    if idx_header is None:
        # No hay nada útil
        return []

    # 2) Procesar encabezados
    encabezado_bruto = [c.strip() for c in lineas_tabla[idx_header].split("|")]
    headers = [_normalizar_header(c) for c in encabezado_bruto]

    filas = []

    # 3) Procesar el resto de líneas como datos
    for linea in lineas_tabla[idx_header + 1 :]:
        partes = [p.strip() for p in linea.split("|")]

        # Saltar líneas vacías o basura
        if not any(partes):
            continue

        # Ajustar cantidad de columnas vs headers
        if len(partes) < len(headers):
            partes += [""] * (len(headers) - len(partes))
        elif len(partes) > len(headers):
            partes = partes[: len(headers)]

        fila_dict = dict(zip(headers, partes))

        # Evitar colar otra fila de encabezado repetida
        texto_fila = " ".join(partes).lower()
        if "identificador_cliente" in texto_fila and "instrumento" in texto_fila:
            continue

        # Si TODO está vacío, no la consideramos
        if not any(v for v in fila_dict.values()):
            continue

        filas.append(fila_dict)

    return filas


def _leer_archivo_a_rows(archivo_carga: ArchivoCarga) -> list[dict]:
    """
    Abre el archivo físico y lo convierte a una lista de dicts (rows).
    Soporta CSV, XLSX/XLS y PDF (siempre por texto).
    """
    path = archivo_carga.ruta_almacenamiento
    ext = os.path.splitext(path)[1].lower()

    if not os.path.exists(path):
        raise FileNotFoundError(f"No se encontró el archivo en ruta_almacenamiento: {path}")

    rows: list[dict] = []

    with open(path, "rb") as file_obj:
        if ext == ".csv":
            df = pd.read_csv(file_obj)
            rows = df.to_dict(orient="records")

        elif ext in (".xls", ".xlsx"):
            df = pd.read_excel(file_obj)
            rows = df.to_dict(orient="records")

        # 3) PDF: usar siempre fallback por texto
        elif ext == ".pdf":
            rows_from_text = _parse_pdf_text_to_rows(file_obj)
            if rows_from_text:
                rows = rows_from_text
            else:
                # devolvemos vacío y que lo maneje el caller
                return []

        else:
            raise ValidationError(f"Extensión de archivo no soportada: {ext}")

    # Normalizar claves de todas las filas
    rows_normalizadas = []
    for row in rows:
        rows_normalizadas.append(_normalizar_row_claves(row))

    return rows_normalizadas


@transaction.atomic
def procesar_archivo_carga(archivo_carga: ArchivoCarga) -> None:
    """
    Procesa un ArchivoCarga:
      - Lee el archivo físico
      - Interpreta filas
      - Detecta país si no viene explícito
      - Crea/actualiza CalificacionTributaria
      - Actualiza resumen y errores en ArchivoCarga
    """
    started = timezone.now()
    archivo_carga.started_at = started
    archivo_carga.estado_proceso = "procesando"
    archivo_carga.save(update_fields=["started_at", "estado_proceso"])

    detector = DetectorPaisTributario()

    try:
        rows = _leer_archivo_a_rows(archivo_carga)
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
            "detalle": f"Error al leer archivo: {str(e)}",
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
    errores_por_fila: list[dict] = []

    factor_fields = [f"factor_{i}" for i in range(8, 20)]

    for index, row in enumerate(rows, start=2):  # asumimos encabezado en fila 1
        errores = {}
        data_fila = dict(row)  # copiamos para log
        fila_numero = index

        identificador_cliente = (row.get("identificador_cliente") or "").strip()
        instrumento = (row.get("instrumento") or "").strip()
        observaciones = (row.get("observaciones") or "").strip()

        if not identificador_cliente:
            errores.setdefault("identificador_cliente", []).append("This field cannot be blank.")
        if not instrumento:
            errores.setdefault("instrumento", []).append("This field cannot be blank.")

        # Parseo de factores
        factores = {}
        for fname in factor_fields:
            valor_bruto = row.get(fname, "")
            try:
                factores[fname] = _to_decimal(valor_bruto)
            except ValidationError as e:
                errores.setdefault(fname, []).append(str(e))

        # Resolución de país
        pais_obj = None
        pais_valor = (row.get("pais") or "").strip()

        if pais_valor:
            # Buscar por código ISO3 o nombre
            pais_obj = (
                Pais.objects.filter(codigo_iso3__iexact=pais_valor).first()
                or Pais.objects.filter(nombre__iexact=pais_valor).first()
            )
            if not pais_obj:
                errores.setdefault("pais", []).append(
                    f"País '{pais_valor}' no encontrado en tabla Pais."
                )
        else:
            codigo_iso3, score = detector.detectar(row)
            if codigo_iso3:
                pais_obj = Pais.objects.filter(codigo_iso3__iexact=codigo_iso3).first()
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

        # Crear / actualizar CalificacionTributaria
        # Clave lógica: corredor + identificador_cliente + instrumento
        calif, created = CalificacionTributaria.objects.get_or_create(
            corredor=archivo_carga.corredor,
            identificador_cliente=identificador_cliente,
            instrumento=instrumento,
            defaults={
                "pais": pais_obj,
                "observaciones": observaciones,
                **factores,
            },
        )

        if created:
            nuevos += 1
        else:
            calif.pais = pais_obj
            calif.observaciones = observaciones
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
