# =============================================================================
# FILE: java_property_extractor.py
# PURPOSE: Properties & Variable Value Extraction for Java 8 codebases
#
# CONTAINS:
#   class PropertyExtractorMixin  - mixin providing:
#     extract_method_loc()
#     load_all_properties()
#     extract_application_properties_from_folder()
#     _collapse_inline_assigned_value()
#     _literal_token_to_value()
#     _initializer_to_inline_value()
#     _extract_inline_field_assignments_ast()
#     _extract_method_body()
#     _extract_method_body_by_signature()
#     _build_file_variable_dict()
#     extract_inline_java_variables_as_properties()
#
#   Extracted helper methods (formerly nested closures):
#     _loc_find_annotation_block_start(sig_line_idx, lines)
#     _loc_find_opening_brace_line(from_line, lines)
#     _loc_find_closing_brace_line(open_line, lines)
#     _compose_properties_path(jpath, method_name, include_trailing_dot)
#     _static_block_to_display_value(token, out)
#     _canon_type_name(tname)
#     _node_param_types(params, _canon)
#     _dbg_var_event(event, debug_var_upper, **kwargs)
#     _compose_inline_path(file_name, method_name, include_trailing_dot, resolved_path)
#     _resolve_java_path_impl(raw_file_name, java_folder, rel_no_ext_to_path, stem_to_paths)
#     _resolve_class_java_path_impl(owner, caller_code, caller_file_name,
#                                   java_folder, stem_to_paths,
#                                   _resolve_class_java_path_cache,
#                                   _caller_import_ctx_cache, _import_re)
#     _extract_enum_constant_values(src_code)
#     _get_code_impl(raw_file_name, code_cache, file_path_map, java_folder,
#                    rel_no_ext_to_path, stem_to_paths)
#     _get_or_build_vars_for_source_impl(source_key, file_var_dict,
#                                        enum_const_cache, code_cache,
#                                        file_path_map, java_folder,
#                                        rel_no_ext_to_path, stem_to_paths)
#     _append_rows_for_target_impl(...)
#     _format_value(v)
#
# WHEN TO EDIT THIS FILE:
#   - Changes to property file parsing or @Value/@ConfigurationProperties handling
#   - Changes to inline variable extraction or enum constant resolution
#   - Changes to method LOC counting
#
# DEPENDS ON:
#   config.py                 (compiled regex patterns via self._rx)
#   utils.py                  (utility helpers)
#   language_adapter_base.py  (LanguageAdapter base class)
#   java_ast_parser.py        (AstParserMixin — parse_ast, fallback_parse, etc.)
#   java_project_indexer.py   (ProjectIndexerMixin — _get_java_file_indexes, etc.)
#
# USED BY:
#   java_adapter.py           (assembled into JavaAdapter via multiple inheritance)
# =============================================================================

import os
import re
import html
import json
import javalang
import javalang.tree as jt
import pandas as pd
from pathlib import Path


from utils import strip_generics, _strip_source_comments


