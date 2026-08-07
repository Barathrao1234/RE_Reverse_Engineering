
# import os
# import re
# import html
# import javalang
# import javalang.tree as jt
# import pandas as pd
# from pathlib import Path

# from method_lineage_generation import LanguageAdapter

# # ---------------------------------------------------------------------------
# # Module-level compiled patterns (Java 8 safe — no var, no records, etc.)
# # ---------------------------------------------------------------------------

# re_value_dollar = re.compile(r'@Value\s*\(\s*["\']\$\{([^}]+)\}["\']\s*\)')
# re_value_spel_dollar = re.compile(r'@Value\s*\(\s*["\']#\{\s*\$\{([^}]+)\}\s*\}["\']\s*\)')
# re_field_decl = re.compile(
#     r'(?:private|public|protected)?\s*[\w<>\[\],\s?]+\s+([A-Za-z_]\w*)\s*(?:=|;)', re.M
# )
# re_configuration_properties = re.compile(
#     r'@ConfigurationProperties\s*\(\s*(?:prefix\s*=\s*)?["\']([^"\')]+)["\']\s*\)'
# )
# re_property_source = re.compile(
#     r'@PropertySource\s*\(\s*(?:value\s*=\s*)?["\']([^"\')]+)["\']'
# )
# re_message_key = re.compile(
#     r'messageSource\.getMessage\s*\(\s*["\']([^"\']+)["\']'
# )

# # Java 8 method declaration regex — same structure as Java 18 adapter but
# # explicitly excludes 'var' as a return type (Java 10+ only).
# re_method_decl = re.compile(
#     r'''
#     ^\s*
#     (?:@\w+(?:\([^)]*\))?\s*)*
#     (?:(?:public|private|protected)\s+)?
#     (?:static\s+|final\s+|synchronized\s+|native\s+|abstract\s+|default\s+)*
#     (?:<[^>]+>\s+)?
#     (?!var\b)                                        # Java 8: no 'var' type inference
#     (?:[\w\[\]<>?,]+\s+)+
#     (?!(?:if|for|while|switch|catch|else)\b)
#     ([A-Za-z_]\w*)
#     \s*\(
#     \s*(
#         (?:
#         (?:@\w+(?:\([^)]*\))?\s*)*
#         (?:final\s+)?
#         [\w\[\]<>?,]+(?:\s*\.\.\.)?\s+
#         [A-Za-z_]\w*
#         )
#         (?:\s*,\s*
#         (?:@\w+(?:\([^)]*\))?\s*)*
#         (?:final\s+)?
#         [\w\[\]<>?,]+(?:\s*\.\.\.)?\s+
#         [A-Za-z_]\w*
#         )*
#     )?
#     \)\s*\{
#     ''',
#     re.M | re.X
# )


# # ---------------------------------------------------------------------------
# # Module-level helper (mirrors the Java 18 adapter)
# # ---------------------------------------------------------------------------

# def _strip_source_comments(src: str) -> str:
#     """Remove // and /* */ comments while preserving string literals."""
#     result = []
#     i = 0
#     n = len(src)
#     in_string = False
#     string_char = None

#     while i < n:
#         ch = src[i]
#         nxt = src[i + 1] if i + 1 < n else ""

#         if in_string:
#             result.append(ch)
#             if ch == '\\':
#                 i += 1
#                 if i < n:
#                     result.append(src[i])
#             elif ch == string_char:
#                 in_string = False
#                 string_char = None
#             i += 1
#             continue

#         if ch == '/' and nxt == '/':
#             while i < n and src[i] != '\n':
#                 i += 1
#             continue

#         if ch == '/' and nxt == '*':
#             i += 2
#             while i < n - 1:
#                 if src[i] == '*' and src[i + 1] == '/':
#                     i += 2
#                     break
#                 i += 1
#             continue

#         if ch in ('"', "'"):
#             in_string = True
#             string_char = ch

#         result.append(ch)
#         i += 1

#     return ''.join(result)


# # ---------------------------------------------------------------------------
# # Java 8 Adapter
# # ---------------------------------------------------------------------------

# class JavaAdapter(LanguageAdapter):
#     """
#     LanguageAdapter implementation for Java 8 codebases.

#     Compared to the Java 18 adapter:
#       - parse_ast uses javalang directly (javalang targets Java 8).
#       - No special handling for records, sealed classes, text blocks,
#         switch expressions, or 'var' type inference.
#       - Lambda bodies and stream chains are captured via the chained-call
#         regex path (same approach as the Java 18 adapter's fallback).
#       - Default / static interface methods (new in Java 8) are handled
#         through get_methods_in_type, which yields MethodDeclaration nodes
#         on interface bodies.
#     """

#     # ------------------------------------------------------------------
#     # Internal helpers
#     # ------------------------------------------------------------------

#     def _normalize_ps_ref(self, ps_str: str) -> str:
#         if ps_str.startswith("classpath:"):
#             return ps_str[len("classpath:"):]
#         if ps_str.startswith("file:"):
#             return ps_str[len("file:"):]
#         return ps_str

#     def _build_method_index_map(self, java_text: str):
#         """Map of (start_pos, method_name) tuples sorted by position."""
#         res = []
#         for m in re_method_decl.finditer(java_text):
#             res.append((m.start(), m.group(1)))
#         res.sort(key=lambda x: x[0])
#         return res

#     def _find_enclosing_method(self, method_index_map, pos):
#         candidate = None
#         for start, name in method_index_map:
#             if start <= pos:
#                 candidate = name
#             else:
#                 break
#         return candidate

#     def _extract_values_with_vars(self, java_text: str):
#         results = []
#         for m in re_value_spel_dollar.finditer(java_text):
#             key = m.group(1)
#             span_end = m.end()
#             var = None
#             m2 = re_field_decl.search(java_text, span_end)
#             if m2:
#                 var = m2.group(1)
#             results.append({
#                 "Annotation": "@Value", "Property": key,
#                 "Variable": var, "span_start": m.start(), "span_end": span_end
#             })

#         for m in re_value_dollar.finditer(java_text):
#             key = m.group(1)
#             span_end = m.end()
#             var = None
#             m2 = re_field_decl.search(java_text, span_end)
#             if m2:
#                 var = m2.group(1)
#             results.append({
#                 "Annotation": "@Value", "Property": key,
#                 "Variable": var, "span_start": m.start(), "span_end": span_end
#             })

#         return results

#     # ------------------------------------------------------------------
#     # Configuration
#     # ------------------------------------------------------------------

#     def file_extension(self) -> str:
#         ext = self.details.get("extension")
#         if isinstance(ext, str) and ext.strip():
#             return ext.strip()
#         return ".java"

#     def _rx(self, key: str, flags: int = 0):
#         pat = self.regex.get(key)
#         if not isinstance(pat, str):
#             raise KeyError(f"Regex key '{key}' missing or not a string")
#         unesc = html.unescape(pat)
#         try:
#             return re.compile(unesc, flags)
#         except re.error as err:
#             raise re.error(
#                 f"[regex compile] key='{key}' pattern='{unesc}' error={err}"
#             ) from err

#     # ------------------------------------------------------------------
#     # AST Parsing  (javalang targets Java 8 — no extra pre-processing needed)
#     # ------------------------------------------------------------------

#     def parse_ast(self, code: str):
#         """
#         Parse Java 8 source.  javalang handles all Java 8 features natively
#         (lambdas, streams, default interface methods, diamond operator, etc.).
#         Returns the compilation unit tree, or None on failure.
#         """
#         try:
#             return javalang.parse.parse(code)
#         except Exception:
#             return None

#     # ------------------------------------------------------------------
#     # Type helpers
#     # ------------------------------------------------------------------

#     def _simple_type_name(self, type_obj_or_str):
#         if type_obj_or_str is None:
#             return None
#         n = type_obj_or_str.name if hasattr(type_obj_or_str, "name") else str(type_obj_or_str)
#         # Strip HTML-escaped generics
#         n = re.sub(r'\s*&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;\s*', '', n)
#         n = re.sub(r'\s*&lt;[^&gt;]+&gt;\s*', '', n)
#         return n.split('.')[-1]

#     def _extract_method_annotations(self, method_node) -> str:
#         ann_list = []
#         if hasattr(method_node, "annotations") and method_node.annotations:
#             for ann in method_node.annotations:
#                 try:
#                     ann_list.append("@" + (ann.name if hasattr(ann, "name") else str(ann)))
#                 except Exception:
#                     continue
#         return ", ".join(ann_list) if ann_list else ""

#     def _extract_method_declaration_type(self, method_node) -> str:
#         if hasattr(method_node, "modifiers") and method_node.modifiers:
#             mods = {m.lower() for m in method_node.modifiers}
#             if "public" in mods:
#                 return "Public"
#             if "private" in mods:
#                 return "Private"
#             if "protected" in mods:
#                 return "Protected"
#         return "Default"

#     def _extract_return_type(self, method_node) -> str:
#         try:
#             rt = method_node.return_type
#             if rt is None:
#                 return "void"
#             base = rt.name if hasattr(rt, "name") else "Unknown"
#             if hasattr(rt, "arguments") and rt.arguments:
#                 args = []
#                 for arg in rt.arguments:
#                     if hasattr(arg, "type") and hasattr(arg.type, "name"):
#                         args.append(arg.type.name)
#                     elif hasattr(arg, "name"):
#                         args.append(arg.name)
#                 return f"{base}&amp;lt;{', '.join(args)}&amp;gt;"
#             return base
#         except Exception:
#             return "Unknown"

#     def _type_to_simple(self, t) -> str:
#         if t is None:
#             return ""
#         base = getattr(t, "name", str(t)) or ""
#         if "." in base:
#             base = base.split(".")[-1]
#         dims = "[]" * int(getattr(t, "dimensions", 0) or 0)
#         return f"{base}{dims}"

#     def extract_method_metadata(self, method_node) -> dict:
#         is_ctor = isinstance(method_node, jt.ConstructorDeclaration)

#         param_types = []
#         for p in getattr(method_node, "parameters", []) or []:
#             t = self._type_to_simple(getattr(p, "type", None))
#             if getattr(p, "varargs", False):
#                 t = t + "[]"
#             param_types.append(t or "")

#         return {
#             "Annotations": self._extract_method_annotations(method_node),
#             "Method_Declaration_Type": self._extract_method_declaration_type(method_node),
#             "return_type": "constructor" if is_ctor else self._extract_return_type(method_node),
#             "member_kind": "Constructor" if is_ctor else "Method",
#             "Parameters": ", ".join(param_types),
#             "Parameter_Arity": len(param_types),
#             "Parameter_Types": ";".join(param_types),
#         }

#     # ------------------------------------------------------------------
#     # Import helpers
#     # ------------------------------------------------------------------

#     def _collect_com_imports(self, tree):
#         imports_types = set()
#         wildcard_packages = set()
#         static_members = set()
#         static_wildcard_classes = set()

#         for imp in getattr(tree, "imports", []):
#             path = getattr(imp, "path", "")
#             if not isinstance(path, str) or not path.startswith("nl."):
#                 continue
#             parts = [p for p in path.split('.') if p]
#             if getattr(imp, "static", False):
#                 if parts[-1] == '*':
#                     if len(parts) >= 2:
#                         static_wildcard_classes.add(parts[-2])
#                 else:
#                     static_members.add(parts[-1])
#                     if len(parts) >= 2:
#                         imports_types.add(parts[-2])
#             else:
#                 if parts[-1] == '*':
#                     wildcard_packages.add('.'.join(parts[:-1]))
#                 else:
#                     imports_types.add(parts[-1])

#         return imports_types, wildcard_packages, static_members, static_wildcard_classes

#     # ------------------------------------------------------------------
#     # Field / DI helpers
#     # ------------------------------------------------------------------

#     def _collect_autowired_fields(self, class_node) -> dict:
#         autowired = {}
#         for _, fd in class_node.filter(jt.FieldDeclaration):
#             has_auto = any(
#                 getattr(a, "name", "") in ("Autowired", "Inject")
#                 for a in (fd.annotations or [])
#             )
#             if not has_auto:
#                 continue
#             tname = self._simple_type_name(fd.type)
#             for decl in getattr(fd, "declarators", []):
#                 autowired[decl.name] = tname
#         return autowired

#     # ------------------------------------------------------------------
#     # Variable type inference
#     # ------------------------------------------------------------------

#     def _infer_type_from_initializer(self, decl):
#         init = getattr(decl, "initializer", None)
#         try:
#             if isinstance(init, jt.ClassCreator):
#                 return self._simple_type_name(init.type)
#         except Exception:
#             pass
#         return None

#     def _build_var_types_for_method(self, method_node, autowired_fields):
#         var_types = {}
#         locals_from_new = set()
#         params_set = set()

#         for p in getattr(method_node, "parameters", []):
#             var_types[p.name] = self._simple_type_name(p.type)
#             params_set.add(p.name)

#         for _, lv in method_node.filter(jt.LocalVariableDeclaration):
#             declared_type = self._simple_type_name(lv.type)
#             for decl in getattr(lv, "declarators", []):
#                 name = decl.name
#                 tname = declared_type
#                 # Java 8 has no 'var'; skip the var-inference branch from Java 18 adapter
#                 inferred = self._infer_type_from_initializer(decl)
#                 if inferred:
#                     tname = inferred
#                     locals_from_new.add(name)
#                 var_types[name] = tname

#         var_types.update(autowired_fields or {})
#         return var_types, locals_from_new, params_set

#     # ------------------------------------------------------------------
#     # Call-filtering helpers
#     # ------------------------------------------------------------------

#     def _normalize_qualifier(self, qual: str, var_types: dict) -> str:
#         if qual in var_types:
#             return qual
#         for k in var_types:
#             if qual == (k + k):
#                 return k
#         return qual

#     def _is_same_package_type(self, type_name, package_name) -> bool:
#         return bool(package_name and package_name.startswith('nl.') and type_name)

#     def _keep_qualified_call(self, qual, var_types, imports_types, autowired_fields,
#                               wildcard_packages, locals_from_new, params_set, package_name) -> bool:
#         qual = self._normalize_qualifier(qual, var_types)
#         if qual in autowired_fields:
#             return True
#         t = var_types.get(qual)
#         if t and qual in locals_from_new and self.accept_local_new_types:
#             return True
#         if t and qual in params_set and self.accept_parameter_types:
#             return True
#         if t and t in imports_types:
#             return True
#         if qual in imports_types:
#             return True
#         if wildcard_packages and t:
#             return True
#         if self.accept_same_package and self._is_same_package_type(t, package_name):
#             return True
#         if t:          # variable has a known declared type in var_types
#             return True
#         return False
#     def _keep_unqualified_call(self, member, static_members, static_wildcard_classes) -> bool:
#         if self.include_unqualified:
#             return True
#         if member in static_members:
#             return True
#         if static_wildcard_classes:
#             return True
#         return False

#     def _get_package_name(self, java_code: str):
#         pat = html.unescape(
#             self.regex.get("package", r'^\s*package\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;')
#         )
#         m = re.search(pat, java_code, flags=re.MULTILINE)
#         return m.group(1) if m else None

#     # ------------------------------------------------------------------
#     # Declared types
#     # ------------------------------------------------------------------

#     def get_declared_types(self, ast):
#         """
#         Yield (name, kind, node) for classes, interfaces, and enums.
#         Java 8 does NOT have records or sealed classes — those are omitted.
#         """
#         types = []

#         # Classes
#         for _, cls in ast.filter(jt.ClassDeclaration):
#             types.append((getattr(cls, "name", "Unknown"), "class", cls))

#         # Interfaces (including those with default/static methods — Java 8)
#         for _, ifc in ast.filter(jt.InterfaceDeclaration):
#             types.append((getattr(ifc, "name", "Unknown"), "interface", ifc))

#         # Enums
#         for _, en in ast.filter(jt.EnumDeclaration):
#             types.append((getattr(en, "name", "Unknown"), "enum", en))

#         return types

#     def get_methods_in_type(self, type_node):
#         """
#         Yield (name, node) for every method and constructor in type_node.
#         For interfaces, this includes default and static methods (Java 8+).
#         """
#         for _, m in type_node.filter(jt.MethodDeclaration):
#             yield m.name, m
#         for _, c in type_node.filter(jt.ConstructorDeclaration):
#             yield c.name, c

#     # ------------------------------------------------------------------
#     # Method source extraction
#     # ------------------------------------------------------------------

#     def _get_method_source(self, code: str, method_node):
#         try:
#             lines = code.splitlines(True)
#             if hasattr(method_node, "position") and method_node.position and method_node.position[0]:
#                 start_line = method_node.position[0] - 1
#                 start_offset = sum(len(lines[i]) for i in range(start_line))
#                 start_brace_idx = code.find('{', start_offset)
#                 if start_brace_idx == -1:
#                     return None
#                 brace_count = 0
#                 end_idx = None
#                 for i in range(start_brace_idx, len(code)):
#                     ch = code[i]
#                     if ch == '{':
#                         brace_count += 1
#                     elif ch == '}':
#                         brace_count -= 1
#                         if brace_count == 0:
#                             end_idx = i
#                             break
#                 return code[start_brace_idx:end_idx + 1] if end_idx is not None else None
#             else:
#                 mname = getattr(method_node, "name", None)
#                 if not mname:
#                     return None
#                 sig_pat = re.compile(
#                     r'\b' + re.escape(mname) + r'\s*\([^)]*\)\s*\{',
#                     re.MULTILINE | re.DOTALL
#                 )
#                 match = sig_pat.search(code)
#                 if not match:
#                     return None
#                 start_brace_idx = match.end() - 1
#                 brace_count = 0
#                 end_idx = None
#                 for i in range(start_brace_idx, len(code)):
#                     ch = code[i]
#                     if ch == '{':
#                         brace_count += 1
#                     elif ch == '}':
#                         brace_count -= 1
#                         if brace_count == 0:
#                             end_idx = i
#                             break
#                 return code[start_brace_idx:end_idx + 1] if end_idx is not None else None
#         except Exception:
#             return None

#     # ------------------------------------------------------------------
#     # Dynamic qualifier / chained-call extraction
#     # ------------------------------------------------------------------

#     def _extract_dynamic_terminal_methods(self, text: str) -> set:
#         """
#         Extract terminal method names from dynamic chains such as
#         map.get(key).doSomething(...).
#         Java 8 streams produce many such patterns.
#         """
#         if not isinstance(text, str) or not text.strip():
#             return set()

#         pat = None
#         if isinstance(self.regex.get("re_dynamic_qual"), str) and self.regex["re_dynamic_qual"].strip():
#             try:
#                 pat = self._rx("re_dynamic_qual", flags=re.MULTILINE | re.DOTALL)
#             except Exception:
#                 pat = None

#         terms = set()
#         if pat is not None:
#             for m in pat.finditer(text):
#                 try:
#                     name = m.group(1)
#                     if isinstance(name, str) and name.strip():
#                         terms.add(f"{name.strip()}()")
#                 except Exception:
#                     continue
#             return terms

#         # Default single-dynamic-segment: base(...).terminal(...)
#         single_dyn = re.compile(
#             r"""\b[A-Za-z_]\w*\s*\([^()]*\)\s*\.\s*([A-Za-z_]\w*)\s*\(""",
#             re.MULTILINE | re.DOTALL,
#         )
#         for m in single_dyn.finditer(text):
#             name = m.group(1)
#             if name and name.strip():
#                 terms.add(f"{name.strip()}()")

#         # Multi-segment chains: base(...).m1(...).m2(...)
#         chain_dyn = re.compile(
#             r"""\b[A-Za-z_]\w*\s*\([^()]*\)(?:\s*\.\s*[A-Za-z_]\w*\s*\([^()]*\))+""",
#             re.MULTILINE | re.DOTALL,
#         )
#         for cm in chain_dyn.finditer(text):
#             last_methods = re.findall(r'\.\s*([A-Za-z_]\w*)\s*\(', cm.group(0))
#             if last_methods:
#                 terms.add(f"{last_methods[-1].strip()}()")

#         return terms

#     # ------------------------------------------------------------------
#     # Expression walker (for nested invocations)
#     # ------------------------------------------------------------------

#     def _collect_invocations_in_expression(
#         self, expr, *,
#         var_types, imports_types, autowired_fields,
#         wildcard_packages, locals_from_new, params_set, package_name,
#     ) -> set:
#         calls = set()
#         if expr is None:
#             return calls
#         try:
#             if isinstance(expr, jt.MethodInvocation):
#                 qual = expr.qualifier or ""
#                 member = expr.member
#                 if qual and ("(" in qual or ")" in qual):
#                     calls.add(f"{qual}.{member}()")
#                 elif qual:
#                     qual = self._normalize_qualifier(qual, var_types)
#                     if self._keep_qualified_call(
#                         qual, var_types, imports_types, autowired_fields,
#                         wildcard_packages, locals_from_new, params_set, package_name,
#                     ):
#                         resolved_type = var_types.get(qual, qual)
#                         calls.add(f"{resolved_type}.{member}()")
#                 else:
#                     if self._keep_unqualified_call(member, set(), set()):
#                         calls.add(f"{member}()")
#                 for a in getattr(expr, "arguments", []) or []:
#                     calls |= self._collect_invocations_in_expression(
#                         a, var_types=var_types, imports_types=imports_types,
#                         autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
#                         locals_from_new=locals_from_new, params_set=params_set,
#                         package_name=package_name,
#                     )
#                 return calls

#             if isinstance(expr, jt.ClassCreator):
#                 ctor_type = self._simple_type_name(expr.type)
#                 if ctor_type:
#                     calls.add(f"{ctor_type}.{ctor_type}()")
#                 for a in getattr(expr, "arguments", []) or []:
#                     calls |= self._collect_invocations_in_expression(
#                         a, var_types=var_types, imports_types=imports_types,
#                         autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
#                         locals_from_new=locals_from_new, params_set=params_set,
#                         package_name=package_name,
#                     )
#                 return calls

#             for attr in ("expression", "condition", "then_expression", "else_expression",
#                          "left", "right", "operand"):
#                 node = getattr(expr, attr, None)
#                 if node is not None:
#                     calls |= self._collect_invocations_in_expression(
#                         node, var_types=var_types, imports_types=imports_types,
#                         autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
#                         locals_from_new=locals_from_new, params_set=params_set,
#                         package_name=package_name,
#                     )
#             for list_attr in ("expressions", "arguments"):
#                 lst = getattr(expr, list_attr, None)
#                 if isinstance(lst, (list, tuple)):
#                     for node in lst:
#                         calls |= self._collect_invocations_in_expression(
#                             node, var_types=var_types, imports_types=imports_types,
#                             autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
#                             locals_from_new=locals_from_new, params_set=params_set,
#                             package_name=package_name,
#                         )
#         except Exception:
#             pass
#         return calls

