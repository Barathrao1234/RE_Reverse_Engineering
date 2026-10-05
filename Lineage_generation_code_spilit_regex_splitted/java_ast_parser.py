# =============================================================================
# FILE: java_ast_parser.py
# PURPOSE: AST Parsing & Method Extraction for Java 8 source code
#
# CONTAINS:
#   class AstParserMixin  - mixin providing:
#     file_extension()
#     _rx()
#     parse_ast()
#     _fqn_type_name()
#     _effective_type_name()
#     _simple_type_name()
#     _extract_method_annotations()
#     _extract_method_declaration_type()
#     _extract_return_type()
#     _type_to_simple()
#     extract_method_metadata()
#     _collect_com_imports()
#     _build_method_index_map()
#     _find_enclosing_method()
#     _extract_values_with_vars()
#     get_declared_types()
#     get_methods_in_type()
#     _get_line_offsets()
#     _get_method_source()
#     _parse_com_imports_fallback()
#     _extract_balanced_args()
#     fallback_parse()
#
# WHEN TO EDIT THIS FILE:
#   - Changes to AST traversal, type resolution, or method extraction
#   - Changes to fallback regex-based parsing
#   - Changes to javalang tree navigation
#
# DEPENDS ON:
#   config.py              (compiled regex patterns via self._rx)
#   utils.py               (strip_generics, _strip_source_comments)
#   language_adapter_base.py  (LanguageAdapter base class)
#
# USED BY:
#   java_adapter.py        (assembled into JavaAdapter via multiple inheritance)
#   java_call_resolver.py  (CallResolverMixin uses AST parsing methods)
#   java_project_indexer.py (ProjectIndexerMixin uses get_declared_types etc.)
#   java_property_extractor.py (PropertyExtractorMixin uses fallback_parse etc.)
# =============================================================================

import re
import html
import javalang
import javalang.tree as jt

from config import _re_throw_new
from utils import strip_generics, _strip_source_comments


