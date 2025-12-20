import os
import io
import re
from decimal import Decimal

import pandas as pd
import pdfplumber
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.core.files import File

from .email_utils import send_email_async
from .models import ArchivoCarga, CalificacionTributaria, Pais


# ============================================================
# DETECTOR DE PAÍS
# ============================================================

class DetectorPaisTributario(object):
    PATRONES = {
        "CHL": {
            "regex": [
                r"\d{1,2}\.\d{3}\.\d{3}-[0-9kK]",
            ],
            "keywords": ["CHL", "CHILE", "SANTIAGO", "RUT"],
            "score_regex": 0.7,
            "score_keyword": 0.3,
        },
        "COL": {
            "regex": [
                r"\d{5,10}",
            ],
            "keywords": ["COL", "COLOMBIA", "BOGOTA", "NIT"],
            "score_regex": 0.6,
            "score_keyword": 0.4,
        },
        "PER": {
            "regex": [
                r"\d{11}",
            ],
            "keywords": ["PER", "PERU", "LIMA", "RUC"],
            "score_regex": 0.6,
            "score_keyword": 0.4,
        },
    }


    @classmethod
    def detectar_pais(cls, fila_dict):
        texto = " ".join([str(v) for v in fila_dict.values() if v]).upper()
        if not texto.strip():
            return None, 0.0

        scores = {pais: 0.0 for pais in cls.PATRONES}

        for codigo, reglas in cls.PATRONES.items():
            for patron in reglas["regex"]:
                if re.search(patron, texto):
                    scores[codigo] += reglas["score_regex"]

            for kw in reglas["keywords"]:
                if kw in texto:
                    scores[codigo] += reglas["score_keyword"]

        mejor = max(scores, key=scores.get)
        return (mejor, scores[mejor]) if scores[mejor] >= 0.3 else (None, scores[mejor])


# ============================================================
# PARSEADOR UNIVERSAL DE PDF
# ============================================================

def _parse_pdf_text_to_rows(file_obj):
    rows = []

    try:
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

            # Si tiene separadores visibles
            if "|" in clean:
                rows.append([p.strip() for p in clean.split("|") if p.strip()])
                continue

            # =============================================
            # NUEVO: DIVISIÓN INTELIGENTE
            # Usa double-space como corte de columna.
            # Mantiene las descripciones completas.
            # =============================================
            parts = re.split(r"\s{2,}", clean)
            parts = [p.strip() for p in parts if p.strip()]

            # Si NO funcionó porque todo viene con 1 espacio → fallback seguro
            if len(parts) <= 1:
                parts = clean.split()

            rows.append(parts)

    pdf.close()

    # Normalizar número de columnas
    max_cols = max(len(r) for r in rows)
    normalized = [r + [""] * (max_cols - len(r)) for r in rows]

    return normalized




# ============================================================
# HELPERS
# ============================================================

def _normalizar_header(nombre_columna):
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
    }

    if nombre in reemplazos:
        return reemplazos[nombre]

    for i in range(8, 20):
        base = f"factor_{i}"
        if nombre in [base, base.replace("_", ""), f"f{i}", f"factor{i}", f"fac_{i}"]:
            return base

    return nombre


def _detectar_tipo_archivo_por_extension(nombre_archivo):
    ext = os.path.splitext(nombre_archivo.lower())[1]
    if ext == ".csv": return "CSV"
    if ext in [".xls", ".xlsx"]: return "EXCEL"
    if ext == ".pdf": return "PDF"
    raise ValidationError(f"Extensión no soportada: {ext}")


def _cargar_dataframe_desde_archivo(file_obj, nombre_archivo, delimiter=","):
    tipo = _detectar_tipo_archivo_por_extension(nombre_archivo)

    # ============================================================
    # CSV
    # ============================================================
    if tipo == "CSV":
        return pd.read_csv(file_obj, delimiter=delimiter)

    # ============================================================
    # EXCEL
    # ============================================================
    if tipo == "EXCEL":
        return pd.read_excel(file_obj)

    # ============================================================
    # PDF  — PARSEO ROBUSTO FINAL
    # ============================================================
    if tipo == "PDF":
        rows = _parse_pdf_text_to_rows(file_obj)
        df = pd.DataFrame(rows)

        # Si el PDF está completamente vacío
        if df.empty:
            raise ValidationError("El PDF no contiene datos legibles.")

        # ===============================================
        # 1) Detectar si la primera fila es cabecera real
        # ===============================================
        first_row = df.iloc[0].tolist()
        first_row_text = " ".join([str(v) for v in first_row]).lower()

        # Palabras clave que normalmente aparecen en cabeceras
        claves = [
            "identificador", "cliente",
            "instrumento",
            "mercado",
            "descripcion", "descripción",
            "pais", "país",
            "factor"
        ]

        coincidencias = sum(1 for c in claves if c in first_row_text)

        # Si detectamos cabecera real (≥2 coincidencias)
        if coincidencias >= 2:
            # Usamos esa fila como cabecera
            header_tokens = [str(h).strip() for h in first_row]

            # Normalizamos duplicados o vacíos
            header_tokens = [
                h if h else f"col_{i}" for i, h in enumerate(header_tokens)
            ]

            df.columns = header_tokens
            df = df[1:]  # remover cabecera real
            df = df.reset_index(drop=True)
            return df

        # ===============================================
        # 2) Si NO detectamos cabecera real:
        #    Asignar nombres genéricos col_0, col_1, ...
        # ===============================================
        df.columns = [f"col_{i}" for i in range(df.shape[1])]
        return df

    # ============================================================
    # Tipo no soportado
    # ============================================================
    raise ValidationError("Tipo no soportado.")



