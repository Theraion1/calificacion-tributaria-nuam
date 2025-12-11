import os
import io
import re
from decimal import Decimal
from math import isnan

import pandas as pd
import pdfplumber
from django.core.exceptions import ValidationError
from django.utils import timezone

from .email_utils import send_email_async
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
            "score_keyword": 0.3,
        },
        "COL": {
            "regex": [
                r"\d{5,10}",  # NIT genérico
            ],
            "keywords": ["COLOMBIA", "BOGOTA", "NIT"],
            "score_regex": 0.6,
            "score_keyword": 0.4,
        },
        "PER": {
            "regex": [
                r"\d{11}",  # RUC Perú 11 dígitos
            ],
            "keywords": ["PERU", "LIMA", "RUC"],
            "score_regex": 0.6,
            "score_keyword": 0.4,
        },
    }

    @classmethod
    def detectar_pais(cls, fila_dict):
        texto_concatenado = " ".join(
            [str(v) for v in fila_dict.values() if v is not None]
        ).upper()

        if not texto_concatenado.strip():
            return None, 0.0

        scores = {codigo: 0.0 for codigo in cls.PATRONES.keys()}

        for codigo, reglas in cls.PATRONES.items():
            for patron in reglas["regex"]:
                if re.search(patron, texto_concatenado):
                    scores[codigo] += reglas.get("score_regex", 0.0)

            for kw in reglas["keywords"]:
                if kw in texto_concatenado:
                    scores[codigo] += reglas.get("score_keyword", 0.0)

        mejor_pais = None
        mejor_score = 0.0
        for codigo, sc in scores.items():
            if sc > mejor_score:
                mejor_score = sc
                mejor_pais = codigo

        if mejor_score < 0.3:
            return None, mejor_score

        return mejor_pais, mejor_score


def _parse_pdf_text_to_rows(file_obj):
    """
    Parsea un PDF a una lista de filas, asumiendo que:
    - El PDF contiene texto en formato tabla.
    - Se usa un separador tipo "|" o espacios.
    Este es un parser muy simple, pensado como ejemplo.
    """
    rows = []
    try:
        with pdfplumber.open(file_obj) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                for line in text.split("\n"):
                    if "|" in line:
                        parts = [col.strip() for col in line.split("|")]
                        rows.append(parts)
    except Exception as e:
        raise ValidationError(f"Error al leer PDF: {e}")
    return rows


def _normalizar_header(nombre_columna: str) -> str:
    if not nombre_columna:
        return ""

    nombre = nombre_columna.strip().lower()

    reemplazos = {
        "id_cliente": "identificador_cliente",
        "idcliente": "identificador_cliente",
        "cliente": "identificador_cliente",
        "identificador": "identificador_cliente",
        "rut": "identificador_cliente",
        "nit": "identificador_cliente",
        "ruc": "identificador_cliente",
        "secuencia_eventos": "secuencia_evento",
        "secuenciaeventos": "secuencia_evento",
        "secuencia": "secuencia_evento",
        "instrumento_financiero": "instrumento",
        "cod_instrumento": "instrumento",
        "codigo_instrumento": "instrumento",
        "ejercicio_fiscal": "ejercicio",
        "anio": "ejercicio",
        "año": "ejercicio",
        "anio_ejercicio": "ejercicio",
        "mercado_valor": "mercado",
        "mercado_valores": "mercado",
        "mercado_de_valores": "mercado",
        "estado_calificacion": "estado",
        "estado_ct": "estado",
        "valor_hist": "valor_historico",
        "valor_h": "valor_historico",
        "valorhist": "valor_historico",
        "valor_actualizado": "valor_actualizado",
        "valor_act": "valor_actualizado",
        "valoract": "valor_actualizado",
    }

    if nombre in reemplazos:
        return reemplazos[nombre]

    for i in range(8, 20):
        base = f"factor_{i}"
        patrones = [
            base,
            base.replace("_", ""),
            f"f{i}",
            f"factor{i}",
            f"fac_{i}",
        ]
        if nombre in patrones:
            return base

    return nombre


def _detectar_tipo_archivo_por_extension(nombre_archivo: str):
    """
    Detecta el tipo de contenido (CSV, EXCEL, PDF) según la extensión.
    """
    _, ext = os.path.splitext(nombre_archivo.lower())
    if ext in [".csv"]:
        return "CSV"
    if ext in [".xls", ".xlsx"]:
        return "EXCEL"
    if ext in [".pdf"]:
        return "PDF"
    raise ValidationError(f"Extensión de archivo no soportada: {ext}")