#     # ------------------------------------------------------------------
#     # Main call-finder (AST path)
#     # ------------------------------------------------------------------

#     def find_calls_in_method(self, type_node, method_node, code: str) -> list:
#         calls = set()
#         package_name = self._get_package_name(code)

#         try:
#             tree = javalang.parse.parse(code)
#             imports_types, wildcard_packages, static_members, static_wildcard_classes = \
#                 self._collect_com_imports(tree)
#         except Exception:
#             imports_types, wildcard_packages, static_members, static_wildcard_classes = \
#                 set(), set(), set(), set()

#         autowired_fields = self._collect_autowired_fields(type_node)
#         var_types, locals_from_new, params_set = self._build_var_types_for_method(
#             method_node, autowired_fields
#         )

#         # Include for-loop element variables
#         for _, forstmt in method_node.filter(jt.ForStatement):
#             if hasattr(forstmt, "control") and hasattr(forstmt.control, "var"):
#                 var_decl = forstmt.control.var
#                 if var_decl:
#                     tname = self._simple_type_name(var_decl.type)
#                     for declarator in getattr(var_decl, "declarators", []):
#                         var_types[declarator.name] = tname

#         def _is_dynamic(q: str) -> bool:
#             return isinstance(q, str) and ("(" in q or ")" in q)

#         # Pre-build set of MethodInvocations that are selectors on a ClassCreator
#         # or another MethodInvocation — they have qualifier=None but are NOT sibling calls.
#         _selector_invocations = set()
        
#         def _collect_selector_ids(node, result_set):
#             for sel in (getattr(node, "selectors", None) or []):
#                 if isinstance(sel, jt.MethodInvocation):
#                     result_set.add(id(sel))
#                     _collect_selector_ids(sel, result_set)  # recurse into nested selectors

#         _selector_invocations = set()
#         for _, cc in method_node.filter(jt.ClassCreator):
#             _collect_selector_ids(cc, _selector_invocations)
#         for _, parent_inv in method_node.filter(jt.MethodInvocation):
#             _collect_selector_ids(parent_inv, _selector_invocations)
#         def _build_chain_string(start_inv, resolved_root):
#             """Build full chain string including selectors.
#             e.g. abc.method1() with selector method2() → 'ABC.method1().method2()'
#             """
#             chain = f"{resolved_root}.{start_inv.member}()"
#             for sel in (start_inv.selectors or []):
#                 if isinstance(sel, jt.MethodInvocation):
#                     chain += f".{sel.member}()"
#             return chain

#         # AST: MethodInvocation nodes
#         for _, inv in method_node.filter(jt.MethodInvocation):
#             qual = inv.qualifier or ""
#             member = inv.member

#             if not qual:
#                 # If this is a selector on a ClassCreator or chained call,
#                 # it is handled by its parent — skip to avoid wrong class attribution.
#                 if id(inv) in _selector_invocations:
#                     pass
#                 else:
#                     sibling_method_names = {n for n, _ in self.get_methods_in_type(type_node)}
#                     if member in sibling_method_names:
#                         class_name = getattr(type_node, "name", None)
#                         if class_name:
#                             chain = _build_chain_string(inv, class_name)
#                             calls.add(chain)
#                         else:
#                             calls.add(f"{member}()")
#                     elif self._keep_unqualified_call(member, static_members, static_wildcard_classes):
#                         calls.add(f"{member}()")
#             elif _is_dynamic(qual):
#                 calls.add(f"{qual}.{member}()")
#             else:
#                 qual = self._normalize_qualifier(qual, var_types)
#                 if self._keep_qualified_call(
#                     qual, var_types, imports_types, autowired_fields,
#                     wildcard_packages, locals_from_new, params_set, package_name,
#                 ):
#                     resolved_type = var_types.get(qual, qual)  # resolve variable → type name
#                     # Build full chain including selectors: "ABC.method1().method2()"
#                     chain = _build_chain_string(inv, resolved_type)
#                     calls.add(chain)

#         # Collect ClassCreator IDs already handled via ThrowStatement
#         _throw_creators = set()
#         for _, th in method_node.filter(jt.ThrowStatement):
#             expr = getattr(th, "expression", None)
#             if isinstance(expr, jt.ClassCreator):
#                 _throw_creators.add(id(expr))

#         # Standalone new X(...) — not inside a throw
#         for _, cc in method_node.filter(jt.ClassCreator):
#             if id(cc) in _throw_creators:
#                 continue
#             ctor_type = self._simple_type_name(cc.type)
#             if not ctor_type:
#                 continue
#             if ctor_type in imports_types or wildcard_packages or \
#                self.accept_local_new_types or self.accept_same_package:
#                 calls.add(f"{ctor_type}.{ctor_type}()")
#             for arg in getattr(cc, "arguments", []) or []:
#                 calls |= self._collect_invocations_in_expression(
#                     arg, var_types=var_types, imports_types=imports_types,
#                     autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
#                     locals_from_new=locals_from_new, params_set=params_set,
#                     package_name=package_name,
#                 )

#         # throw new Type(...) — constructors + nested calls
        
#         for _, th in method_node.filter(jt.ThrowStatement):
#             expr = getattr(th, "expression", None)
#             if isinstance(expr, jt.ClassCreator) and getattr(expr, "type", None):
#                 ctor_type = self._simple_type_name(expr.type)
#                 if ctor_type:
#                     calls.add(f"{ctor_type}.{ctor_type}()")
#             calls |= self._collect_invocations_in_expression(
#                 expr, var_types=var_types, imports_types=imports_types,
#                 autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
#                 locals_from_new=locals_from_new, params_set=params_set,
#                 package_name=package_name,
#             )

#         # # Chained / stream calls via method source regex
#         # chained_pat = re.compile(
#         #     r'''\b[a-zA-Z_]\w*(?:\s*\([^()]*\))?(?:\s*\.\s*[a-zA-Z_]\w*\s*\([^()]*\)){1,}''',
#         #     re.MULTILINE | re.VERBOSE,
#         # )
#         # src = self._get_method_source(code, method_node)
#         # if src:
#         #     src_clean = _strip_source_comments(src)
#         #     for chain in chained_pat.findall(src_clean):
#         #         chain = chain.strip()
#         #         if chain:
#         #             calls.add(chain)
#         #     for dyn in self._extract_dynamic_terminal_methods(src_clean):
#         #         calls.add(dyn)

#         # Chained / stream calls via method source regex
#         chained_pat = re.compile(
#             r'''\b[a-zA-Z_]\w*(?:\s*\([^()]*\))?(?:\s*\.\s*[a-zA-Z_]\w*\s*\([^()]*\)){1,}''',
#             re.MULTILINE | re.VERBOSE,
#         )
#         # Matches chains rooted at a constructor call: Word(...).method(...)
#         # e.g. "BigDecimal(quantity).multiply(price)" — the root is a ctor, not a var/class.
#         ctor_rooted_pat = re.compile(r'^([A-Za-z_]\w*)\s*\(')
#         leading_var_pat = re.compile(r'^([A-Za-z_]\w*)\.')
#         java_kw = self.language_keywords()
#         src = self._get_method_source(code, method_node)
#         # Track method names claimed by chained_pat so _extract_dynamic_terminal_methods
#         # does not re-emit them as bare unqualified calls (which get attributed to this class).
#         chained_claimed_methods: set = set()
#         if src:
#             src_clean = _strip_source_comments(src)
#             for chain in chained_pat.findall(src_clean):
#                 chain = chain.strip()
#                 if not chain:
#                     continue
#                 lv = leading_var_pat.match(chain)
#                 if lv:
#                     leading = lv.group(1)
#                     # Skip chains rooted at a Java keyword (return, new, etc.)
#                     if leading in java_kw:
#                         continue
#                     resolved = var_types.get(leading)
#                     if resolved and resolved != leading:
#                         # Known variable — replace with its resolved type name
#                         chain = resolved + chain[len(leading):]
#                         calls.add(chain)
#                     elif leading in var_types:
#                         # Known variable whose name matches its type
#                         calls.add(chain)
#                     elif leading[0].isupper():
#                         # Looks like a class name (UpperCamelCase) — keep as-is
#                         calls.add(chain)
#                     # else: unknown lowercase token — skip to avoid false attribution
#                 else:
#                     # No leading "Word." prefix.  This happens for constructor-rooted
#                     # chains like "BigDecimal(quantity).multiply(price)" where the
#                     # token before the first "(" is the type name, not a variable.
#                     # Rewrite as "TypeName.method1().method2()..." so the call is
#                     # attributed to the right type rather than added as a raw string
#                     # (which downstream code cannot parse) or dropped silently.
#                     cr = ctor_rooted_pat.match(chain)
#                     if cr:
#                         ctor_type = cr.group(1)
#                         if ctor_type not in java_kw:
#                             # Extract every .method() segment after the constructor call.
#                             segments = re.findall(r'\.\s*([A-Za-z_]\w*)\s*\(', chain)
#                             for seg in segments:
#                                 rewritten = f"{ctor_type}.{seg}()"
#                                 calls.add(rewritten)
#                                 chained_claimed_methods.add(seg)
#                     # else: truly unclassifiable — skip to avoid false attribution
#             for dyn in self._extract_dynamic_terminal_methods(src_clean):
#                 # Strip trailing "()" to get the bare name for the duplicate check.
#                 bare = dyn[:-2] if dyn.endswith("()") else dyn
#                 if bare in chained_claimed_methods:
#                     # chained_pat already emitted a properly qualified version;
#                     # the bare unqualified form would be attributed to this class — skip.
#                     continue
#                 calls.add(dyn)

#         return sorted(calls)

#     # ------------------------------------------------------------------
#     # Fallback parse (regex-only path when AST fails)
#     # ------------------------------------------------------------------

#     def _parse_com_imports_fallback(self, java_code: str):
#         re_import_line = self._rx("import_static", flags=re.MULTILINE)
#         imports_types = set()
#         wildcard_packages = set()
#         static_members = set()
#         static_wildcard_classes = set()
#         for m in re_import_line.finditer(java_code):
#             line = m.group(0)
#             path = m.group(1)
#             parts = [p for p in path.split('.') if p]
#             is_static = 'static' in line
#             if is_static:
#                 if parts[-1] == '*':
#                     if len(parts) >= 2:
#                         static_wildcard_classes.add(parts[-2])
#                 else:
#                     static_members.add(parts[-1])
#                     if len(parts) >= 2:
#                         imports_types.add(parts[-2])
#             else:
#                 if parts[-1] == '*':
#                     wildcard_packages.add('.'.join(parts[:-1]))
#                 else:
#                     imports_types.add(parts[-1])
#         return imports_types, wildcard_packages, static_members, static_wildcard_classes

#     def _extract_balanced_args(self, text: str, start_idx: int) -> str:
#         if start_idx < 0 or start_idx >= len(text) or text[start_idx] != '(':
#             return ""
#         depth = 0
#         end = None
#         for i in range(start_idx, len(text)):
#             ch = text[i]
#             if ch == '(':
#                 depth += 1
#             elif ch == ')':
#                 depth -= 1
#                 if depth == 0:
#                     end = i
#                     break
#         return text[start_idx:end + 1] if end is not None else ""

#     def fallback_parse(self, code_raw: str) -> dict:
#         """
#         Regex-only fallback for files whose AST cannot be parsed.
#         Handles all Java 8 patterns including lambdas and streams
#         (they appear as regular method calls in regex terms).
#         """
#         java_code = html.unescape(code_raw)
#         package_name = self._get_package_name(java_code)
#         imports_types, wildcard_packages, static_members, static_wildcard_classes = \
#             self._parse_com_imports_fallback(java_code)

#         re_autowired_field  = self._rx("autowired_field", flags=re.MULTILINE)
#         re_loose_decl       = self._rx("variable_declaration")
#         re_var_decl         = self._rx("re_var_decl")
#         re_var_new          = self._rx("re_var_new")
#         re_simple_call      = self._rx("re_simple_call", flags=re.MULTILINE)
#         re_member_access    = self._rx("re_member_access")
#         re_unqualified_call = self._rx("re_unqualified_call")
#         re_chain            = self._rx("re_chain") if self.regex.get("re_chain") else re.compile(r"$^")
#         re_method_with_throw = self._rx("method_with_throw", flags=re.MULTILINE | re.DOTALL)
#         re_method_name_in_sig = re.compile(r'\b([A-Za-z_]\w*)\s*\(', re.MULTILINE)

#         re_class_implements      = self._rx("class_implements", flags=re.MULTILINE)
#         re_class_declaration     = self._rx("class_declaration", flags=re.MULTILINE)
#         re_interface_declaration = self._rx("interface_declaration", flags=re.MULTILINE)

#         fallback_types = {}
#         for m in re_interface_declaration.finditer(java_code):
#             fallback_types[m.group(1)] = "interface"
#         for m in re_class_implements.finditer(java_code):
#             fallback_types.setdefault(m.group(1), "class_implements_interface")
#         for m in re_class_declaration.finditer(java_code):
#             fallback_types.setdefault(m.group(1), "class")

#         class_or_interface_name = next(iter(fallback_types.keys()), None)

#         # --- Variable / DI types ---
#         autowired_fields = {}
#         for m in re_autowired_field.finditer(java_code):
#             raw_type = m.group(1)
#             var_name = m.group(2)
#             tname = re.sub(
#                 r'(&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;|&lt;[^&gt;]+&gt;)', '', raw_type
#             ).strip().split('.')[-1]
#             autowired_fields[var_name] = tname

#         var_types = {}
#         locals_from_new = set()
#         params_set = set()

#         for m in re_var_decl.finditer(java_code):
#             raw_type, var_name = m.group(1), m.group(2)
#             # Java 8: skip if type is literally 'var' (shouldn't appear, but guard anyway)
#             if raw_type.strip() == 'var':
#                 continue
#             tname = re.sub(
#                 r'(&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;|&lt;[^&gt;]+&gt;)', '', raw_type
#             ).strip().split('.')[-1]
#             var_types[var_name] = tname

#         for m in re_var_new.finditer(java_code):
#             var_name, fq_type = m.group(1), m.group(2)
#             var_types.setdefault(var_name, fq_type.split('.')[-1])
#             locals_from_new.add(var_name)

#         for m in re_loose_decl.finditer(java_code):
#             raw_type, var_name = m.group(1), m.group(2)
#             if var_name in var_types or raw_type.strip() == 'var':
#                 continue
#             tname = re.sub(
#                 r'(&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;|&lt;[^&gt;]+&gt;)', '', raw_type
#             ).strip().split('.')[-1]
#             var_types[var_name] = tname

#         var_types.update(autowired_fields)

#         java_keywords = {"return", "this", "super", "new"} | set(
#             self.details.get("control_keywords", [])
#         )

#         per_method_calls = []

#         def _is_dyn(q: str) -> bool:
#             return isinstance(q, str) and ("(" in q or ")" in q)

#         def _process_block(block_text: str, method_name):
#             filtered = set()

#             for m in re_simple_call.finditer(block_text):
#                 qual, member = m.group(1), m.group(2)
#                 if str(qual).strip().lower() in java_keywords:
#                     continue
#                 if _is_dyn(qual):
#                     filtered.add(f"{qual}.{member}()")
#                 elif self._keep_qualified_call(
#                     qual, var_types, imports_types, autowired_fields,
#                     wildcard_packages, locals_from_new, params_set, package_name,
#                 ):
#                     filtered.add(f"{qual}.{member}()")

#             for m in re_member_access.finditer(block_text):
#                 qual, member = m.group(1), m.group(2)
#                 if str(qual).strip().lower() in java_keywords:
#                     continue
#                 if _is_dyn(qual):
#                     filtered.add(f"{member}")
#                 elif self._keep_qualified_call(
#                     qual, var_types, imports_types, autowired_fields,
#                     wildcard_packages, locals_from_new, params_set, package_name,
#                 ):
#                     filtered.add(f"{qual}.{member}")

#             for m in re_unqualified_call.finditer(block_text):
#                 member = m.group(1)
#                 if member in java_keywords:
#                     continue
#                 if method_name and member == method_name:
#                     continue
#                 if class_or_interface_name and re.search(
#                     r'\b(?:public|private|protected)\b[^{;]*\b' + re.escape(member) + r'\s*\(',
#                     java_code, re.MULTILINE
#                 ):
#                     filtered.add(f"{class_or_interface_name}.{member}()")
#                 elif self.include_unqualified or member in static_members or static_wildcard_classes:
#                     filtered.add(f"{member}()")

#             for m in re_chain.finditer(block_text):
#                 root = m.group(1)
#                 if str(root).strip().lower() in java_keywords:
#                     continue
#                 filtered.add(m.group(0))

#             # throw new ...

#             throw_pat = re.compile(r'\bthrow\s+new\s+([A-Za-z_]\w+)\s*\(', re.MULTILINE)
#             for tm in throw_pat.finditer(block_text):
#                 ctor_class = tm.group(1)
#                 filtered.add(f"{ctor_class}.{ctor_class}()")
#                 args_block = self._extract_balanced_args(block_text, tm.end() - 1)
#                 if args_block:
#                     for sm in re_simple_call.finditer(args_block):
#                         q, mem = sm.group(1), sm.group(2)
#                         if str(q).strip().lower() in java_keywords:
#                             continue
#                         if _is_dyn(q):
#                             filtered.add(f"{q}.{mem}()")
#                         elif self._keep_qualified_call(
#                             q, var_types, imports_types, autowired_fields,
#                             wildcard_packages, locals_from_new, params_set, package_name,
#                         ):
#                             filtered.add(f"{q}.{mem}()")
#                     for um in re_unqualified_call.finditer(args_block):
#                         mem = um.group(1)
#                         if mem not in java_keywords and self.include_unqualified:
#                             filtered.add(f"{mem}()")
#                     for cm in re_chain.finditer(args_block):
#                         filtered.add(cm.group(0))

#             # standalone new X(...) — outside throw
#             new_pat = re.compile(r'\bnew\s+([A-Za-z_]\w+)\s*\(', re.MULTILINE)
#             throw_positions = {tm.start() for tm in throw_pat.finditer(block_text)}
#             for nm in new_pat.finditer(block_text):
#                 # Skip if this new is part of a throw new (already handled above)
#                 preceding = block_text[max(0, nm.start() - 10):nm.start()].strip()
#                 if preceding.endswith('throw'):
#                     continue
#                 ctor_class = nm.group(1)
#                 filtered.add(f"{ctor_class}.{ctor_class}()")
#                 args_block = self._extract_balanced_args(block_text, nm.end() - 1)
#                 if args_block:
#                     for sm in re_simple_call.finditer(args_block):
#                         q, mem = sm.group(1), sm.group(2)
#                         if str(q).strip().lower() in java_keywords:
#                             continue
#                         if _is_dyn(q):
#                             filtered.add(f"{q}.{mem}()")
#                         elif self._keep_qualified_call(
#                             q, var_types, imports_types, autowired_fields,
#                             wildcard_packages, locals_from_new, params_set, package_name,
#                         ):
#                             filtered.add(f"{q}.{mem}()")
#                     for um in re_unqualified_call.finditer(args_block):
#                         mem = um.group(1)
#                         if mem not in java_keywords and self.include_unqualified:
#                             filtered.add(f"{mem}()")
#                     for cm in re_chain.finditer(args_block):
#                         filtered.add(cm.group(0))

#             for dyn in self._extract_dynamic_terminal_methods(block_text):
#                 filtered.add(dyn)

#             for call in sorted(filtered):
#                 per_method_calls.append({'method_name': method_name, 'object_call': call})

#         # Walk method bodies
#         for sig_match in re_method_with_throw.finditer(java_code):
#             brace_pos = java_code.find('{', sig_match.end() - 1)
#             if brace_pos == -1:
#                 continue
#             brace_count, end_idx = 0, None
#             for i in range(brace_pos, len(java_code)):
#                 if java_code[i] == '{':
#                     brace_count += 1
#                 elif java_code[i] == '}':
#                     brace_count -= 1
#                     if brace_count == 0:
#                         end_idx = i
#                         break
#             if end_idx is None:
#                 continue
#             method_text = java_code[sig_match.start():end_idx + 1]
#             name_match = re_method_name_in_sig.search(method_text)
#             method_name = name_match.group(1) if name_match else None
#             _process_block(method_text, method_name)

#         # Constructor bodies
#         if class_or_interface_name:
#             ctor_pat = re.compile(
#                 r'(?:public|protected|private)\s+' + re.escape(class_or_interface_name) + r'\s*\([^)]*\)\s*\{',
#                 re.MULTILINE,
#             )
#             for cm in ctor_pat.finditer(java_code):
#                 brace_pos = java_code.find('{', cm.end() - 1)
#                 if brace_pos == -1:
#                     continue
#                 brace_count, end_idx = 0, None
#                 for i in range(brace_pos, len(java_code)):
#                     if java_code[i] == '{':
#                         brace_count += 1
#                     elif java_code[i] == '}':
#                         brace_count -= 1
#                         if brace_count == 0:
#                             end_idx = i
#                             break
#                 if end_idx is None:
#                     continue
#                 ctor_text = java_code[cm.start():end_idx + 1]
#                 _process_block(ctor_text, class_or_interface_name)

#         if per_method_calls:
#             row_type = fallback_types.get(class_or_interface_name or '', 'Unknown')
#             return {
#                 'type_name': class_or_interface_name or 'Unknown',
#                 'row_type': row_type,
#                 'per_method_calls': per_method_calls,
#             }

#         row_type = fallback_types.get(class_or_interface_name or '', 'Unknown')
#         return {
#             'type_name': class_or_interface_name or 'Unknown',
#             'row_type': row_type,
#             'filtered_calls': [],
#         }

#     # ------------------------------------------------------------------
#     # System-call filter
#     # ------------------------------------------------------------------

#     def is_system_call(self, call: str) -> bool:
#         if not isinstance(call, str):
#             return False
#         call = call.strip()
#         if not call:
#             return False

#         call_ng = re.sub(r'\s*&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;\s*', '', call)
#         call_ng = re.sub(r'\s*&lt;[^&gt;]+&gt;\s*', '', call_ng)
#         lc = call_ng.lower()

