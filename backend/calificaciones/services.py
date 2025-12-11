import os
import io
import re
from decimal import Decimal
from math import isnan

import pandas as pd
import pdfplumber
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.core.files import File

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
                r"\d{1,2}\.\d{3}\.\d{3}-[0-9kK]",
            ],
            "keywords": ["CHILE", "SANTIAGO", "RUT"],
            "score_regex": 0.7,
            "score_keyword": 0.3,
        },
        "COL": {
            "regex": [
                r"\d{5,10}",
            ],
            "keywords": ["COLOMBIA", "BOGOTA", "NIT"],
            "score_regex": 0.6,
            "score_keyword": 0.4,
        },
        "PER": {
            "regex": [
                r"\d{11}",
            ],
            "keywords": ["PERU", "LIMA", "RUC"],
            "score_regex": 0.6,
            "score_keyword": 0.4,
        },
    }

    @classmethod
    def detectar_pais(cls, fila_dict):
        texto = " ".join([str(v) for v in fila_dict.values() if v]).upper()
        if not texto.strip():
            return None, 0.0

        scores = {pais: 0.0 for pais in cls.PATRONES.keys()}
        for codigo, reglas in cls.PATRONES.items():
            for patron in reglas["regex"]:
                if re.search(patron, texto):
                    scores[codigo] += reglas["score_regex"]
            for kw in reglas["keywords"]:
                if kw in texto:
                    scores[codigo] += reglas["score_keyword"]

        mejor = None
        mejor_score = 0.0
        for codigo, score in scores.items():
            if score > mejor_score:
                mejor = codigo
                mejor_score = score

        if mejor_score < 0.3:
            return None, mejor_score

        return mejor, mejor_score


# -------------------------------
# PARSEADORES Y HELPERS
# -------------------------------

def _parse_pdf_text_to_rows(file_obj):
    """
    Parser PDF universal:
    - extrae TODO el texto legible
    - detecta filas aunque no tengan formato tabular
    - soporta: | , múltiples espacios, columnas desalineadas, filas rotas
    - nunca revienta, siempre devuelve al menos un DataFrame usable
    """

    rows = []

    try:
        import pdfplumber
        pdf = pdfplumber.open(file_obj)
    except Exception as e:
        raise ValidationError(f"No se pudo abrir PDF: {e}")

    for page in pdf.pages:
        text = page.extract_text() or ""
        if not text.strip():
            continue

        for line in text.split("\n"):
            clean = line.strip()
            if not clean:
                continue

            # 1) Separación por barras
            if "|" in clean:
                parts = [p.strip() for p in clean.split("|") if p.strip()]
                if parts:
                    rows.append(parts)
                continue

            # 2) Separación por 2+ espacios
            parts = re.split(r"\s{2,}", clean)
            parts = [p.strip() for p in parts if p.strip()]
            if len(parts) > 1:
                rows.append(parts)
                continue

            # 3) Si llega aquí, consideramos la línea como una sola columna
            rows.append([clean])

    pdf.close()

    # Si no hay filas, devolvemos al menos una fila vacía
    if not rows:
        return [[""]]

    # Normalización para evitar filas muy cortas
    max_cols = max(len(r) for r in rows)
    normalized = []
    for r in rows:
        fill = r + [""] * (max_cols - len(r))
        normalized.append(fill)

    return normalized