class PropertyExtractorMixin:
    """
    Mixin: properties and variable value extraction for Java 8 codebases.
    Provides all methods related to reading application.properties,
    @Value/@ConfigurationProperties annotations, inline field assignments,
    and enum constant values.
    All methods use self and are designed for multiple inheritance
    into JavaAdapter.
    """

    # ------------------------------------------------------------------
    # LOC counter — extracted helpers (formerly nested closures)
    # ------------------------------------------------------------------

    def _loc_find_annotation_block_start(self, sig_line_idx, lines):
        i = sig_line_idx - 2
        if i < 0:
            return None
        paren_balance = 0
        started = False
        start_line = None
        while i >= 0:
            raw = lines[i].rstrip()
            if not raw.strip() and not (started and paren_balance > 0):
                break
            is_anno = bool(re.match(r'^[ \t]*@', raw))
            if not started:
                if is_anno:
                    started = True
                    start_line = i + 1
                    paren_balance = raw.count("(") - raw.count(")")
                else:
                    break
            else:
                if is_anno or paren_balance > 0:
                    start_line = i + 1
                    paren_balance += raw.count("(") - raw.count(")")
                else:
                    break
            i -= 1
        return start_line

    def _loc_find_opening_brace_line(self, from_line, lines):
        in_block_comment = False
        for i in range(from_line - 1, len(lines)):
            line = lines[i]
            j, n = 0, len(line)
            in_string = False
            string_char = None
            while j < n:
                ch = line[j]
                nxt = line[j + 1] if j + 1 < n else ""
                if in_block_comment:
                    if ch == "*" and nxt == "/":
                        in_block_comment = False
                        j += 2
                        continue
                    j += 1
                    continue
                if in_string:
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == string_char:
                        in_string = False
                        string_char = None
                    j += 1
                    continue
                if ch == "/" and nxt == "*":
                    in_block_comment = True
                    j += 2
                    continue
                if ch == "/" and nxt == "/":
                    break
                if ch in ("'", '"'):
                    in_string = True
                    string_char = ch
                    j += 1
                    continue
                if ch == "{":
                    return i + 1
                j += 1
        return None

    def _loc_find_closing_brace_line(self, open_line, lines):
        in_block_comment = False
        depth = 0
        started = False
        for i in range(open_line - 1, len(lines)):
            line = lines[i]
            j, n = 0, len(line)
            in_string = False
            string_char = None
            while j < n:
                ch = line[j]
                nxt = line[j + 1] if j + 1 < n else ""
                if in_block_comment:
                    if ch == "*" and nxt == "/":
                        in_block_comment = False
                        j += 2
                        continue
                    j += 1
                    continue
                if in_string:
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == string_char:
                        in_string = False
                        string_char = None
                    j += 1
                    continue
                if ch == "/" and nxt == "*":
                    in_block_comment = True
                    j += 2
                    continue
                if ch == "/" and nxt == "/":
                    break
                if ch in ("'", '"'):
                    in_string = True
                    string_char = ch
                    j += 1
                    continue
                if ch == "{":
                    depth += 1
                    started = True
                elif ch == "}":
                    depth -= 1
                    if started and depth == 0:
                        return i + 1
                j += 1
        return None

    # ------------------------------------------------------------------
    # LOC counter
    # ------------------------------------------------------------------

    def extract_method_loc(
        self,
        java_file_path: str,
        method_name: str,
        classname=None,
        include_package_private: bool = False,
        count_empty_lines: bool = True,
    ):
        if not java_file_path:
            return None

        try:
            with open(java_file_path, "r", encoding="utf-8") as f:
                code = f.read()
        except Exception:
            try:
                with open(java_file_path, "r", encoding="latin-1") as f:
                    code = f.read()
            except Exception:
                return None

        code = code.replace("\r\n", "\n").replace("\r", "\n")
        lines = code.split("\n")

        access_req = r"(?:public|private|protected)"
        access = rf"(?:{access_req})?" if include_package_private else access_req
        mname_esc = re.escape(method_name)

        method_decl_pat = rf"""
            (?m)
            ^[ \t]*
            {access}[ \t]*
            (?:(?:static|final|abstract|synchronized|native|strictfp)\b[ \t]*)*
            [\w.<>\[\],? \t]+
            \b(?P<mname>{mname_esc})[ \t]*\(
        """

        constructor_decl_pat = None
        if classname and method_name == classname:
            cname_esc = re.escape(classname)
            constructor_decl_pat = rf"""
                (?m)
                ^[ \t]*
                {access}[ \t]*
                (?:(?:static|final|abstract|synchronized|native|strictfp)\b[ \t]*)*
                \b(?P<mname>{cname_esc})[ \t]*\(
            """

        patterns = []
        if constructor_decl_pat:
            patterns.append(re.compile(constructor_decl_pat, re.IGNORECASE | re.VERBOSE))
        patterns.append(re.compile(method_decl_pat, re.IGNORECASE | re.VERBOSE))

        decl_match = None
        for pat in patterns:
            decl_match = pat.search(code)
            if decl_match:
                break
        if not decl_match:
            return None

        sig_line_idx = code.count("\n", 0, decl_match.start("mname")) + 1

        anno_start = self._loc_find_annotation_block_start(sig_line_idx, lines)
        start_line_idx = anno_start if anno_start is not None else sig_line_idx

        brace_open_line = self._loc_find_opening_brace_line(sig_line_idx, lines)
        if brace_open_line is None:
            return 1

        end_line_idx = self._loc_find_closing_brace_line(brace_open_line, lines)
        if end_line_idx is None:
            end_line_idx = len(lines)

        if count_empty_lines:
            return max(1, end_line_idx - start_line_idx + 1)
        else:
            segment = lines[start_line_idx - 1:end_line_idx]
            return max(1, sum(1 for ln in segment if ln.strip()))

    # ------------------------------------------------------------------
    # Properties extraction
    # ------------------------------------------------------------------

    def load_all_properties(self, app_folder, additional_property_refs=None):
        app_folder = Path(app_folder)
        paths = set()
        for p in app_folder.rglob("*.properties"):
            paths.add(p.resolve())

        if additional_property_refs:
            for ref in additional_property_refs:
                ref_norm = self._normalize_ps_ref(ref)
                matches = list(app_folder.rglob(ref_norm))
                if not matches:
                    matches = list(app_folder.rglob(os.path.basename(ref_norm)))
                for m in matches:
                    paths.add(m.resolve())

        props = {}
        for p in sorted(paths, key=str):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    for raw in fh:
                        line = raw.strip()
                        if not line or line.startswith("#") or line.startswith("!"):
                            continue
                        if "=" in line:
                            k, v = line.split("=", 1)
                        elif ":" in line:
                            k, v = line.split(":", 1)
                        else:
                            continue
                        props[k.strip()] = v.strip()
            except Exception as e:
                print(f"Error reading {p}: {e}")
        return props

    # ------------------------------------------------------------------
    # _compose helper for extract_application_properties_from_folder
    # (formerly a nested closure; include_trailing_dot passed explicitly)
    # ------------------------------------------------------------------

    def _compose_properties_path(self, jpath: Path, method_name, include_trailing_dot: bool) -> str:
        base = jpath.stem
        if method_name:
            return f"{base}.{method_name}"
        return f"{base}." if include_trailing_dot else base

    def extract_application_properties_from_folder(
        self,
        app_folder,
        include_filepath: bool = True,
        include_trailing_dot: bool = True,
    ):
        app_folder = Path(app_folder)
        ps_refs = set()
        java_paths = []

        for p in app_folder.rglob("*.java"):
            java_paths.append(p.resolve())
            try:
                txt = p.read_text(encoding="utf-8")
            except Exception:
                try:
                    txt = p.read_text(encoding="latin-1")
                except Exception:
                    txt = ""
            for m in self._irx("re_property_source").finditer(txt):
                ps_refs.add(m.group(1).strip())

        properties_map = self.load_all_properties(app_folder, additional_property_refs=ps_refs)

        debug_named_query = "RequestorConfig.findByRequestorIdAndSubProcessAndKey"

        # Collect all @NamedQuery definitions first so usage sites can be resolved
        # to their JPQL text during createNamedQuery(...) extraction.
        named_query_map = {}
        for jf in java_paths:
            try:
                nq_code = jf.read_text(encoding="utf-8")
            except Exception:
                try:
                    nq_code = jf.read_text(encoding="latin-1")
                except Exception:
                    nq_code = ""
            for nm in self._irx("re_named_query_decl", re.MULTILINE).finditer(nq_code):
                qname = (nm.group(1) or "").strip()
                qtext = (nm.group(2) or "").strip()
                if qname and qname not in named_query_map:
                    named_query_map[qname] = qtext
                    if qname == debug_named_query:
                        print(
                            "[DEBUG][NAMED_QUERY][DECL_FOUND] "
                            f"query={qname} file={jf} value={qtext}"
                        )

        rows = []
        named_query_seen = set()

        for jf in java_paths:
            try:
                code = jf.read_text(encoding="utf-8")
            except Exception:
                try:
                    code = jf.read_text(encoding="latin-1")
                except Exception:
                    code = ""

            method_index_map = self._build_method_index_map(code)

            # @Value
            for item in self._extract_values_with_vars(code):
                key = item["Property"]
                var = item["Variable"]
                actual = properties_map.get(key, "NOT_FOUND")
                method_name = None
                if var:
                    pattern = re.compile(r'\b' + re.escape(var) + r'\b')
                    for mu in pattern.finditer(code, item["span_end"]):
                        method_name = self._find_enclosing_method(method_index_map, mu.start())
                        if method_name:
                            break
                rows.append({
                    "FileName": jf.name.replace(".java", ""),
                    "FilePath": str(jf),
                    "Filename.methodname": self._compose_properties_path(jf, method_name, include_trailing_dot),
                    "Annotation": item["Annotation"],
                    "Property": key,
                    "Variable": var,
                    "method_name": method_name,
                    "Actual Value": actual,
                })

            # @ConfigurationProperties
            for m in self._irx("re_configuration_properties").finditer(code):
                prefix = m.group(1)
                matched = {k: v for k, v in properties_map.items()
                           if k == prefix or k.startswith(prefix + ".")}
                actual = "; ".join(f"{k}={v}" for k, v in matched.items()) if matched else "NOT_FOUND"
                rows.append({
                    "FileName": jf.name.replace(".java", ""),
                    "FilePath": str(jf),
                    "Filename.methodname": self._compose_properties_path(jf, None, include_trailing_dot),
                    "Annotation": "@ConfigurationProperties",
                    "Property": prefix,
                    "Variable": None,
                    "method_name": None,
                    "Actual Value": actual,
                })

            # @PropertySource
            for m in self._irx("re_property_source").finditer(code):
                rows.append({
                    "FileName": jf.name.replace(".java", ""),
                    "FilePath": str(jf),
                    "Filename.methodname": self._compose_properties_path(jf, None, include_trailing_dot),
                    "Annotation": "@PropertySource",
                    "Property": m.group(1),
                    "Variable": None,
                    "method_name": None,
                    "Actual Value": "FILE_REFERENCE",
                })

            # messageSource.getMessage(...)
            for mm in self._irx("re_message_key").finditer(code):
                key = mm.group(1)
                actual = properties_map.get(key, "NOT_FOUND")
                method_name = self._find_enclosing_method(method_index_map, mm.start())
                rows.append({
                    "FileName": jf.name.replace(".java", ""),
                    "FilePath": str(jf),
                    "Filename.methodname": self._compose_properties_path(jf, method_name, include_trailing_dot),
                    "Annotation": "MessageSource",
                    "Property": key,
                    "Variable": None,
                    "method_name": method_name,
                    "Actual Value": actual,
                })

            # Generic method("NamedQuery.Name", ...)
            # Do not hardcode a specific API method name; include any call where
            # the first string argument matches a declared @NamedQuery name.
            for nm in self._irx("re_any_method_first_string_arg", re.MULTILINE).finditer(code):
                method_token = (nm.group(1) or "").strip()
                query_name = (nm.group(2) or "").strip()
                if not query_name or query_name not in named_query_map:
                    continue
                method_name = self._find_enclosing_method(method_index_map, nm.start())
                dedup_key = (str(jf), method_name or "", method_token, query_name)
                if dedup_key in named_query_seen:
                    continue
                named_query_seen.add(dedup_key)
                rows.append({
                    "FileName": jf.name.replace(".java", ""),
                    "FilePath": str(jf),
                    "Filename.methodname": self._compose_properties_path(jf, method_name, include_trailing_dot),
                    "Annotation": "@NamedQuery",
                    "Property": query_name,
                    "Variable": method_token or None,
                    "method_name": method_name,
                    "Actual Value": named_query_map.get(query_name, "NOT_FOUND"),
                })
                if query_name == debug_named_query:
                    print(
                        "[DEBUG][NAMED_QUERY][ROW_ADDED] "
                        f"query={query_name} caller_file={jf} "
                        f"caller_method={method_name} method_token={method_token} "
                        f"value={named_query_map.get(query_name, 'NOT_FOUND')}"
                    )

        df = pd.DataFrame(rows)
        if include_filepath:
            cols = ["FileName", "FilePath", "Filename.methodname", "Annotation",
                    "Property", "Variable", "method_name", "Actual Value"]
        else:
            cols = ["FileName", "Filename.methodname", "Annotation",
                    "Property", "Variable", "method_name", "Actual Value"]
        df = df.reindex(columns=cols)
        if "Actual Value" in df.columns:
            # Do not populate unresolved entries in application.properties output.
            df = df[
                df["Actual Value"].notna()
                & (df["Actual Value"].astype(str).str.strip() != "")
                & (df["Actual Value"].astype(str).str.strip().str.upper() != "NOT_FOUND")
            ]
        if "method_name" in df.columns:
            df = df[df["method_name"].notna()]
        return df

    # ------------------------------------------------------------------
    # Inline Java variable extraction (no separate .properties file)
    # ------------------------------------------------------------------

    def _collapse_inline_assigned_value(self, rhs_expr: str) -> str:
        """Collapse concatenated Java literal expressions into one value string.

        Example:
            "a" + "b" + "c" -> "abc"
        If the expression includes unsupported syntax, returns rhs_expr as-is.
        """
        if not isinstance(rhs_expr, str):
            return ""
        expr = rhs_expr.strip()
        if not expr:
            return ""

        pieces = []
        pos = 0
        for m in self._irx("module___re_inline_value_token", re.DOTALL).finditer(expr):
            gap = expr[pos:m.start()]
            if not re.fullmatch(r'(?:\s*\+\s*)*', gap):
                return expr
            pieces.append(m.group(1) or m.group(2) or m.group(3) or "")
            pos = m.end()

        tail = expr[pos:]
        if not re.fullmatch(r'(?:\s*\+\s*)*', tail):
            return expr

        return "".join(pieces) if pieces else expr

    def _literal_token_to_value(self, token: str) -> str:
        """Normalize a Java literal token to a plain value string."""
        if not isinstance(token, str):
            return ""
        t = token.strip()
        if len(t) >= 2 and ((t[0] == '"' and t[-1] == '"') or (t[0] == "'" and t[-1] == "'")):
            return t[1:-1]
        return t

    def _initializer_to_inline_value(self, init_node, known_constants=None):
        """Extract value from a field initializer.

        Supports:
          - literals
          - literal concatenation with '+'
          - same-class constant references used inside concatenation
        """
        if init_node is None:
            return None

        if known_constants is None:
            known_constants = {}

        if isinstance(init_node, jt.Literal):
            return self._literal_token_to_value(getattr(init_node, "value", ""))

        if isinstance(init_node, jt.MemberReference):
            ref_name = getattr(init_node, "member", None)
            if isinstance(ref_name, str) and ref_name in known_constants:
                return known_constants.get(ref_name)
            return None

        if isinstance(init_node, jt.BinaryOperation):
            op = getattr(init_node, "operator", None)
            if op != "+":
                return None
            left_node = getattr(init_node, "operandl", None)
            right_node = getattr(init_node, "operandr", None)
            if left_node is None:
                left_node = getattr(init_node, "left", None)
            if right_node is None:
                right_node = getattr(init_node, "right", None)
            left_val = self._initializer_to_inline_value(left_node, known_constants=known_constants)
            right_val = self._initializer_to_inline_value(right_node, known_constants=known_constants)
            if left_val is None or right_val is None:
                return None
            return f"{left_val}{right_val}"

        return None

    # ------------------------------------------------------------------
    # _to_display_value helper for _extract_inline_field_assignments_ast
    # (formerly a nested closure inside the static-block loop)
    # ------------------------------------------------------------------

    def _static_block_to_display_value(self, token: str, out: dict) -> str:
        t = str(token or "").strip()
        if not t:
            return ""
        if (len(t) >= 2 and t[0] == '"' and t[-1] == '"') or (len(t) >= 2 and t[0] == "'" and t[-1] == "'"):
            return t[1:-1]
        return out.get(t, t)

    def _extract_inline_field_assignments_ast(self, code: str) -> dict:
        """Extract class-level assignments, including static-block assignments."""
        out = {}
        if not isinstance(code, str) or not code.strip():
            return out

        tree = self.parse_ast(code)
        if tree is None:
            return out

        field_names = set()
        try:
            # Multi-pass resolution so later constants can reference earlier ones,
            # and composite SQL constants can include other constant tokens.
            pending = []
            for _, field_decl in tree.filter(jt.FieldDeclaration):
                for decl in getattr(field_decl, "declarators", []) or []:
                    field_names.add(str(getattr(decl, "name", "") or ""))
                    init = getattr(decl, "initializer", None)
                    if init is None:
                        continue
                    pending.append((decl.name, init))

            progress = True
            while progress and pending:
                progress = False
                still_pending = []
                for name, init in pending:
                    value = self._initializer_to_inline_value(init, known_constants=out)
                    if value is None:
                        still_pending.append((name, init))
                        continue
                    out[name] = str(value).strip()
                    progress = True
                pending = still_pending
        except Exception:
            return {}

        # Capture static-block assignments to class fields such as:
        #   static { TARGET = Collections.unmodifiableMap(localMap); }
        # and resolve map-builder local values from repeated localMap.put(k, v).
        try:
            static_blocks = []
            scan_pos = 0
            code_len = len(code)
            while True:
                m_static = re.search(r'\bstatic\s*\{', code[scan_pos:])
                if not m_static:
                    break
                block_open = scan_pos + m_static.end() - 1
                depth = 0
                block_end = None
                for i in range(block_open, code_len):
                    ch = code[i]
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            block_end = i
                            break
                if block_end is None:
                    break
                static_blocks.append(code[block_open + 1:block_end])
                scan_pos = block_end + 1

            for block in static_blocks:
                local_maps = {}

                map_decl_re = re.compile(
                    r'\b(?:final\s+)?(?:Map|HashMap|LinkedHashMap|TreeMap)\s*<[^>]*>\s*([A-Za-z_]\w*)\s*=\s*new\s+[A-Za-z_]\w*\s*<[^>]*>\s*\(\s*\)\s*;',
                    re.MULTILINE,
                )
                for mm in map_decl_re.finditer(block):
                    local_maps.setdefault(mm.group(1), [])

                put_re = re.compile(
                    r'\b([A-Za-z_]\w*)\s*\.\s*put\s*\(\s*("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[^,]+?)\s*,\s*("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[^\)]+?)\s*\)\s*;',
                    re.MULTILINE,
                )
                for pm in put_re.finditer(block):
                    map_name = pm.group(1)
                    if map_name not in local_maps:
                        continue
                    k = self._static_block_to_display_value(pm.group(2), out)
                    v = self._static_block_to_display_value(pm.group(3), out)
                    local_maps[map_name].append((k, v))

                local_map_values = {
                    name: "{" + ", ".join(['{}={}'.format(k, v) for k, v in pairs]) + "}"
                    for name, pairs in local_maps.items()
                    if pairs
                }

                assign_re = re.compile(r'\b([A-Za-z_]\w*)\s*=\s*([^;]+);', re.MULTILINE)
                for am in assign_re.finditer(block):
                    lhs = am.group(1)
                    rhs = (am.group(2) or "").strip()
                    if lhs not in field_names or not rhs:
                        continue
                    final_rhs = rhs
                    for map_name, map_value in local_map_values.items():
                        final_rhs = re.sub(r'\b' + re.escape(map_name) + r'\b', map_value, final_rhs)
                    out[lhs] = final_rhs
        except Exception:
            pass

        return out

    def _extract_method_body(self, java_text: str, method_name: str) -> str:
        """
        Return the source text of the first method whose name matches
        *method_name* found in *java_text*.  Returns an empty string when
        the method cannot be located.

        Strategy:
          1. Find the method declaration position via _build_method_index_map.
          2. Walk forward from that position counting braces to find the
             matching closing brace — that slice is the method body.
        """
        method_index_map = self._build_method_index_map(java_text)
        start_pos = None
        for pos, name in method_index_map:
            if name == method_name:
                start_pos = pos
                break
        if start_pos is None:
            return ""

        # Advance to the first opening brace of the method body
        brace_start = java_text.find("{", start_pos)
        if brace_start == -1:
            return ""

        depth = 0
        i = brace_start
        n = len(java_text)
        while i < n:
            ch = java_text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return java_text[brace_start: i + 1]
            i += 1
        # Unclosed brace — return whatever we have
        return java_text[brace_start:]

    # ------------------------------------------------------------------
    # _canon / _node_param_types helpers for _extract_method_body_by_signature
    # (formerly nested closures)
    # ------------------------------------------------------------------

    def _canon_type_name(self, tname: str) -> str:
        if not isinstance(tname, str):
            return ""
        t = tname.strip()
        if not t:
            return ""
        t = re.sub(r'\s*<[^>]+>\s*', '', t)
        t = t.replace("...", "[]")
        return t.lower()

    def _node_param_types(self, params) -> tuple:
        out = []

        for p in params or []:
            ptype = getattr(p, "type", None)

            t = self._effective_type_name(ptype) or ""

            dims = getattr(ptype, "dimensions", 0)

            if isinstance(dims, (list, tuple)):
                dims = len(dims)
            elif dims is None:
                dims = 0
            else:
                try:
                    dims = int(dims)
                except Exception:
                    dims = 0

            t += "[]" * dims

            if getattr(p, "varargs", False):
                t += "[]"

            out.append(self._canon_type_name(t))

        return tuple(out)

    def _extract_method_body_by_signature(self, java_text: str, method_name: str, parameter_types_hint: str = None) -> str:
        """Return method/constructor body using AST + optional parameter-type hint.

        The hint uses the same semicolon-separated format as Parameter_Types
        in lineage rows. Matching is best-effort and overload-aware.
        """
        if not isinstance(java_text, str) or not java_text.strip() or not method_name:
            return ""

        tree = self.parse_ast(java_text)
        if tree is None:
            return self._extract_method_body(java_text, method_name)

        hint_tuple = tuple(
            self._canon_type_name(x)
            for x in str(parameter_types_hint or "").split(";")
            if str(x).strip()
        )

        candidates = []

        for _, m in tree.filter(jt.MethodDeclaration):
            if getattr(m, "name", None) == method_name:
                candidates.append((m, self._node_param_types(getattr(m, "parameters", []))))

        for _, c in tree.filter(jt.ConstructorDeclaration):
            if getattr(c, "name", None) == method_name:
                candidates.append((c, self._node_param_types(getattr(c, "parameters", []))))

        if not candidates:
            return self._extract_method_body(java_text, method_name)

        chosen = None
        if hint_tuple:
            for node, ptypes in candidates:
                if ptypes == hint_tuple:
                    chosen = node
                    break
            if chosen is None:
                for node, ptypes in candidates:
                    if len(ptypes) == len(hint_tuple):
                        chosen = node
                        break

        if chosen is None:
            chosen = candidates[0][0]

        try:
            pos = getattr(chosen, "position", None)
            if pos and pos[0]:
                lines = java_text.splitlines(True)
                offsets = self._get_line_offsets(java_text, lines)
                start_line = max(pos[0] - 1, 0)
                start_offset = offsets[start_line] if start_line < len(offsets) else 0
                brace_start = java_text.find("{", start_offset)
                if brace_start == -1:
                    return ""
                depth = 0
                for i in range(brace_start, len(java_text)):
                    ch = java_text[i]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            return java_text[brace_start:i + 1]
                return java_text[brace_start:]
        except Exception:
            pass

        return self._extract_method_body(java_text, method_name)

    def _build_file_variable_dict(self, java_folder: str, file_names) -> dict:
        """
        For each unique file_name from Cleaned_AST_Details, parse the
        corresponding .java file and extract all inline variable=value
        assignments.

        Parameters
        ----------
        java_folder : str
            Root folder of the Java codebase.
        file_names : iterable of str
            Unique values from the ``file_name`` column of Cleaned_AST_Details.
            Each entry is a full or relative path such as
            ``path/to/MyClass.java`` or a stem like ``MyClass``.

        Returns
        -------
        dict
            Structure::

                {
                    "<file_name_as_given>": {
                        "<variable_name>": "<value>",
                        ...
                    },
                    ...
                }
        """
        java_folder = Path(java_folder)
        java_idx = self._get_java_file_indexes(java_folder)
        stem_to_paths = java_idx.get("stem_to_paths", {})
        rel_no_ext_to_path = java_idx.get("rel_no_ext_to_path", {})
        result = {}

        for raw_file_name in file_names:
            raw_file_name = str(raw_file_name)

            # Resolve the actual .java file path.
            # file_name column may be an absolute path, relative path, or stem.
            candidate = Path(raw_file_name)
            if candidate.is_file():
                java_path = candidate.resolve()
            else:
                # Try treating it as a path relative to java_folder
                rel = java_folder / raw_file_name
                if rel.is_file():
                    java_path = rel.resolve()
                else:
                    # Fall back: search by filename stem inside the folder
                    stem = candidate.stem if candidate.suffix else candidate.name
                    rel_key = str(candidate).replace("\\", "/")
                    if rel_key.lower().endswith(".java"):
                        rel_key = rel_key[:-5]
                    java_path = rel_no_ext_to_path.get(rel_key.lower())
                    if java_path is None:
                        matches = stem_to_paths.get(stem.lower(), [])
                        java_path = matches[0] if matches else None
                    if java_path is None:
                        # Nothing found — skip
                        result[raw_file_name] = {}
                        continue
                    java_path = Path(java_path).resolve()

            # Read the file
            try:
                code = java_path.read_text(encoding="utf-8")
            except Exception:
                try:
                    code = java_path.read_text(encoding="latin-1")
                except Exception:
                    result[raw_file_name] = {}
                    continue

            # Extract inline variable assignments from class fields only.
            # This avoids method-local values like hashCode()'s prime/result.
            var_dict = self._extract_inline_field_assignments_ast(code)

            # Fallback regex path only when AST yields nothing.
            if not var_dict:
                for m in self._irx("module___re_inline_var_assign", re.MULTILINE | re.DOTALL).finditer(code):
                    var_name = m.group(1)
                    rhs_expr = m.group(2) or ""
                    value = self._collapse_inline_assigned_value(rhs_expr)
                    var_dict[var_name] = value.strip()

            result[raw_file_name] = var_dict

        return result

    # ------------------------------------------------------------------
    # Helpers for extract_inline_java_variables_as_properties
    # (formerly nested closures; outer-scope deps become explicit params)
    # ------------------------------------------------------------------

    def _dbg_var_event(self, event: str, debug_var_upper: str, **kwargs):
        if not debug_var_upper:
            return
        payload = " | ".join(["{}={}".format(k, kwargs[k]) for k in sorted(kwargs.keys())])
        print("[DEBUG][INLINE_PROPERTY][{}] {}".format(event, payload))

    def _compose_inline_path(
        self,
        file_name: str,
        method_name,
        include_trailing_dot: bool,
        resolved_path=None,
    ) -> str:
        base_path = str(Path(resolved_path).with_suffix("")) if resolved_path else str(Path(file_name).with_suffix(""))
        if method_name:
            return f"{base_path}.{method_name}"
        return f"{base_path}." if include_trailing_dot else base_path

    def _resolve_java_path_impl(
        self,
        raw_file_name: str,
        java_folder,
        rel_no_ext_to_path: dict,
        stem_to_paths: dict,
    ):
        """Resolve a raw file token to an actual .java Path when possible."""
        candidate = Path(str(raw_file_name))
        if candidate.is_file():
            return candidate.resolve()

        # Try treating as path relative to java_folder
        rel = java_folder / str(raw_file_name)
        if rel.is_file():
            return rel.resolve()

        # Try appending .java for explicit path-like values without suffix
        if not candidate.suffix:
            c_java = Path(str(candidate) + ".java")
            if c_java.is_file():
                return c_java.resolve()
            rel_java = java_folder / (str(raw_file_name) + ".java")
            if rel_java.is_file():
                return rel_java.resolve()

        # Fall back: search by filename stem inside the folder
        stem = candidate.stem if candidate.suffix else candidate.name
        rel_key = str(candidate).replace("\\", "/")
        if rel_key.lower().endswith(".java"):
            rel_key = rel_key[:-5]
        rel_hit = rel_no_ext_to_path.get(rel_key.lower())
        if rel_hit is not None:
            return Path(rel_hit).resolve()

        stem_hits = stem_to_paths.get(stem.lower(), [])
        if stem_hits:
            return Path(stem_hits[0]).resolve()
        return None

    def _resolve_class_java_path_impl(
        self,
        owner: str,
        caller_code: str,
        caller_file_name: str,
        java_folder,
        stem_to_paths: dict,
        _resolve_class_java_path_cache: dict,
        _caller_import_ctx_cache: dict,
        _import_re,
    ):
        """Resolve class source path from explicit import, same package, wildcard or FQN."""
        owner = str(owner or "").strip()
        if not owner:
            return None

        _cache_key = (owner, str(caller_file_name or ""))
        if _cache_key in _resolve_class_java_path_cache:
            return _resolve_class_java_path_cache[_cache_key]

        _ctx_key = str(caller_file_name or "")
        _ctx = _caller_import_ctx_cache.get(_ctx_key)
        if _ctx is None:
            imports = [x.strip() for x in _import_re.findall(caller_code or "") if x]
            import_map = {
                imp.split(".")[-1]: imp
                for imp in imports
                if not imp.endswith(".*")
            }
            wildcard_imports = [imp[:-2] for imp in imports if imp.endswith(".*")]
            pkg_m = re.search(r'^\s*package\s+([\w.]+)\s*;', caller_code or "", re.MULTILINE)
            same_pkg = pkg_m.group(1) if pkg_m else ""
            _ctx = (import_map, wildcard_imports, same_pkg)
            _caller_import_ctx_cache[_ctx_key] = _ctx

        import_map, wildcard_imports, same_pkg = _ctx

        # FQN directly used as owner.
        if "." in owner and owner[0].islower():
            fqn_rel = Path(*owner.split(".")).with_suffix(".java")
            fqn_abs = java_folder / fqn_rel
            if fqn_abs.is_file():
                _out = fqn_abs.resolve()
                _resolve_class_java_path_cache[_cache_key] = _out
                return _out
            fqn_name = owner.split(".")[-1]
            fqn_hits = stem_to_paths.get(fqn_name.lower(), [])
            if fqn_hits:
                _out = Path(fqn_hits[0]).resolve()
                _resolve_class_java_path_cache[_cache_key] = _out
                return _out

        imp_fqn = import_map.get(owner)
        if imp_fqn:
            imp_rel = Path(*imp_fqn.split(".")).with_suffix(".java")
            imp_abs = java_folder / imp_rel
            if imp_abs.is_file():
                _out = imp_abs.resolve()
                _resolve_class_java_path_cache[_cache_key] = _out
                return _out

        # Same package fallback.
        if same_pkg:
            same_pkg_fqn = same_pkg + "." + owner
            same_pkg_rel = Path(*same_pkg_fqn.split(".")).with_suffix(".java")
            same_pkg_abs = java_folder / same_pkg_rel
            if same_pkg_abs.is_file():
                _out = same_pkg_abs.resolve()
                _resolve_class_java_path_cache[_cache_key] = _out
                return _out

        # Wildcard imports fallback.
        for _pkg in wildcard_imports:
            wfqn = _pkg + "." + owner
            wrel = Path(*wfqn.split(".")).with_suffix(".java")
            wabs = java_folder / wrel
            if wabs.is_file():
                _out = wabs.resolve()
                _resolve_class_java_path_cache[_cache_key] = _out
                return _out

        # Last fallback by filename.
        owner_hits = stem_to_paths.get(owner.lower(), [])
        if owner_hits:
            _out = Path(owner_hits[0]).resolve()
            _resolve_class_java_path_cache[_cache_key] = _out
            return _out
        _resolve_class_java_path_cache[_cache_key] = None
        return None

    def _extract_enum_constant_values(self, src_code: str) -> dict:
        """Extract enum constants and their display values from Java source.

        Example:
          SENT_TO_GPS("Sent to GPS"),
        becomes:
                        {
                            "SENT_TO_GPS": "SENT_TO_GPS(\"Sent to GPS\")",
                            "DISPLAY_SENT_TO_GPS": "Sent to GPS"
                        }
        """
        if not isinstance(src_code, str) or not src_code.strip():
            return {}

        out = {}

        # Resolve local constants such as:
        #   private static final String DISPLAY_SENT_TO_GPS = "Sent to GPS";
        const_value_map = {}
        const_decl_pat = re.compile(
            r'(?m)^\s*(?:(?:public|private|protected)\s+)?'
            r'(?:(?:static|final|transient|volatile)\s+)*'
            r'[A-Za-z_][\w$.<>,\[\]? \t]*\s+'
            r'([A-Z][A-Z0-9_]*)\s*=\s*'
            r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')\s*;'
        )
        for kvm in const_decl_pat.finditer(src_code):
            ckey = (kvm.group(1) or "").strip()
            raw_val = (kvm.group(2) or "").strip()
            if not ckey:
                continue
            if len(raw_val) >= 2 and raw_val[0] in ('"', "'") and raw_val[-1] == raw_val[0]:
                raw_val = raw_val[1:-1]
            const_value_map[ckey] = raw_val

        # Capture enum headers and parse the constant list up to ';' or first method/field.
        enum_hdr = re.compile(r'\benum\s+[A-Za-z_]\w*\s*\{', re.MULTILINE)
        for eh in enum_hdr.finditer(src_code):
            body_start = src_code.find('{', eh.end() - 1)
            if body_start == -1:
                continue

            depth = 0
            body_end = None
            for i in range(body_start, len(src_code)):
                ch = src_code[i]
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        body_end = i
                        break
            if body_end is None:
                continue

            enum_body = src_code[body_start + 1:body_end]
            const_section = enum_body.split(';', 1)[0]

            # Match constants with optional constructor args.
            # NAME("x"), NAME2(10), NAME3,
            const_pat = re.compile(
                r'\b([A-Z][A-Z0-9_]*)\b\s*(?:\(([^)]*)\))?\s*(?:,|$)',
                re.MULTILINE,
            )
            for cm in const_pat.finditer(const_section):
                cname = (cm.group(1) or "").strip()
                cargs = (cm.group(2) or "").strip()
                if not cname:
                    continue

                # Keep enum constant as raw constructor expression.
                if cargs:
                    out[cname] = "{}({})".format(cname, cargs)
                else:
                    out[cname] = cname

        # Also expose class/enum-level constants as resolved literal values.
        # Example: DISPLAY_SENT_TO_GPS -> Sent to GPS
        for _ckey, _cval in const_value_map.items():
            out.setdefault(_ckey, str(_cval))

        return out

    def _get_code_impl(
        self,
        raw_file_name: str,
        code_cache: dict,
        file_path_map: dict,
        java_folder,
        rel_no_ext_to_path: dict,
        stem_to_paths: dict,
    ) -> str:
        if raw_file_name in code_cache:
            return code_cache[raw_file_name]
        java_path = file_path_map.get(raw_file_name)
        # Support both lineage file keys and resolved absolute java paths.
        if java_path is None:
            candidate = Path(str(raw_file_name))
            if candidate.is_file():
                java_path = candidate.resolve()
            else:
                java_path = self._resolve_java_path_impl(str(raw_file_name), java_folder, rel_no_ext_to_path, stem_to_paths)
        if java_path is None:
            code_cache[raw_file_name] = ""
            return ""
        try:
            code = java_path.read_text(encoding="utf-8")
        except Exception:
            try:
                code = java_path.read_text(encoding="latin-1")
            except Exception:
                code = ""
        code_cache[raw_file_name] = code
        return code

    def _get_or_build_vars_for_source_impl(
        self,
        source_key: str,
        file_var_dict: dict,
        enum_const_cache: dict,
        code_cache: dict,
        file_path_map: dict,
        java_folder,
        rel_no_ext_to_path: dict,
        stem_to_paths: dict,
    ) -> dict:
        """Return parsed variable dict for a source file key/path, with caching."""
        if source_key in file_var_dict:
            return file_var_dict.get(source_key, {}) or {}

        src_code = self._get_code_impl(source_key, code_cache, file_path_map, java_folder, rel_no_ext_to_path, stem_to_paths)
        if not src_code:
            file_var_dict[source_key] = {}
            return {}

        vars_map = self._extract_inline_field_assignments_ast(src_code) or {}
        if not vars_map:
            for m_iv in self._irx("module___re_inline_var_assign", re.MULTILINE | re.DOTALL).finditer(src_code):
                vars_map[m_iv.group(1)] = self._collapse_inline_assigned_value(m_iv.group(2) or "").strip()

        enum_vars = enum_const_cache.get(source_key)
        if enum_vars is None:
            enum_vars = self._extract_enum_constant_values(src_code)
            enum_const_cache[source_key] = enum_vars
        if enum_vars:
            vars_map.update(enum_vars)

        file_var_dict[source_key] = vars_map
        return vars_map

    def _append_rows_for_target_impl(
        self,
        raw_file_name: str,
        method_name: str,
        parameter_types_hint: str,
        rows: list,
        file_var_dict: dict,
        global_var_dict: dict,
        code_cache: dict,
        file_path_map: dict,
        method_body_cache: dict,
        class_to_raw_files: dict,
        _identifier_re,
        _qualified_access_re,
        _import_static_re,
        java_folder,
        rel_no_ext_to_path: dict,
        stem_to_paths: dict,
        _resolve_class_java_path_cache: dict,
        _caller_import_ctx_cache: dict,
        _import_re,
        debug_var_upper: str,
        include_trailing_dot: bool,
    ):
        local_var_dict = file_var_dict.get(raw_file_name, {})
        if not local_var_dict and not global_var_dict:
            return

        code = self._get_code_impl(raw_file_name, code_cache, file_path_map, java_folder, rel_no_ext_to_path, stem_to_paths)
        if not code:
            return

        _mb_key = (str(raw_file_name), str(method_name), str(parameter_types_hint or ""))
        method_body = method_body_cache.get(_mb_key)
        if method_body is None:
            method_body = self._extract_method_body_by_signature(
                code,
                method_name,
                parameter_types_hint=parameter_types_hint,
            )
            method_body_cache[_mb_key] = method_body
        if not method_body:
            return

        _method_identifiers = set(_identifier_re.findall(method_body))

        java_path = file_path_map.get(raw_file_name)
        file_stem = Path(raw_file_name).stem

        # Use class-level variables from the same file first, then
        # fallback to variables declared in other scanned files.
        # Method-local declarations are still excluded because both maps
        # are built from field initializers only.
        effective_var_dict = dict(global_var_dict)
        effective_var_dict.update(local_var_dict)

        # Handle explicit qualified access: ClassName.VAR
        # Resolve VAR from the referenced class file when available.
        qualified_hits = []
        qualified_seen = set()
        for qm in _qualified_access_re.finditer(method_body):
            owner = qm.group(1)
            qvar = qm.group(2)
            owner_sources = class_to_raw_files.get(owner.lower(), [])
            for src_raw in owner_sources:
                src_vars = file_var_dict.get(src_raw, {}) or {}
                if qvar in src_vars:
                    _qkey = (owner, qvar)
                    if _qkey not in qualified_seen:
                        qualified_seen.add(_qkey)
                        qualified_hits.append((owner, qvar, src_vars[qvar]))
                    break

            if (owner, qvar) in qualified_seen:
                continue

            # Owner class may not be in reachable files; resolve via imports/FQN.
            owner_path = self._resolve_class_java_path_impl(
                owner, code, raw_file_name,
                java_folder, stem_to_paths,
                _resolve_class_java_path_cache,
                _caller_import_ctx_cache,
                _import_re,
            )
            if owner_path is not None:
                owner_raw = str(owner_path)
                owner_vars = self._get_or_build_vars_for_source_impl(
                    owner_raw, file_var_dict, {},
                    code_cache, file_path_map, java_folder,
                    rel_no_ext_to_path, stem_to_paths,
                )
                if qvar in owner_vars:
                    _qkey = (owner, qvar)
                    if _qkey not in qualified_seen:
                        qualified_seen.add(_qkey)
                        qualified_hits.append((owner, qvar, owner_vars[qvar]))

        # Handle static imports: import static a.b.C.CONSTANT;
        # Method may reference CONSTANT directly without ClassName prefix.
        static_import_hits = []
        for imp_static in _import_static_re.findall(code):
            imp_static = (imp_static or "").strip()
            if not imp_static:
                continue
            if imp_static.endswith(".*"):
                owner_fqn = imp_static[:-2]
                owner_cls = owner_fqn.split(".")[-1]
                owner_path = self._resolve_class_java_path_impl(
                    owner_fqn, code, raw_file_name,
                    java_folder, stem_to_paths,
                    _resolve_class_java_path_cache,
                    _caller_import_ctx_cache,
                    _import_re,
                )
                if owner_path is None:
                    owner_path = self._resolve_class_java_path_impl(
                        owner_cls, code, raw_file_name,
                        java_folder, stem_to_paths,
                        _resolve_class_java_path_cache,
                        _caller_import_ctx_cache,
                        _import_re,
                    )
                if owner_path is None:
                    continue
                owner_raw = str(owner_path)
                owner_vars = self._get_or_build_vars_for_source_impl(
                    owner_raw, file_var_dict, {},
                    code_cache, file_path_map, java_folder,
                    rel_no_ext_to_path, stem_to_paths,
                )
                for svar, sval in owner_vars.items():
                    if str(svar) in _method_identifiers:
                        static_import_hits.append((owner_cls, str(svar), str(sval)))
                continue

            parts = imp_static.split(".")
            if len(parts) < 2:
                continue
            svar = parts[-1]
            owner_fqn = ".".join(parts[:-1])
            owner_cls = owner_fqn.split(".")[-1]

            if svar not in _method_identifiers:
                continue

            owner_path = self._resolve_class_java_path_impl(
                owner_fqn, code, raw_file_name,
                java_folder, stem_to_paths,
                _resolve_class_java_path_cache,
                _caller_import_ctx_cache,
                _import_re,
            )
            if owner_path is None:
                owner_path = self._resolve_class_java_path_impl(
                    owner_cls, code, raw_file_name,
                    java_folder, stem_to_paths,
                    _resolve_class_java_path_cache,
                    _caller_import_ctx_cache,
                    _import_re,
                )
            if owner_path is None:
                continue

            owner_raw = str(owner_path)
            owner_vars = self._get_or_build_vars_for_source_impl(
                owner_raw, file_var_dict, {},
                code_cache, file_path_map, java_folder,
                rel_no_ext_to_path, stem_to_paths,
            )
            if svar in owner_vars:
                static_import_hits.append((owner_cls, svar, owner_vars[svar]))

        if static_import_hits:
            dedup = []
            seen = set()
            for item in static_import_hits:
                if item in seen:
                    continue
                seen.add(item)
                dedup.append(item)
            static_import_hits = dedup

        static_import_var_names = {name for _, name, _ in static_import_hits}

        for owner, qvar, qval in static_import_hits:
            if debug_var_upper and str(qvar).upper() == debug_var_upper:
                self._dbg_var_event(
                    "STATIC_IMPORT_HIT",
                    debug_var_upper,
                    variable=qvar,
                    owner=owner,
                    value=qval,
                    caller_file=raw_file_name,
                    method=method_name,
                )
            rows.append({
                "FileName":             file_stem,
                "FilePath":             str(java_path) if java_path else raw_file_name,
                "Filename.methodname":  self._compose_inline_path(raw_file_name, method_name, include_trailing_dot, java_path),
                "Annotation":           "InlineVariable",
                "Property":             qvar,
                "Variable":             f"{owner}.{qvar}",
                "method_name":          method_name,
                "Actual Value":         qval,
            })

        qualified_var_names = {qv for _, qv, _ in qualified_hits}
        qualified_var_names.update(static_import_var_names)

        for owner, qvar, qval in qualified_hits:
            if debug_var_upper and str(qvar).upper() == debug_var_upper:
                self._dbg_var_event(
                    "QUALIFIED_HIT",
                    debug_var_upper,
                    variable=qvar,
                    owner=owner,
                    value=qval,
                    caller_file=raw_file_name,
                    method=method_name,
                )
            rows.append({
                "FileName":             file_stem,
                "FilePath":             str(java_path) if java_path else raw_file_name,
                "Filename.methodname":  self._compose_inline_path(raw_file_name, method_name, include_trailing_dot, java_path),
                "Annotation":           "InlineVariable",
                "Property":             qvar,
                "Variable":             f"{owner}.{qvar}",
                "method_name":          method_name,
                "Actual Value":         qval,
            })

        # Variable matching is intentionally case-sensitive.
        for var_name, var_value in effective_var_dict.items():
            # If explicitly referenced as ClassName.var in this method,
            # keep that owner-resolved value and skip generic fallback.
            if var_name in qualified_var_names:
                continue
            if var_name in _method_identifiers:
                if debug_var_upper and str(var_name).upper() == debug_var_upper:
                    self._dbg_var_event(
                        "UNQUALIFIED_HIT",
                        debug_var_upper,
                        variable=var_name,
                        value=var_value,
                        caller_file=raw_file_name,
                        method=method_name,
                    )
                rows.append({
                    "FileName":             file_stem,
                    "FilePath":             str(java_path) if java_path else raw_file_name,
                    "Filename.methodname":  self._compose_inline_path(raw_file_name, method_name, include_trailing_dot, java_path),
                    "Annotation":           "InlineVariable",
                    "Property":             var_name,
                    "Variable":             var_name,
                    "method_name":          method_name,
                    "Actual Value":         var_value,
                })

    def _format_value(self, v) -> str:
        s = str(v or "").strip()
        if s.startswith("{") and s.endswith("}") and "=" in s:
            pairs = []
            for chunk in s[1:-1].split(","):
                if "=" not in chunk:
                    continue
                k, vv = chunk.split("=", 1)
                pairs.append((k.strip(), vv.strip()))
            if pairs:
                return json.dumps({k: vv for k, vv in pairs}, ensure_ascii=True)
        return s

    def extract_inline_java_variables_as_properties(
        self,
        java_folder: str,
        df_cleaned_ast,
        include_filepath: bool = True,
        include_trailing_dot: bool = True,
    ):
        """
        For codebases that store configuration values directly inside Java
        source files (no separate .properties file), this method:

        1. Reads the ``file_name`` column of *df_cleaned_ast*
           (the Cleaned_AST_Details sheet) to get the list of Java files.
          2. Parses each file and builds a per-file dictionary of
              ``variable → value`` for every inline assignment found.
          3. Builds one global variable dictionary by combining all per-file
              dictionaries from the ``file_name`` set.
        3. For every unique ``(file_name, method_name)`` pair in the sheet,
              extracts the method body and checks which variables from the
              combined dictionary are referenced inside that method.
              If a variable is present in both the same file and another file,
              the same-file value is preferred.
          4. Emits one output row per matched variable, using the same column
           layout as ``extract_application_properties_from_folder`` so the
           two DataFrames can be concatenated and written to the same
           ``application.properties`` Excel sheet.

        Parameters
        ----------
        java_folder : str
            Root directory of the Java codebase.
        df_cleaned_ast : pd.DataFrame
            The Cleaned_AST_Details DataFrame (must have at least
            ``file_name`` and ``method_name`` columns).
        include_filepath : bool
            When True, the ``FilePath`` column is included in the output.
        include_trailing_dot : bool
            When True, ``Filename.methodname`` ends with a trailing dot when
            no method name is available.

        Returns
        -------
        pd.DataFrame
            Same columns as ``extract_application_properties_from_folder``.
        """
        debug_var_name = str(
            (self.details or {}).get("debug_property_variable")
            or os.environ.get("LINEAGE_DEBUG_PROPERTY_VARIABLE")
            or ""
        ).strip()
        debug_var_upper = debug_var_name.upper() if debug_var_name else ""

        java_folder = Path(java_folder)
        java_idx = self._get_java_file_indexes(java_folder)
        stem_to_paths = java_idx.get("stem_to_paths", {})
        rel_no_ext_to_path = java_idx.get("rel_no_ext_to_path", {})

        _import_re = re.compile(r'^\s*import\s+([\w.]+)\s*;', re.MULTILINE)
        _import_static_re = re.compile(r'^\s*import\s+static\s+([\w.]+)\s*;', re.MULTILINE)
        _identifier_re = re.compile(r'\b[A-Za-z_]\w*\b')
        _qualified_access_re = re.compile(r'\b([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\b')
        _resolve_class_java_path_cache = {}
        _caller_import_ctx_cache = {}

        # ── Step 1: collect unique file names ──────────────────────────
        if "file_name" not in df_cleaned_ast.columns:
            return pd.DataFrame()

        unique_file_names = df_cleaned_ast["file_name"].dropna().unique().tolist()

        # ── Step 2: build per-file variable dictionaries ───────────────
        # IMPORTANT: dictionaries are built only from file_name values.
        file_var_dict = self._build_file_variable_dict(java_folder, unique_file_names)
        # Expose for callers to reuse (avoids rebuilding in export stage).
        self._last_inline_file_var_dict = file_var_dict

        # Build one global dictionary from all file_name files.
        # If duplicate variable names exist across files, the later file in
        # unique_file_names order overwrites the previous global value.
        global_var_dict = {}
        for _fname in unique_file_names:
            _vars = file_var_dict.get(_fname, {})
            if _vars:
                global_var_dict.update(_vars)

        # ── Step 3: resolve actual .java paths for method body lookup ──
        # Build a map: raw_file_name → resolved Path (reuse logic from above)
        file_path_map = {}
        for raw_file_name in unique_file_names:
            resolved = self._resolve_java_path_impl(str(raw_file_name), java_folder, rel_no_ext_to_path, stem_to_paths)
            if resolved is not None:
                file_path_map[raw_file_name] = resolved

        # Build class-name -> file tokens map so qualified usages like
        # ClassName.VAR can resolve VAR from that class's file dictionary.
        class_to_raw_files = {}
        for raw_file_name in unique_file_names:
            _raw = str(raw_file_name)
            _stem_raw = Path(_raw).stem.lower()
            class_to_raw_files.setdefault(_stem_raw, [])
            if _raw not in class_to_raw_files[_stem_raw]:
                class_to_raw_files[_stem_raw].append(_raw)

            _resolved = file_path_map.get(raw_file_name)
            if _resolved is not None:
                _stem_resolved = _resolved.stem.lower()
                class_to_raw_files.setdefault(_stem_resolved, [])
                if _raw not in class_to_raw_files[_stem_resolved]:
                    class_to_raw_files[_stem_resolved].append(_raw)

        # Cache for file source code (avoid re-reading the same file)
        code_cache = {}
        enum_const_cache = {}
        method_body_cache = {}

        # ── Step 4: iterate targets and extract variable usages ────────
        rows = []

        # Work with unique (file_name, method_name) pairs to avoid
        # duplicate scanning of the same method body.
        pair_cols = ["file_name", "method_name", "Parameter_Types"]
        available_cols = [c for c in pair_cols if c in df_cleaned_ast.columns]
        if "method_name" not in available_cols:
            return pd.DataFrame()

        unique_pairs = (
            df_cleaned_ast[available_cols]
            .dropna(subset=["method_name"])
            .drop_duplicates()
        )

        for pair_row in unique_pairs.to_dict("records"):
            raw_file_name = str(pair_row.get("file_name"))
            method_name   = str(pair_row.get("method_name"))
            param_types   = str(pair_row.get("Parameter_Types") or "")
            self._append_rows_for_target_impl(
                raw_file_name, method_name, param_types,
                rows,
                file_var_dict,
                global_var_dict,
                code_cache,
                file_path_map,
                method_body_cache,
                class_to_raw_files,
                _identifier_re,
                _qualified_access_re,
                _import_static_re,
                java_folder,
                rel_no_ext_to_path,
                stem_to_paths,
                _resolve_class_java_path_cache,
                _caller_import_ctx_cache,
                _import_re,
                debug_var_upper,
                include_trailing_dot,
            )

        # ── Step 5: build DataFrame with standard column layout ─────────
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.drop_duplicates()

            if "Actual Value" in df.columns:
                df["Actual Value"] = df["Actual Value"].apply(self._format_value)

            # Persist target constant values for quick debugging outside Excel.
            try:
                _target_prop = "QUERY_WITH_COUNTER_PARTY_CON"
                _target_df = df[df["Property"].astype(str) == _target_prop]
                if not _target_df.empty:
                    out_txt = Path(os.environ.get("LINEAGE_DEBUG_QUERY_VALUE_TXT", "query_with_counter_party_con_value.txt"))
                    lines = []
                    lines.append("Property={}".format(_target_prop))
                    for _rec in _target_df[["FilePath", "Filename.methodname", "Variable", "Actual Value"]].to_dict("records"):
                        lines.append("FilePath={}".format(_rec.get("FilePath", "")))
                        lines.append("Filename.methodname={}".format(_rec.get("Filename.methodname", "")))
                        lines.append("Variable={}".format(_rec.get("Variable", "")))
                        lines.append("Actual Value={}".format(_rec.get("Actual Value", "")))
                        lines.append("-" * 80)
                    out_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
            except Exception:
                pass

            if debug_var_upper and "Property" in df.columns:
                _match_df = df[df["Property"].astype(str).str.upper() == debug_var_upper]
                self._dbg_var_event("FINAL_ROWS", debug_var_upper, count=len(_match_df))
                for _rec in _match_df[["FilePath", "Filename.methodname", "Property", "Variable", "Actual Value"]].to_dict("records"):
                    self._dbg_var_event(
                        "FINAL_ROW",
                        debug_var_upper,
                        file_path=_rec.get("FilePath"),
                        filename_method=_rec.get("Filename.methodname"),
                        property=_rec.get("Property"),
                        variable=_rec.get("Variable"),
                        actual_value=_rec.get("Actual Value"),
                    )
        if include_filepath:
            cols = ["FileName", "FilePath", "Filename.methodname", "Annotation",
                    "Property", "Variable", "method_name", "Actual Value"]
        else:
            cols = ["FileName", "Filename.methodname", "Annotation",
                    "Property", "Variable", "method_name", "Actual Value"]
        df = df.reindex(columns=cols)
        return df