#         system_qualifiers = self.details.get("SYSTEM_QUALIFIERS", [
#             r"^logger\.", r"^log\.", r"^system\.", r"^string\.", r"^objects\.", r"^arrays\.",
#             r"^collections\.", r"^optional\.", r"^stream\.", r"^httpsecurity\.", r"^security\.",
#         ])
#         for pattern in system_qualifiers:
#             if re.match(pattern, lc):
#                 return True

#         def extract_method(c: str) -> str:
#             part = c.split(".")[-1]
#             part = re.sub(r"\(.*\)", "", part)
#             return part.replace(";", "").replace('"', "").replace("'", "").strip().lower()

#         default_system_methods = {"equals"}
#         system_methods = default_system_methods | {
#             m.lower() for m in self.details.get("SYSTEM_METHODS", [])
#         }
#         return extract_method(call_ng) in system_methods

#     def language_keywords(self) -> set:
#         return {"return", "this", "super", "new"} | set(self.details.get("control_keywords", []))

#     # ------------------------------------------------------------------
#     # Object-class map
#     # ------------------------------------------------------------------

#     def build_object_class_map(self, app_folder: str) -> dict:
#         obj_class_map = {}
#         PRIMITIVES = set(self.details.get("PRIMITIVE", []))
#         COLLECTION_TYPES = set(self.details.get("COLLECTION_TYPES", [
#             "List", "Set", "Map", "Collection", "Iterable"
#         ]))

#         var_decl_pattern  = self._rx("var_decl_pattern", flags=re.MULTILINE)
#         for_loop_pattern  = self._rx("for_loop_pattern", flags=re.MULTILINE)
#         simple_local_decl = re.compile(r'\b([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*=')
#         method_param_list_pat = re.compile(
#             r'\b(?:public|protected|private)\b[^{;]*\(([^)]*)\)', re.MULTILINE
#         )
#         method_param_decl_pat = self._rx("method_param_decl")

#         def clean_type(t: str) -> str:
#             if not t:
#                 return t
#             t2 = re.sub(r'\s*&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;\s*', '', t)
#             t2 = re.sub(r'\s*&lt;[^&gt;]+&gt;\s*', '', t2)
#             return t2.replace('[]', '').strip()

#         def update_map(f: str, var: str, typ: str, *, source: str):
#             if not typ or typ in PRIMITIVES:
#                 return
#             key_scoped = (f.lower(), var.lower())
#             key_global = var.lower()
#             existing_s = obj_class_map.get(key_scoped)
#             if existing_s:
#                 if existing_s in COLLECTION_TYPES and typ not in COLLECTION_TYPES:
#                     obj_class_map[key_scoped] = typ
#             else:
#                 obj_class_map[key_scoped] = typ
#             existing_g = obj_class_map.get(key_global)
#             if existing_g:
#                 if existing_g in COLLECTION_TYPES and typ not in COLLECTION_TYPES:
#                     obj_class_map[key_global] = typ
#             else:
#                 obj_class_map[key_global] = typ

#         for root, _, files in os.walk(app_folder):
#             for file in files:
#                 if not file.endswith(self.file_extension()):
#                     continue
#                 fpath = os.path.join(root, file)
#                 try:
#                     with open(fpath, "r", encoding="utf-8") as fh:
#                         code = fh.read()
#                 except Exception:
#                     continue

#                 # --- AST path ---
#                 try:
#                     parsed = javalang.parse.parse(code)
#                     for _, type_node in parsed.filter(jt.ClassDeclaration):
#                         for _, fd in type_node.filter(jt.FieldDeclaration):
#                             tname = self._simple_type_name(fd.type)
#                             for decl in getattr(fd, "declarators", []):
#                                 if tname and tname not in PRIMITIVES:
#                                     update_map(file, decl.name, tname, source="ast_field")

#                         for _, mnode in type_node.filter(jt.MethodDeclaration):
#                             for p in getattr(mnode, "parameters", []):
#                                 tname = self._simple_type_name(p.type)
#                                 if tname and tname not in PRIMITIVES:
#                                     update_map(file, p.name, tname, source="ast_param")
#                             for _, lv in mnode.filter(jt.LocalVariableDeclaration):
#                                 declared_type = self._simple_type_name(lv.type)
#                                 for decl in getattr(lv, "declarators", []):
#                                     tname = declared_type or self._infer_type_from_initializer(decl)
#                                     if tname and tname not in PRIMITIVES:
#                                         update_map(file, decl.name, tname, source="ast_local")
#                             for _, forstmt in mnode.filter(jt.ForStatement):
#                                 if hasattr(forstmt, "control") and hasattr(forstmt.control, "var"):
#                                     var_decl = forstmt.control.var
#                                     if var_decl:
#                                         tname = self._simple_type_name(var_decl.type)
#                                         for declarator in getattr(var_decl, "declarators", []):
#                                             if tname and tname not in PRIMITIVES:
#                                                 update_map(file, declarator.name, tname, source="ast_for")

#                         for _, cnode in type_node.filter(jt.ConstructorDeclaration):
#                             for p in getattr(cnode, "parameters", []):
#                                 tname = self._simple_type_name(p.type)
#                                 if tname and tname not in PRIMITIVES:
#                                     update_map(file, p.name, tname, source="ast_ctor_param")
#                             for _, lv in cnode.filter(jt.LocalVariableDeclaration):
#                                 declared_type = self._simple_type_name(lv.type)
#                                 for decl in getattr(lv, "declarators", []):
#                                     tname = declared_type or self._infer_type_from_initializer(decl)
#                                     if tname and tname not in PRIMITIVES:
#                                         update_map(file, decl.name, tname, source="ast_ctor_local")
#                     continue
#                 except Exception:
#                     pass

#                 # --- Regex fallback ---
#                 for m in var_decl_pattern.finditer(code):
#                     raw_type, var_name = m.group(1), m.group(2)
#                     t = clean_type(raw_type)
#                     if t and t not in PRIMITIVES:
#                         update_map(file, var_name, t, source="regex_var_decl")

#                 for m in for_loop_pattern.finditer(code):
#                     raw_type, var_name = m.group(1), m.group(2)
#                     t = clean_type(raw_type)
#                     if t and t not in PRIMITIVES:
#                         update_map(file, var_name, t, source="regex_for_loop")

#                 for m in simple_local_decl.finditer(code):
#                     raw_type, var_name = m.group(1), m.group(2)
#                     t = clean_type(raw_type)
#                     if t and t not in PRIMITIVES:
#                         update_map(file, var_name, t, source="regex_simple_local")

#                 for pl_match in method_param_list_pat.finditer(code):
#                     for pm in method_param_decl_pat.finditer(pl_match.group(1)):
#                         raw_type, var_name = pm.group(1), pm.group(2)
#                         t = clean_type(raw_type)
#                         if t and t not in PRIMITIVES:
#                             update_map(file, var_name, t, source="regex_param")

#         return obj_class_map

#     # ------------------------------------------------------------------
#     # Method return index
#     # ------------------------------------------------------------------

#     def build_method_return_index(self, app_folder: str) -> dict:
#         method_return_index = {}
#         class_decl_pat = re.compile(r'\bclass\s+(\w+)\b')
#         method_sig_pat = re.compile(
#             r'(?:public|protected|private)?\s+(?:static\s+)?([\w\.&lt;<>\[\]]+)\s+(\w+)\s*\(',
#             re.MULTILINE,
#         )
#         constructor_sig_pat = re.compile(
#             r'(?:public|protected|private)\s+(\w+)\s*\(', re.MULTILINE
#         )

#         for root, _, files in os.walk(app_folder):
#             for file in files:
#                 if not file.endswith(self.file_extension()):
#                     continue
#                 fpath = os.path.join(root, file)
#                 try:
#                     with open(fpath, "r", encoding="utf-8") as f:
#                         code = f.read()
#                 except Exception:
#                     continue

#                 try:
#                     parsed = javalang.parse.parse(code)
#                 except Exception:
#                     parsed = None

#                 if parsed:
#                     for _, cls in parsed.filter(jt.ClassDeclaration):
#                         cls_name = getattr(cls, "name", None)
#                         if not cls_name:
#                             continue
#                         method_return_index.setdefault(cls_name, {})
#                         for _, m in cls.filter(jt.MethodDeclaration):
#                             rt = m.return_type
#                             if rt is None:
#                                 rname = "void"
#                             else:
#                                 base = rt.name if hasattr(rt, "name") else "Unknown"
#                                 rname = re.sub(
#                                     r'(&amp;lt;[^&amp;gt]+&amp;gt;|&lt;[^&gt;]+&gt;)', '', base
#                                 )
#                             method_return_index[cls_name][m.name] = rname.strip().split('.')[-1]
#                         for _, c in cls.filter(jt.ConstructorDeclaration):
#                             method_return_index[cls_name][c.name] = "<constructor>"

#                     for _, itf in parsed.filter(jt.InterfaceDeclaration):
#                         itf_name = getattr(itf, "name", None)
#                         if not itf_name:
#                             continue
#                         method_return_index.setdefault(itf_name, {})
#                         for _, m in itf.filter(jt.MethodDeclaration):
#                             rt = m.return_type
#                             if rt is None:
#                                 rname = "void"
#                             else:
#                                 base = rt.name if hasattr(rt, "name") else "Unknown"
#                                 rname = re.sub(
#                                     r'(&amp;lt;[^&amp;gt]+&amp;gt;|&lt;[^&gt;]+&gt;)', '', base
#                                 )
#                             method_return_index[itf_name][m.name] = rname.strip().split('.')[-1]
#                     continue

#                 # Regex fallback
#                 cls_match = class_decl_pat.search(code)
#                 if not cls_match:
#                     continue
#                 cls_name = cls_match.group(1)
#                 method_return_index.setdefault(cls_name, {})
#                 for mm in method_sig_pat.finditer(code):
#                     return_type = mm.group(1)
#                     method_name = mm.group(2)
#                     simple_return = re.sub(
#                         r'(&amp;lt;[^&amp;gt]+&amp;gt;|&lt;[^&gt;]+&gt;)', '', return_type
#                     ).strip().split('.')[-1]
#                     method_return_index[cls_name][method_name] = simple_return
#                 for cm in constructor_sig_pat.finditer(code):
#                     ctor_name = cm.group(1)
#                     if ctor_name == cls_name:
#                         method_return_index[cls_name][ctor_name] = "<constructor>"

#         return method_return_index

#     # ------------------------------------------------------------------
#     # File → type map
#     # ------------------------------------------------------------------

#     def find_type_to_file_map(self, app_folder: str) -> dict:
#         java_files_map = {}
#         for root, _, files in os.walk(app_folder):
#             for f in files:
#                 if f.endswith(self.file_extension()):
#                     class_name = os.path.splitext(f)[0]
#                     java_files_map[class_name] = os.path.join(root, f)
#         return java_files_map

#     # ------------------------------------------------------------------
#     # LOC counter
#     # ------------------------------------------------------------------

#     def extract_method_loc(
#         self,
#         java_file_path: str,
#         method_name: str,
#         classname=None,
#         include_package_private: bool = False,
#         count_empty_lines: bool = True,
#     ):
#         if not java_file_path:
#             return None

#         try:
#             with open(java_file_path, "r", encoding="utf-8") as f:
#                 code = f.read()
#         except Exception:
#             try:
#                 with open(java_file_path, "r", encoding="latin-1") as f:
#                     code = f.read()
#             except Exception:
#                 return None

#         code = code.replace("\r\n", "\n").replace("\r", "\n")
#         lines = code.split("\n")

#         access_req = r"(?:public|private|protected)"
#         access = rf"(?:{access_req})?" if include_package_private else access_req
#         mname_esc = re.escape(method_name)

#         method_decl_pat = rf"""
#             (?m)
#             ^[ \t]*
#             {access}[ \t]*
#             (?:(?:static|final|abstract|synchronized|native|strictfp)\b[ \t]*)*
#             [\w.<>\[\],? \t]+
#             \b(?P<mname>{mname_esc})[ \t]*\(
#         """

#         constructor_decl_pat = None
#         if classname and method_name == classname:
#             cname_esc = re.escape(classname)
#             constructor_decl_pat = rf"""
#                 (?m)
#                 ^[ \t]*
#                 {access}[ \t]*
#                 (?:(?:static|final|abstract|synchronized|native|strictfp)\b[ \t]*)*
#                 \b(?P<mname>{cname_esc})[ \t]*\(
#             """

#         patterns = []
#         if constructor_decl_pat:
#             patterns.append(re.compile(constructor_decl_pat, re.IGNORECASE | re.VERBOSE))
#         patterns.append(re.compile(method_decl_pat, re.IGNORECASE | re.VERBOSE))

#         decl_match = None
#         for pat in patterns:
#             decl_match = pat.search(code)
#             if decl_match:
#                 break
#         if not decl_match:
#             return None

#         sig_line_idx = code.count("\n", 0, decl_match.start("mname")) + 1

#         def find_annotation_block_start(sig_idx):
#             i = sig_idx - 2
#             if i < 0:
#                 return None
#             paren_balance = 0
#             started = False
#             start_line = None
#             while i >= 0:
#                 raw = lines[i].rstrip()
#                 if not raw.strip() and not (started and paren_balance > 0):
#                     break
#                 is_anno = bool(re.match(r'^[ \t]*@', raw))
#                 if not started:
#                     if is_anno:
#                         started = True
#                         start_line = i + 1
#                         paren_balance = raw.count("(") - raw.count(")")
#                     else:
#                         break
#                 else:
#                     if is_anno or paren_balance > 0:
#                         start_line = i + 1
#                         paren_balance += raw.count("(") - raw.count(")")
#                     else:
#                         break
#                 i -= 1
#             return start_line

#         anno_start = find_annotation_block_start(sig_line_idx)
#         start_line_idx = anno_start if anno_start is not None else sig_line_idx

#         def find_opening_brace_line(from_line):
#             in_block_comment = False
#             for i in range(from_line - 1, len(lines)):
#                 line = lines[i]
#                 j, n = 0, len(line)
#                 in_string = False
#                 string_char = None
#                 while j < n:
#                     ch = line[j]
#                     nxt = line[j + 1] if j + 1 < n else ""
#                     if in_block_comment:
#                         if ch == "*" and nxt == "/":
#                             in_block_comment = False
#                             j += 2
#                             continue
#                         j += 1
#                         continue
#                     if in_string:
#                         if ch == "\\":
#                             j += 2
#                             continue
#                         if ch == string_char:
#                             in_string = False
#                             string_char = None
#                         j += 1
#                         continue
#                     if ch == "/" and nxt == "*":
#                         in_block_comment = True
#                         j += 2
#                         continue
#                     if ch == "/" and nxt == "/":
#                         break
#                     if ch in ("'", '"'):
#                         in_string = True
#                         string_char = ch
#                         j += 1
#                         continue
#                     if ch == "{":
#                         return i + 1
#                     j += 1
#             return None

#         def find_closing_brace_line(open_line):
#             in_block_comment = False
#             depth = 0
#             started = False
#             for i in range(open_line - 1, len(lines)):
#                 line = lines[i]
#                 j, n = 0, len(line)
#                 in_string = False
#                 string_char = None
#                 while j < n:
#                     ch = line[j]
#                     nxt = line[j + 1] if j + 1 < n else ""
#                     if in_block_comment:
#                         if ch == "*" and nxt == "/":
#                             in_block_comment = False
#                             j += 2
#                             continue
#                         j += 1
#                         continue
#                     if in_string:
#                         if ch == "\\":
#                             j += 2
#                             continue
#                         if ch == string_char:
#                             in_string = False
#                             string_char = None
#                         j += 1
#                         continue
#                     if ch == "/" and nxt == "*":
#                         in_block_comment = True
#                         j += 2
#                         continue
#                     if ch == "/" and nxt == "/":
#                         break
#                     if ch in ("'", '"'):
#                         in_string = True
#                         string_char = ch
#                         j += 1
#                         continue
#                     if ch == "{":
#                         depth += 1
#                         started = True
#                     elif ch == "}":
#                         depth -= 1
#                         if started and depth == 0:
#                             return i + 1
#                     j += 1
#             return None

#         brace_open_line = find_opening_brace_line(sig_line_idx)
#         if brace_open_line is None:
#             return 1

#         end_line_idx = find_closing_brace_line(brace_open_line)
#         if end_line_idx is None:
#             end_line_idx = len(lines)

#         if count_empty_lines:
#             return max(1, end_line_idx - start_line_idx + 1)
#         else:
#             segment = lines[start_line_idx - 1:end_line_idx]
#             return max(1, sum(1 for ln in segment if ln.strip()))

#     # ------------------------------------------------------------------
#     # Properties extraction
#     # ------------------------------------------------------------------

#     def load_all_properties(self, app_folder, additional_property_refs=None):
#         app_folder = Path(app_folder)
#         paths = set()
#         for p in app_folder.rglob("*.properties"):
#             paths.add(p.resolve())

#         if additional_property_refs:
#             for ref in additional_property_refs:
#                 ref_norm = self._normalize_ps_ref(ref)
#                 matches = list(app_folder.rglob(ref_norm))
#                 if not matches:
#                     matches = list(app_folder.rglob(os.path.basename(ref_norm)))
#                 for m in matches:
#                     paths.add(m.resolve())

#         props = {}
#         for p in sorted(paths, key=str):
#             try:
#                 with open(p, "r", encoding="utf-8") as fh:
#                     for raw in fh:
#                         line = raw.strip()
#                         if not line or line.startswith("#") or line.startswith("!"):
#                             continue
#                         if "=" in line:
#                             k, v = line.split("=", 1)
#                         elif ":" in line:
#                             k, v = line.split(":", 1)
#                         else:
#                             continue
#                         props[k.strip()] = v.strip()
#             except Exception as e:
#                 print(f"Error reading {p}: {e}")
#         return props

#     def extract_application_properties_from_folder(
#         self,
#         app_folder,
#         include_filepath: bool = True,
#         include_trailing_dot: bool = True,
#     ):
#         def _compose(jpath: Path, method_name) -> str:
#             base = jpath.stem
#             if method_name:
#                 return f"{base}.{method_name}"
#             return f"{base}." if include_trailing_dot else base

#         app_folder = Path(app_folder)
#         ps_refs = set()
#         java_paths = []

#         for p in app_folder.rglob("*.java"):
#             java_paths.append(p.resolve())
#             try:
#                 txt = p.read_text(encoding="utf-8")
#             except Exception:
#                 try:
#                     txt = p.read_text(encoding="latin-1")
#                 except Exception:
#                     txt = ""
#             for m in re_property_source.finditer(txt):
#                 ps_refs.add(m.group(1).strip())

#         properties_map = self.load_all_properties(app_folder, additional_property_refs=ps_refs)
#         rows = []

#         for jf in java_paths:
#             try:
#                 code = jf.read_text(encoding="utf-8")
#             except Exception:
#                 try:
#                     code = jf.read_text(encoding="latin-1")
#                 except Exception:
#                     code = ""

#             method_index_map = self._build_method_index_map(code)

#             # @Value
#             for item in self._extract_values_with_vars(code):
#                 key = item["Property"]
#                 var = item["Variable"]
#                 actual = properties_map.get(key, "NOT_FOUND")
#                 method_name = None
#                 if var:
#                     pattern = re.compile(r'\b' + re.escape(var) + r'\b')
#                     for mu in pattern.finditer(code, item["span_end"]):
#                         method_name = self._find_enclosing_method(method_index_map, mu.start())
#                         if method_name:
#                             break
#                 rows.append({
#                     "FileName": jf.name.replace(".java", ""),
#                     "FilePath": str(jf),
#                     "Filename.methodname": _compose(jf, method_name),
#                     "Annotation": item["Annotation"],
#                     "Property": key,
#                     "Variable": var,
#                     "method_name": method_name,
#                     "Actual Value": actual,
#                 })

#             # @ConfigurationProperties
#             for m in re_configuration_properties.finditer(code):
#                 prefix = m.group(1)
#                 matched = {k: v for k, v in properties_map.items()
#                            if k == prefix or k.startswith(prefix + ".")}
#                 actual = "; ".join(f"{k}={v}" for k, v in matched.items()) if matched else "NOT_FOUND"
#                 rows.append({
#                     "FileName": jf.name.replace(".java", ""),
#                     "FilePath": str(jf),
#                     "Filename.methodname": _compose(jf, None),
#                     "Annotation": "@ConfigurationProperties",
#                     "Property": prefix,
#                     "Variable": None,
#                     "method_name": None,
#                     "Actual Value": actual,
#                 })

#             # @PropertySource
#             for m in re_property_source.finditer(code):
#                 rows.append({
#                     "FileName": jf.name.replace(".java", ""),
#                     "FilePath": str(jf),
#                     "Filename.methodname": _compose(jf, None),
#                     "Annotation": "@PropertySource",
#                     "Property": m.group(1),
#                     "Variable": None,
#                     "method_name": None,
#                     "Actual Value": "FILE_REFERENCE",
#                 })

#             # messageSource.getMessage(...)
#             for mm in re_message_key.finditer(code):
#                 key = mm.group(1)
#                 actual = properties_map.get(key, "NOT_FOUND")
#                 method_name = self._find_enclosing_method(method_index_map, mm.start())
#                 rows.append({
#                     "FileName": jf.name.replace(".java", ""),
#                     "FilePath": str(jf),
#                     "Filename.methodname": _compose(jf, method_name),
#                     "Annotation": "MessageSource",
#                     "Property": key,
#                     "Variable": None,
#                     "method_name": method_name,
#                     "Actual Value": actual,
#                 })

#         df = pd.DataFrame(rows)
#         if include_filepath:
#             cols = ["FileName", "FilePath", "Filename.methodname", "Annotation",
#                     "Property", "Variable", "method_name", "Actual Value"]
#         else:
#             cols = ["FileName", "Filename.methodname", "Annotation",
#                     "Property", "Variable", "method_name", "Actual Value"]
#         df = df.reindex(columns=cols)
#         if "method_name" in df.columns:
#             df = df[df["method_name"].notna()]
#         return df

# import os
# import re
# import html
# import javalang
# import javalang.tree as jt
# import pandas as pd
# from pathlib import Path

# from method_lineage_generation import LanguageAdapter

# # ---------------------------------------------------------------------------
# # Module-level compiled patterns (Java 8 safe — no var, no records, etc.)
# # ---------------------------------------------------------------------------