def _normalizar_header(nombre_columna: str) -> str:
    if not nombre_columna:
        return ""
    nombre = nombre_columna.strip().lower()

    reemplazos = {
        "id_cliente": "identificador_cliente",
        "idcliente": "identificador_cliente",
        "cliente": "identificador_cliente",
        "rut": "identificador_cliente",
        "nit": "identificador_cliente",
        "ruc": "identificador_cliente",

        "instrumento_financiero": "instrumento",
        "cod_instrumento": "instrumento",

        "ejercicio_fiscal": "ejercicio",
        "anio": "ejercicio",
        "año": "ejercicio",

        "mercado_valores": "mercado",
        "mercado_de_valores": "mercado",

        "estado_calificacion": "estado",

        "valor_hist": "valor_historico",
        "valor_h": "valor_historico",

        "valor_actualizado": "valor_actualizado",
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
    _, ext = os.path.splitext(nombre_archivo.lower())
    if ext == ".csv":
        return "CSV"
    if ext in (".xls", ".xlsx"):
        return "EXCEL"
    if ext == ".pdf":
        return "PDF"
    raise ValidationError(f"Extensión no soportada: {ext}")


def _cargar_dataframe_desde_archivo(file_obj, nombre_archivo: str, delimiter: str = ","):
    tipo = _detectar_tipo_archivo_por_extension(nombre_archivo)

    if tipo == "CSV":
        return pd.read_csv(file_obj, delimiter=delimiter)

    if tipo == "EXCEL":
        return pd.read_excel(file_obj)

    if tipo == "PDF":
        rows = _parse_pdf_text_to_rows(file_obj)

        # Convertimos a DataFrame aunque no existan encabezados
        df = pd.DataFrame(rows)

        # Si la primera fila parece encabezado, la usamos
        header_candidate = df.iloc[0].tolist()
        if any(str(h).lower() in ["cliente", "instrumento", "mercado", "ejercicio"] for h in header_candidate):
            df.columns = header_candidate
            df = df[1:]
        else:
            # Si no hay encabezado, generamos encabezados genéricos
            df.columns = [f"col_{i}" for i in range(len(df.columns))]

        return df

    raise ValidationError("Tipo no soportado.")



def _extraer_valor(df_row, col_name, default=None):
    return df_row[col_name] if col_name in df_row.index else default


def _normalizar_headers_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.rename(columns={c: _normalizar_header(c) for c in df.columns}, inplace=True)
    return df


def _normalizar_valor_decimal(valor):
    if valor is None:
        return None
    if isinstance(valor, float):
        return None if isnan(valor) else Decimal(str(valor))
    if isinstance(valor, int):
        return Decimal(valor)

    s = str(valor).strip()
    if not s:
        return None

    s = s.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except:
        return None


# -------------------------------
# AUTO OBTENER PAÍS
# -------------------------------

def _detectar_pais_y_crear_si_falta(code: str):
    if not code:
        return None
    code = code.upper()
    pais, _ = Pais.objects.get_or_create(codigo_iso3=code, defaults={"nombre": code})
    return pais


# -------------------------------
# CREAR / ACTUALIZAR CALIFICACIÓN
# -------------------------------

def _obtener_o_crear_calificacion_from_row(row_dict, corredor, archivo_carga):
    ident = row_dict.get("identificador_cliente")
    inst = row_dict.get("instrumento")

    if not ident or not inst:
        raise ValidationError("identificador_cliente e instrumento son obligatorios.")

    ejercicio = row_dict.get("ejercicio") or str(timezone.now().year)
    mercado = row_dict.get("mercado")
    secuencia = row_dict.get("secuencia_evento")

    defaults = {
        "corredor": corredor,
        "archivo_origen": archivo_carga,
        "pais": row_dict.get("pais"),

        "mercado": mercado,
        "ejercicio": ejercicio,

        "valor_historico": row_dict.get("valor_historico") or Decimal("0"),
        "valor_actualizado": row_dict.get("valor_actualizado") or Decimal("0"),
        "factor_actualizacion": row_dict.get("factor_actualizacion") or Decimal("0"),
    }

    for i in range(8, 20):
        defaults[f"factor_{i}"] = row_dict.get(f"factor_{i}") or Decimal("0")

    # OJO: ya no usamos secuencia_evento como clave de búsqueda
    obj, created = CalificacionTributaria.objects.update_or_create(
        corredor=corredor,
        identificador_cliente=ident,
        instrumento=inst,
        ejercicio=ejercicio,
        mercado=mercado,
        defaults=defaults,
    )

    # Si el archivo trae una secuencia válida, la usamos
    if secuencia and secuencia.isdigit():
        obj.secuencia_evento = secuencia
    else:
        # Si no, dejamos que el modelo la genere (10000, 10001, ...)
        obj.secuencia_evento = None

    # Importante: esto dispara el save() del modelo y genera la secuencia
    obj.save()

    return obj, created



# -------------------------------
# AUTO MODO FACTOR O MONTO
# -------------------------------

def _es_modo_monto(factores_dict):
    total = Decimal("0")
    for v in factores_dict.values():
        if v:
            total += v
            if v > Decimal("1"):
                return True
    if total > Decimal("1.0001"):
        return True
    return False


def _normalizar_factores_a_1(factores):
    total = sum([v for v in factores.values() if v], Decimal("0"))
    if total == 0:
        return {k: Decimal("0") for k in factores}
    return {k: (v or Decimal("0")) / total for k, v in factores.items()}


# -------------------------------
# PROCESADORES FACTOR Y MONTO
# -------------------------------

def procesar_archivo_carga_factores(archivo_carga, file_obj, corredor, delimiter=","):
    started = timezone.now()
    archivo_carga.started_at = started
    archivo_carga.estado_proceso = "procesando"
    archivo_carga.save(update_fields=["started_at", "estado_proceso"])

    try:
        df = _cargar_dataframe_desde_archivo(file_obj, archivo_carga.nombre_original, delimiter)
        df = _normalizar_headers_dataframe(df)
    except ValidationError as e:
        _finalizar_error(archivo_carga, started, str(e))
        return

    total = nuevos = actualizados = rechazados = 0
    errores = []

    for idx, row in df.iterrows():
        total += 1
        try:
            row_dict = {}

            row_dict["identificador_cliente"] = str(_extraer_valor(row, "identificador_cliente", "")).strip()
            row_dict["instrumento"] = str(_extraer_valor(row, "instrumento", "")).strip()
            row_dict["mercado"] = str(_extraer_valor(row, "mercado", "")).strip()
            row_dict["ejercicio"] = str(_extraer_valor(row, "ejercicio", "")).strip()
            row_dict["secuencia_evento"] = str(_extraer_valor(row, "secuencia_evento", "")).strip()

            # país en columna
            for k, v in row.items():
                if "pais" in str(k).lower():
                    code = str(v).strip().upper()
                    row_dict["pais"] = _detectar_pais_y_crear_si_falta(code) if code in ["CHL","COL","PER"] else None
                    break
            if "pais" not in row_dict:
                code, _ = DetectorPaisTributario.detectar_pais({
                    "identificador_cliente": row_dict["identificador_cliente"],
                    "instrumento": row_dict["instrumento"],
                })
                row_dict["pais"] = _detectar_pais_y_crear_si_falta(code) if code else None

            # factores
            factores = {}
            for i in range(8, 20):
                col = f"factor_{i}"
                raw = _extraer_valor(row, col, None)
                val = _normalizar_valor_decimal(raw)
                row_dict[col] = val
                factores[col] = val

            if _es_modo_monto(factores):
                norm = _normalizar_factores_a_1(factores)
                row_dict.update(norm)
                row_dict["factor_actualizacion"] = Decimal("1")
            else:
                suma = sum([v for v in factores.values() if v], Decimal("0"))
                row_dict["factor_actualizacion"] = suma

            obj, created = _obtener_o_crear_calificacion_from_row(row_dict, corredor, archivo_carga)
            if created:
                nuevos += 1
            else:
                actualizados += 1

        except Exception as e:
            rechazados += 1
            errores.append({
                "fila": idx + 1,
                "error": str(e),
                "datos": row.to_dict(),
            })

    _finalizar_ok(archivo_carga, started, total, nuevos, actualizados, rechazados, errores)


def procesar_archivo_carga_monto(archivo_carga, file_obj, corredor, delimiter=","):
    started = timezone.now()
    archivo_carga.started_at = started
    archivo_carga.estado_proceso = "procesando"
    archivo_carga.save(update_fields=["started_at", "estado_proceso"])

    try:
        df = _cargar_dataframe_desde_archivo(file_obj, archivo_carga.nombre_original, delimiter)
        df = _normalizar_headers_dataframe(df)
    except ValidationError as e:
        _finalizar_error(archivo_carga, started, str(e))
        return

    total = nuevos = actualizados = rechazados = 0
    errores = []

    for idx, row in df.iterrows():
        total += 1
        try:
            row_dict = {}

            row_dict["identificador_cliente"] = str(_extraer_valor(row, "identificador_cliente", "")).strip()
            row_dict["instrumento"] = str(_extraer_valor(row, "instrumento", "")).strip()
            row_dict["mercado"] = str(_extraer_valor(row, "mercado", "")).strip()
            row_dict["ejercicio"] = str(_extraer_valor(row, "ejercicio", "")).strip()
            row_dict["secuencia_evento"] = str(_extraer_valor(row, "secuencia_evento", "")).strip()

            # país
            for k, v in row.items():
                if "pais" in str(k).lower():
                    code = str(v).strip().upper()
                    row_dict["pais"] = _detectar_pais_y_crear_si_falta(code) if code in ["CHL","COL","PER"] else None
                    break
            if "pais" not in row_dict:
                code, _ = DetectorPaisTributario.detectar_pais({
                    "identificador_cliente": row_dict["identificador_cliente"],
                    "instrumento": row_dict["instrumento"],
                })
                row_dict["pais"] = _detectar_pais_y_crear_si_falta(code) if code else None

            # factores raw
            factores = {}
            for i in range(8, 20):
                col = f"factor_{i}"
                raw = _extraer_valor(row, col, None)
                val = _normalizar_valor_decimal(raw)
                row_dict[col] = val
                factores[col] = val

            if not _es_modo_monto(factores):
                raise ValidationError("Los valores no parecen montos (>1).")

            norm = _normalizar_factores_a_1(factores)
            row_dict.update(norm)
            row_dict["factor_actualizacion"] = Decimal("1")

            obj, created = _obtener_o_crear_calificacion_from_row(row_dict, corredor, archivo_carga)

            if created:
                nuevos += 1
            else:
                actualizados += 1

        except Exception as e:
            rechazados += 1
            errores.append({
                "fila": idx + 1,
                "error": str(e),
                "datos": row.to_dict(),
            })

    _finalizar_ok(archivo_carga, started, total, nuevos, actualizados, rechazados, errores)


# -------------------------------
# FINALIZADORES
# -------------------------------

def _finalizar_ok(archivo_carga, started, total, nuevos, actualizados, rechazados, errores):
    finished = timezone.now()
    archivo_carga.finished_at = finished
    archivo_carga.tiempo_procesamiento_seg = (finished - started).total_seconds()
    archivo_carga.estado_proceso = "ok"
    archivo_carga.resumen_proceso = {
        "total_registros": total,
        "nuevos": nuevos,
        "actualizados": actualizados,
        "rechazados": rechazados,
    }
    archivo_carga.errores_por_fila = errores
    archivo_carga.save(update_fields=[
        "finished_at", "tiempo_procesamiento_seg",
        "estado_proceso", "resumen_proceso", "errores_por_fila"
    ])

    notificar_resultado_archivo(archivo_carga)


def _finalizar_error(archivo_carga, started, detalle_error):
    finished = timezone.now()
    archivo_carga.finished_at = finished
    archivo_carga.tiempo_procesamiento_seg = (finished - started).total_seconds()
    archivo_carga.estado_proceso = "error"
    archivo_carga.resumen_proceso = {
        "total_registros": 0,
        "nuevos": 0,
        "actualizados": 0,
        "rechazados": 0,
        "detalle": detalle_error,
    }
    archivo_carga.errores_por_fila = []
    archivo_carga.save(update_fields=[
        "finished_at", "tiempo_procesamiento_seg",
        "estado_proceso", "resumen_proceso", "errores_por_fila"
    ])

    notificar_resultado_archivo(archivo_carga)


# -------------------------------
# ROUTER PRINCIPAL — AHORA AUTO FILE_OBJ
# -------------------------------

def procesar_archivo_carga(archivo_carga, file_obj=None, corredor=None, delimiter=","):
    """
    Esta función ahora soporta LLAMADA SIMPLE:

        procesar_archivo_carga(archivo_carga)

    SIN file_obj y SIN corredor.
    Lo obtiene automáticamente del archivo físico y del modelo.
    """

    # Obtener file_obj si no fue entregado
    if file_obj is None:
        try:
            f = open(archivo_carga.ruta_almacenamiento, "rb")
            file_obj = File(f)
        except Exception as e:
            raise ValidationError(f"No se pudo abrir archivo físico: {e}")

    # Obtener corredor automáticamente
    if corredor is None:
        corredor = archivo_carga.corredor

    tipo = (archivo_carga.tipo_carga or "FACTOR").upper()

    if tipo == "FACTOR":
        return procesar_archivo_carga_factores(archivo_carga, file_obj, corredor, delimiter)

    if tipo == "MONTO":
        return procesar_archivo_carga_monto(archivo_carga, file_obj, corredor, delimiter)

    raise ValidationError(f"Tipo de carga no soportado: {tipo}")


# -------------------------------
# EMAIL
# -------------------------------

def notificar_resultado_archivo(archivo_carga: ArchivoCarga):
    corredor = archivo_carga.corredor
    if not corredor or not corredor.email_contacto:
        return

    resumen = archivo_carga.resumen_proceso or {}
    errores = archivo_carga.errores_por_fila or []

    body = []
    body.append(f"Estimado/a {corredor.nombre},\n")
    body.append("Se ha procesado su carga masiva.")
    body.append(f"\nID carga: {archivo_carga.id}")
    body.append(f"\nArchivo: {archivo_carga.nombre_original}")
    body.append(f"\nEstado: {archivo_carga.estado_proceso}")

    body.append("\n\nResumen:")
    body.append(f"  Total: {resumen.get('total_registros',0)}")
    body.append(f"  Nuevos: {resumen.get('nuevos',0)}")
    body.append(f"  Actualizados: {resumen.get('actualizados',0)}")
    body.append(f"  Rechazados: {resumen.get('rechazados',0)}")

    if errores:
        body.append("\nErrores (primeros 10):")
        for e in errores[:10]:
            body.append(f"  Fila {e['fila']}: {e['error']}")

    send_email_async(
        subject=f"Resultado carga {archivo_carga.id}",
        message="\n".join(body),
        recipient_list=[corredor.email_contacto],
    )


# -------------------------------
# CONVERSIÓN DE ARCHIVOS
# -------------------------------

def _sanitizar_delimitador(delimiter: str) -> str:
    d = (delimiter or ",").strip()
    return d if len(d) == 1 else ","


def _leer_archivo_a_dataframe_generico(file_obj, filename, delimiter=","):
    delimiter = _sanitizar_delimitador(delimiter)
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".csv":
        return pd.read_csv(file_obj, delimiter=delimiter)

    if ext in (".xls", ".xlsx"):
        return pd.read_excel(file_obj)

    if ext == ".pdf":
        rows = _parse_pdf_text_to_rows(file_obj)
        return pd.DataFrame(rows)

    raise ValidationError(f"No se soporta extensión: {ext}")



def convertir_archivo_generico(file_obj, filename: str, formato_destino: str, delimiter=","):
    import io
    delimiter = _sanitizar_delimitador(delimiter)
    formato = (formato_destino or "").upper().strip()
    if formato not in {"EXCEL_TO_CSV", "CSV_TO_EXCEL", "PDF_TO_EXCEL"}:
        raise ValidationError("Formato conversión inválido.")

    df = _leer_archivo_a_dataframe_generico(file_obj, filename, delimiter)

    buffer = io.BytesIO()

    if formato == "EXCEL_TO_CSV":
        df.to_csv(buffer, index=False, sep=delimiter)
        mimetype = "text/csv"
        out_name = filename.replace(".xlsx", "_conv.csv")

    else:
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        out_name = filename.replace(".csv", "_conv.xlsx")

    buffer.seek(0)
    return buffer, out_name, mimetype


def generar_vista_previa_archivo(file_obj, filename: str, delimiter=",", max_filas=50):
    df = _leer_archivo_a_dataframe_generico(file_obj, filename, delimiter)
    df_preview = df.head(max_filas).fillna("")
    return {
        "columns": list(df_preview.columns),
        "rows": df_preview.to_dict(orient="records"),
        "total_rows": len(df),
    }