def _cargar_dataframe_desde_archivo(file_obj, nombre_archivo: str, delimiter: str = ","):
    """
    Dado un archivo subido y su nombre, lo transforma en un DataFrame.
    Soporta CSV, EXCEL y PDF (simple).
    """
    tipo = _detectar_tipo_archivo_por_extension(nombre_archivo)
    if tipo == "CSV":
        try:
            df = pd.read_csv(file_obj, delimiter=delimiter)
        except Exception as e:
            raise ValidationError(f"Error al leer CSV: {e}")
        return df
    elif tipo == "EXCEL":
        try:
            df = pd.read_excel(file_obj)
        except Exception as e:
            raise ValidationError(f"Error al leer Excel: {e}")
        return df
    elif tipo == "PDF":
        rows = _parse_pdf_text_to_rows(file_obj)
        if not rows:
            raise ValidationError("No se pudo extraer ninguna fila del PDF.")
        df = pd.DataFrame(rows)
        return df
    else:
        raise ValidationError("Tipo de archivo no soportado.")


def _detectar_pais_y_crear_si_falta(codigo_iso3: str):
    """
    Dado un código ISO3, obtiene el País o lo crea si no existe.
    """
    if not codigo_iso3:
        return None
    codigo_iso3 = codigo_iso3.upper()
    pais, _ = Pais.objects.get_or_create(
        codigo_iso3=codigo_iso3,
        defaults={
            "nombre": codigo_iso3,
        },
    )
    return pais


def _normalizar_valor_decimal(valor):
    """
    Recibe un valor que puede venir como string con separadores,
    NaN de pandas, etc., y devuelve Decimal o None.
    """
    if valor is None:
        return None

    if isinstance(valor, float):
        if isnan(valor):
            return None
        return Decimal(str(valor))

    if isinstance(valor, int):
        return Decimal(valor)

    s = str(valor).strip()
    if not s:
        return None

    s = s.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except Exception:
        return None


def _extraer_valor(df_row, col_name, default=None):
    """
    Extrae un valor de la fila (Series) y devuelve default si no existe.
    """
    if col_name in df_row.index:
        return df_row[col_name]
    return default