# re_value_dollar = re.compile(r'@Value\s*\(\s*["\']\$\{([^}]+)\}["\']\s*\)')
# re_value_spel_dollar = re.compile(r'@Value\s*\(\s*["\']#\{\s*\$\{([^}]+)\}\s*\}["\']\s*\)')
# re_field_decl = re.compile(
#     r'(?:private|public|protected)?\s*[\w<>\[\],\s?]+\s+([A-Za-z_]\w*)\s*(?:=|;)', re.M
# )
# re_configuration_properties = re.compile(
#     r'@ConfigurationProperties\s*\(\s*(?:prefix\s*=\s*)?["\']([^"\')]+)["\']\s*\)'
# )
# re_property_source = re.compile(
#     r'@PropertySource\s*\(\s*(?:value\s*=\s*)?["\']([^"\')]+)["\']'
# )
# re_message_key = re.compile(
#     r'messageSource\.getMessage\s*\(\s*["\']([^"\']+)["\']'
# )

# # Java 8 method declaration regex — same structure as Java 18 adapter but
# # explicitly excludes 'var' as a return type (Java 10+ only).
# re_method_decl = re.compile(
#     r'''
#     ^\s*
#     (?:@\w+(?:\([^)]*\))?\s*)*
#     (?:(?:public|private|protected)\s+)?
#     (?:static\s+|final\s+|synchronized\s+|native\s+|abstract\s+|default\s+)*
#     (?:<[^>]+>\s+)?
#     (?!var\b)                                        # Java 8: no 'var' type inference
#     (?:[\w\[\]<>?,]+\s+)+
#     (?!(?:if|for|while|switch|catch|else)\b)
#     ([A-Za-z_]\w*)
#     \s*\(
#     \s*(
#         (?:
#         (?:@\w+(?:\([^)]*\))?\s*)*
#         (?:final\s+)?
#         [\w\[\]<>?,]+(?:\s*\.\.\.)?\s+
#         [A-Za-z_]\w*
#         )
#         (?:\s*,\s*
#         (?:@\w+(?:\([^)]*\))?\s*)*
#         (?:final\s+)?
#         [\w\[\]<>?,]+(?:\s*\.\.\.)?\s+
#         [A-Za-z_]\w*
#         )*
#     )?
#     \)\s*\{
#     ''',
#     re.M | re.X
# )


# # ---------------------------------------------------------------------------
# # Module-level helper (mirrors the Java 18 adapter)
# # ---------------------------------------------------------------------------

# def _strip_source_comments(src: str) -> str:
#     """Remove // and /* */ comments while preserving string literals."""
#     result = []
#     i = 0
#     n = len(src)
#     in_string = False
#     string_char = None

#     while i < n:
#         ch = src[i]
#         nxt = src[i + 1] if i + 1 < n else ""

#         if in_string:
#             result.append(ch)
#             if ch == '\\':
#                 i += 1
#                 if i < n:
#                     result.append(src[i])
#             elif ch == string_char:
#                 in_string = False
#                 string_char = None
#             i += 1
#             continue

#         if ch == '/' and nxt == '/':
#             while i < n and src[i] != '\n':
#                 i += 1
#             continue

#         if ch == '/' and nxt == '*':
#             i += 2
#             while i < n - 1:
#                 if src[i] == '*' and src[i + 1] == '/':
#                     i += 2
#                     break
#                 i += 1
#             continue

#         if ch in ('"', "'"):
#             in_string = True
#             string_char = ch

#         result.append(ch)
#         i += 1

#     return ''.join(result)


# # ---------------------------------------------------------------------------
# # Java 8 Adapter
# # ---------------------------------------------------------------------------

# class JavaAdapter(LanguageAdapter):
#     """
#     LanguageAdapter implementation for Java 8 codebases.

#     Compared to the Java 18 adapter:
#       - parse_ast uses javalang directly (javalang targets Java 8).
#       - No special handling for records, sealed classes, text blocks,
#         switch expressions, or 'var' type inference.
#       - Lambda bodies and stream chains are captured via the chained-call
#         regex path (same approach as the Java 18 adapter's fallback).
#       - Default / static interface methods (new in Java 8) are handled
#         through get_methods_in_type, which yields MethodDeclaration nodes
#         on interface bodies.
#     """

#     # ------------------------------------------------------------------
#     # Internal helpers
#     # ------------------------------------------------------------------

#     def _normalize_ps_ref(self, ps_str: str) -> str:
#         if ps_str.startswith("classpath:"):
#             return ps_str[len("classpath:"):]
#         if ps_str.startswith("file:"):
#             return ps_str[len("file:"):]
#         return ps_str

#     def _build_method_index_map(self, java_text: str):
#         """Map of (start_pos, method_name) tuples sorted by position."""
#         res = []
#         for m in re_method_decl.finditer(java_text):
#             res.append((m.start(), m.group(1)))
#         res.sort(key=lambda x: x[0])
#         return res

#     def _find_enclosing_method(self, method_index_map, pos):
#         candidate = None
#         for start, name in method_index_map:
#             if start <= pos:
#                 candidate = name
#             else:
#                 break
#         return candidate

#     def _extract_values_with_vars(self, java_text: str):
#         results = []
#         for m in re_value_spel_dollar.finditer(java_text):
#             key = m.group(1)
#             span_end = m.end()
#             var = None
#             m2 = re_field_decl.search(java_text, span_end)
#             if m2:
#                 var = m2.group(1)
#             results.append({
#                 "Annotation": "@Value", "Property": key,
#                 "Variable": var, "span_start": m.start(), "span_end": span_end
#             })

#         for m in re_value_dollar.finditer(java_text):
#             key = m.group(1)
#             span_end = m.end()
#             var = None
#             m2 = re_field_decl.search(java_text, span_end)
#             if m2:
#                 var = m2.group(1)
#             results.append({
#                 "Annotation": "@Value", "Property": key,
#                 "Variable": var, "span_start": m.start(), "span_end": span_end
#             })

#         return results

#     # ------------------------------------------------------------------
#     # Configuration
#     # ------------------------------------------------------------------

#     def file_extension(self) -> str:
#         ext = self.details.get("extension")
#         if isinstance(ext, str) and ext.strip():
#             return ext.strip()
#         return ".java"

#     def _rx(self, key: str, flags: int = 0):
#         pat = self.regex.get(key)
#         if not isinstance(pat, str):
#             raise KeyError(f"Regex key '{key}' missing or not a string")
#         unesc = html.unescape(pat)
#         try:
#             return re.compile(unesc, flags)
#         except re.error as err:
#             raise re.error(
#                 f"[regex compile] key='{key}' pattern='{unesc}' error={err}"
#             ) from err

#     # ------------------------------------------------------------------
#     # AST Parsing  (javalang targets Java 8 — no extra pre-processing needed)
#     # ------------------------------------------------------------------

#     def parse_ast(self, code: str):
#         """
#         Parse Java 8 source.  javalang handles all Java 8 features natively
#         (lambdas, streams, default interface methods, diamond operator, etc.).
#         Returns the compilation unit tree, or None on failure.
#         """
#         try:
#             return javalang.parse.parse(code)
#         except Exception:
#             return None

#     # ------------------------------------------------------------------
#     # Type helpers
#     # ------------------------------------------------------------------

#     def _simple_type_name(self, type_obj_or_str):
#         if type_obj_or_str is None:
#             return None
#         n = type_obj_or_str.name if hasattr(type_obj_or_str, "name") else str(type_obj_or_str)
#         # Strip HTML-escaped generics
#         n = re.sub(r'\s*&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;\s*', '', n)
#         n = re.sub(r'\s*&lt;[^&gt;]+&gt;\s*', '', n)
#         return n.split('.')[-1]

#     def _extract_method_annotations(self, method_node) -> str:
#         ann_list = []
#         if hasattr(method_node, "annotations") and method_node.annotations:
#             for ann in method_node.annotations:
#                 try:
#                     ann_list.append("@" + (ann.name if hasattr(ann, "name") else str(ann)))
#                 except Exception:
#                     continue
#         return ", ".join(ann_list) if ann_list else ""

#     def _extract_method_declaration_type(self, method_node) -> str:
#         if hasattr(method_node, "modifiers") and method_node.modifiers:
#             mods = {m.lower() for m in method_node.modifiers}
#             if "public" in mods:
#                 return "Public"
#             if "private" in mods:
#                 return "Private"
#             if "protected" in mods:
#                 return "Protected"
#         return "Default"

#     def _extract_return_type(self, method_node) -> str:
#         try:
#             rt = method_node.return_type
#             if rt is None:
#                 return "void"
#             base = rt.name if hasattr(rt, "name") else "Unknown"
#             if hasattr(rt, "arguments") and rt.arguments:
#                 args = []
#                 for arg in rt.arguments:
#                     if hasattr(arg, "type") and hasattr(arg.type, "name"):
#                         args.append(arg.type.name)
#                     elif hasattr(arg, "name"):
#                         args.append(arg.name)
#                 return f"{base}&amp;lt;{', '.join(args)}&amp;gt;"
#             return base
#         except Exception:
#             return "Unknown"

#     def _type_to_simple(self, t) -> str:
#         if t is None:
#             return ""
#         base = getattr(t, "name", str(t)) or ""
#         if "." in base:
#             base = base.split(".")[-1]
#         dims = "[]" * int(getattr(t, "dimensions", 0) or 0)
#         return f"{base}{dims}"

#     def extract_method_metadata(self, method_node) -> dict:
#         is_ctor = isinstance(method_node, jt.ConstructorDeclaration)

#         param_types = []
#         for p in getattr(method_node, "parameters", []) or []:
#             t = self._type_to_simple(getattr(p, "type", None))
#             if getattr(p, "varargs", False):
#                 t = t + "[]"
#             param_types.append(t or "")

#         return {
#             "Annotations": self._extract_method_annotations(method_node),
#             "Method_Declaration_Type": self._extract_method_declaration_type(method_node),
#             "return_type": "constructor" if is_ctor else self._extract_return_type(method_node),
#             "member_kind": "Constructor" if is_ctor else "Method",
#             "Parameters": ", ".join(param_types),
#             "Parameter_Arity": len(param_types),
#             "Parameter_Types": ";".join(param_types),
#         }

#     # ------------------------------------------------------------------
#     # Import helpers
#     # ------------------------------------------------------------------

#     def _collect_com_imports(self, tree):
#         imports_types = set()
#         wildcard_packages = set()
#         static_members = set()
#         static_wildcard_classes = set()

#         for imp in getattr(tree, "imports", []):
#             path = getattr(imp, "path", "")
#             if not isinstance(path, str) or not path.startswith("nl."):
#                 continue
#             parts = [p for p in path.split('.') if p]
#             if getattr(imp, "static", False):
#                 if parts[-1] == '*':
#                     if len(parts) >= 2:
#                         static_wildcard_classes.add(parts[-2])
#                 else:
#                     static_members.add(parts[-1])
#                     if len(parts) >= 2:
#                         imports_types.add(parts[-2])
#             else:
#                 if parts[-1] == '*':
#                     wildcard_packages.add('.'.join(parts[:-1]))
#                 else:
#                     imports_types.add(parts[-1])

#         return imports_types, wildcard_packages, static_members, static_wildcard_classes

#     # ------------------------------------------------------------------
#     # Field / DI helpers
#     # ------------------------------------------------------------------

#     def _collect_autowired_fields(self, class_node) -> dict:
#         autowired = {}
#         for _, fd in class_node.filter(jt.FieldDeclaration):
#             has_auto = any(
#                 getattr(a, "name", "") in ("Autowired", "Inject")
#                 for a in (fd.annotations or [])
#             )
#             if not has_auto:
#                 continue
#             tname = self._simple_type_name(fd.type)
#             for decl in getattr(fd, "declarators", []):
#                 autowired[decl.name] = tname
#         return autowired

#     # ------------------------------------------------------------------
#     # Variable type inference
#     # ------------------------------------------------------------------

#     def _infer_type_from_initializer(self, decl):
#         init = getattr(decl, "initializer", None)
#         try:
#             if isinstance(init, jt.ClassCreator):
#                 return self._simple_type_name(init.type)
#         except Exception:
#             pass
#         return None

#     def _build_var_types_for_method(self, method_node, autowired_fields):
#         var_types = {}
#         locals_from_new = set()
#         params_set = set()

#         for p in getattr(method_node, "parameters", []):
#             var_types[p.name] = self._simple_type_name(p.type)
#             params_set.add(p.name)

#         for _, lv in method_node.filter(jt.LocalVariableDeclaration):
#             declared_type = self._simple_type_name(lv.type)
#             for decl in getattr(lv, "declarators", []):
#                 name = decl.name
#                 tname = declared_type
#                 # Java 8 has no 'var'; skip the var-inference branch from Java 18 adapter
#                 inferred = self._infer_type_from_initializer(decl)
#                 if inferred:
#                     tname = inferred
#                     locals_from_new.add(name)
#                 var_types[name] = tname

#         var_types.update(autowired_fields or {})
#         return var_types, locals_from_new, params_set

#     # ------------------------------------------------------------------
#     # Call-filtering helpers
#     # ------------------------------------------------------------------

#     def _normalize_qualifier(self, qual: str, var_types: dict) -> str:
#         if qual in var_types:
#             return qual
#         for k in var_types:
#             if qual == (k + k):
#                 return k
#         return qual

#     def _is_same_package_type(self, type_name, package_name) -> bool:
#         return bool(package_name and package_name.startswith('nl.') and type_name)

#     def _keep_qualified_call(self, qual, var_types, imports_types, autowired_fields,
#                               wildcard_packages, locals_from_new, params_set, package_name) -> bool:
#         qual = self._normalize_qualifier(qual, var_types)
#         if qual in autowired_fields:
#             return True
#         t = var_types.get(qual)
#         if t and qual in locals_from_new and self.accept_local_new_types:
#             return True
#         if t and qual in params_set and self.accept_parameter_types:
#             return True
#         if t and t in imports_types:
#             return True
#         if qual in imports_types:
#             return True
#         if wildcard_packages and t:
#             return True
#         if self.accept_same_package and self._is_same_package_type(t, package_name):
#             return True
#         if t:          # variable has a known declared type in var_types
#             return True
#         return False
#     def _keep_unqualified_call(self, member, static_members, static_wildcard_classes) -> bool:
#         if self.include_unqualified:
#             return True
#         if member in static_members:
#             return True
#         if static_wildcard_classes:
#             return True
#         return False

#     def _get_package_name(self, java_code: str):
#         pat = html.unescape(
#             self.regex.get("package", r'^\s*package\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;')
#         )
#         m = re.search(pat, java_code, flags=re.MULTILINE)
#         return m.group(1) if m else None

#     # ------------------------------------------------------------------
#     # Declared types
#     # ------------------------------------------------------------------

#     def get_declared_types(self, ast):
#         """
#         Yield (name, kind, node) for classes, interfaces, and enums.
#         Java 8 does NOT have records or sealed classes — those are omitted.
#         """
#         types = []

#         # Classes
#         for _, cls in ast.filter(jt.ClassDeclaration):
#             types.append((getattr(cls, "name", "Unknown"), "class", cls))

#         # Interfaces (including those with default/static methods — Java 8)
#         for _, ifc in ast.filter(jt.InterfaceDeclaration):
#             types.append((getattr(ifc, "name", "Unknown"), "interface", ifc))

#         # Enums
#         for _, en in ast.filter(jt.EnumDeclaration):
#             types.append((getattr(en, "name", "Unknown"), "enum", en))

#         return types

#     def get_methods_in_type(self, type_node):
#         """
#         Yield (name, node) for every method and constructor in type_node.
#         For interfaces, this includes default and static methods (Java 8+).
#         """
#         for _, m in type_node.filter(jt.MethodDeclaration):
#             yield m.name, m
#         for _, c in type_node.filter(jt.ConstructorDeclaration):
#             yield c.name, c

#     # ------------------------------------------------------------------
#     # Method source extraction
#     # ------------------------------------------------------------------

#     def _get_method_source(self, code: str, method_node):
#         try:
#             lines = code.splitlines(True)
#             if hasattr(method_node, "position") and method_node.position and method_node.position[0]:
#                 start_line = method_node.position[0] - 1
#                 start_offset = sum(len(lines[i]) for i in range(start_line))
#                 start_brace_idx = code.find('{', start_offset)
#                 if start_brace_idx == -1:
#                     return None
#                 brace_count = 0
#                 end_idx = None
#                 for i in range(start_brace_idx, len(code)):
#                     ch = code[i]
#                     if ch == '{':
#                         brace_count += 1
#                     elif ch == '}':
#                         brace_count -= 1
#                         if brace_count == 0:
#                             end_idx = i
#                             break
#                 return code[start_brace_idx:end_idx + 1] if end_idx is not None else None
#             else:
#                 mname = getattr(method_node, "name", None)
#                 if not mname:
#                     return None
#                 sig_pat = re.compile(
#                     r'\b' + re.escape(mname) + r'\s*\([^)]*\)\s*\{',
#                     re.MULTILINE | re.DOTALL
#                 )
#                 match = sig_pat.search(code)
#                 if not match:
#                     return None
#                 start_brace_idx = match.end() - 1
#                 brace_count = 0
#                 end_idx = None
#                 for i in range(start_brace_idx, len(code)):
#                     ch = code[i]
#                     if ch == '{':
#                         brace_count += 1
#                     elif ch == '}':
#                         brace_count -= 1
#                         if brace_count == 0:
#                             end_idx = i
#                             break
#                 return code[start_brace_idx:end_idx + 1] if end_idx is not None else None
#         except Exception:
#             return None

#     # ------------------------------------------------------------------
#     # Dynamic qualifier / chained-call extraction
#     # ------------------------------------------------------------------

#     def _extract_dynamic_terminal_methods(self, text: str) -> set:
#         """
#         Extract terminal method names from dynamic chains such as
#         map.get(key).doSomething(...).
#         Java 8 streams produce many such patterns.
#         """
#         if not isinstance(text, str) or not text.strip():
#             return set()

#         pat = None
#         if isinstance(self.regex.get("re_dynamic_qual"), str) and self.regex["re_dynamic_qual"].strip():
#             try:
#                 pat = self._rx("re_dynamic_qual", flags=re.MULTILINE | re.DOTALL)
#             except Exception:
#                 pat = None

#         terms = set()
#         if pat is not None:
#             for m in pat.finditer(text):
#                 try:
#                     name = m.group(1)
#                     if isinstance(name, str) and name.strip():
#                         terms.add(f"{name.strip()}()")
#                 except Exception:
#                     continue
#             return terms

#         # Default single-dynamic-segment: base(...).terminal(...)
#         single_dyn = re.compile(
#             r"""\b[A-Za-z_]\w*\s*\([^()]*\)\s*\.\s*([A-Za-z_]\w*)\s*\(""",
#             re.MULTILINE | re.DOTALL,
#         )
#         for m in single_dyn.finditer(text):
#             name = m.group(1)
#             if name and name.strip():
#                 terms.add(f"{name.strip()}()")

#         # Multi-segment chains: base(...).m1(...).m2(...)
#         chain_dyn = re.compile(
#             r"""\b[A-Za-z_]\w*\s*\([^()]*\)(?:\s*\.\s*[A-Za-z_]\w*\s*\([^()]*\))+""",
#             re.MULTILINE | re.DOTALL,
#         )
#         for cm in chain_dyn.finditer(text):
#             last_methods = re.findall(r'\.\s*([A-Za-z_]\w*)\s*\(', cm.group(0))
#             if last_methods:
#                 terms.add(f"{last_methods[-1].strip()}()")

#         return terms

#     # ------------------------------------------------------------------
#     # Expression walker (for nested invocations)
#     # ------------------------------------------------------------------

#     def _collect_invocations_in_expression(
#         self, expr, *,
#         var_types, imports_types, autowired_fields,
#         wildcard_packages, locals_from_new, params_set, package_name,
#     ) -> set:
#         calls = set()
#         if expr is None:
#             return calls
#         try:
#             if isinstance(expr, jt.MethodInvocation):
#                 qual = expr.qualifier or ""
#                 member = expr.member
#                 if qual and ("(" in qual or ")" in qual):
#                     calls.add(f"{qual}.{member}()")
#                 elif qual:
#                     qual = self._normalize_qualifier(qual, var_types)
#                     if self._keep_qualified_call(
#                         qual, var_types, imports_types, autowired_fields,
#                         wildcard_packages, locals_from_new, params_set, package_name,
#                     ):
#                         resolved_type = var_types.get(qual, qual)
#                         calls.add(f"{resolved_type}.{member}()")
#                 else:
#                     if self._keep_unqualified_call(member, set(), set()):
#                         calls.add(f"{member}()")
#                 for a in getattr(expr, "arguments", []) or []:
#                     calls |= self._collect_invocations_in_expression(
#                         a, var_types=var_types, imports_types=imports_types,
#                         autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
#                         locals_from_new=locals_from_new, params_set=params_set,
#                         package_name=package_name,
#                     )
#                 return calls

#             if isinstance(expr, jt.ClassCreator):
#                 ctor_type = self._simple_type_name(expr.type)
#                 if ctor_type:
#                     calls.add(f"{ctor_type}.{ctor_type}()")
#                 for a in getattr(expr, "arguments", []) or []:
#                     calls |= self._collect_invocations_in_expression(
#                         a, var_types=var_types, imports_types=imports_types,
#                         autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
#                         locals_from_new=locals_from_new, params_set=params_set,
#                         package_name=package_name,
#                     )
#                 return calls

#             for attr in ("expression", "condition", "then_expression", "else_expression",
#                          "left", "right", "operand"):
#                 node = getattr(expr, attr, None)
#                 if node is not None:
#                     calls |= self._collect_invocations_in_expression(
#                         node, var_types=var_types, imports_types=imports_types,
#                         autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
#                         locals_from_new=locals_from_new, params_set=params_set,
#                         package_name=package_name,
#                     )
#             for list_attr in ("expressions", "arguments"):
#                 lst = getattr(expr, list_attr, None)
#                 if isinstance(lst, (list, tuple)):
#                     for node in lst:
#                         calls |= self._collect_invocations_in_expression(
#                             node, var_types=var_types, imports_types=imports_types,
#                             autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
#                             locals_from_new=locals_from_new, params_set=params_set,
#                             package_name=package_name,
#                         )
#         except Exception:
#             pass
#         return calls

#     # ------------------------------------------------------------------
#     # Main call-finder (AST path)
#     # ------------------------------------------------------------------

#     def find_calls_in_method(self, type_node, method_node, code: str) -> list:
#         calls = set()
#         package_name = self._get_package_name(code)

