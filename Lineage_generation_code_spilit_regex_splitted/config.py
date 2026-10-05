# =============================================================================
# config.py
# =============================================================================
# PURPOSE:
#   Single source of truth for ALL compiled regex patterns used across the
#   Java 8 lineage tool.
#
# WHAT IT CONTAINS:
#   - @Value / @ConfigurationProperties / @PropertySource patterns
#   - Field declaration pattern
#   - Named query pattern
#   - Generic method-call-with-string-arg pattern
#   - Internal helper patterns (throw-new, unqualified method decl)
#   - Java 8 method declaration regex (large verbose pattern)
#
# WHEN TO EDIT THIS FILE:
#   When a Java pattern is being missed or wrongly detected.
#   A developer only needs to change a regex here - no business logic touched.
#
# DEPENDS ON: Nothing (Python stdlib `re` only)
# USED BY:    java_ast_parser.py, java_call_resolver.py,
#             java_project_indexer.py, java_property_extractor.py
# =============================================================================

import re

# ---------------------------------------------------------------------------
# Module-level compiled patterns (Java 8 safe — no var, no records, etc.)
# ---------------------------------------------------------------------------

re_value_dollar = re.compile(r'@Value\s*\(\s*["\']\$\{([^}]+)\}["\']\s*\)')
re_value_spel_dollar = re.compile(r'@Value\s*\(\s*["\']#\{\s*\$\{([^}]+)\}\s*\}["\']\s*\)')
re_field_decl = re.compile(
    r'(?:private|public|protected)?\s*[\w<>\[\],\s?]+\s+([A-Za-z_]\w*)\s*(?:=|;)', re.M
)
re_configuration_properties = re.compile(
    r'@ConfigurationProperties\s*\(\s*(?:prefix\s*=\s*)?["\']([^"\')]+)["\']\s*\)'
)
re_property_source = re.compile(
    r'@PropertySource\s*\(\s*(?:value\s*=\s*)?["\']([^"\')]+)["\']'
)
re_message_key = re.compile(
    r'messageSource\.getMessage\s*\(\s*["\']([^"\']+)["\']'
)
re_named_query_decl = re.compile(
    r'@NamedQuery\s*\(\s*name\s*=\s*["\']([^"\']+)["\']\s*,\s*query\s*=\s*["\']([\s\S]*?)["\']\s*\)',
    re.MULTILINE,
)
re_any_method_first_string_arg = re.compile(
    r'(?<!@)\b(?:[A-Za-z_]\w*\s*\.\s*)*([A-Za-z_]\w*)\s*\(\s*["\']([^"\']+)["\']',
    re.MULTILINE,
)

# Pre-compiled patterns reused inside fallback_parse / find_calls_in_method
_re_throw_new = re.compile(r'\bthrow\s+new\s+([A-Za-z_]\w+)\s*\(', re.MULTILINE)
_re_unqualified_method_decl = re.compile(
    r'\b(?:public|private|protected)\b[^{;]*\b(\w+)\s*\(',
    re.MULTILINE,
)

# Java 8 method declaration regex — same structure as Java 18 adapter but
# explicitly excludes 'var' as a return type (Java 10+ only).
re_method_decl = re.compile(
    r'''
    ^\s*
    (?:@\w+(?:\([^)]*\))?\s*)*
    (?:(?:public|private|protected)\s+)?
    (?:static\s+|final\s+|synchronized\s+|native\s+|abstract\s+|default\s+)*
    (?:<[^>]+>\s+)?
    (?!var\b)                                        # Java 8: no 'var' type inference
    (?:[A-Za-z_][\w$.]*(?:\s*<[^>{}]+>)?(?:\s*\[\s*\])?\s+)+
    (?!(?:if|for|while|switch|catch|else)\b)
    ([A-Za-z_]\w*)
    \s*\(
    \s*(
        (?:
        (?:@\w+(?:\([^)]*\))?\s*)*
        (?:final\s+)?
        [A-Za-z_][\w$.]*(?:\s*<[^>{}]+>)?(?:\s*\[\s*\])*(?:\s*\.\.\.)?\s+
        [A-Za-z_]\w*
        )
        (?:\s*,\s*
        (?:@\w+(?:\([^)]*\))?\s*)*
        (?:final\s+)?
        [A-Za-z_][\w$.]*(?:\s*<[^>{}]+>)?(?:\s*\[\s*\])*(?:\s*\.\.\.)?\s+
        [A-Za-z_]\w*
        )*
    )?
    \)\s*\{
    ''',
    re.M | re.X
)