def _normalizar_headers_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza los nombres de columnas a un formato fijo.
    """
    df = df.copy()
    new_columns = {}
    for col in df.columns:
        new_columns[col] = _normalizar_header(col)
    df.rename(columns=new_columns, inplace=True)
    return df


def _calcular_factor_actualizacion_desde_montos(factores_dict):
    """
    Si los valores en factores_dict se interpretan como montos, este helper
    calcula el "factor" normalizado:
      - Si la suma total es 0, devuelve 0.
      - Si son montos crudos, se puede dividir cada valor por la suma para
        obtener proporciones, pero aquí solo devolvemos la suma.
    En este servicio, usaremos esta función para decidir si se trata de
    Montos o Factores.
    """
    total = Decimal("0")
    for v in factores_dict.values():
        if v is not None:
            total += v
    return total


def _es_modo_monto(factores_dict):
    """
    Determina si los valores parecen ser montos en lugar de factores.
    Heurísticas:
      - Si hay algún valor > 1, sospechamos que son montos (por ejemplo 100, 200).
      - Si la suma total es > 1.0001, también sospechamos montos.
    """
    total = Decimal("0")
    for v in factores_dict.values():
        if v is None:
            continue
        total += v
        if v > Decimal("1"):
            return True
    if total > Decimal("1.0001"):
        return True
    return False


def _normalizar_factores_a_1(factores_dict):
    """
    Dado un dict de factores en versión "monto" (o mezcla),
    genera un nuevo dict donde:
      - Se suman los valores totales.
      - Cada valor se divide por la suma total para dejar la suma en 1.0.
      - Si total es 0, se devuelven todos 0.
    """
    total = Decimal("0")
    for v in factores_dict.values():
        if v is not None:
            total += v

    if total == 0:
        return {k: Decimal("0") for k in factores_dict.keys()}

    normalizados = {}
    for k, v in factores_dict.items():
        if v is None:
            normalizados[k] = Decimal("0")
        else:
            normalizados[k] = v / total
    return normalizados


def _obtener_o_crear_calificacion_from_row(row_dict, corredor, archivo_carga):
    """
    Dado un dict con datos normalizados (keys del modelo),
    busca si existe una CalificacionTributaria con identificador_cliente,
    instrumento, ejercicio, mercado y secuencia_evento. Si existe, la
    actualiza; si no, la crea.
    """
    identificador_cliente = row_dict.get("identificador_cliente")
    instrumento = row_dict.get("instrumento")
    ejercicio = row_dict.get("ejercicio")
    mercado = row_dict.get("mercado")
    secuencia_evento = row_dict.get("secuencia_evento")

    if not identificador_cliente or not instrumento:
        raise ValidationError(
            "Faltan datos obligatorios: identificador_cliente o instrumento."
        )

    if not ejercicio:
        ejercicio = str(timezone.now().year)

    defaults = {
        "corredor": corredor,
        "archivo_carga": archivo_carga,
        "mercado": mercado,
        "ejercicio": ejercicio,
        "secuencia_evento": secuencia_evento,
        "pais": row_dict.get("pais"),
        "estado": row_dict.get("estado") or "VIGENTE",
        "valor_historico": row_dict.get("valor_historico") or Decimal("0"),
        "valor_actualizado": row_dict.get("valor_actualizado") or Decimal("0"),
        "factor_actualizacion": row_dict.get("factor_actualizacion") or Decimal("0"),
    }

    for i in range(8, 20):
        key = f"factor_{i}"
        defaults[key] = row_dict.get(key) or Decimal("0")

    obj, created = CalificacionTributaria.objects.update_or_create(
        corredor=corredor,
        identificador_cliente=identificador_cliente,
        instrumento=instrumento,
        ejercicio=ejercicio,
        mercado=mercado,
        secuencia_evento=secuencia_evento,
        defaults=defaults,
    )

    return obj, created


def procesar_archivo_carga_factores(
    archivo_carga: ArchivoCarga, file_obj, corredor, delimiter: str = ","
):
    """
    Procesa una carga de tipo FACTOR a partir de un archivo.
    """
    started = timezone.now()
    archivo_carga.started_at = started
    archivo_carga.estado_proceso = "procesando"
    archivo_carga.save(update_fields=["started_at", "estado_proceso"])

    try:
        df = _cargar_dataframe_desde_archivo(
            file_obj, archivo_carga.nombre_original, delimiter=delimiter
        )
        df = _normalizar_headers_dataframe(df)
    except ValidationError as e:
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
            "detalle": str(e),
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
        notificar_resultado_archivo(archivo_carga)
        return

    total_registros = 0
    nuevos = 0
    actualizados = 0
    rechazados = 0
    errores_por_fila = []

    for idx, row in df.iterrows():
        total_registros += 1

        try:
            row_dict = {}

            row_dict["identificador_cliente"] = str(
                _extraer_valor(row, "identificador_cliente", "")
            ).strip()
            row_dict["instrumento"] = str(
                _extraer_valor(row, "instrumento", "")
            ).strip()
            row_dict["mercado"] = str(_extraer_valor(row, "mercado", "")).strip()
            row_dict["ejercicio"] = str(_extraer_valor(row, "ejercicio", "")).strip()
            row_dict["secuencia_evento"] = str(
                _extraer_valor(row, "secuencia_evento", "")
            ).strip()

            for k, v in row.items():
                if "pais" in str(k).lower():
                    valor_pais = str(v).strip().upper()
                    if valor_pais in ["CHL", "COL", "PER"]:
                        row_dict["pais"] = _detectar_pais_y_crear_si_falta(valor_pais)
                    else:
                        row_dict["pais"] = None
                    break

            if "pais" not in row_dict:
                fila_dict_simple = {
                    "identificador_cliente": row_dict["identificador_cliente"],
                    "instrumento": row_dict["instrumento"],
                }
                pais_code, _score = DetectorPaisTributario.detectar_pais(fila_dict_simple)
                if pais_code:
                    row_dict["pais"] = _detectar_pais_y_crear_si_falta(pais_code)
                else:
                    row_dict["pais"] = None

            for i in range(8, 20):
                col = f"factor_{i}"
                raw_val = _extraer_valor(row, col, None)
                row_dict[col] = _normalizar_valor_decimal(raw_val)

            factores = {f"factor_{i}": row_dict.get(f"factor_{i}") for i in range(8, 20)}
            if _es_modo_monto(factores):
                factores_norm = _normalizar_factores_a_1(factores)
                row_dict.update(factores_norm)
                row_dict["factor_actualizacion"] = Decimal("1")
            else:
                suma_factores = Decimal("0")
                for val in factores.values():
                    if val is not None:
                        suma_factores += val
                row_dict["factor_actualizacion"] = suma_factores

            obj, created = _obtener_o_crear_calificacion_from_row(
                row_dict, corredor, archivo_carga
            )
            if created:
                nuevos += 1
            else:
                actualizados += 1

        except Exception as e:
            rechazados += 1
            errores_por_fila.append(
                {"fila": int(idx) + 1, "error": str(e), "datos": row.to_dict()}
            )

    finished = timezone.now()
    elapsed = (finished - started).total_seconds()
    archivo_carga.finished_at = finished
    archivo_carga.tiempo_procesamiento_seg = elapsed
    archivo_carga.estado_proceso = "ok"
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
    notificar_resultado_archivo(archivo_carga)


def procesar_archivo_carga_monto(
    archivo_carga: ArchivoCarga, file_obj, corredor, delimiter: str = ","
):
    """
    Procesa una carga de tipo MONTO, interpretando las columnas factor_8..factor_19
    como montos que deben normalizarse para sumar 1.
    """
    started = timezone.now()
    archivo_carga.started_at = started
    archivo_carga.estado_proceso = "procesando"
    archivo_carga.save(update_fields=["started_at", "estado_proceso"])

    try:
        df = _cargar_dataframe_desde_archivo(
            file_obj, archivo_carga.nombre_original, delimiter=delimiter
        )
        df = _normalizar_headers_dataframe(df)
    except ValidationError as e:
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
            "detalle": str(e),
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
        notificar_resultado_archivo(archivo_carga)
        return

    total_registros = 0
    nuevos = 0
    actualizados = 0
    rechazados = 0
    errores_por_fila = []

    for idx, row in df.iterrows():
        total_registros += 1

        try:
            row_dict = {}

            row_dict["identificador_cliente"] = str(
                _extraer_valor(row, "identificador_cliente", "")
            ).strip()
            row_dict["instrumento"] = str(
                _extraer_valor(row, "instrumento", "")
            ).strip()
            row_dict["mercado"] = str(_extraer_valor(row, "mercado", "")).strip()
            row_dict["ejercicio"] = str(_extraer_valor(row, "ejercicio", "")).strip()
            row_dict["secuencia_evento"] = str(
                _extraer_valor(row, "secuencia_evento", "")
            ).strip()

            for k, v in row.items():
                if "pais" in str(k).lower():
                    valor_pais = str(v).strip().upper()
                    if valor_pais in ["CHL", "COL", "PER"]:
                        row_dict["pais"] = _detectar_pais_y_crear_si_falta(valor_pais)
                    else:
                        row_dict["pais"] = None
                    break

            if "pais" not in row_dict:
                fila_dict_simple = {
                    "identificador_cliente": row_dict["identificador_cliente"],
                    "instrumento": row_dict["instrumento"],
                }
                pais_code, _score = DetectorPaisTributario.detectar_pais(fila_dict_simple)
                if pais_code:
                    row_dict["pais"] = _detectar_pais_y_crear_si_falta(pais_code)
                else:
                    row_dict["pais"] = None

            for i in range(8, 20):
                col = f"factor_{i}"
                raw_val = _extraer_valor(row, col, None)
                row_dict[col] = _normalizar_valor_decimal(raw_val)

            factores = {f"factor_{i}": row_dict.get(f"factor_{i}") for i in range(8, 20)}

            if not _es_modo_monto(factores):
                raise ValidationError(
                    "Los valores de factor_8..factor_19 no parecen montos (suman <= 1)."
                )

            factores_norm = _normalizar_factores_a_1(factores)
            row_dict.update(factores_norm)
            row_dict["factor_actualizacion"] = Decimal("1")

            obj, created = _obtener_o_crear_calificacion_from_row(
                row_dict, corredor, archivo_carga
            )
            if created:
                nuevos += 1
            else:
                actualizados += 1

        except Exception as e:
            rechazados += 1
            errores_por_fila.append(
                {"fila": int(idx) + 1, "error": str(e), "datos": row.to_dict()}
            )

    finished = timezone.now()
    elapsed = (finished - started).total_seconds()
    archivo_carga.finished_at = finished
    archivo_carga.tiempo_procesamiento_seg = elapsed
    archivo_carga.estado_proceso = "ok"
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
    notificar_resultado_archivo(archivo_carga)


def procesar_archivo_carga(archivo_carga: ArchivoCarga, file_obj, corredor, delimiter=","):
    """
    Router principal que decide si procesa como FACTOR o como MONTO
    en base al campo tipo_carga.
    """
    tipo = archivo_carga.tipo_carga.upper()
    if tipo == "FACTOR":
        return procesar_archivo_carga_factores(
            archivo_carga, file_obj, corredor, delimiter=delimiter
        )
    elif tipo == "MONTO":
        return procesar_archivo_carga_monto(
            archivo_carga, file_obj, corredor, delimiter=delimiter
        )
    else:
        raise ValidationError(f"Tipo de carga no soportado: {archivo_carga.tipo_carga}")


def notificar_resultado_archivo(archivo_carga: ArchivoCarga):
    """
    Envía un correo electrónico con un resumen de la carga.
    """
    corredor = archivo_carga.corredor
    if not corredor or not corredor.email_contacto:
        return

    subject = f"Resultado de carga masiva ID {archivo_carga.id}"
    resumen = archivo_carga.resumen_proceso or {}
    errores = archivo_carga.errores_por_fila or []

    body_lines = [
        f"Estimado/a {corredor.nombre},",
        "",
        "Se ha procesado su archivo de carga masiva.",
        "",
        f"ID de la carga: {archivo_carga.id}",
        f"Nombre original: {archivo_carga.nombre_original}",
        f"Estado: {archivo_carga.estado_proceso}",
        "",
        "Resumen:",
        f"  - Total registros: {resumen.get('total_registros', 0)}",
        f"  - Nuevos: {resumen.get('nuevos', 0)}",
        f"  - Actualizados: {resumen.get('actualizados', 0)}",
        f"  - Rechazados: {resumen.get('rechazados', 0)}",
    ]

    if errores:
        body_lines.append("")
        body_lines.append("Algunos errores detectados (primeros 10):")
        for err in errores[:10]:
            body_lines.append(
                f"  - Fila {err.get('fila')}: {err.get('error')}"
            )

    body_lines.append("")
    body_lines.append("Saludos cordiales.")
    body = "\n".join(body_lines)

    send_email_async(
        subject=subject,
        body=body,
        to=[corredor.email_contacto],
    )


# =====================================================================
# CONVERSION DE ARCHIVOS (Excel <-> CSV, PDF -> Excel)
# =====================================================================

def _leer_archivo_a_dataframe_generico(file_obj, filename: str, delimiter: str = ","):
    """Lee un archivo subido (CSV, XLSX/XLS, PDF) y lo convierte en un DataFrame."""
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".csv":
        df = pd.read_csv(file_obj, delimiter=delimiter)

    elif ext in (".xls", ".xlsx"):
        df = pd.read_excel(file_obj)

    elif ext == ".pdf":
        with pdfplumber.open(file_obj) as pdf:
            rows = []
            for page in pdf.pages:
                text = page.extract_text() or ""
                for line in text.split("\n"):
                    if "|" in line:
                        parts = [col.strip() for col in line.split("|")]
                        rows.append(parts)
        if not rows:
            raise ValidationError("No se pudo extraer una tabla válida desde el PDF.")
        df = pd.DataFrame(rows)

    else:
        raise ValidationError(
            f"Extensión de archivo no soportada para conversión: {ext}"
        )

    return df


def convertir_archivo_generico(file_obj, filename: str, formato_destino: str, delimiter: str = ","):
    """Convierte un archivo a otro formato soportado."""
    formato = (formato_destino or "").upper().strip()
    if formato not in {"EXCEL_TO_CSV", "CSV_TO_EXCEL", "PDF_TO_EXCEL"}:
        raise ValidationError("Formato de conversión no soportado.")

    df = _leer_archivo_a_dataframe_generico(file_obj, filename, delimiter=delimiter)

    buffer = io.BytesIO()

    if formato == "EXCEL_TO_CSV":
        df.to_csv(buffer, index=False, sep=delimiter)
        mimetype = "text/csv"
        base_name, _ = os.path.splitext(filename)
        out_name = f"{base_name}_convertido.csv"

    else:
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        base_name, _ = os.path.splitext(filename)
        out_name = f"{base_name}_convertido.xlsx"

    buffer.seek(0)
    return buffer, out_name, mimetype


def generar_vista_previa_archivo(file_obj, filename: str, delimiter: str = ",", max_filas: int = 50):
    """Devuelve una vista previa (JSON) con columnas y primeras filas."""
    df = _leer_archivo_a_dataframe_generico(file_obj, filename, delimiter=delimiter)
    df_preview = df.head(max_filas).fillna("")

    return {
        "columns": list(df_preview.columns),
        "rows": df_preview.to_dict(orient="records"),
        "total_rows": int(df.shape[0]),
    }