#         # FIX 1 & 3: Re-use cached AST instead of re-parsing the full file
#         # on every method call.  _raw_ast_cache is injected by configure().
#         _cache_key = id(code)  # code object is the same str within one file run
#         _cached = self._raw_ast_cache.get(_cache_key)
#         if _cached is None:
#             try:
#                 _cached = javalang.parse.parse(code)
#             except Exception:
#                 _cached = False  # sentinel: parse failed
#             self._raw_ast_cache[_cache_key] = _cached

#         try:
#             if _cached and _cached is not False:
#                 tree = _cached
#             else:
#                 tree = javalang.parse.parse(code)
#             imports_types, wildcard_packages, static_members, static_wildcard_classes = \
#                 self._collect_com_imports(tree)
#         except Exception:
#             imports_types, wildcard_packages, static_members, static_wildcard_classes = \
#                 set(), set(), set(), set()

#         autowired_fields = self._collect_autowired_fields(type_node)
#         var_types, locals_from_new, params_set = self._build_var_types_for_method(
#             method_node, autowired_fields
#         )

#         # Include for-loop element variables
#         for _, forstmt in method_node.filter(jt.ForStatement):
#             if hasattr(forstmt, "control") and hasattr(forstmt.control, "var"):
#                 var_decl = forstmt.control.var
#                 if var_decl:
#                     tname = self._simple_type_name(var_decl.type)
#                     for declarator in getattr(var_decl, "declarators", []):
#                         var_types[declarator.name] = tname

#         def _is_dynamic(q: str) -> bool:
#             return isinstance(q, str) and ("(" in q or ")" in q)

#         # Pre-build set of MethodInvocations that are selectors on a ClassCreator
#         # or another MethodInvocation — they have qualifier=None but are NOT sibling calls.
#         _selector_invocations = set()
        
#         def _collect_selector_ids(node, result_set):
#             for sel in (getattr(node, "selectors", None) or []):
#                 if isinstance(sel, jt.MethodInvocation):
#                     result_set.add(id(sel))
#                     _collect_selector_ids(sel, result_set)  # recurse into nested selectors

#         _selector_invocations = set()
#         for _, cc in method_node.filter(jt.ClassCreator):
#             _collect_selector_ids(cc, _selector_invocations)
#         for _, parent_inv in method_node.filter(jt.MethodInvocation):
#             _collect_selector_ids(parent_inv, _selector_invocations)
#         def _build_chain_string(start_inv, resolved_root):
#             """Build full chain string including selectors.
#             e.g. abc.method1() with selector method2() → 'ABC.method1().method2()'
#             """
#             chain = f"{resolved_root}.{start_inv.member}()"
#             for sel in (start_inv.selectors or []):
#                 if isinstance(sel, jt.MethodInvocation):
#                     chain += f".{sel.member}()"
#             return chain

#         def _emit_chain_segments(start_inv, resolved_root, calls_set):
#             # Always emit the root segment independently
#             calls_set.add(f"{resolved_root}.{start_inv.member}()")
#             # Walk selectors, resolving class at each step via method_return_index
#             selectors = [s for s in (start_inv.selectors or [])
#                         if isinstance(s, jt.MethodInvocation)]
#             if not selectors:
#                 return
#             current_class = resolved_root
#             # Try to resolve return type of root method to get next class
#             ret = method_return_index.get(current_class, {}).get(start_inv.member)
#             if ret and str(ret).lower() not in ('void', 'unknown', '<constructor>', ''):
#                 current_class = str(ret).split('.')[-1]
#             for sel in selectors:
#                 calls_set.add(f"{current_class}.{sel.member}()")
#                 ret = method_return_index.get(current_class, {}).get(sel.member)
#                 if ret and str(ret).lower() not in ('void', 'unknown', '<constructor>', ''):
#                     current_class = str(ret).split('.')[-1]
#                 else:
#                     break

#         # AST: MethodInvocation nodes
#         for _, inv in method_node.filter(jt.MethodInvocation):
#             qual = inv.qualifier or ""
#             member = inv.member

#             if not qual:
#                 # If this is a selector on a ClassCreator or chained call,
#                 # it is handled by its parent — skip to avoid wrong class attribution.
#                 if id(inv) in _selector_invocations:
#                     pass
#                 else:
#                     sibling_method_names = {n for n, _ in self.get_methods_in_type(type_node)}
#                     if member in sibling_method_names:
#                         class_name = getattr(type_node, "name", None)
#                         if class_name:
#                             # FIX 2: emit each chain segment independently
#                             _emit_chain_segments(inv, class_name, calls)
#                         else:
#                             calls.add(f"{member}()")
#                     elif self._keep_unqualified_call(member, static_members, static_wildcard_classes):
#                         calls.add(f"{member}()")
#             elif _is_dynamic(qual):
#                 calls.add(f"{qual}.{member}()")
#             else:
#                 qual = self._normalize_qualifier(qual, var_types)
#                 if self._keep_qualified_call(
#                     qual, var_types, imports_types, autowired_fields,
#                     wildcard_packages, locals_from_new, params_set, package_name,
#                 ):
#                     resolved_type = var_types.get(qual, qual)  # resolve variable → type name
#                     # FIX 2: emit each segment individually so method1 is never lost
#                     _emit_chain_segments(inv, resolved_type, calls)

#         # Collect ClassCreator IDs already handled via ThrowStatement
#         _throw_creators = set()
#         for _, th in method_node.filter(jt.ThrowStatement):
#             expr = getattr(th, "expression", None)
#             if isinstance(expr, jt.ClassCreator):
#                 _throw_creators.add(id(expr))

#         # Standalone new X(...) — not inside a throw
#         for _, cc in method_node.filter(jt.ClassCreator):
#             if id(cc) in _throw_creators:
#                 continue
#             ctor_type = self._simple_type_name(cc.type)
#             if not ctor_type:
#                 continue
#             if ctor_type in imports_types or wildcard_packages or \
#                self.accept_local_new_types or self.accept_same_package:
#                 calls.add(f"{ctor_type}.{ctor_type}()")
#             for arg in getattr(cc, "arguments", []) or []:
#                 calls |= self._collect_invocations_in_expression(
#                     arg, var_types=var_types, imports_types=imports_types,
#                     autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
#                     locals_from_new=locals_from_new, params_set=params_set,
#                     package_name=package_name,
#                 )

#         # throw new Type(...) — constructors + nested calls
        
#         for _, th in method_node.filter(jt.ThrowStatement):
#             expr = getattr(th, "expression", None)
#             if isinstance(expr, jt.ClassCreator) and getattr(expr, "type", None):
#                 ctor_type = self._simple_type_name(expr.type)
#                 if ctor_type:
#                     calls.add(f"{ctor_type}.{ctor_type}()")
#             calls |= self._collect_invocations_in_expression(
#                 expr, var_types=var_types, imports_types=imports_types,
#                 autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
#                 locals_from_new=locals_from_new, params_set=params_set,
#                 package_name=package_name,
#             )

#         # # Chained / stream calls via method source regex
#         # chained_pat = re.compile(
#         #     r'''\b[a-zA-Z_]\w*(?:\s*\([^()]*\))?(?:\s*\.\s*[a-zA-Z_]\w*\s*\([^()]*\)){1,}''',
#         #     re.MULTILINE | re.VERBOSE,
#         # )
#         # src = self._get_method_source(code, method_node)
#         # if src:
#         #     src_clean = _strip_source_comments(src)
#         #     for chain in chained_pat.findall(src_clean):
#         #         chain = chain.strip()
#         #         if chain:
#         #             calls.add(chain)
#         #     for dyn in self._extract_dynamic_terminal_methods(src_clean):
#         #         calls.add(dyn)

#         # Chained / stream calls via method source regex
#         chained_pat = re.compile(
#             r'''\b[a-zA-Z_]\w*(?:\s*\([^()]*\))?(?:\s*\.\s*[a-zA-Z_]\w*\s*\([^()]*\)){1,}''',
#             re.MULTILINE | re.VERBOSE,
#         )
#         # Matches chains rooted at a constructor call: Word(...).method(...)
#         # e.g. "BigDecimal(quantity).multiply(price)" — the root is a ctor, not a var/class.
#         ctor_rooted_pat = re.compile(r'^([A-Za-z_]\w*)\s*\(')
#         leading_var_pat = re.compile(r'^([A-Za-z_]\w*)\.')
#         java_kw = self.language_keywords()
#         src = self._get_method_source(code, method_node)
#         # Track method names claimed by chained_pat so _extract_dynamic_terminal_methods
#         # does not re-emit them as bare unqualified calls (which get attributed to this class).
#         chained_claimed_methods: set = set()
#         if src:
#             src_clean = _strip_source_comments(src)
#             for chain in chained_pat.findall(src_clean):
#                 chain = chain.strip()
#                 if not chain:
#                     continue
#                 lv = leading_var_pat.match(chain)
#                 if lv:
#                     leading = lv.group(1)
#                     # Skip chains rooted at a Java keyword (return, new, etc.)
#                     if leading in java_kw:
#                         continue
#                     resolved = var_types.get(leading)
#                     if resolved and resolved != leading:
#                         # Known variable — replace with its resolved type name
#                         chain = resolved + chain[len(leading):]
#                         calls.add(chain)
#                     elif leading in var_types:
#                         # Known variable whose name matches its type
#                         calls.add(chain)
#                     elif leading[0].isupper():
#                         # Looks like a class name (UpperCamelCase) — keep as-is
#                         calls.add(chain)
#                     # else: unknown lowercase token — skip to avoid false attribution
#                 else:
#                     # No leading "Word." prefix.  This happens for constructor-rooted
#                     # chains like "BigDecimal(quantity).multiply(price)" where the
#                     # token before the first "(" is the type name, not a variable.
#                     # Rewrite as "TypeName.method1().method2()..." so the call is
#                     # attributed to the right type rather than added as a raw string
#                     # (which downstream code cannot parse) or dropped silently.
#                     cr = ctor_rooted_pat.match(chain)
#                     if cr:
#                         ctor_type = cr.group(1)
#                         if ctor_type not in java_kw:
#                             # Extract every .method() segment after the constructor call.
#                             segments = re.findall(r'\.\s*([A-Za-z_]\w*)\s*\(', chain)
#                             for seg in segments:
#                                 rewritten = f"{ctor_type}.{seg}()"
#                                 calls.add(rewritten)
#                                 chained_claimed_methods.add(seg)
#                     # else: truly unclassifiable — skip to avoid false attribution
#             for dyn in self._extract_dynamic_terminal_methods(src_clean):
#                 # Strip trailing "()" to get the bare name for the duplicate check.
#                 bare = dyn[:-2] if dyn.endswith("()") else dyn
#                 if bare in chained_claimed_methods:
#                     # chained_pat already emitted a properly qualified version;
#                     # the bare unqualified form would be attributed to this class — skip.
#                     continue
#                 calls.add(dyn)

#         return sorted(calls)

#     # ------------------------------------------------------------------
#     # Fallback parse (regex-only path when AST fails)
#     # ------------------------------------------------------------------

#     def _parse_com_imports_fallback(self, java_code: str):
#         re_import_line = self._rx("import_static", flags=re.MULTILINE)
#         imports_types = set()
#         wildcard_packages = set()
#         static_members = set()
#         static_wildcard_classes = set()
#         for m in re_import_line.finditer(java_code):
#             line = m.group(0)
#             path = m.group(1)
#             parts = [p for p in path.split('.') if p]
#             is_static = 'static' in line
#             if is_static:
#                 if parts[-1] == '*':
#                     if len(parts) >= 2:
#                         static_wildcard_classes.add(parts[-2])
#                 else:
#                     static_members.add(parts[-1])
#                     if len(parts) >= 2:
#                         imports_types.add(parts[-2])
#             else:
#                 if parts[-1] == '*':
#                     wildcard_packages.add('.'.join(parts[:-1]))
#                 else:
#                     imports_types.add(parts[-1])
#         return imports_types, wildcard_packages, static_members, static_wildcard_classes

#     def _extract_balanced_args(self, text: str, start_idx: int) -> str:
#         if start_idx < 0 or start_idx >= len(text) or text[start_idx] != '(':
#             return ""
#         depth = 0
#         end = None
#         for i in range(start_idx, len(text)):
#             ch = text[i]
#             if ch == '(':
#                 depth += 1
#             elif ch == ')':
#                 depth -= 1
#                 if depth == 0:
#                     end = i
#                     break
#         return text[start_idx:end + 1] if end is not None else ""

#     def fallback_parse(self, code_raw: str) -> dict:
#         """
#         Regex-only fallback for files whose AST cannot be parsed.
#         Handles all Java 8 patterns including lambdas and streams
#         (they appear as regular method calls in regex terms).
#         """
#         java_code = html.unescape(code_raw)
#         package_name = self._get_package_name(java_code)
#         imports_types, wildcard_packages, static_members, static_wildcard_classes = \
#             self._parse_com_imports_fallback(java_code)

#         re_autowired_field  = self._rx("autowired_field", flags=re.MULTILINE)
#         re_loose_decl       = self._rx("variable_declaration")
#         re_var_decl         = self._rx("re_var_decl")
#         re_var_new          = self._rx("re_var_new")
#         re_simple_call      = self._rx("re_simple_call", flags=re.MULTILINE)
#         re_member_access    = self._rx("re_member_access")
#         re_unqualified_call = self._rx("re_unqualified_call")
#         re_chain            = self._rx("re_chain") if self.regex.get("re_chain") else re.compile(r"$^")
#         re_method_with_throw = self._rx("method_with_throw", flags=re.MULTILINE | re.DOTALL)
#         re_method_name_in_sig = re.compile(r'\b([A-Za-z_]\w*)\s*\(', re.MULTILINE)

#         re_class_implements      = self._rx("class_implements", flags=re.MULTILINE)
#         re_class_declaration     = self._rx("class_declaration", flags=re.MULTILINE)
#         re_interface_declaration = self._rx("interface_declaration", flags=re.MULTILINE)

#         fallback_types = {}
#         for m in re_interface_declaration.finditer(java_code):
#             fallback_types[m.group(1)] = "interface"
#         for m in re_class_implements.finditer(java_code):
#             fallback_types.setdefault(m.group(1), "class_implements_interface")
#         for m in re_class_declaration.finditer(java_code):
#             fallback_types.setdefault(m.group(1), "class")

#         class_or_interface_name = next(iter(fallback_types.keys()), None)

#         # --- Variable / DI types ---
#         autowired_fields = {}
#         for m in re_autowired_field.finditer(java_code):
#             raw_type = m.group(1)
#             var_name = m.group(2)
#             tname = re.sub(
#                 r'(&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;|&lt;[^&gt;]+&gt;)', '', raw_type
#             ).strip().split('.')[-1]
#             autowired_fields[var_name] = tname

#         var_types = {}
#         locals_from_new = set()
#         params_set = set()

#         for m in re_var_decl.finditer(java_code):
#             raw_type, var_name = m.group(1), m.group(2)
#             # Java 8: skip if type is literally 'var' (shouldn't appear, but guard anyway)
#             if raw_type.strip() == 'var':
#                 continue
#             tname = re.sub(
#                 r'(&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;|&lt;[^&gt;]+&gt;)', '', raw_type
#             ).strip().split('.')[-1]
#             var_types[var_name] = tname

#         for m in re_var_new.finditer(java_code):
#             var_name, fq_type = m.group(1), m.group(2)
#             var_types.setdefault(var_name, fq_type.split('.')[-1])
#             locals_from_new.add(var_name)

#         for m in re_loose_decl.finditer(java_code):
#             raw_type, var_name = m.group(1), m.group(2)
#             if var_name in var_types or raw_type.strip() == 'var':
#                 continue
#             tname = re.sub(
#                 r'(&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;|&lt;[^&gt;]+&gt;)', '', raw_type
#             ).strip().split('.')[-1]
#             var_types[var_name] = tname

#         var_types.update(autowired_fields)

#         java_keywords = {"return", "this", "super", "new"} | set(
#             self.details.get("control_keywords", [])
#         )

#         per_method_calls = []

#         def _is_dyn(q: str) -> bool:
#             return isinstance(q, str) and ("(" in q or ")" in q)

#         def _process_block(block_text: str, method_name):
#             filtered = set()

#             for m in re_simple_call.finditer(block_text):
#                 qual, member = m.group(1), m.group(2)
#                 if str(qual).strip().lower() in java_keywords:
#                     continue
#                 if _is_dyn(qual):
#                     filtered.add(f"{qual}.{member}()")
#                 elif self._keep_qualified_call(
#                     qual, var_types, imports_types, autowired_fields,
#                     wildcard_packages, locals_from_new, params_set, package_name,
#                 ):
#                     filtered.add(f"{qual}.{member}()")

#             for m in re_member_access.finditer(block_text):
#                 qual, member = m.group(1), m.group(2)
#                 if str(qual).strip().lower() in java_keywords:
#                     continue
#                 if _is_dyn(qual):
#                     filtered.add(f"{member}")
#                 elif self._keep_qualified_call(
#                     qual, var_types, imports_types, autowired_fields,
#                     wildcard_packages, locals_from_new, params_set, package_name,
#                 ):
#                     filtered.add(f"{qual}.{member}")

#             for m in re_unqualified_call.finditer(block_text):
#                 member = m.group(1)
#                 if member in java_keywords:
#                     continue
#                 if method_name and member == method_name:
#                     continue
#                 if class_or_interface_name and re.search(
#                     r'\b(?:public|private|protected)\b[^{;]*\b' + re.escape(member) + r'\s*\(',
#                     java_code, re.MULTILINE
#                 ):
#                     filtered.add(f"{class_or_interface_name}.{member}()")
#                 elif self.include_unqualified or member in static_members or static_wildcard_classes:
#                     filtered.add(f"{member}()")

#             for m in re_chain.finditer(block_text):
#                 root = m.group(1)
#                 if str(root).strip().lower() in java_keywords:
#                     continue
#                 filtered.add(m.group(0))

#             # throw new ...

#             throw_pat = re.compile(r'\bthrow\s+new\s+([A-Za-z_]\w+)\s*\(', re.MULTILINE)
#             for tm in throw_pat.finditer(block_text):
#                 ctor_class = tm.group(1)
#                 filtered.add(f"{ctor_class}.{ctor_class}()")
#                 args_block = self._extract_balanced_args(block_text, tm.end() - 1)
#                 if args_block:
#                     for sm in re_simple_call.finditer(args_block):
#                         q, mem = sm.group(1), sm.group(2)
#                         if str(q).strip().lower() in java_keywords:
#                             continue
#                         if _is_dyn(q):
#                             filtered.add(f"{q}.{mem}()")
#                         elif self._keep_qualified_call(
#                             q, var_types, imports_types, autowired_fields,
#                             wildcard_packages, locals_from_new, params_set, package_name,
#                         ):
#                             filtered.add(f"{q}.{mem}()")
#                     for um in re_unqualified_call.finditer(args_block):
#                         mem = um.group(1)
#                         if mem not in java_keywords and self.include_unqualified:
#                             filtered.add(f"{mem}()")
#                     for cm in re_chain.finditer(args_block):
#                         filtered.add(cm.group(0))

#             # standalone new X(...) — outside throw
#             new_pat = re.compile(r'\bnew\s+([A-Za-z_]\w+)\s*\(', re.MULTILINE)
#             throw_positions = {tm.start() for tm in throw_pat.finditer(block_text)}
#             for nm in new_pat.finditer(block_text):
#                 # Skip if this new is part of a throw new (already handled above)
#                 preceding = block_text[max(0, nm.start() - 10):nm.start()].strip()
#                 if preceding.endswith('throw'):
#                     continue
#                 ctor_class = nm.group(1)
#                 filtered.add(f"{ctor_class}.{ctor_class}()")
#                 args_block = self._extract_balanced_args(block_text, nm.end() - 1)
#                 if args_block:
#                     for sm in re_simple_call.finditer(args_block):
#                         q, mem = sm.group(1), sm.group(2)
#                         if str(q).strip().lower() in java_keywords:
#                             continue
#                         if _is_dyn(q):
#                             filtered.add(f"{q}.{mem}()")
#                         elif self._keep_qualified_call(
#                             q, var_types, imports_types, autowired_fields,
#                             wildcard_packages, locals_from_new, params_set, package_name,
#                         ):
#                             filtered.add(f"{q}.{mem}()")
#                     for um in re_unqualified_call.finditer(args_block):
#                         mem = um.group(1)
#                         if mem not in java_keywords and self.include_unqualified:
#                             filtered.add(f"{mem}()")
#                     for cm in re_chain.finditer(args_block):
#                         filtered.add(cm.group(0))

#             for dyn in self._extract_dynamic_terminal_methods(block_text):
#                 filtered.add(dyn)

#             for call in sorted(filtered):
#                 per_method_calls.append({'method_name': method_name, 'object_call': call})

#         # Walk method bodies
#         for sig_match in re_method_with_throw.finditer(java_code):
#             brace_pos = java_code.find('{', sig_match.end() - 1)
#             if brace_pos == -1:
#                 continue
#             brace_count, end_idx = 0, None
#             for i in range(brace_pos, len(java_code)):
#                 if java_code[i] == '{':
#                     brace_count += 1
#                 elif java_code[i] == '}':
#                     brace_count -= 1
#                     if brace_count == 0:
#                         end_idx = i
#                         break
#             if end_idx is None:
#                 continue
#             method_text = java_code[sig_match.start():end_idx + 1]
#             name_match = re_method_name_in_sig.search(method_text)
#             method_name = name_match.group(1) if name_match else None
#             _process_block(method_text, method_name)

#         # Constructor bodies
#         if class_or_interface_name:
#             ctor_pat = re.compile(
#                 r'(?:public|protected|private)\s+' + re.escape(class_or_interface_name) + r'\s*\([^)]*\)\s*\{',
#                 re.MULTILINE,
#             )
#             for cm in ctor_pat.finditer(java_code):
#                 brace_pos = java_code.find('{', cm.end() - 1)
#                 if brace_pos == -1:
#                     continue
#                 brace_count, end_idx = 0, None
#                 for i in range(brace_pos, len(java_code)):
#                     if java_code[i] == '{':
#                         brace_count += 1
#                     elif java_code[i] == '}':
#                         brace_count -= 1
#                         if brace_count == 0:
#                             end_idx = i
#                             break
#                 if end_idx is None:
#                     continue
#                 ctor_text = java_code[cm.start():end_idx + 1]
#                 _process_block(ctor_text, class_or_interface_name)