def _extraer_valor(df_row, col, default=None):
    return df_row[col] if col in df_row.index else default


def _normalizar_headers_dataframe(df):
    df = df.copy()
    df.rename(columns={c: _normalizar_header(c) for c in df.columns}, inplace=True)
    return df


def _normalizar_valor_decimal(valor):
    if valor is None:
        return None

    s = str(valor).strip()
    s = re.sub(r"[^\d\.,\-]", "", s)
    match = re.search(r"[-+]?\d*[\.,]?\d+", s)
    if not match:
        return None

    s = match.group(0).replace(",", ".")

    try:
        d = Decimal(s)
    except:
        return None

    return d.quantize(Decimal("0.0001"))


def _detectar_pais_y_crear_si_falta(code):
    if not code:
        return None
    code = code.upper()
    pais, _ = Pais.objects.get_or_create(codigo_iso3=code, defaults={"nombre": code})
    return pais


# ============================================================
# CREAR O ACTUALIZAR CALIFICACIÓN
# ============================================================

def _obtener_o_crear_calificacion_from_row(row_dict, corredor, archivo_carga):
    row = { k.strip().lower().replace(" ", "_").replace("-", "_"): v for k, v in row_dict.items()}
    ident = (row_dict.get("identificador_cliente") or row_dict.get("id_cliente") or row_dict.get("cliente") 
    or (corredor.identificador if hasattr(corredor, "identificador") else corredor.id))

    if isinstance(ident, int) or (isinstance(ident, str) and ident.isdigit()):
        ident = archivo_carga.submitted_by.username

    inst = row_dict.get("instrumento")

    if not inst:
        raise ValidationError("instrumento es obligatorio.")

    ejercicio = row_dict.get("ejercicio") or timezone.now().year
    mercado = row_dict.get("mercado")
    secuencia = row_dict.get("secuencia_evento")

    defaults = {
        "corredor": corredor,
        "archivo_origen": archivo_carga,
        "pais": row_dict.get("pais") or corredor.pais,
        "mercado": mercado,
        "ejercicio": ejercicio,
        "valor_historico": row_dict.get("valor_historico") or Decimal("0"),
        "valor_actualizado": row_dict.get("valor_actualizado") or Decimal("0"),
        "factor_actualizacion": row_dict.get("factor_actualizacion") or Decimal("0"),
    }

    for i in range(8, 20):
        defaults[f"factor_{i}"] = row_dict.get(f"factor_{i}") or Decimal("0")

    obj, created = CalificacionTributaria.objects.update_or_create(
        corredor=corredor,
        instrumento=inst,
        ejercicio=ejercicio,
        mercado=mercado,
        identificador_cliente=ident,
        defaults=defaults,
    )

    if secuencia and secuencia.isdigit():
        obj.secuencia_evento = secuencia
    else:
        obj.secuencia_evento = None

    obj.save()
    return obj, created


# ============================================================
# DETECTAR MONTO O FACTOR
# ============================================================

def _es_modo_monto(factores):
    total = Decimal("0")
    for v in factores.values():
        if v:
            total += v
            if v > Decimal("1"):
                return True
    return total > Decimal("1.0001")


def _normalizar_factores_a_1(factores):
    total = sum((v or Decimal("0")) for v in factores.values())
    if total == 0:
        return {k: Decimal("0") for k in factores}
    return {k: (v or Decimal("0")) / total for k, v in factores.items()}


# ============================================================
# PROCESAR FACTOR
# ============================================================

