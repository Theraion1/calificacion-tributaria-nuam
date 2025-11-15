from django.contrib import admin
from .models import (
    Pais,
    Corredor,
    UsuarioPerfil,
    ArchivoCarga,
    CalificacionTributaria,
    HistorialCalificacion,
)


@admin.register(Pais)
class PaisAdmin(admin.ModelAdmin):
    list_display = ("id", "nombre", "codigo_iso3", "creado_en", "activo")
    search_fields = ("nombre", "codigo_iso3")
    list_filter = ("activo",)


@admin.register(Corredor)
class CorredorAdmin(admin.ModelAdmin):
    list_display = ("id", "nombre", "codigo_interno", "pais", "creado_en", "activo")
    search_fields = ("nombre", "codigo_interno")
    list_filter = ("pais", "activo")


@admin.register(UsuarioPerfil)
class UsuarioPerfilAdmin(admin.ModelAdmin):
    list_display = ("id", "nombre", "rol", "corredor", "creado_en", "activo")
    search_fields = ("nombre", "user__username", "rol")
    list_filter = ("rol", "corredor", "activo")


@admin.register(ArchivoCarga)
class ArchivoCargaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "nombre_original",
        "corredor",
        "estado_proceso",
        "started_at",
        "finished_at",
        "creado_en",
    )
    list_filter = ("estado_proceso", "corredor")
    search_fields = ("nombre_original",)


@admin.register(CalificacionTributaria)
class CalificacionTributariaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "corredor",
        "identificador_cliente",
        "instrumento",
        "pais",
        "moneda",
        "creado_en",
    )
    list_filter = ("corredor", "pais", "moneda")
    search_fields = ("identificador_cliente", "instrumento")


@admin.register(HistorialCalificacion)
class HistorialCalificacionAdmin(admin.ModelAdmin):
    list_display = ("id", "calificacion", "usuario", "accion", "creado_en")
    list_filter = ("accion", "usuario")
    search_fields = ("descripcion_cambio",)
