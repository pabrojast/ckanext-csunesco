"""Compatibilidad del modelo User de CKAN con las bases SQLite de pruebas."""
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, 'sqlite')
def compile_jsonb_for_sqlite(element, compiler, **kwargs):
    return 'JSON'