def procesar_archivo_carga_factores(archivo_carga, file_obj, corredor, delimiter=","):

    archivo_carga.started_at = timezone.now()
    archivo_carga.estado_proceso = "procesando"
    archivo_carga.save()

    try:
        df = _cargar_dataframe_desde_archivo(file_obj, archivo_carga.nombre_original, delimiter)
        df = _normalizar_headers_dataframe(df)
    except ValidationError as e:
        _finalizar_error(archivo_carga, archivo_carga.started_at, str(e))
        return

    total = nuevos = actualizados = rechazados = 0
    errores = []

    for idx, row in df.iterrows():
        total += 1
        try:
            # ------------------------------
            # CAMPOS BÁSICOS
            # ------------------------------
            row_dict = {
                "instrumento": str(_extraer_valor(row, "instrumento", "")).strip(),
                "mercado": str(_extraer_valor(row, "mercado", "")).strip(),
                "ejercicio": str(_extraer_valor(row, "ejercicio", "")).strip(),
                "secuencia_evento": str(_extraer_valor(row, "secuencia_evento", "")).strip(),
            }

            # ==============================
            # IDENTIFICADOR CLIENTE 
            # ==============================
            identificador = (_extraer_valor(row, "identificador_cliente")
                    or _extraer_valor(row, "id_cliente")
                    or _extraer_valor(row, "cliente"))

            if identificador:
                row_dict["identificador_cliente"] = str(identificador).strip()

            # =====================================================
            #   DETECCIÓN DE PAÍS — FINAL
            # =====================================================
            pais_obj = None

            # 1) Intentar encontrar columna 'pais'
            for k, v in row.items():
                if "pais" in str(k).lower():
                    code = str(v).strip().upper()
                    if code and len(code) >= 2 and code.isalpha():
                        pais_obj = _detectar_pais_y_crear_si_falta(code)
                    break

            # 2) Si no se obtuvo país válido → detección automática completa
            if not pais_obj:
                code, _ = DetectorPaisTributario.detectar_pais(row.to_dict())
                pais_obj = _detectar_pais_y_crear_si_falta(code) if code else None

            row_dict["pais"] = pais_obj

            # ------------------------------
            # FACTORES
            # ------------------------------
            factores = {}
            for i in range(8, 20):
                col = f"factor_{i}"
                raw = _extraer_valor(row, col)
                val = _normalizar_valor_decimal(raw)
                val = val or Decimal("0")
                row_dict[col] = val
                factores[col] = val

            # MONTO vs FACTOR
            if _es_modo_monto(factores):
                norm = _normalizar_factores_a_1(factores)
                row_dict.update(norm)
                row_dict["factor_actualizacion"] = Decimal("1")
            else:
                row_dict["factor_actualizacion"] = sum(factores.values())

            # ------------------------------
            # CREAR / ACTUALIZAR
            # ------------------------------
            obj, created = _obtener_o_crear_calificacion_from_row(row_dict, corredor, archivo_carga)

            nuevos += created
            actualizados += (1 - created)

        except Exception as e:
            rechazados += 1
            errores.append({"fila": idx + 1, "error": str(e), "datos": row.to_dict()})

    _finalizar_ok(
        archivo_carga,
        archivo_carga.started_at,
        total,
        nuevos,
        actualizados,
        rechazados,
        errores,
    )

# ============================================================
# PROCESAR MONTO
# ============================================================

def procesar_archivo_carga_monto(archivo_carga, file_obj, corredor, delimiter=","):

    archivo_carga.started_at = timezone.now()
    archivo_carga.estado_proceso = "procesando"
    archivo_carga.save()

    try:
        df = _cargar_dataframe_desde_archivo(file_obj, archivo_carga.nombre_original, delimiter)
        df = _normalizar_headers_dataframe(df)
    except ValidationError as e:
        _finalizar_error(archivo_carga, archivo_carga.started_at, str(e))
        return

    total = nuevos = actualizados = rechazados = 0
    errores = []

    for idx, row in df.iterrows():
        total += 1
        try:
            row_dict = {
                "instrumento": str(_extraer_valor(row, "instrumento", "")).strip(),
                "mercado": str(_extraer_valor(row, "mercado", "")).strip(),
                "ejercicio": str(_extraer_valor(row, "ejercicio", "")).strip(),
                "secuencia_evento": str(_extraer_valor(row, "secuencia_evento", "")).strip(),
            }

            # ==============================
            # IDENTIFICADOR CLIENTE 
            # ==============================
            identificador = (_extraer_valor(row, "identificador_cliente")
                    or _extraer_valor(row, "id_cliente")
                    or _extraer_valor(row, "cliente"))

            if identificador:
                row_dict["identificador_cliente"] = str(identificador).strip()

            # =====================================================
            #   DETECCIÓN DE PAÍS — FINAL
            # =====================================================
            pais_obj = None

            for k, v in row.items():
                if "pais" in str(k).lower():
                    code = str(v).strip().upper()
                    if code and len(code) >= 2 and code.isalpha():
                        pais_obj = _detectar_pais_y_crear_si_falta(code)
                    break

            if not pais_obj:
                code, _ = DetectorPaisTributario.detectar_pais(row.to_dict())
                pais_obj = _detectar_pais_y_crear_si_falta(code) if code else None

            row_dict["pais"] = pais_obj

            # ------------------------------
            # FACTORES / MONTOS
            # ------------------------------
            factores = {}
            for i in range(8, 20):
                col = f"factor_{i}"
                raw = _extraer_valor(row, col)
                val = _normalizar_valor_decimal(raw)
                val = val or Decimal("0")
                row_dict[col] = val
                factores[col] = val

            if not _es_modo_monto(factores):
                raise ValidationError("Los valores no parecen montos (>1).")

            norm = _normalizar_factores_a_1(factores)
            row_dict.update(norm)
            row_dict["factor_actualizacion"] = Decimal("1")

            obj, created = _obtener_o_crear_calificacion_from_row(row_dict, corredor, archivo_carga)

            nuevos += created
            actualizados += (1 - created)

        except Exception as e:
            rechazados += 1
            errores.append({"fila": idx + 1, "error": str(e), "datos": row.to_dict()})

    _finalizar_ok(
        archivo_carga,
        archivo_carga.started_at,
        total,
        nuevos,
        actualizados,
        rechazados,
        errores,
    )