#         if per_method_calls:
#             row_type = fallback_types.get(class_or_interface_name or '', 'Unknown')
#             return {
#                 'type_name': class_or_interface_name or 'Unknown',
#                 'row_type': row_type,
#                 'per_method_calls': per_method_calls,
#             }

#         row_type = fallback_types.get(class_or_interface_name or '', 'Unknown')
#         return {
#             'type_name': class_or_interface_name or 'Unknown',
#             'row_type': row_type,
#             'filtered_calls': [],
#         }

#     # ------------------------------------------------------------------
#     # System-call filter
#     # ------------------------------------------------------------------

#     def is_system_call(self, call: str) -> bool:
#         if not isinstance(call, str):
#             return False
#         call = call.strip()
#         if not call:
#             return False

#         call_ng = re.sub(r'\s*&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;\s*', '', call)
#         call_ng = re.sub(r'\s*&lt;[^&gt;]+&gt;\s*', '', call_ng)
#         lc = call_ng.lower()

#         system_qualifiers = self.details.get("SYSTEM_QUALIFIERS", [
#             r"^logger\.", r"^log\.", r"^system\.", r"^string\.", r"^objects\.", r"^arrays\.",
#             r"^collections\.", r"^optional\.", r"^stream\.", r"^httpsecurity\.", r"^security\.",
#         ])
#         for pattern in system_qualifiers:
#             if re.match(pattern, lc):
#                 return True

#         def extract_method(c: str) -> str:
#             part = c.split(".")[-1]
#             part = re.sub(r"\(.*\)", "", part)
#             return part.replace(";", "").replace('"', "").replace("'", "").strip().lower()

#         default_system_methods = {"equals"}
#         system_methods = default_system_methods | {
#             m.lower() for m in self.details.get("SYSTEM_METHODS", [])
#         }
#         return extract_method(call_ng) in system_methods

#     def language_keywords(self) -> set:
#         return {"return", "this", "super", "new"} | set(self.details.get("control_keywords", []))

#     # ------------------------------------------------------------------
#     # Object-class map
#     # ------------------------------------------------------------------

#     def build_object_class_map(self, app_folder: str) -> dict:
#         obj_class_map = {}
#         PRIMITIVES = set(self.details.get("PRIMITIVE", []))
#         COLLECTION_TYPES = set(self.details.get("COLLECTION_TYPES", [
#             "List", "Set", "Map", "Collection", "Iterable"
#         ]))

#         var_decl_pattern  = self._rx("var_decl_pattern", flags=re.MULTILINE)
#         for_loop_pattern  = self._rx("for_loop_pattern", flags=re.MULTILINE)
#         simple_local_decl = re.compile(r'\b([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*=')
#         method_param_list_pat = re.compile(
#             r'\b(?:public|protected|private)\b[^{;]*\(([^)]*)\)', re.MULTILINE
#         )
#         method_param_decl_pat = self._rx("method_param_decl")

#         def clean_type(t: str) -> str:
#             if not t:
#                 return t
#             t2 = re.sub(r'\s*&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;\s*', '', t)
#             t2 = re.sub(r'\s*&lt;[^&gt;]+&gt;\s*', '', t2)
#             return t2.replace('[]', '').strip()

#         def update_map(f: str, var: str, typ: str, *, source: str):
#             if not typ or typ in PRIMITIVES:
#                 return
#             key_scoped = (f.lower(), var.lower())
#             key_global = var.lower()
#             existing_s = obj_class_map.get(key_scoped)
#             if existing_s:
#                 if existing_s in COLLECTION_TYPES and typ not in COLLECTION_TYPES:
#                     obj_class_map[key_scoped] = typ
#             else:
#                 obj_class_map[key_scoped] = typ
#             existing_g = obj_class_map.get(key_global)
#             if existing_g:
#                 if existing_g in COLLECTION_TYPES and typ not in COLLECTION_TYPES:
#                     obj_class_map[key_global] = typ
#             else:
#                 obj_class_map[key_global] = typ

#         for root, _, files in os.walk(app_folder):
#             for file in files:
#                 if not file.endswith(self.file_extension()):
#                     continue
#                 fpath = os.path.join(root, file)
#                 # FIX 3: reuse cached file content and parsed AST
#                 try:
#                     if fpath in self._file_content_cache:
#                         code = self._file_content_cache[fpath]
#                     else:
#                         with open(fpath, "r", encoding="utf-8") as fh:
#                             code = fh.read()
#                         self._file_content_cache[fpath] = code
#                 except Exception:
#                     continue

#                 # --- AST path ---
#                 _ast_key = fpath
#                 try:
#                     if _ast_key in self._raw_ast_cache:
#                         parsed = self._raw_ast_cache[_ast_key]
#                         if parsed is False:
#                             raise Exception("cached parse failure")
#                     else:
#                         parsed = javalang.parse.parse(code)
#                         self._raw_ast_cache[_ast_key] = parsed
#                     for _, type_node in parsed.filter(jt.ClassDeclaration):
#                         for _, fd in type_node.filter(jt.FieldDeclaration):
#                             tname = self._simple_type_name(fd.type)
#                             for decl in getattr(fd, "declarators", []):
#                                 if tname and tname not in PRIMITIVES:
#                                     update_map(file, decl.name, tname, source="ast_field")

#                         for _, mnode in type_node.filter(jt.MethodDeclaration):
#                             for p in getattr(mnode, "parameters", []):
#                                 tname = self._simple_type_name(p.type)
#                                 if tname and tname not in PRIMITIVES:
#                                     update_map(file, p.name, tname, source="ast_param")
#                             for _, lv in mnode.filter(jt.LocalVariableDeclaration):
#                                 declared_type = self._simple_type_name(lv.type)
#                                 for decl in getattr(lv, "declarators", []):
#                                     tname = declared_type or self._infer_type_from_initializer(decl)
#                                     if tname and tname not in PRIMITIVES:
#                                         update_map(file, decl.name, tname, source="ast_local")
#                             for _, forstmt in mnode.filter(jt.ForStatement):
#                                 if hasattr(forstmt, "control") and hasattr(forstmt.control, "var"):
#                                     var_decl = forstmt.control.var
#                                     if var_decl:
#                                         tname = self._simple_type_name(var_decl.type)
#                                         for declarator in getattr(var_decl, "declarators", []):
#                                             if tname and tname not in PRIMITIVES:
#                                                 update_map(file, declarator.name, tname, source="ast_for")

#                         for _, cnode in type_node.filter(jt.ConstructorDeclaration):
#                             for p in getattr(cnode, "parameters", []):
#                                 tname = self._simple_type_name(p.type)
#                                 if tname and tname not in PRIMITIVES:
#                                     update_map(file, p.name, tname, source="ast_ctor_param")
#                             for _, lv in cnode.filter(jt.LocalVariableDeclaration):
#                                 declared_type = self._simple_type_name(lv.type)
#                                 for decl in getattr(lv, "declarators", []):
#                                     tname = declared_type or self._infer_type_from_initializer(decl)
#                                     if tname and tname not in PRIMITIVES:
#                                         update_map(file, decl.name, tname, source="ast_ctor_local")
#                     continue
#                 except Exception:
#                     pass

#                 # --- Regex fallback ---
#                 for m in var_decl_pattern.finditer(code):
#                     raw_type, var_name = m.group(1), m.group(2)
#                     t = clean_type(raw_type)
#                     if t and t not in PRIMITIVES:
#                         update_map(file, var_name, t, source="regex_var_decl")

#                 for m in for_loop_pattern.finditer(code):
#                     raw_type, var_name = m.group(1), m.group(2)
#                     t = clean_type(raw_type)
#                     if t and t not in PRIMITIVES:
#                         update_map(file, var_name, t, source="regex_for_loop")

#                 for m in simple_local_decl.finditer(code):
#                     raw_type, var_name = m.group(1), m.group(2)
#                     t = clean_type(raw_type)
#                     if t and t not in PRIMITIVES:
#                         update_map(file, var_name, t, source="regex_simple_local")

#                 for pl_match in method_param_list_pat.finditer(code):
#                     for pm in method_param_decl_pat.finditer(pl_match.group(1)):
#                         raw_type, var_name = pm.group(1), pm.group(2)
#                         t = clean_type(raw_type)
#                         if t and t not in PRIMITIVES:
#                             update_map(file, var_name, t, source="regex_param")

#         return obj_class_map

#     # ------------------------------------------------------------------
#     # Method return index
#     # ------------------------------------------------------------------

#     def build_method_return_index(self, app_folder: str) -> dict:
#         method_return_index = {}
#         class_decl_pat = re.compile(r'\bclass\s+(\w+)\b')
#         method_sig_pat = re.compile(
#             r'(?:public|protected|private)?\s+(?:static\s+)?([\w\.&lt;<>\[\]]+)\s+(\w+)\s*\(',
#             re.MULTILINE,
#         )
#         constructor_sig_pat = re.compile(
#             r'(?:public|protected|private)\s+(\w+)\s*\(', re.MULTILINE
#         )

#         for root, _, files in os.walk(app_folder):
#             for file in files:
#                 if not file.endswith(self.file_extension()):
#                     continue
#                 fpath = os.path.join(root, file)
#                 # FIX 3: reuse cached file content and parsed AST
#                 try:
#                     if fpath in self._file_content_cache:
#                         code = self._file_content_cache[fpath]
#                     else:
#                         with open(fpath, "r", encoding="utf-8") as f:
#                             code = f.read()
#                         self._file_content_cache[fpath] = code
#                 except Exception:
#                     continue

#                 _ast_key2 = fpath
#                 if _ast_key2 in self._raw_ast_cache:
#                     _cached2 = self._raw_ast_cache[_ast_key2]
#                     parsed = None if (_cached2 is False) else _cached2
#                 else:
#                     try:
#                         parsed = javalang.parse.parse(code)
#                         self._raw_ast_cache[_ast_key2] = parsed
#                     except Exception:
#                         parsed = None
#                         self._raw_ast_cache[_ast_key2] = False

#                 if parsed:
#                     for _, cls in parsed.filter(jt.ClassDeclaration):
#                         cls_name = getattr(cls, "name", None)
#                         if not cls_name:
#                             continue
#                         method_return_index.setdefault(cls_name, {})
#                         for _, m in cls.filter(jt.MethodDeclaration):
#                             rt = m.return_type
#                             if rt is None:
#                                 rname = "void"
#                             else:
#                                 base = rt.name if hasattr(rt, "name") else "Unknown"
#                                 rname = re.sub(
#                                     r'(&amp;lt;[^&amp;gt]+&amp;gt;|&lt;[^&gt;]+&gt;)', '', base
#                                 )
#                             method_return_index[cls_name][m.name] = rname.strip().split('.')[-1]
#                         for _, c in cls.filter(jt.ConstructorDeclaration):
#                             method_return_index[cls_name][c.name] = "<constructor>"

#                     for _, itf in parsed.filter(jt.InterfaceDeclaration):
#                         itf_name = getattr(itf, "name", None)
#                         if not itf_name:
#                             continue
#                         method_return_index.setdefault(itf_name, {})
#                         for _, m in itf.filter(jt.MethodDeclaration):
#                             rt = m.return_type
#                             if rt is None:
#                                 rname = "void"
#                             else:
#                                 base = rt.name if hasattr(rt, "name") else "Unknown"
#                                 rname = re.sub(
#                                     r'(&amp;lt;[^&amp;gt]+&amp;gt;|&lt;[^&gt;]+&gt;)', '', base
#                                 )
#                             method_return_index[itf_name][m.name] = rname.strip().split('.')[-1]
#                     continue

#                 # Regex fallback
#                 cls_match = class_decl_pat.search(code)
#                 if not cls_match:
#                     continue
#                 cls_name = cls_match.group(1)
#                 method_return_index.setdefault(cls_name, {})
#                 for mm in method_sig_pat.finditer(code):
#                     return_type = mm.group(1)
#                     method_name = mm.group(2)
#                     simple_return = re.sub(
#                         r'(&amp;lt;[^&amp;gt]+&amp;gt;|&lt;[^&gt;]+&gt;)', '', return_type
#                     ).strip().split('.')[-1]
#                     method_return_index[cls_name][method_name] = simple_return
#                 for cm in constructor_sig_pat.finditer(code):
#                     ctor_name = cm.group(1)
#                     if ctor_name == cls_name:
#                         method_return_index[cls_name][ctor_name] = "<constructor>"

#         return method_return_index

#     # ------------------------------------------------------------------
#     # File → type map
#     # ------------------------------------------------------------------

#     def find_type_to_file_map(self, app_folder: str) -> dict:
#         java_files_map = {}
#         for root, _, files in os.walk(app_folder):
#             for f in files:
#                 if f.endswith(self.file_extension()):
#                     class_name = os.path.splitext(f)[0]
#                     java_files_map[class_name] = os.path.join(root, f)
#         return java_files_map

#     # ------------------------------------------------------------------
#     # LOC counter
#     # ------------------------------------------------------------------

#     def extract_method_loc(
#         self,
#         java_file_path: str,
#         method_name: str,
#         classname=None,
#         include_package_private: bool = False,
#         count_empty_lines: bool = True,
#     ):
#         if not java_file_path:
#             return None

#         try:
#             with open(java_file_path, "r", encoding="utf-8") as f:
#                 code = f.read()
#         except Exception:
#             try:
#                 with open(java_file_path, "r", encoding="latin-1") as f:
#                     code = f.read()
#             except Exception:
#                 return None

#         code = code.replace("\r\n", "\n").replace("\r", "\n")
#         lines = code.split("\n")

#         access_req = r"(?:public|private|protected)"
#         access = rf"(?:{access_req})?" if include_package_private else access_req
#         mname_esc = re.escape(method_name)

#         method_decl_pat = rf"""
#             (?m)
#             ^[ \t]*
#             {access}[ \t]*
#             (?:(?:static|final|abstract|synchronized|native|strictfp)\b[ \t]*)*
#             [\w.<>\[\],? \t]+
#             \b(?P<mname>{mname_esc})[ \t]*\(
#         """

#         constructor_decl_pat = None
#         if classname and method_name == classname:
#             cname_esc = re.escape(classname)
#             constructor_decl_pat = rf"""
#                 (?m)
#                 ^[ \t]*
#                 {access}[ \t]*
#                 (?:(?:static|final|abstract|synchronized|native|strictfp)\b[ \t]*)*
#                 \b(?P<mname>{cname_esc})[ \t]*\(
#             """

#         patterns = []
#         if constructor_decl_pat:
#             patterns.append(re.compile(constructor_decl_pat, re.IGNORECASE | re.VERBOSE))
#         patterns.append(re.compile(method_decl_pat, re.IGNORECASE | re.VERBOSE))

#         decl_match = None
#         for pat in patterns:
#             decl_match = pat.search(code)
#             if decl_match:
#                 break
#         if not decl_match:
#             return None

#         sig_line_idx = code.count("\n", 0, decl_match.start("mname")) + 1

#         def find_annotation_block_start(sig_idx):
#             i = sig_idx - 2
#             if i < 0:
#                 return None
#             paren_balance = 0
#             started = False
#             start_line = None
#             while i >= 0:
#                 raw = lines[i].rstrip()
#                 if not raw.strip() and not (started and paren_balance > 0):
#                     break
#                 is_anno = bool(re.match(r'^[ \t]*@', raw))
#                 if not started:
#                     if is_anno:
#                         started = True
#                         start_line = i + 1
#                         paren_balance = raw.count("(") - raw.count(")")
#                     else:
#                         break
#                 else:
#                     if is_anno or paren_balance > 0:
#                         start_line = i + 1
#                         paren_balance += raw.count("(") - raw.count(")")
#                     else:
#                         break
#                 i -= 1
#             return start_line

#         anno_start = find_annotation_block_start(sig_line_idx)
#         start_line_idx = anno_start if anno_start is not None else sig_line_idx

#         def find_opening_brace_line(from_line):
#             in_block_comment = False
#             for i in range(from_line - 1, len(lines)):
#                 line = lines[i]
#                 j, n = 0, len(line)
#                 in_string = False
#                 string_char = None
#                 while j < n:
#                     ch = line[j]
#                     nxt = line[j + 1] if j + 1 < n else ""
#                     if in_block_comment:
#                         if ch == "*" and nxt == "/":
#                             in_block_comment = False
#                             j += 2
#                             continue
#                         j += 1
#                         continue
#                     if in_string:
#                         if ch == "\\":
#                             j += 2
#                             continue
#                         if ch == string_char:
#                             in_string = False
#                             string_char = None
#                         j += 1
#                         continue
#                     if ch == "/" and nxt == "*":
#                         in_block_comment = True
#                         j += 2
#                         continue
#                     if ch == "/" and nxt == "/":
#                         break
#                     if ch in ("'", '"'):
#                         in_string = True
#                         string_char = ch
#                         j += 1
#                         continue
#                     if ch == "{":
#                         return i + 1
#                     j += 1
#             return None

#         def find_closing_brace_line(open_line):
#             in_block_comment = False
#             depth = 0
#             started = False
#             for i in range(open_line - 1, len(lines)):
#                 line = lines[i]
#                 j, n = 0, len(line)
#                 in_string = False
#                 string_char = None
#                 while j < n:
#                     ch = line[j]
#                     nxt = line[j + 1] if j + 1 < n else ""
#                     if in_block_comment:
#                         if ch == "*" and nxt == "/":
#                             in_block_comment = False
#                             j += 2
#                             continue
#                         j += 1
#                         continue
#                     if in_string:
#                         if ch == "\\":
#                             j += 2
#                             continue
#                         if ch == string_char:
#                             in_string = False
#                             string_char = None
#                         j += 1
#                         continue
#                     if ch == "/" and nxt == "*":
#                         in_block_comment = True
#                         j += 2
#                         continue
#                     if ch == "/" and nxt == "/":
#                         break
#                     if ch in ("'", '"'):
#                         in_string = True
#                         string_char = ch
#                         j += 1
#                         continue
#                     if ch == "{":
#                         depth += 1
#                         started = True
#                     elif ch == "}":
#                         depth -= 1
#                         if started and depth == 0:
#                             return i + 1
#                     j += 1
#             return None

#         brace_open_line = find_opening_brace_line(sig_line_idx)
#         if brace_open_line is None:
#             return 1

#         end_line_idx = find_closing_brace_line(brace_open_line)
#         if end_line_idx is None:
#             end_line_idx = len(lines)

#         if count_empty_lines:
#             return max(1, end_line_idx - start_line_idx + 1)
#         else:
#             segment = lines[start_line_idx - 1:end_line_idx]
#             return max(1, sum(1 for ln in segment if ln.strip()))

#     # ------------------------------------------------------------------
#     # Properties extraction
#     # ------------------------------------------------------------------

#     def load_all_properties(self, app_folder, additional_property_refs=None):
#         app_folder = Path(app_folder)
#         paths = set()
#         for p in app_folder.rglob("*.properties"):
#             paths.add(p.resolve())

#         if additional_property_refs:
#             for ref in additional_property_refs:
#                 ref_norm = self._normalize_ps_ref(ref)
#                 matches = list(app_folder.rglob(ref_norm))
#                 if not matches:
#                     matches = list(app_folder.rglob(os.path.basename(ref_norm)))
#                 for m in matches:
#                     paths.add(m.resolve())

#         props = {}
#         for p in sorted(paths, key=str):
#             try:
#                 with open(p, "r", encoding="utf-8") as fh:
#                     for raw in fh:
#                         line = raw.strip()
#                         if not line or line.startswith("#") or line.startswith("!"):
#                             continue
#                         if "=" in line:
#                             k, v = line.split("=", 1)
#                         elif ":" in line:
#                             k, v = line.split(":", 1)
#                         else:
#                             continue
#                         props[k.strip()] = v.strip()
#             except Exception as e:
#                 print(f"Error reading {p}: {e}")
#         return props

#     def extract_application_properties_from_folder(
#         self,
#         app_folder,
#         include_filepath: bool = True,
#         include_trailing_dot: bool = True,
#     ):
#         def _compose(jpath: Path, method_name) -> str:
#             base = jpath.stem
#             if method_name:
#                 return f"{base}.{method_name}"
#             return f"{base}." if include_trailing_dot else base

#         app_folder = Path(app_folder)
#         ps_refs = set()
#         java_paths = []

#         for p in app_folder.rglob("*.java"):
#             java_paths.append(p.resolve())
#             try:
#                 txt = p.read_text(encoding="utf-8")
#             except Exception:
#                 try:
#                     txt = p.read_text(encoding="latin-1")
#                 except Exception:
#                     txt = ""
#             for m in re_property_source.finditer(txt):
#                 ps_refs.add(m.group(1).strip())

#         properties_map = self.load_all_properties(app_folder, additional_property_refs=ps_refs)
#         rows = []

#         for jf in java_paths:
#             try:
#                 code = jf.read_text(encoding="utf-8")
#             except Exception:
#                 try:
#                     code = jf.read_text(encoding="latin-1")
#                 except Exception:
#                     code = ""

#             method_index_map = self._build_method_index_map(code)

#             # @Value
#             for item in self._extract_values_with_vars(code):
#                 key = item["Property"]
#                 var = item["Variable"]
#                 actual = properties_map.get(key, "NOT_FOUND")
#                 method_name = None
#                 if var:
#                     pattern = re.compile(r'\b' + re.escape(var) + r'\b')
#                     for mu in pattern.finditer(code, item["span_end"]):
#                         method_name = self._find_enclosing_method(method_index_map, mu.start())
#                         if method_name:
#                             break
#                 rows.append({
#                     "FileName": jf.name.replace(".java", ""),
#                     "FilePath": str(jf),
#                     "Filename.methodname": _compose(jf, method_name),
#                     "Annotation": item["Annotation"],
#                     "Property": key,
#                     "Variable": var,
#                     "method_name": method_name,
#                     "Actual Value": actual,
#                 })

#             # @ConfigurationProperties
#             for m in re_configuration_properties.finditer(code):
#                 prefix = m.group(1)
#                 matched = {k: v for k, v in properties_map.items()
#                            if k == prefix or k.startswith(prefix + ".")}
#                 actual = "; ".join(f"{k}={v}" for k, v in matched.items()) if matched else "NOT_FOUND"
#                 rows.append({
#                     "FileName": jf.name.replace(".java", ""),
#                     "FilePath": str(jf),
#                     "Filename.methodname": _compose(jf, None),
#                     "Annotation": "@ConfigurationProperties",
#                     "Property": prefix,
#                     "Variable": None,
#                     "method_name": None,
#                     "Actual Value": actual,
#                 })

