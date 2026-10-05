# =============================================================================
# FILE: java_adapter.py
# PURPOSE: Thin assembly file — combines all mixins into the concrete JavaAdapter.
#
# This file does NOT contain any logic. It simply inherits from the four mixin
# classes and LanguageAdapter so the entire Java 8 adapter surface is available
# through a single import.
#
# USAGE:
#   from java_adapter import JavaAdapter
#   adapter = JavaAdapter()
#   adapter.configure(details=..., regex=...)
#
# DEPENDS ON:
#   language_adapter_base  – LanguageAdapter  (abstract base / interface)
#   java_ast_parser        – AstParserMixin
#   java_call_resolver     – CallResolverMixin
#   java_project_indexer   – ProjectIndexerMixin
#   java_property_extractor– PropertyExtractorMixin
# =============================================================================

from language_adapter_base import LanguageAdapter
from java_ast_parser import AstParserMixin
from java_call_resolver import CallResolverMixin
from java_project_indexer import ProjectIndexerMixin
from java_property_extractor import PropertyExtractorMixin


class JavaAdapter(
    AstParserMixin,
    CallResolverMixin,
    ProjectIndexerMixin,
    PropertyExtractorMixin,
    LanguageAdapter,
):
    """
    Java 8 language adapter.
    Assembles all mixin capabilities into a single adapter class.

    Compared to the Java 18 adapter:
      - parse_ast uses javalang directly (javalang targets Java 8).
      - No special handling for records, sealed classes, text blocks,
        switch expressions, or 'var' type inference.
      - Lambda bodies and stream chains are captured via the chained-call
        regex path (same approach as the Java 18 adapter's fallback).
      - Default / static interface methods (new in Java 8) are handled
        through get_methods_in_type, which yields MethodDeclaration nodes
        on interface bodies.
    """

    pass