# ============================================================
# FINALIZADORES
# ============================================================

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
    archivo_carga.save()

    notificar_resultado_archivo(archivo_carga)


def _finalizar_error(archivo_carga, started, detalle):
    finished = timezone.now()
    archivo_carga.finished_at = finished
    archivo_carga.tiempo_procesamiento_seg = (finished - started).total_seconds()
    archivo_carga.estado_proceso = "error"
    archivo_carga.resumen_proceso = {
        "total_registros": 0,
        "nuevos": 0,
        "actualizados": 0,
        "rechazados": 0,
        "detalle": detalle,
    }
    archivo_carga.errores_por_fila = []
    archivo_carga.save()

    notificar_resultado_archivo(archivo_carga)


# ============================================================
# ROUTER PRINCIPAL
# ============================================================

def procesar_archivo_carga(archivo_carga, file_obj=None, corredor=None, delimiter=","):

    if file_obj is None:
        try:
            f = open(archivo_carga.ruta_almacenamiento, "rb")
            file_obj = File(f)
        except:
            raise ValidationError("No se pudo abrir archivo físico.")

    corredor = corredor or archivo_carga.corredor
    tipo = (archivo_carga.tipo_carga or "FACTOR").upper()

    if tipo == "FACTOR":
        return procesar_archivo_carga_factores(archivo_carga, file_obj, corredor, delimiter)

    if tipo == "MONTO":
        return procesar_archivo_carga_monto(archivo_carga, file_obj, corredor, delimiter)

    raise ValidationError(f"Tipo de carga no soportado: {tipo}")


# ============================================================
# EMAIL
# ============================================================

def notificar_resultado_archivo(archivo_carga):
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
    body.append(f"  Total: {resumen.get('total_registros', 0)}")
    body.append(f"  Nuevos: {resumen.get('nuevos', 0)}")
    body.append(f"  Actualizados: {resumen.get('actualizados', 0)}")
    body.append(f"  Rechazados: {resumen.get('rechazados', 0)}")

    if errores:
        body.append("\nErrores (primeros 10):")
        for e in errores[:10]:
            body.append(f"  Fila {e['fila']}: {e['error']}")

    send_email_async(
        subject=f"Resultado carga {archivo_carga.id}",
        message="\n".join(body),
        recipient_list=[corredor.email_contacto],
    )


# ============================================================
# CONVERSIÓN Y PREVIEW
# ============================================================

def _sanitizar_delimitador(delimiter):
    d = (delimiter or ",").strip()
    return d if len(d) == 1 else ","


def _leer_archivo_a_dataframe_generico(file_obj, filename, delimiter=","):
    ext = os.path.splitext(filename)[1].lower()
    delimiter = _sanitizar_delimitador(delimiter)

    if ext == ".csv":
        return pd.read_csv(file_obj, delimiter=delimiter)

    if ext in [".xls", ".xlsx"]:
        return pd.read_excel(file_obj)

    if ext == ".pdf":
        return pd.DataFrame(_parse_pdf_text_to_rows(file_obj))

    raise ValidationError(f"No se soporta extensión: {ext}")


def convertir_archivo_generico(file_obj, filename, formato_destino, delimiter=","):
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


def generar_vista_previa_archivo(file_obj, filename, delimiter=",", max_filas=50):
    df = _leer_archivo_a_dataframe_generico(file_obj, filename, delimiter)
    df_preview = df.head(max_filas).fillna("")
    return {
        "columns": list(df_preview.columns),
        "rows": df_preview.to_dict(orient="records"),
        "total_rows": len(df),
    }