#             # @PropertySource
#             for m in re_property_source.finditer(code):
#                 rows.append({
#                     "FileName": jf.name.replace(".java", ""),
#                     "FilePath": str(jf),
#                     "Filename.methodname": _compose(jf, None),
#                     "Annotation": "@PropertySource",
#                     "Property": m.group(1),
#                     "Variable": None,
#                     "method_name": None,
#                     "Actual Value": "FILE_REFERENCE",
#                 })

#             # messageSource.getMessage(...)
#             for mm in re_message_key.finditer(code):
#                 key = mm.group(1)
#                 actual = properties_map.get(key, "NOT_FOUND")
#                 method_name = self._find_enclosing_method(method_index_map, mm.start())
#                 rows.append({
#                     "FileName": jf.name.replace(".java", ""),
#                     "FilePath": str(jf),
#                     "Filename.methodname": _compose(jf, method_name),
#                     "Annotation": "MessageSource",
#                     "Property": key,
#                     "Variable": None,
#                     "method_name": method_name,
#                     "Actual Value": actual,
#                 })

#         df = pd.DataFrame(rows)
#         if include_filepath:
#             cols = ["FileName", "FilePath", "Filename.methodname", "Annotation",
#                     "Property", "Variable", "method_name", "Actual Value"]
#         else:
#             cols = ["FileName", "Filename.methodname", "Annotation",
#                     "Property", "Variable", "method_name", "Actual Value"]
#         df = df.reindex(columns=cols)
#         if "method_name" in df.columns:
#             df = df[df["method_name"].notna()]
#         return df


import os
import re
import html
import javalang
import javalang.tree as jt
import pandas as pd
from pathlib import Path