class AstParserMixin:
    """
    Mixin: AST parsing and method extraction for Java 8 source files.
    Provides all methods related to reading, parsing, and navigating
    javalang ASTs as well as regex-based fallback parsing.
    All methods use self and are designed for multiple inheritance
    into JavaAdapter.
    """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_method_index_map(self, java_text: str):
        """Map of (start_pos, method_name) tuples sorted by position."""
        res = []
        for m in self._irx("re_method_decl", re.M | re.X).finditer(java_text):
            res.append((m.start(), m.group(1)))
        res.sort(key=lambda x: x[0])
        return res

    def _find_enclosing_method(self, method_index_map, pos):
        candidate = None
        for start, name in method_index_map:
            if start <= pos:
                candidate = name
            else:
                break
        return candidate

    def _extract_values_with_vars(self, java_text: str):
        results = []
        for m in self._irx("re_value_spel_dollar").finditer(java_text):
            key = m.group(1)
            span_end = m.end()
            var = None
            m2 = self._irx("re_field_decl").search(java_text, span_end)
            if m2:
                var = m2.group(1)
            results.append({
                "Annotation": "@Value", "Property": key,
                "Variable": var, "span_start": m.start(), "span_end": span_end
            })

        for m in self._irx("re_value_dollar").finditer(java_text):
            key = m.group(1)
            span_end = m.end()
            var = None
            m2 = self._irx("re_field_decl").search(java_text, span_end)
            if m2:
                var = m2.group(1)
            results.append({
                "Annotation": "@Value", "Property": key,
                "Variable": var, "span_start": m.start(), "span_end": span_end
            })

        return results

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def file_extension(self) -> str:
        ext = self.details.get("extension")
        if isinstance(ext, str) and ext.strip():
            return ext.strip()
        return ".java"

    def _rx(self, key: str, flags: int = 0):
        pat = self.regex.get(key)
        if not isinstance(pat, str):
            raise KeyError(f"Regex key '{key}' missing or not a string")
        unesc = html.unescape(pat)
        try:
            return re.compile(unesc, flags)
        except re.error as err:
            raise re.error(
                f"[regex compile] key='{key}' pattern='{unesc}' error={err}"
            ) from err

    def _irx(self, key: str, flags: int = 0):
        pat = self.internal_logic.get(key)
        if not isinstance(pat, str):
            raise KeyError(f"Internal_logic key '{key}' missing or not a string")
        return re.compile(pat, flags)
    
    # ------------------------------------------------------------------
    # AST Parsing  (javalang targets Java 8 — no extra pre-processing needed)
    # ------------------------------------------------------------------

    def parse_ast(self, code: str):
        """
        Parse Java 8 source.  javalang handles all Java 8 features natively
        (lambdas, streams, default interface methods, diamond operator, etc.).
        Returns the compilation unit tree, or None on failure.
        """
        try:
            return javalang.parse.parse(code)
        except Exception:
            # Some javalang builds fail on method references like String[]::new.
            # Keep AST mode alive by replacing method-reference tokens with a
            # placeholder identifier for parsing only.
            try:
                _sanitized = re.sub(
                    r'\b[A-Za-z_][\w.$\[\]]*\s*::\s*(?:[A-Za-z_]\w*|new)\b',
                    '__method_ref__',
                    code or '',
                )
                return javalang.parse.parse(_sanitized)
            except Exception:
                return None

    # ------------------------------------------------------------------
    # Type helpers
    # ------------------------------------------------------------------

    def _fqn_type_name(self, type_obj_or_str) -> str:
        """Return the fully-qualified type name by walking javalang sub_type chains.

        When Java source declares a variable with a package-qualified type like
        ``nl.row.path.ClassName obj``, javalang represents the type as a nested
        chain of ReferenceType nodes:
            ReferenceType(name='nl',
                sub_type=ReferenceType(name='row',
                    sub_type=ReferenceType(name='path',
                        sub_type=ReferenceType(name='ClassName'))))

        ``_simple_type_name`` only reads ``type.name`` (the root segment, 'nl')
        and then calls ``.split('.')[-1]`` — which returns ``'nl'``, not
        ``'ClassName'``.  This method walks the full chain and returns
        ``'nl.row.path.ClassName'`` so callers can store the FQN in
        object_class_map and let _resolve_fqn_path pick the correct file.
        """
        if type_obj_or_str is None:
            return None
        if not hasattr(type_obj_or_str, "name"):
            return str(type_obj_or_str)
        parts = [type_obj_or_str.name]
        sub = getattr(type_obj_or_str, "sub_type", None)
        while sub is not None:
            parts.append(sub.name)
            sub = getattr(sub, "sub_type", None)
        return ".".join(parts)

    def _effective_type_name(self, type_obj_or_str) -> str:
        """Return the best type name for storing in object_class_map.

        For a simple type like ``ClassName``, returns ``'ClassName'`` (same as
        _simple_type_name).

        For a package-qualified FQN like ``nl.row.path.ClassName``, returns the
        full FQN ``'nl.row.path.ClassName'`` so that _enrich_call_with_path can
        route it through _resolve_fqn_path and pick the correct source file even
        when multiple modules have a class with the same simple name.

        The key insight: javalang represents ``nl.row.path.ClassName`` as a
        chain of ReferenceType nodes linked via ``sub_type``.  Only by walking
        the whole chain (via _fqn_type_name) can we recover the full FQN;
        reading only ``type.name`` gives the root segment ``'nl'``.
        """
        fqn = self._fqn_type_name(type_obj_or_str)
        if not fqn:
            return None
        # Strip HTML-escaped generics
        fqn = re.sub(r'\s*&amp;lt;[^&amp;gt]+&amp;gt;\s*', '', fqn)
        fqn = re.sub(r'\s*<[^>]+>\s*', '', fqn)
        # If the FQN starts with a lowercase segment (package-qualified), preserve
        # the full FQN so _resolve_class_path → _resolve_fqn_path can use it.
        # Otherwise (simple class name or already-simple) return the last segment.
        if fqn and fqn[0].islower() and '.' in fqn:
            return fqn  # e.g. 'nl.row.path.ClassName'
        return fqn.split('.')[-1]  # e.g. 'ClassName' or 'List'

    def _simple_type_name(self, type_obj_or_str):
        if type_obj_or_str is None:
            return None
        # Walk sub_type chain first to get the correct simple name when the
        # declared type is a package-qualified FQN (e.g. nl.row.path.ClassName).
        # javalang stores the root package segment in type.name and nests the
        # rest via sub_type — so type.name alone gives 'nl', not 'ClassName'.
        fqn = self._fqn_type_name(type_obj_or_str)
        if fqn is None:
            return None
        # Strip HTML-escaped generics
        n = re.sub(r'\s*&amp;lt;[^&amp;gt]+&amp;gt;\s*', '', fqn)
        n = re.sub(r'\s*<[^>]+>\s*', '', n)
        return n.split('.')[-1]

    def _extract_method_annotations(self, method_node) -> str:
        ann_list = []
        if hasattr(method_node, "annotations") and method_node.annotations:
            for ann in method_node.annotations:
                try:
                    ann_list.append("@" + (ann.name if hasattr(ann, "name") else str(ann)))
                except Exception:
                    continue
        return ", ".join(ann_list) if ann_list else ""

    def _extract_method_declaration_type(self, method_node) -> str:
        if hasattr(method_node, "modifiers") and method_node.modifiers:
            mods = {m.lower() for m in method_node.modifiers}
            if "public" in mods:
                return "Public"
            if "private" in mods:
                return "Private"
            if "protected" in mods:
                return "Protected"
        return "Default"

    def _extract_return_type(self, method_node) -> str:
        try:
            rt = method_node.return_type
            if rt is None:
                return "void"
            base = rt.name if hasattr(rt, "name") else "Unknown"
            if hasattr(rt, "arguments") and rt.arguments:
                args = []
                for arg in rt.arguments:
                    if hasattr(arg, "type") and hasattr(arg.type, "name"):
                        args.append(arg.type.name)
                    elif hasattr(arg, "name"):
                        args.append(arg.name)
                return f"{base}&lt;{', '.join(args)}&gt;"
            return base
        except Exception:
            return "Unknown"

    def _type_to_simple(self, t) -> str:
        def _dims_count(type_obj) -> int:
            d = getattr(type_obj, "dimensions", 0)
            if isinstance(d, int):
                return d
            if isinstance(d, (list, tuple)):
                return len(d)
            return int(d or 0)

        if t is None:
            return ""
        base = getattr(t, "name", str(t)) or ""
        if "." in base:
            base = base.split(".")[-1]
        dims = "[]" * _dims_count(t)
        return f"{base}{dims}"

    def extract_method_metadata(self, method_node) -> dict:
        is_ctor = isinstance(method_node, jt.ConstructorDeclaration)

        param_types = []
        for p in getattr(method_node, "parameters", []) or []:
            param_type = getattr(p, "type", None)
            t = self._effective_type_name(param_type) or ""
            d = getattr(param_type, "dimensions", 0) if param_type is not None else 0
            if isinstance(d, int):
                dim_count = d
            elif isinstance(d, (list, tuple)):
                dim_count = len(d)
            else:
                dim_count = int(d or 0)
            t += "[]" * dim_count
            if getattr(p, "varargs", False):
                t = t + "[]"
            param_types.append(t)

        return {
            "Annotations": self._extract_method_annotations(method_node),
            "Method_Declaration_Type": self._extract_method_declaration_type(method_node),
            "return_type": "constructor" if is_ctor else self._extract_return_type(method_node),
            "member_kind": "Constructor" if is_ctor else "Method",
            "Parameters": ", ".join(param_types),
            "Parameter_Arity": len(param_types),
            "Parameter_Types": ";".join(param_types),
        }

    # ------------------------------------------------------------------
    # Import helpers
    # ------------------------------------------------------------------

    def _collect_com_imports(self, tree):
        imports_types = set()
        wildcard_packages = set()
        static_members = set()
        static_wildcard_classes = set()

        for imp in getattr(tree, "imports", []):
            path = getattr(imp, "path", "")
            if not isinstance(path, str) or not path.startswith("nl."):
                continue
            parts = [p for p in path.split('.') if p]
            if getattr(imp, "static", False):
                if parts[-1] == '*':
                    if len(parts) >= 2:
                        static_wildcard_classes.add(parts[-2])
                else:
                    static_members.add(parts[-1])
                    if len(parts) >= 2:
                        imports_types.add(parts[-2])
            else:
                if parts[-1] == '*':
                    wildcard_packages.add('.'.join(parts[:-1]))
                else:
                    imports_types.add(parts[-1])

        return imports_types, wildcard_packages, static_members, static_wildcard_classes

    def get_declared_types(self, ast):
        """
        Yield (name, kind, node) for classes, interfaces, and enums.
        Java 8 does NOT have records or sealed classes — those are omitted.
        """
        types = []

        # Classes
        for _, cls in ast.filter(jt.ClassDeclaration):
            types.append((getattr(cls, "name", "Unknown"), "class", cls))

        # Interfaces (including those with default/static methods — Java 8)
        for _, ifc in ast.filter(jt.InterfaceDeclaration):
            types.append((getattr(ifc, "name", "Unknown"), "interface", ifc))

        # Enums
        for _, en in ast.filter(jt.EnumDeclaration):
            types.append((getattr(en, "name", "Unknown"), "enum", en))

        return types

    def get_methods_in_type(self, type_node):
        """
        Yield (name, node) for every method and constructor in type_node.
        For interfaces, this includes default and static methods (Java 8+).
        """
        # Iterate only direct members declared in this type body.
        # Using recursive filter() can mix nested-type members into the
        # parent type and misattribute calls/metadata (e.g. constructor tags).
        for member in getattr(type_node, "body", []) or []:
            if isinstance(member, jt.MethodDeclaration):
                yield member.name, member
            elif isinstance(member, jt.ConstructorDeclaration):
                yield member.name, member

    # ------------------------------------------------------------------
    # Method source extraction
    # ------------------------------------------------------------------

    # Cache of code-id → cumulative line offsets so we only build it once per file
    _line_offset_cache: dict = {}

    def _get_line_offsets(self, code: str, lines) -> list:
        """Return cumulative byte offsets for each line (cached per code object)."""
        key = id(code)
        cached = self._line_offset_cache.get(key)
        if cached is not None:
            return cached
        offsets = [0] * (len(lines) + 1)
        for i, ln in enumerate(lines):
            offsets[i + 1] = offsets[i] + len(ln)
        self._line_offset_cache[key] = offsets
        # Evict old entries to cap memory (keep last 8 files)
        if len(self._line_offset_cache) > 8:
            oldest = next(iter(self._line_offset_cache))
            del self._line_offset_cache[oldest]
        return offsets

    def _get_method_source(self, code: str, method_node):
        try:
            lines = code.splitlines(True)
            if hasattr(method_node, "position") and method_node.position and method_node.position[0]:
                start_line = method_node.position[0] - 1
                offsets = self._get_line_offsets(code, lines)
                start_offset = offsets[start_line]
                start_brace_idx = code.find('{', start_offset)
                if start_brace_idx == -1:
                    return None
                brace_count = 0
                end_idx = None
                for i in range(start_brace_idx, len(code)):
                    ch = code[i]
                    if ch == '{':
                        brace_count += 1
                    elif ch == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            end_idx = i
                            break
                return code[start_brace_idx:end_idx + 1] if end_idx is not None else None
            else:
                mname = getattr(method_node, "name", None)
                if not mname:
                    return None
                # Cache compiled patterns — same method name reused across many calls
                _sig_cache = getattr(self, '_sig_pat_cache', None)
                if _sig_cache is None:
                    self._sig_pat_cache = {}
                    _sig_cache = self._sig_pat_cache
                sig_pat = _sig_cache.get(mname)
                if sig_pat is None:
                    sig_pat = re.compile(
                        r'\b' + re.escape(mname) + r'\s*\([^)]*\)\s*\{',
                        re.MULTILINE | re.DOTALL
                    )
                    _sig_cache[mname] = sig_pat
                match = sig_pat.search(code)
                if not match:
                    return None
                start_brace_idx = match.end() - 1
                brace_count = 0
                end_idx = None
                for i in range(start_brace_idx, len(code)):
                    ch = code[i]
                    if ch == '{':
                        brace_count += 1
                    elif ch == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            end_idx = i
                            break
                return code[start_brace_idx:end_idx + 1] if end_idx is not None else None
        except Exception:
            return None


    def _parse_com_imports_fallback(self, java_code: str):
        re_import_line = self._rx("import_static", flags=re.MULTILINE)
        imports_types = set()
        wildcard_packages = set()
        static_members = set()
        static_wildcard_classes = set()
        for m in re_import_line.finditer(java_code):
            line = m.group(0)
            path = m.group(1)
            parts = [p for p in path.split('.') if p]
            is_static = 'static' in line
            if is_static:
                if parts[-1] == '*':
                    if len(parts) >= 2:
                        static_wildcard_classes.add(parts[-2])
                else:
                    static_members.add(parts[-1])
                    if len(parts) >= 2:
                        imports_types.add(parts[-2])
            else:
                if parts[-1] == '*':
                    wildcard_packages.add('.'.join(parts[:-1]))
                else:
                    imports_types.add(parts[-1])
        return imports_types, wildcard_packages, static_members, static_wildcard_classes

    def _extract_balanced_args(self, text: str, start_idx: int) -> str:
        if start_idx < 0 or start_idx >= len(text) or text[start_idx] != '(':
            return ""
        depth = 0
        end = None
        for i in range(start_idx, len(text)):
            ch = text[i]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        return text[start_idx:end + 1] if end is not None else ""

    def fallback_parse(self, code_raw: str) -> dict:
        """
        Regex-only fallback for files whose AST cannot be parsed.
        Handles all Java 8 patterns including lambdas and streams
        (they appear as regular method calls in regex terms).
        """
        java_code = html.unescape(code_raw)
        package_name = self._get_package_name(java_code)
        imports_types, wildcard_packages, static_members, static_wildcard_classes = \
            self._parse_com_imports_fallback(java_code)

        re_autowired_field  = self._rx("autowired_field", flags=re.MULTILINE)
        re_loose_decl       = self._rx("variable_declaration")
        re_var_decl         = self._rx("re_var_decl")
        re_var_new          = self._rx("re_var_new")
        re_simple_call      = self._rx("re_simple_call", flags=re.MULTILINE)
        re_member_access    = self._rx("re_member_access")
        re_unqualified_call = self._rx("re_unqualified_call")
        re_chain            = self._rx("re_chain") if self.regex.get("re_chain") else re.compile(r"$^")
        re_this_call        = re.compile(r'\bthis\s*\.\s*([A-Za-z_]\w*)\s*\(')
        re_method_with_throw = self._rx("method_with_throw", flags=re.MULTILINE | re.DOTALL)
        re_method_name_in_sig = re.compile(r'\b([A-Za-z_]\w*)\s*\(', re.MULTILINE)

        re_class_implements      = self._rx("class_implements", flags=re.MULTILINE)
        re_class_declaration     = self._rx("class_declaration", flags=re.MULTILINE)
        re_interface_declaration = self._rx("interface_declaration", flags=re.MULTILINE)

        fallback_types = {}
        for m in re_interface_declaration.finditer(java_code):
            fallback_types[m.group(1)] = "interface"
        for m in re_class_implements.finditer(java_code):
            fallback_types.setdefault(m.group(1), "class_implements_interface")
        for m in re_class_declaration.finditer(java_code):
            fallback_types.setdefault(m.group(1), "class")

        class_or_interface_name = next(iter(fallback_types.keys()), None)
        _super_of_class = {}
        _extends_re = re.compile(r'\bclass\s+(\w+)\s+extends\s+(\w+)')
        for _m_ext in _extends_re.finditer(java_code):
            _super_of_class[_m_ext.group(1)] = _m_ext.group(2)
        parent_class_name = _super_of_class.get(class_or_interface_name)

        # --- Variable / DI types ---
        autowired_fields = {}
        for m in re_autowired_field.finditer(java_code):
            raw_type = m.group(1)
            var_name = m.group(2)
            tname = re.sub(
                r'(&amp;lt;[^&amp;gt]+&amp;gt;|<[^>]+>)', '', raw_type
            ).strip().split('.')[-1]
            autowired_fields[var_name] = tname

        var_types = {}
        locals_from_new = set()
        params_set = set()

        for m in re_var_decl.finditer(java_code):
            raw_type, var_name = m.group(1), m.group(2)
            # Java 8: skip if type is literally 'var' (shouldn't appear, but guard anyway)
            if raw_type.strip() == 'var':
                continue
            tname = re.sub(
                r'(&amp;lt;[^&amp;gt]+&amp;gt;|<[^>]+>)', '', raw_type
            ).strip().split('.')[-1]
            var_types[var_name] = tname

        for m in re_var_new.finditer(java_code):
            var_name, fq_type = m.group(1), m.group(2)
            var_types.setdefault(var_name, fq_type.split('.')[-1])
            locals_from_new.add(var_name)

        for m in re_loose_decl.finditer(java_code):
            raw_type, var_name = m.group(1), m.group(2)
            if var_name in var_types or raw_type.strip() == 'var':
                continue
            tname = re.sub(
                r'(&amp;lt;[^&amp;gt]+&amp;gt;|<[^>]+>)', '', raw_type
            ).strip().split('.')[-1]
            var_types[var_name] = tname

        var_types.update(autowired_fields)

        java_keywords = {"return", "this", "super", "new"} | set(
            self.details.get("control_keywords", [])
        )

        per_method_calls = []

        # Pre-build set of declared method names in this file so unqualified-call
        # lookup is O(1) instead of O(N) re.search per call per block.
        _declared_method_names: set = set()
        if class_or_interface_name:
            _decl_method_re = re.compile(
                r'\b(?:public|private|protected)\b[^{;]*\b([A-Za-z_]\w*)\s*\(',
                re.MULTILINE,
            )
            for _dm in _decl_method_re.finditer(java_code):
                _declared_method_names.add(_dm.group(1))

        def _is_dyn(q: str) -> bool:
            return isinstance(q, str) and ("(" in q or ")" in q)

        def _resolved_call_root(qual: str) -> str:
            """Resolve qualifier variable to declared class for fallback calls."""
            resolved = var_types.get(qual) or autowired_fields.get(qual) or qual
            if isinstance(resolved, str) and '.' in resolved and resolved[0].islower():
                # Keep fallback output compatible with downstream class/path enrichment.
                return resolved.split('.')[-1]
            return resolved

        def _process_block(block_text: str, method_name):
            class _OrderedCallCollector:
                def __init__(self):
                    self._seen = set()
                    self._items = []

                def add(self, item):
                    if item in self._seen:
                        return
                    self._seen.add(item)
                    self._items.append(item)

                def __iter__(self):
                    return iter(self._items)

            filtered = _OrderedCallCollector()

            # Always capture this.method(...) directly, independent of configurable
            # re_simple_call patterns. This covers nested calls in ctor args such as
            # new StatusHistory(..., this.getPurgeDate()).
            for tm in re_this_call.finditer(block_text or ""):
                _m = tm.group(1)
                if not _m:
                    continue
                if class_or_interface_name:
                    filtered.add(f"{class_or_interface_name}.{_m}()")
                else:
                    filtered.add(f"{_m}()")

            for m in re_simple_call.finditer(block_text):
                qual, member = m.group(1), m.group(2)
                if self._is_enum_runtime_accessor(qual, member):
                    continue
                if str(qual).strip().lower() == "super":
                    if parent_class_name:
                        filtered.add(f"{parent_class_name}.{member}()")
                    else:
                        filtered.add(f"{member}()")
                    continue
                if str(qual).strip().lower() == "this":
                    if class_or_interface_name:
                        filtered.add(f"{class_or_interface_name}.{member}()")
                    else:
                        filtered.add(f"{member}()")
                    continue
                if str(qual).strip().lower() in java_keywords:
                    continue
                if _is_dyn(qual):
                    filtered.add(f"{qual}.{member}()")
                elif self._keep_qualified_call(
                    qual, var_types, imports_types, autowired_fields,
                    wildcard_packages, locals_from_new, params_set, package_name,
                ):
                    root = _resolved_call_root(qual)
                    filtered.add(f"{root}.{member}()")

            for m in re_member_access.finditer(block_text):
                qual, member = m.group(1), m.group(2)
                if str(qual).strip().lower() in java_keywords:
                    continue
                if _is_dyn(qual):
                    filtered.add(f"{member}")
                elif self._keep_qualified_call(
                    qual, var_types, imports_types, autowired_fields,
                    wildcard_packages, locals_from_new, params_set, package_name,
                ):
                    root = _resolved_call_root(qual)
                    filtered.add(f"{root}.{member}")

            for m in re_unqualified_call.finditer(block_text):
                member = m.group(1)
                if member in java_keywords:
                    continue
                if method_name and member == method_name:
                    continue
                if class_or_interface_name and member in _declared_method_names:
                    filtered.add(f"{class_or_interface_name}.{member}()")
                elif self.include_unqualified or member in static_members or static_wildcard_classes:
                    filtered.add(f"{member}()")

            for m in re_chain.finditer(block_text):
                root = m.group(1)
                if str(root).strip().lower() in java_keywords:
                    continue
                filtered.add(m.group(0))

            # throw new ...
            for tm in _re_throw_new.finditer(block_text):
                ctor_class = tm.group(1)
                args_block = self._extract_balanced_args(block_text, tm.end() - 1)
                _args_inner = args_block[1:-1] if args_block else ""
                _args_inner = re.sub(r'//.*?$|/\*.*?\*/', '', _args_inner, flags=re.MULTILINE | re.DOTALL)
                has_ctor_args = bool(_args_inner.strip())
                if has_ctor_args:
                    filtered.add(f"{ctor_class}.{ctor_class}()")
                if args_block:
                    for sm in re_simple_call.finditer(args_block):
                        q, mem = sm.group(1), sm.group(2)
                        if str(q).strip().lower() == "this":
                            if class_or_interface_name:
                                filtered.add(f"{class_or_interface_name}.{mem}()")
                            else:
                                filtered.add(f"{mem}()")
                            continue
                        if str(q).strip().lower() in java_keywords:
                            continue
                        if _is_dyn(q):
                            filtered.add(f"{q}.{mem}()")
                        elif self._keep_qualified_call(
                            q, var_types, imports_types, autowired_fields,
                            wildcard_packages, locals_from_new, params_set, package_name,
                        ):
                            root = _resolved_call_root(q)
                            filtered.add(f"{root}.{mem}()")
                    for um in re_unqualified_call.finditer(args_block):
                        mem = um.group(1)
                        if mem not in java_keywords and self.include_unqualified:
                            filtered.add(f"{mem}()")
                    for cm in re_chain.finditer(args_block):
                        filtered.add(cm.group(0))

            # standalone new X(...) — outside throw
            throw_pat = re.compile(r'\bthrow\s+new\s+([A-Za-z_]\w+)\s*\(', re.MULTILINE)
            new_pat = re.compile(r'\bnew\s+([A-Za-z_]\w+)\s*\(', re.MULTILINE)
            throw_positions = {tm.start() for tm in throw_pat.finditer(block_text)}
            for nm in new_pat.finditer(block_text):
                # Skip if this new is part of a throw new (already handled above)
                preceding = block_text[max(0, nm.start() - 10):nm.start()].strip()
                if preceding.endswith('throw'):
                    continue
                ctor_class = nm.group(1)
                args_block = self._extract_balanced_args(block_text, nm.end() - 1)
                _args_inner = args_block[1:-1] if args_block else ""
                _args_inner = re.sub(r'//.*?$|/\*.*?\*/', '', _args_inner, flags=re.MULTILINE | re.DOTALL)
                has_ctor_args = bool(_args_inner.strip())
                if has_ctor_args:
                    filtered.add(f"{ctor_class}.{ctor_class}()")
                if args_block:
                    for sm in re_simple_call.finditer(args_block):
                        q, mem = sm.group(1), sm.group(2)
                        if str(q).strip().lower() == "this":
                            if class_or_interface_name:
                                filtered.add(f"{class_or_interface_name}.{mem}()")
                            else:
                                filtered.add(f"{mem}()")
                            continue
                        if str(q).strip().lower() in java_keywords:
                            continue
                        if _is_dyn(q):
                            filtered.add(f"{q}.{mem}()")
                        elif self._keep_qualified_call(
                            q, var_types, imports_types, autowired_fields,
                            wildcard_packages, locals_from_new, params_set, package_name,
                        ):
                            root = _resolved_call_root(q)
                            filtered.add(f"{root}.{mem}()")
                    for um in re_unqualified_call.finditer(args_block):
                        mem = um.group(1)
                        if mem not in java_keywords and self.include_unqualified:
                            filtered.add(f"{mem}()")
                    for cm in re_chain.finditer(args_block):
                        filtered.add(cm.group(0))

            # Do not emit dynamic terminals as unqualified calls.
            # Chained calls must remain chain-qualified only.
            for dyn in self._extract_dynamic_terminal_methods(block_text):
                continue

            for call in filtered:
                if self._is_enum_runtime_accessor_call(call):
                    continue
                per_method_calls.append({'method_name': method_name, 'object_call': call})

        # Walk method bodies with a balanced signature scan.
        # This avoids greedy cross-method matches from config regexes.
        method_sig_head = re.compile(
            r'''^[ \t]*(?:@\w+(?:\([^)]*\))?\s*)*
                (?:(?:public|private|protected)\s+)?
                (?:static\s+|final\s+|synchronized\s+|native\s+|abstract\s+|default\s+)*
                (?:<[^>{}]+>\s+)?
                (?:[A-Za-z_][\w$.]*(?:\s*<[^>{}]+>)?(?:\s*\[\s*\])?\s+)+
                ([A-Za-z_]\w*)\s*\(
            ''',
            re.MULTILINE | re.VERBOSE,
        )

        def _scan_method_blocks(src_text: str):
            for mh in method_sig_head.finditer(src_text):
                method_name = mh.group(1)
                paren_open = src_text.find('(', mh.end() - 1)
                if paren_open == -1:
                    continue

                # Balance method parameter parentheses.
                pdepth = 0
                paren_close = None
                for pi in range(paren_open, len(src_text)):
                    ch = src_text[pi]
                    if ch == '(':
                        pdepth += 1
                    elif ch == ')':
                        pdepth -= 1
                        if pdepth == 0:
                            paren_close = pi
                            break
                if paren_close is None:
                    continue

                # Skip optional throws clause and whitespace to find body start.
                sig_tail = src_text[paren_close + 1:]
                m_tail = re.match(r'^\s*(?:throws\s+[^\{]+)?\s*\{', sig_tail)
                if not m_tail:
                    continue
                brace_pos = paren_close + 1 + m_tail.group(0).rfind('{')

                brace_count, end_idx = 0, None
                for i in range(brace_pos, len(src_text)):
                    if src_text[i] == '{':
                        brace_count += 1
                    elif src_text[i] == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            end_idx = i
                            break
                if end_idx is None:
                    continue

                yield src_text[mh.start():end_idx + 1], method_name

        for method_text, method_name in _scan_method_blocks(java_code):
            _process_block(method_text, method_name)

        # Constructor bodies
        if class_or_interface_name:
            ctor_pat = re.compile(
                r'(?:public|protected|private)\s+' + re.escape(class_or_interface_name) + r'\s*\([^)]*\)\s*\{',
                re.MULTILINE,
            )
            for cm in ctor_pat.finditer(java_code):
                brace_pos = java_code.find('{', cm.end() - 1)
                if brace_pos == -1:
                    continue
                brace_count, end_idx = 0, None
                for i in range(brace_pos, len(java_code)):
                    if java_code[i] == '{':
                        brace_count += 1
                    elif java_code[i] == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            end_idx = i
                            break
                if end_idx is None:
                    continue
                ctor_text = java_code[cm.start():end_idx + 1]
                _process_block(ctor_text, class_or_interface_name)

        if per_method_calls:
            row_type = fallback_types.get(class_or_interface_name or '', 'Unknown')
            return {
                'type_name': class_or_interface_name or 'Unknown',
                'row_type': row_type,
                'per_method_calls': per_method_calls,
            }

        row_type = fallback_types.get(class_or_interface_name or '', 'Unknown')
        return {
            'type_name': class_or_interface_name or 'Unknown',
            'row_type': row_type,
            'filtered_calls': [],
        }