from method_lineage_generation import LanguageAdapter

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
    (?:[\w\[\]<>?,]+\s+)+
    (?!(?:if|for|while|switch|catch|else)\b)
    ([A-Za-z_]\w*)
    \s*\(
    \s*(
        (?:
        (?:@\w+(?:\([^)]*\))?\s*)*
        (?:final\s+)?
        [\w\[\]<>?,]+(?:\s*\.\.\.)?\s+
        [A-Za-z_]\w*
        )
        (?:\s*,\s*
        (?:@\w+(?:\([^)]*\))?\s*)*
        (?:final\s+)?
        [\w\[\]<>?,]+(?:\s*\.\.\.)?\s+
        [A-Za-z_]\w*
        )*
    )?
    \)\s*\{
    ''',
    re.M | re.X
)


# ---------------------------------------------------------------------------
# Module-level helper (mirrors the Java 18 adapter)
# ---------------------------------------------------------------------------

def _strip_source_comments(src: str) -> str:
    """Remove // and /* */ comments while preserving string literals."""
    result = []
    i = 0
    n = len(src)
    in_string = False
    string_char = None

    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        if in_string:
            result.append(ch)
            if ch == '\\':
                i += 1
                if i < n:
                    result.append(src[i])
            elif ch == string_char:
                in_string = False
                string_char = None
            i += 1
            continue

        if ch == '/' and nxt == '/':
            while i < n and src[i] != '\n':
                i += 1
            continue

        if ch == '/' and nxt == '*':
            i += 2
            while i < n - 1:
                if src[i] == '*' and src[i + 1] == '/':
                    i += 2
                    break
                i += 1
            continue

        if ch in ('"', "'"):
            in_string = True
            string_char = ch

        result.append(ch)
        i += 1

    return ''.join(result)


# ---------------------------------------------------------------------------
# Java 8 Adapter
# ---------------------------------------------------------------------------

class JavaAdapter(LanguageAdapter):
    """
    LanguageAdapter implementation for Java 8 codebases.

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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalize_ps_ref(self, ps_str: str) -> str:
        if ps_str.startswith("classpath:"):
            return ps_str[len("classpath:"):]
        if ps_str.startswith("file:"):
            return ps_str[len("file:"):]
        return ps_str

    def _build_method_index_map(self, java_text: str):
        """Map of (start_pos, method_name) tuples sorted by position."""
        res = []
        for m in re_method_decl.finditer(java_text):
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
        for m in re_value_spel_dollar.finditer(java_text):
            key = m.group(1)
            span_end = m.end()
            var = None
            m2 = re_field_decl.search(java_text, span_end)
            if m2:
                var = m2.group(1)
            results.append({
                "Annotation": "@Value", "Property": key,
                "Variable": var, "span_start": m.start(), "span_end": span_end
            })

        for m in re_value_dollar.finditer(java_text):
            key = m.group(1)
            span_end = m.end()
            var = None
            m2 = re_field_decl.search(java_text, span_end)
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
            return None

    # ------------------------------------------------------------------
    # Type helpers
    # ------------------------------------------------------------------

    def _simple_type_name(self, type_obj_or_str):
        if type_obj_or_str is None:
            return None
        n = type_obj_or_str.name if hasattr(type_obj_or_str, "name") else str(type_obj_or_str)
        # Strip HTML-escaped generics
        n = re.sub(r'\s*&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;\s*', '', n)
        n = re.sub(r'\s*&lt;[^&gt;]+&gt;\s*', '', n)
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
                return f"{base}&amp;lt;{', '.join(args)}&amp;gt;"
            return base
        except Exception:
            return "Unknown"

    def _type_to_simple(self, t) -> str:
        if t is None:
            return ""
        base = getattr(t, "name", str(t)) or ""
        if "." in base:
            base = base.split(".")[-1]
        dims = "[]" * int(getattr(t, "dimensions", 0) or 0)
        return f"{base}{dims}"

    def extract_method_metadata(self, method_node) -> dict:
        is_ctor = isinstance(method_node, jt.ConstructorDeclaration)

        param_types = []
        for p in getattr(method_node, "parameters", []) or []:
            t = self._type_to_simple(getattr(p, "type", None))
            if getattr(p, "varargs", False):
                t = t + "[]"
            param_types.append(t or "")

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
            if not isinstance(path, str) or not path.startswith("com."):
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

    # ------------------------------------------------------------------
    # Field / DI helpers
    # ------------------------------------------------------------------

    def _collect_autowired_fields(self, class_node) -> dict:
        """Collect ALL instance field declarations (not just @Autowired/@Inject).

        Plain private fields like:
            private RequestDetailsValidator requestDetailsValidator;
            private MultiSortPagingContextValidator pagingContextValidator;
        must be included so that calls like:
            this.requestDetailsValidator.validate(...)
            this.pagingContextValidator.setMaxPageSize(...)
        pass _keep_qualified_call (which checks `if qual in autowired_fields`)
        and resolve to the correct class name rather than the bare variable name.
        """
        autowired = {}
        for _, fd in class_node.filter(jt.FieldDeclaration):
            tname = self._simple_type_name(fd.type)
            for decl in getattr(fd, "declarators", []):
                autowired[decl.name] = tname
        return autowired

    # ------------------------------------------------------------------
    # Variable type inference
    # ------------------------------------------------------------------

    def _infer_type_from_initializer(self, decl):
        init = getattr(decl, "initializer", None)
        try:
            if isinstance(init, jt.ClassCreator):
                return self._simple_type_name(init.type)
        except Exception:
            pass
        return None

    def _build_var_types_for_method(self, method_node, autowired_fields):
        var_types = {}
        locals_from_new = set()
        params_set = set()

        for p in getattr(method_node, "parameters", []):
            var_types[p.name] = self._simple_type_name(p.type)
            params_set.add(p.name)

        for _, lv in method_node.filter(jt.LocalVariableDeclaration):
            declared_type = self._simple_type_name(lv.type)
            for decl in getattr(lv, "declarators", []):
                name = decl.name
                tname = declared_type
                # Java 8 has no 'var'; skip the var-inference branch from Java 18 adapter
                inferred = self._infer_type_from_initializer(decl)
                if inferred:
                    tname = inferred
                    locals_from_new.add(name)
                var_types[name] = tname

        var_types.update(autowired_fields or {})
        return var_types, locals_from_new, params_set

    # ------------------------------------------------------------------
    # Call-filtering helpers
    # ------------------------------------------------------------------

    def _normalize_qualifier(self, qual: str, var_types: dict) -> str:
        if qual in var_types:
            return qual
        for k in var_types:
            if qual == (k + k):
                return k
        return qual

    def _is_same_package_type(self, type_name, package_name) -> bool:
        return bool(package_name and package_name.startswith('com.') and type_name)

    def _keep_qualified_call(self, qual, var_types, imports_types, autowired_fields,
                              wildcard_packages, locals_from_new, params_set, package_name) -> bool:
        qual = self._normalize_qualifier(qual, var_types)
        if qual in autowired_fields:
            return True
        t = var_types.get(qual)
        if t and qual in locals_from_new and self.accept_local_new_types:
            return True
        if t and qual in params_set and self.accept_parameter_types:
            return True
        if t and t in imports_types:
            return True
        if qual in imports_types:
            return True
        if wildcard_packages and t:
            return True
        if self.accept_same_package and self._is_same_package_type(t, package_name):
            return True
        if t:          # variable has a known declared type in var_types
            return True
        return False
    def _keep_unqualified_call(self, member, static_members, static_wildcard_classes) -> bool:
        if self.include_unqualified:
            return True
        if member in static_members:
            return True
        if static_wildcard_classes:
            return True
        return False

    def _get_package_name(self, java_code: str):
        pat = html.unescape(
            self.regex.get("package", r'^\s*package\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;')
        )
        m = re.search(pat, java_code, flags=re.MULTILINE)
        return m.group(1) if m else None

    # ------------------------------------------------------------------
    # Declared types
    # ------------------------------------------------------------------

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
        for _, m in type_node.filter(jt.MethodDeclaration):
            yield m.name, m
        for _, c in type_node.filter(jt.ConstructorDeclaration):
            yield c.name, c

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

    # ------------------------------------------------------------------
    # Dynamic qualifier / chained-call extraction
    # ------------------------------------------------------------------

    def _extract_dynamic_terminal_methods(self, text: str) -> set:
        """
        Extract terminal method names from dynamic chains such as
        map.get(key).doSomething(...).
        Java 8 streams produce many such patterns.
        """
        if not isinstance(text, str) or not text.strip():
            return set()

        pat = None
        if isinstance(self.regex.get("re_dynamic_qual"), str) and self.regex["re_dynamic_qual"].strip():
            try:
                pat = self._rx("re_dynamic_qual", flags=re.MULTILINE | re.DOTALL)
            except Exception:
                pat = None

        terms = set()
        if pat is not None:
            for m in pat.finditer(text):
                try:
                    name = m.group(1)
                    if isinstance(name, str) and name.strip():
                        terms.add(f"{name.strip()}()")
                except Exception:
                    continue
            return terms

        # Default single-dynamic-segment: base(...).terminal(...)
        single_dyn = re.compile(
            r"""\b[A-Za-z_]\w*\s*\([^()]*\)\s*\.\s*([A-Za-z_]\w*)\s*\(""",
            re.MULTILINE | re.DOTALL,
        )
        for m in single_dyn.finditer(text):
            name = m.group(1)
            if name and name.strip():
                terms.add(f"{name.strip()}()")

        # Multi-segment chains: base(...).m1(...).m2(...)
        chain_dyn = re.compile(
            r"""\b[A-Za-z_]\w*\s*\([^()]*\)(?:\s*\.\s*[A-Za-z_]\w*\s*\([^()]*\))+""",
            re.MULTILINE | re.DOTALL,
        )
        for cm in chain_dyn.finditer(text):
            last_methods = re.findall(r'\.\s*([A-Za-z_]\w*)\s*\(', cm.group(0))
            if last_methods:
                terms.add(f"{last_methods[-1].strip()}()")

        return terms

    # ------------------------------------------------------------------
    # Expression walker (for nested invocations)
    # ------------------------------------------------------------------

    def _collect_invocations_in_expression(
        self, expr, *,
        var_types, imports_types, autowired_fields,
        wildcard_packages, locals_from_new, params_set, package_name,
    ) -> set:
        calls = set()
        if expr is None:
            return calls
        try:
            if isinstance(expr, jt.MethodInvocation):
                qual = expr.qualifier or ""
                member = expr.member
                if qual and ("(" in qual or ")" in qual):
                    calls.add(f"{qual}.{member}()")
                elif qual:
                    qual = self._normalize_qualifier(qual, var_types)
                    if self._keep_qualified_call(
                        qual, var_types, imports_types, autowired_fields,
                        wildcard_packages, locals_from_new, params_set, package_name,
                    ):
                        resolved_type = var_types.get(qual) or autowired_fields.get(qual) or qual
                        calls.add(f"{resolved_type}.{member}()")
                else:
                    if self._keep_unqualified_call(member, set(), set()):
                        calls.add(f"{member}()")
                for a in getattr(expr, "arguments", []) or []:
                    calls |= self._collect_invocations_in_expression(
                        a, var_types=var_types, imports_types=imports_types,
                        autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
                        locals_from_new=locals_from_new, params_set=params_set,
                        package_name=package_name,
                    )
                return calls

            if isinstance(expr, jt.ClassCreator):
                ctor_type = self._simple_type_name(expr.type)
                if ctor_type:
                    calls.add(f"{ctor_type}.{ctor_type}()")
                for a in getattr(expr, "arguments", []) or []:
                    calls |= self._collect_invocations_in_expression(
                        a, var_types=var_types, imports_types=imports_types,
                        autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
                        locals_from_new=locals_from_new, params_set=params_set,
                        package_name=package_name,
                    )
                return calls

            for attr in ("expression", "condition", "then_expression", "else_expression",
                         "left", "right", "operand"):
                node = getattr(expr, attr, None)
                if node is not None:
                    calls |= self._collect_invocations_in_expression(
                        node, var_types=var_types, imports_types=imports_types,
                        autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
                        locals_from_new=locals_from_new, params_set=params_set,
                        package_name=package_name,
                    )
            for list_attr in ("expressions", "arguments"):
                lst = getattr(expr, list_attr, None)
                if isinstance(lst, (list, tuple)):
                    for node in lst:
                        calls |= self._collect_invocations_in_expression(
                            node, var_types=var_types, imports_types=imports_types,
                            autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
                            locals_from_new=locals_from_new, params_set=params_set,
                            package_name=package_name,
                        )
        except Exception:
            pass
        return calls

    # ------------------------------------------------------------------
    # Main call-finder (AST path)
    # ------------------------------------------------------------------

    def find_calls_in_method(self, type_node, method_node, code: str) -> list:
        calls = set()
        package_name = self._get_package_name(code)

        # FIX 1 & 3: Re-use cached AST instead of re-parsing the full file
        # on every method call.  _raw_ast_cache is injected by configure().
        _cache_key = id(code)  # code object is the same str within one file run
        _cached = self._raw_ast_cache.get(_cache_key)
        if _cached is None:
            try:
                _cached = javalang.parse.parse(code)
            except Exception:
                _cached = False  # sentinel: parse failed
            self._raw_ast_cache[_cache_key] = _cached

        try:
            if _cached and _cached is not False:
                tree = _cached
            else:
                tree = javalang.parse.parse(code)
            imports_types, wildcard_packages, static_members, static_wildcard_classes = \
                self._collect_com_imports(tree)
        except Exception:
            imports_types, wildcard_packages, static_members, static_wildcard_classes = \
                set(), set(), set(), set()

        autowired_fields = self._collect_autowired_fields(type_node)
        var_types, locals_from_new, params_set = self._build_var_types_for_method(
            method_node, autowired_fields
        )

        # Include for-loop element variables
        for _, forstmt in method_node.filter(jt.ForStatement):
            if hasattr(forstmt, "control") and hasattr(forstmt.control, "var"):
                var_decl = forstmt.control.var
                if var_decl:
                    tname = self._simple_type_name(var_decl.type)
                    for declarator in getattr(var_decl, "declarators", []):
                        var_types[declarator.name] = tname

        def _is_dynamic(q: str) -> bool:
            return isinstance(q, str) and ("(" in q or ")" in q)

        # Pre-build set of MethodInvocations that are selectors on a ClassCreator
        # or another MethodInvocation — they have qualifier=None but are NOT sibling calls.
        _selector_invocations = set()
        
        def _collect_selector_ids(node, result_set):
            for sel in (getattr(node, "selectors", None) or []):
                if isinstance(sel, jt.MethodInvocation):
                    result_set.add(id(sel))
                    _collect_selector_ids(sel, result_set)  # recurse into nested selectors

        _selector_invocations = set()
        for _, cc in method_node.filter(jt.ClassCreator):
            _collect_selector_ids(cc, _selector_invocations)
        for _, parent_inv in method_node.filter(jt.MethodInvocation):
            _collect_selector_ids(parent_inv, _selector_invocations)
        def _build_chain_string(start_inv, resolved_root):
            """Build full chain string including selectors.
            e.g. abc.method1() with selector method2() → 'ABC.method1().method2()'
            """
            chain = f"{resolved_root}.{start_inv.member}()"
            for sel in (start_inv.selectors or []):
                if isinstance(sel, jt.MethodInvocation):
                    chain += f".{sel.member}()"
            return chain

        def _emit_chain_segments(start_inv, resolved_root, calls_set):
            """FIX 2: emit EACH segment of a chained call individually.

            For  a.method1().method2()  where 'a' resolves to 'ClassA':
              - emits "ClassA.method1()"  (always — we know this class)
              - emits full chain "ClassA.method1().method2()" for downstream
                resolution in case the cleaner can resolve method2's class.

            This ensures method1 is never lost even when method2's return
            type is missing from method_return_index.
            """
            # Always emit the root segment independently
            calls_set.add(f"{resolved_root}.{start_inv.member}()")
            # Record every method name emitted with a real class prefix so the
            # regex fallback path can suppress the bare unqualified duplicates.
            _ast_qualified_methods.add(start_inv.member)
            # Also emit the full chain so the cleaner can resolve downstream
            selectors = [s for s in (start_inv.selectors or [])
                         if isinstance(s, jt.MethodInvocation)]
            if selectors:
                full_chain = f"{resolved_root}.{start_inv.member}()"
                for sel in selectors:
                    full_chain += f".{sel.member}()"
                    _ast_qualified_methods.add(sel.member)
                calls_set.add(full_chain)

        # Pre-build sibling set ONCE (was rebuilt inside every MethodInvocation iteration)
        _sibling_method_names = {n for n, _ in self.get_methods_in_type(type_node)}
        _type_class_name = getattr(type_node, "name", None)

        # Track every method name the AST path emits with a resolved class prefix
        # (e.g. "RequestorValidator.validate") so the regex fallback path below
        # does not re-emit them as bare unqualified calls ("validate", "setMaxPageSize")
        # which the cleaner cannot attribute and which become spurious output rows.
        _ast_qualified_methods: set = set()

        # AST: MethodInvocation nodes
        for _, inv in method_node.filter(jt.MethodInvocation):
            qual = inv.qualifier or ""
            member = inv.member

            if not qual:
                # If this is a selector on a ClassCreator or chained call,
                # it is handled by its parent — skip to avoid wrong class attribution.
                if id(inv) in _selector_invocations:
                    pass
                else:
                    sibling_method_names = _sibling_method_names
                    if member in sibling_method_names:
                        class_name = _type_class_name
                        if class_name:
                            # FIX 2: emit each chain segment independently
                            _emit_chain_segments(inv, class_name, calls)
                        else:
                            calls.add(f"{member}()")
                    elif self._keep_unqualified_call(member, static_members, static_wildcard_classes):
                        calls.add(f"{member}()")
            elif _is_dynamic(qual):
                calls.add(f"{qual}.{member}()")
            else:
                # Strip "this." prefix so "this.obj" resolves the same as "obj"
                if qual.startswith("this."):
                    qual = qual[5:]
                qual = self._normalize_qualifier(qual, var_types)
                if self._keep_qualified_call(
                    qual, var_types, imports_types, autowired_fields,
                    wildcard_packages, locals_from_new, params_set, package_name,
                ):
                    # Resolve the variable name to its declared class.
                    # var_types covers local variables and parameters;
                    # autowired_fields covers all field declarations (injected or plain private).
                    # Without the autowired_fields fallback, plain private fields like
                    #   private RequestDetailsValidator requestDetailsValidator;
                    # resolve to the bare variable name ("requestDetailsValidator")
                    # instead of the class name ("RequestDetailsValidator"), producing
                    # a lowercase-rooted call that the cleaner silently drops.
                    resolved_type = var_types.get(qual) or autowired_fields.get(qual) or qual
                    _emit_chain_segments(inv, resolved_type, calls)

        # Collect ClassCreator IDs already handled via ThrowStatement
        _throw_creators = set()
        for _, th in method_node.filter(jt.ThrowStatement):
            expr = getattr(th, "expression", None)
            if isinstance(expr, jt.ClassCreator):
                _throw_creators.add(id(expr))

        # Standalone new X(...) — not inside a throw
        for _, cc in method_node.filter(jt.ClassCreator):
            if id(cc) in _throw_creators:
                continue
            ctor_type = self._simple_type_name(cc.type)
            if not ctor_type:
                continue
            if ctor_type in imports_types or wildcard_packages or \
               self.accept_local_new_types or self.accept_same_package:
                calls.add(f"{ctor_type}.{ctor_type}()")
            for arg in getattr(cc, "arguments", []) or []:
                calls |= self._collect_invocations_in_expression(
                    arg, var_types=var_types, imports_types=imports_types,
                    autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
                    locals_from_new=locals_from_new, params_set=params_set,
                    package_name=package_name,
                )

        # throw new Type(...) — constructors + nested calls
        
        for _, th in method_node.filter(jt.ThrowStatement):
            expr = getattr(th, "expression", None)
            if isinstance(expr, jt.ClassCreator) and getattr(expr, "type", None):
                ctor_type = self._simple_type_name(expr.type)
                if ctor_type:
                    calls.add(f"{ctor_type}.{ctor_type}()")
            calls |= self._collect_invocations_in_expression(
                expr, var_types=var_types, imports_types=imports_types,
                autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
                locals_from_new=locals_from_new, params_set=params_set,
                package_name=package_name,
            )

        # # Chained / stream calls via method source regex
        # chained_pat = re.compile(
        #     r'''\b[a-zA-Z_]\w*(?:\s*\([^()]*\))?(?:\s*\.\s*[a-zA-Z_]\w*\s*\([^()]*\)){1,}''',
        #     re.MULTILINE | re.VERBOSE,
        # )
        # src = self._get_method_source(code, method_node)
        # if src:
        #     src_clean = _strip_source_comments(src)
        #     for chain in chained_pat.findall(src_clean):
        #         chain = chain.strip()
        #         if chain:
        #             calls.add(chain)
        #     for dyn in self._extract_dynamic_terminal_methods(src_clean):
        #         calls.add(dyn)

        # Chained / stream calls via method source regex
        chained_pat = re.compile(
            r'''\b[a-zA-Z_]\w*(?:\s*\([^()]*\))?(?:\s*\.\s*[a-zA-Z_]\w*\s*\([^()]*\)){1,}''',
            re.MULTILINE | re.VERBOSE,
        )
        # Matches chains rooted at a constructor call: Word(...).method(...)
        # e.g. "BigDecimal(quantity).multiply(price)" — the root is a ctor, not a var/class.
        ctor_rooted_pat = re.compile(r'^([A-Za-z_]\w*)\s*\(')
        leading_var_pat = re.compile(r'^([A-Za-z_]\w*)\.')
        java_kw = self.language_keywords()
        src = self._get_method_source(code, method_node)
        # Track method names claimed by chained_pat so _extract_dynamic_terminal_methods
        # does not re-emit them as bare unqualified calls (which get attributed to this class).
        chained_claimed_methods: set = set()
        if src:
            src_clean = _strip_source_comments(src)
            for chain in chained_pat.findall(src_clean):
                chain = chain.strip()
                if not chain:
                    continue
                lv = leading_var_pat.match(chain)
                if lv:
                    leading = lv.group(1)
                    # Skip chains rooted at a Java keyword (return, new, etc.)
                    if leading in java_kw:
                        continue
                    resolved = var_types.get(leading)
                    if resolved and resolved != leading:
                        # Known variable — replace with its resolved type name
                        chain = resolved + chain[len(leading):]
                        calls.add(chain)
                    elif leading in var_types:
                        # Known variable whose name matches its type
                        calls.add(chain)
                    elif leading[0].isupper():
                        # Looks like a class name (UpperCamelCase) — keep as-is
                        calls.add(chain)
                    else:
                        # Lowercase token not in var_types — try autowired/private fields.
                        # e.g. "requestorValidator.validate(...)" where requestorValidator
                        # is a plain private field (not @Autowired) lives in autowired_fields.
                        field_type = autowired_fields.get(leading)
                        if field_type:
                            chain = field_type + chain[len(leading):]
                            calls.add(chain)
                        # else: truly unknown — skip to avoid false attribution
                else:
                    # No leading "Word." prefix.  This happens for constructor-rooted
                    # chains like "BigDecimal(quantity).multiply(price)" where the
                    # token before the first "(" is the type name, not a variable.
                    # Rewrite as "TypeName.method1().method2()..." so the call is
                    # attributed to the right type rather than added as a raw string
                    # (which downstream code cannot parse) or dropped silently.
                    cr = ctor_rooted_pat.match(chain)
                    if cr:
                        ctor_type = cr.group(1)
                        if ctor_type not in java_kw:
                            # Extract every .method() segment after the constructor call.
                            segments = re.findall(r'\.\s*([A-Za-z_]\w*)\s*\(', chain)
                            for seg in segments:
                                rewritten = f"{ctor_type}.{seg}()"
                                calls.add(rewritten)
                                chained_claimed_methods.add(seg)
                    # else: truly unclassifiable — skip to avoid false attribution
            for dyn in self._extract_dynamic_terminal_methods(src_clean):
                # Strip trailing "()" to get the bare name for the duplicate check.
                bare = dyn[:-2] if dyn.endswith("()") else dyn
                if bare in chained_claimed_methods:
                    # chained_pat already emitted a properly qualified version;
                    # the bare unqualified form would be attributed to this class — skip.
                    continue
                if bare in _ast_qualified_methods:
                    # The AST path already emitted this method with its correct class prefix
                    # (e.g. "RequestorValidator.validate"). Suppress the bare form here —
                    # it would produce a spurious row attributed to the wrong class.
                    continue
                calls.add(dyn)

        return sorted(calls)

    # ------------------------------------------------------------------
    # Fallback parse (regex-only path when AST fails)
    # ------------------------------------------------------------------

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

        # --- Variable / DI types ---
        autowired_fields = {}
        for m in re_autowired_field.finditer(java_code):
            raw_type = m.group(1)
            var_name = m.group(2)
            tname = re.sub(
                r'(&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;|&lt;[^&gt;]+&gt;)', '', raw_type
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
                r'(&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;|&lt;[^&gt;]+&gt;)', '', raw_type
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
                r'(&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;|&lt;[^&gt;]+&gt;)', '', raw_type
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

        def _process_block(block_text: str, method_name):
            filtered = set()

            for m in re_simple_call.finditer(block_text):
                qual, member = m.group(1), m.group(2)
                if str(qual).strip().lower() in java_keywords:
                    continue
                if _is_dyn(qual):
                    filtered.add(f"{qual}.{member}()")
                elif self._keep_qualified_call(
                    qual, var_types, imports_types, autowired_fields,
                    wildcard_packages, locals_from_new, params_set, package_name,
                ):
                    filtered.add(f"{qual}.{member}()")

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
                    filtered.add(f"{qual}.{member}")

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
                filtered.add(f"{ctor_class}.{ctor_class}()")
                args_block = self._extract_balanced_args(block_text, tm.end() - 1)
                if args_block:
                    for sm in re_simple_call.finditer(args_block):
                        q, mem = sm.group(1), sm.group(2)
                        if str(q).strip().lower() in java_keywords:
                            continue
                        if _is_dyn(q):
                            filtered.add(f"{q}.{mem}()")
                        elif self._keep_qualified_call(
                            q, var_types, imports_types, autowired_fields,
                            wildcard_packages, locals_from_new, params_set, package_name,
                        ):
                            filtered.add(f"{q}.{mem}()")
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
                filtered.add(f"{ctor_class}.{ctor_class}()")
                args_block = self._extract_balanced_args(block_text, nm.end() - 1)
                if args_block:
                    for sm in re_simple_call.finditer(args_block):
                        q, mem = sm.group(1), sm.group(2)
                        if str(q).strip().lower() in java_keywords:
                            continue
                        if _is_dyn(q):
                            filtered.add(f"{q}.{mem}()")
                        elif self._keep_qualified_call(
                            q, var_types, imports_types, autowired_fields,
                            wildcard_packages, locals_from_new, params_set, package_name,
                        ):
                            filtered.add(f"{q}.{mem}()")
                    for um in re_unqualified_call.finditer(args_block):
                        mem = um.group(1)
                        if mem not in java_keywords and self.include_unqualified:
                            filtered.add(f"{mem}()")
                    for cm in re_chain.finditer(args_block):
                        filtered.add(cm.group(0))

            for dyn in self._extract_dynamic_terminal_methods(block_text):
                filtered.add(dyn)

            for call in sorted(filtered):
                per_method_calls.append({'method_name': method_name, 'object_call': call})

        # Walk method bodies
        for sig_match in re_method_with_throw.finditer(java_code):
            brace_pos = java_code.find('{', sig_match.end() - 1)
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
            method_text = java_code[sig_match.start():end_idx + 1]
            name_match = re_method_name_in_sig.search(method_text)
            method_name = name_match.group(1) if name_match else None
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

    # ------------------------------------------------------------------
    # System-call filter
    # ------------------------------------------------------------------

    def is_system_call(self, call: str) -> bool:
        if not isinstance(call, str):
            return False
        call = call.strip()
        if not call:
            return False

        call_ng = re.sub(r'\s*&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;\s*', '', call)
        call_ng = re.sub(r'\s*&lt;[^&gt;]+&gt;\s*', '', call_ng)
        lc = call_ng.lower()

        system_qualifiers = self.details.get("SYSTEM_QUALIFIERS", [
            r"^logger\.", r"^log\.", r"^system\.", r"^string\.", r"^objects\.", r"^arrays\.",
            r"^collections\.", r"^optional\.", r"^stream\.", r"^httpsecurity\.", r"^security\.",
        ])
        for pattern in system_qualifiers:
            if re.match(pattern, lc):
                return True

        def extract_method(c: str) -> str:
            part = c.split(".")[-1]
            part = re.sub(r"\(.*\)", "", part)
            return part.replace(";", "").replace('"', "").replace("'", "").strip().lower()

        default_system_methods = {"equals"}
        system_methods = default_system_methods | {
            m.lower() for m in self.details.get("SYSTEM_METHODS", [])
        }
        return extract_method(call_ng) in system_methods

    def language_keywords(self) -> set:
        return {"return", "this", "super", "new"} | set(self.details.get("control_keywords", []))

    # ------------------------------------------------------------------
    # Object-class map
    # ------------------------------------------------------------------

    def build_object_class_map(self, app_folder: str) -> dict:
        obj_class_map = {}
        PRIMITIVES = set(self.details.get("PRIMITIVE", []))
        COLLECTION_TYPES = set(self.details.get("COLLECTION_TYPES", [
            "List", "Set", "Map", "Collection", "Iterable"
        ]))

        var_decl_pattern  = self._rx("var_decl_pattern", flags=re.MULTILINE)
        for_loop_pattern  = self._rx("for_loop_pattern", flags=re.MULTILINE)
        simple_local_decl = re.compile(r'\b([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*=')
        method_param_list_pat = re.compile(
            r'\b(?:public|protected|private)\b[^{;]*\(([^)]*)\)', re.MULTILINE
        )
        method_param_decl_pat = self._rx("method_param_decl")

        def clean_type(t: str) -> str:
            if not t:
                return t
            t2 = re.sub(r'\s*&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;\s*', '', t)
            t2 = re.sub(r'\s*&lt;[^&gt;]+&gt;\s*', '', t2)
            return t2.replace('[]', '').strip()

        def update_map(f: str, var: str, typ: str, *, source: str):
            if not typ or typ in PRIMITIVES:
                return
            key_scoped = (f.lower(), var.lower())
            key_global = var.lower()
            existing_s = obj_class_map.get(key_scoped)
            if existing_s:
                if existing_s in COLLECTION_TYPES and typ not in COLLECTION_TYPES:
                    obj_class_map[key_scoped] = typ
            else:
                obj_class_map[key_scoped] = typ
            existing_g = obj_class_map.get(key_global)
            if existing_g:
                if existing_g in COLLECTION_TYPES and typ not in COLLECTION_TYPES:
                    obj_class_map[key_global] = typ
            else:
                obj_class_map[key_global] = typ

        for root, _, files in os.walk(app_folder):
            for file in files:
                if not file.endswith(self.file_extension()):
                    continue
                fpath = os.path.join(root, file)
                # FIX 3: reuse cached file content and parsed AST
                try:
                    if fpath in self._file_content_cache:
                        code = self._file_content_cache[fpath]
                    else:
                        with open(fpath, "r", encoding="utf-8") as fh:
                            code = fh.read()
                        self._file_content_cache[fpath] = code
                except Exception:
                    continue

                # --- AST path ---
                _ast_key = fpath
                try:
                    if _ast_key in self._raw_ast_cache:
                        parsed = self._raw_ast_cache[_ast_key]
                        if parsed is False:
                            raise Exception("cached parse failure")
                    else:
                        parsed = javalang.parse.parse(code)
                        self._raw_ast_cache[_ast_key] = parsed
                    for _, type_node in parsed.filter(jt.ClassDeclaration):
                        for _, fd in type_node.filter(jt.FieldDeclaration):
                            tname = self._simple_type_name(fd.type)
                            for decl in getattr(fd, "declarators", []):
                                if tname and tname not in PRIMITIVES:
                                    update_map(file, decl.name, tname, source="ast_field")

                        for _, mnode in type_node.filter(jt.MethodDeclaration):
                            for p in getattr(mnode, "parameters", []):
                                tname = self._simple_type_name(p.type)
                                if tname and tname not in PRIMITIVES:
                                    update_map(file, p.name, tname, source="ast_param")
                            for _, lv in mnode.filter(jt.LocalVariableDeclaration):
                                declared_type = self._simple_type_name(lv.type)
                                for decl in getattr(lv, "declarators", []):
                                    tname = declared_type or self._infer_type_from_initializer(decl)
                                    if tname and tname not in PRIMITIVES:
                                        update_map(file, decl.name, tname, source="ast_local")
                            for _, forstmt in mnode.filter(jt.ForStatement):
                                if hasattr(forstmt, "control") and hasattr(forstmt.control, "var"):
                                    var_decl = forstmt.control.var
                                    if var_decl:
                                        tname = self._simple_type_name(var_decl.type)
                                        for declarator in getattr(var_decl, "declarators", []):
                                            if tname and tname not in PRIMITIVES:
                                                update_map(file, declarator.name, tname, source="ast_for")

                        for _, cnode in type_node.filter(jt.ConstructorDeclaration):
                            for p in getattr(cnode, "parameters", []):
                                tname = self._simple_type_name(p.type)
                                if tname and tname not in PRIMITIVES:
                                    update_map(file, p.name, tname, source="ast_ctor_param")
                            for _, lv in cnode.filter(jt.LocalVariableDeclaration):
                                declared_type = self._simple_type_name(lv.type)
                                for decl in getattr(lv, "declarators", []):
                                    tname = declared_type or self._infer_type_from_initializer(decl)
                                    if tname and tname not in PRIMITIVES:
                                        update_map(file, decl.name, tname, source="ast_ctor_local")
                    continue
                except Exception:
                    pass

                # --- Regex fallback ---
                for m in var_decl_pattern.finditer(code):
                    raw_type, var_name = m.group(1), m.group(2)
                    t = clean_type(raw_type)
                    if t and t not in PRIMITIVES:
                        update_map(file, var_name, t, source="regex_var_decl")

                for m in for_loop_pattern.finditer(code):
                    raw_type, var_name = m.group(1), m.group(2)
                    t = clean_type(raw_type)
                    if t and t not in PRIMITIVES:
                        update_map(file, var_name, t, source="regex_for_loop")

                for m in simple_local_decl.finditer(code):
                    raw_type, var_name = m.group(1), m.group(2)
                    t = clean_type(raw_type)
                    if t and t not in PRIMITIVES:
                        update_map(file, var_name, t, source="regex_simple_local")

                for pl_match in method_param_list_pat.finditer(code):
                    for pm in method_param_decl_pat.finditer(pl_match.group(1)):
                        raw_type, var_name = pm.group(1), pm.group(2)
                        t = clean_type(raw_type)
                        if t and t not in PRIMITIVES:
                            update_map(file, var_name, t, source="regex_param")

        return obj_class_map

    # ------------------------------------------------------------------
    # Method return index
    # ------------------------------------------------------------------

    def build_method_return_index(self, app_folder: str) -> dict:
        method_return_index = {}
        class_decl_pat = re.compile(r'\bclass\s+(\w+)\b')
        # Captures "class Foo extends Bar" — used for Case 1 inheritance walk
        class_extends_pat = re.compile(r'\bclass\s+(\w+)\s+extends\s+(\w+)')
        method_sig_pat = re.compile(
            r'(?:public|protected|private)?\s+(?:static\s+)?([\w\.&lt;<>\[\]]+)\s+(\w+)\s*\(',
            re.MULTILINE,
        )
        constructor_sig_pat = re.compile(
            r'(?:public|protected|private)\s+(\w+)\s*\(', re.MULTILINE
        )

        for root, _, files in os.walk(app_folder):
            for file in files:
                if not file.endswith(self.file_extension()):
                    continue
                fpath = os.path.join(root, file)
                # FIX 3: reuse cached file content and parsed AST
                try:
                    if fpath in self._file_content_cache:
                        code = self._file_content_cache[fpath]
                    else:
                        with open(fpath, "r", encoding="utf-8") as f:
                            code = f.read()
                        self._file_content_cache[fpath] = code
                except Exception:
                    continue

                _ast_key2 = fpath
                if _ast_key2 in self._raw_ast_cache:
                    _cached2 = self._raw_ast_cache[_ast_key2]
                    parsed = None if (_cached2 is False) else _cached2
                else:
                    try:
                        parsed = javalang.parse.parse(code)
                        self._raw_ast_cache[_ast_key2] = parsed
                    except Exception:
                        parsed = None
                        self._raw_ast_cache[_ast_key2] = False

                if parsed:
                    for _, cls in parsed.filter(jt.ClassDeclaration):
                        cls_name = getattr(cls, "name", None)
                        if not cls_name:
                            continue
                        method_return_index.setdefault(cls_name, {})
                        # Case 1: record parent class so service can walk extends chain
                        parent_type = getattr(cls, "extends", None)
                        if parent_type is not None:
                            parent_name = getattr(parent_type, "name", None)
                            if parent_name:
                                method_return_index[cls_name]["__extends__"] = parent_name
                        for _, m in cls.filter(jt.MethodDeclaration):
                            rt = m.return_type
                            if rt is None:
                                rname = "void"
                            else:
                                base = rt.name if hasattr(rt, "name") else "Unknown"
                                rname = re.sub(
                                    r'(&amp;lt;[^&amp;gt]+&amp;gt;|&lt;[^&gt;]+&gt;)', '', base
                                )
                            method_return_index[cls_name][m.name] = rname.strip().split('.')[-1]
                        for _, c in cls.filter(jt.ConstructorDeclaration):
                            method_return_index[cls_name][c.name] = "<constructor>"

                    for _, itf in parsed.filter(jt.InterfaceDeclaration):
                        itf_name = getattr(itf, "name", None)
                        if not itf_name:
                            continue
                        method_return_index.setdefault(itf_name, {})
                        for _, m in itf.filter(jt.MethodDeclaration):
                            rt = m.return_type
                            if rt is None:
                                rname = "void"
                            else:
                                base = rt.name if hasattr(rt, "name") else "Unknown"
                                rname = re.sub(
                                    r'(&amp;lt;[^&amp;gt]+&amp;gt;|&lt;[^&gt;]+&gt;)', '', base
                                )
                            method_return_index[itf_name][m.name] = rname.strip().split('.')[-1]
                    continue

                # Regex fallback
                cls_match = class_decl_pat.search(code)
                if not cls_match:
                    continue
                cls_name = cls_match.group(1)
                method_return_index.setdefault(cls_name, {})
                # Case 1: capture extends from regex fallback too
                ext_match = class_extends_pat.search(code)
                if ext_match and ext_match.group(1) == cls_name:
                    method_return_index[cls_name]["__extends__"] = ext_match.group(2)
                for mm in method_sig_pat.finditer(code):
                    return_type = mm.group(1)
                    method_name = mm.group(2)
                    simple_return = re.sub(
                        r'(&amp;lt;[^&amp;gt]+&amp;gt;|&lt;[^&gt;]+&gt;)', '', return_type
                    ).strip().split('.')[-1]
                    method_return_index[cls_name][method_name] = simple_return
                for cm in constructor_sig_pat.finditer(code):
                    ctor_name = cm.group(1)
                    if ctor_name == cls_name:
                        method_return_index[cls_name][ctor_name] = "<constructor>"

        return method_return_index

    # ------------------------------------------------------------------
    # File → type map
    # ------------------------------------------------------------------

    def find_type_to_file_map(self, app_folder: str) -> dict:
        java_files_map = {}
        for root, _, files in os.walk(app_folder):
            for f in files:
                if f.endswith(self.file_extension()):
                    class_name = os.path.splitext(f)[0]
                    java_files_map[class_name] = os.path.join(root, f)
        return java_files_map

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

        def find_annotation_block_start(sig_idx):
            i = sig_idx - 2
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

        anno_start = find_annotation_block_start(sig_line_idx)
        start_line_idx = anno_start if anno_start is not None else sig_line_idx

        def find_opening_brace_line(from_line):
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

        def find_closing_brace_line(open_line):
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

        brace_open_line = find_opening_brace_line(sig_line_idx)
        if brace_open_line is None:
            return 1

        end_line_idx = find_closing_brace_line(brace_open_line)
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

    def extract_application_properties_from_folder(
        self,
        app_folder,
        include_filepath: bool = True,
        include_trailing_dot: bool = True,
    ):
        def _compose(jpath: Path, method_name) -> str:
            base = jpath.stem
            if method_name:
                return f"{base}.{method_name}"
            return f"{base}." if include_trailing_dot else base

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
            for m in re_property_source.finditer(txt):
                ps_refs.add(m.group(1).strip())

        properties_map = self.load_all_properties(app_folder, additional_property_refs=ps_refs)
        rows = []

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
                    "Filename.methodname": _compose(jf, method_name),
                    "Annotation": item["Annotation"],
                    "Property": key,
                    "Variable": var,
                    "method_name": method_name,
                    "Actual Value": actual,
                })

            # @ConfigurationProperties
            for m in re_configuration_properties.finditer(code):
                prefix = m.group(1)
                matched = {k: v for k, v in properties_map.items()
                           if k == prefix or k.startswith(prefix + ".")}
                actual = "; ".join(f"{k}={v}" for k, v in matched.items()) if matched else "NOT_FOUND"
                rows.append({
                    "FileName": jf.name.replace(".java", ""),
                    "FilePath": str(jf),
                    "Filename.methodname": _compose(jf, None),
                    "Annotation": "@ConfigurationProperties",
                    "Property": prefix,
                    "Variable": None,
                    "method_name": None,
                    "Actual Value": actual,
                })

            # @PropertySource
            for m in re_property_source.finditer(code):
                rows.append({
                    "FileName": jf.name.replace(".java", ""),
                    "FilePath": str(jf),
                    "Filename.methodname": _compose(jf, None),
                    "Annotation": "@PropertySource",
                    "Property": m.group(1),
                    "Variable": None,
                    "method_name": None,
                    "Actual Value": "FILE_REFERENCE",
                })

            # messageSource.getMessage(...)
            for mm in re_message_key.finditer(code):
                key = mm.group(1)
                actual = properties_map.get(key, "NOT_FOUND")
                method_name = self._find_enclosing_method(method_index_map, mm.start())
                rows.append({
                    "FileName": jf.name.replace(".java", ""),
                    "FilePath": str(jf),
                    "Filename.methodname": _compose(jf, method_name),
                    "Annotation": "MessageSource",
                    "Property": key,
                    "Variable": None,
                    "method_name": method_name,
                    "Actual Value": actual,
                })

        df = pd.DataFrame(rows)
        if include_filepath:
            cols = ["FileName", "FilePath", "Filename.methodname", "Annotation",
                    "Property", "Variable", "method_name", "Actual Value"]
        else:
            cols = ["FileName", "Filename.methodname", "Annotation",
                    "Property", "Variable", "method_name", "Actual Value"]
        df = df.reindex(columns=cols)
        if "method_name" in df.columns:
            df = df[df["method_name"].notna()]
        return df
