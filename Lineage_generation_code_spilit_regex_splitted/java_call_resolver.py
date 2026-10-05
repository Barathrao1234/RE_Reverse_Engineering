# =============================================================================
# FILE: java_call_resolver.py
# PURPOSE: Call Detection & Resolution Logic for Java 8 source code
#
# CONTAINS:
#   class CallResolverMixin  - mixin providing:
#     _collect_autowired_fields()
#     _infer_type_from_initializer()
#     _build_var_types_for_method()
#     _normalize_qualifier()
#     _is_same_package_type()
#     _has_lombok_getter_setter()
#     _lombok_accessor_targets()
#     _extract_declared_contract_types()
#     _type_node_to_signature()
#     _extract_declared_contract_signatures()
#     _is_wrapper_contract_type()
#     _collect_injected_user_qualified_fields()
#     _keep_qualified_call()
#     _keep_unqualified_call()
#     _extract_dynamic_terminal_methods()
#     _is_enum_runtime_accessor()
#     _is_enum_runtime_accessor_call()
#     _collect_invocations_in_expression()
#     find_calls_in_method()        [~1260 lines, contains inner classes/defs]
#     is_system_call()
#     language_keywords()
#
# WHEN TO EDIT THIS FILE:
#   - Changes to call detection, qualifier resolution, or DI field tracking
#   - Changes to Lombok accessor handling
#   - Changes to find_calls_in_method walk logic or chain resolution
#   - Changes to system-call filter or language keyword list
#
# DEPENDS ON:
#   config.py                 (compiled regex patterns via self._rx)
#   utils.py                  (utility helpers)
#   language_adapter_base.py  (LanguageAdapter base class)
#   java_ast_parser.py        (AstParserMixin — provides parse_ast, type helpers,
#                              _parse_com_imports_fallback, fallback_parse)
#
# USED BY:
#   java_adapter.py           (assembled into JavaAdapter via multiple inheritance)
# =============================================================================

import re
import html
import javalang
import javalang.tree as jt


from utils import strip_generics, _strip_source_comments


class CallResolverMixin:
    """
    Mixin: call detection and resolution for Java 8 source files.
    Provides all methods related to finding, qualifying, and filtering
    method calls within a parsed Java AST.
    All methods use self and are designed for multiple inheritance
    into JavaAdapter.
    """

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
            tname = self._effective_type_name(fd.type)
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
                # FIX: use _effective_type_name instead of _simple_type_name so that
                # a new-expression with a package-qualified type like
                #   new nl.acme.foo.ClassName()
                # preserves the full FQN 'nl.acme.foo.ClassName' in object_class_map /
                # var_map.  _simple_type_name strips all but the last segment, so the
                # FQN was lost and _resolve_class_path could not route through
                # _resolve_fqn_path → the import was effectively ignored.
                return self._effective_type_name(init.type)
        except Exception:
            pass
        return None

    def _build_var_types_for_method(self, method_node, autowired_fields):
        var_types = {}
        locals_from_new = set()
        params_set = set()

        for p in getattr(method_node, "parameters", []):
            # FIX: use _effective_type_name so that package-qualified parameter types
            # like  nl.rabobank.schemas...SearchOptions searchOptions  are stored as
            # the full FQN 'nl.rabobank.schemas...SearchOptions' rather than the
            # truncated simple name 'SearchOptions'.
            # Without this, when find_calls_in_method resolves searchOptions.method()
            # it emits 'SearchOptions.method()' which _enrich_call_with_path maps to
            # the wrong (ambiguous) class file found first in type_to_path_full.
            var_types[p.name] = self._effective_type_name(p.type)
            params_set.add(p.name)

        for _, lv in method_node.filter(jt.LocalVariableDeclaration):
            # FIX: use _effective_type_name for local variables too, for the same
            # reason: a variable declared as  nl.x.y.Foo f = ...  must be stored as
            # 'nl.x.y.Foo' so _resolve_class_path can route through _resolve_fqn_path.
            declared_type = self._effective_type_name(lv.type)
            for decl in getattr(lv, "declarators", []):
                name = decl.name
                tname = declared_type
                # Java 8 has no 'var'; skip the var-inference branch from Java 18 adapter
                inferred = self._infer_type_from_initializer(decl)
                if inferred:
                    # FIX: only use the inferred (new-expression) type when there is no
                    # declared type.  For  Class obj = new Class()  the declared_type is
                    # already 'Class' (with its import-resolvable simple name), so
                    # overwriting it with the inferred value was harmless but hid the case
                    # where declared_type carried a package-qualified FQN that inferred
                    # (coming from _simple_type_name before Fix 1) stripped away.
                    # Now that _infer_type_from_initializer uses _effective_type_name both
                    # values are equivalent for the FQN case, but keeping declared_type
                    # as the primary source is semantically correct (the compiler uses it).
                    if not tname:
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
        return bool(package_name and type_name)

    def _has_lombok_getter_setter(self, code: str, class_node=None, class_name: str = None) -> bool:
        """Return True when the class is annotated with Lombok @Getter or @Setter."""
        if not isinstance(code, str) or not code.strip():
            return False

        if class_node is not None:
            try:
                for ann in getattr(class_node, "annotations", []) or []:
                    ann_name = getattr(ann, "name", None)
                    if ann_name in {"Getter", "Setter"} or str(ann_name).endswith("Getter") or str(ann_name).endswith("Setter"):
                        return True
            except Exception:
                pass

        if class_name:
            class_pat = re.compile(r'\bclass\s+' + re.escape(class_name) + r'\b[^\{]*\{', re.MULTILINE)
            m = class_pat.search(code)
            if m:
                head = code[:m.end()]
                if re.search(r'@(?:lombok\.)?Getter\b|@(?:lombok\.)?Setter\b', head):
                    return True

        return bool(re.search(r'@(?:lombok\.)?Getter\b|@(?:lombok\.)?Setter\b', code))

    def _lombok_accessor_targets(self, code: str, class_node=None, class_name: str = None) -> dict:
        """Map Lombok-style getter/setter names to backing field names when possible."""
        if not self._has_lombok_getter_setter(code, class_node=class_node, class_name=class_name):
            return {}

        field_names = set()
        if class_node is not None:
            for _, fd in class_node.filter(jt.FieldDeclaration):
                for decl in getattr(fd, "declarators", []) or []:
                    field_names.add(decl.name)

        if not field_names and isinstance(code, str):
            class_body = code
            if class_name:
                class_pat = re.compile(r'\bclass\s+' + re.escape(class_name) + r'\b[^\{]*\{', re.MULTILINE)
                m = class_pat.search(code)
                if m:
                    class_body = code[m.end():]
            for m in re.finditer(r'\b(?:private|protected|public)\s+[\w<>,\[\]\.?\s]+\s+([A-Za-z_]\w*)\s*(?:=|;)', class_body):
                field_names.add(m.group(1))

        targets = {}
        for field_name in field_names:
            if not isinstance(field_name, str) or not field_name:
                continue
            cap = field_name[0].upper() + field_name[1:] if len(field_name) > 1 else field_name.upper()
            targets[f"get{cap}"] = field_name
            targets[f"is{cap}"] = field_name
            targets[f"set{cap}"] = field_name
        return targets

    def _extract_declared_contract_types(self, field_type_node) -> set:
        """Extract contract type candidates from a field type declaration.

        Example:
          Instance<Validator<Requestor>> -> {"Validator"}
        """
        out = set()
        wrapper_types = {
            "Instance", "Provider", "Optional", "List", "Set",
            "Collection", "Iterable", "Stream"
        }

        def _simple(_t):
            nm = self._simple_type_name(_t)
            return str(nm).strip().split('.')[-1] if nm else None

        def _walk(_t):
            if _t is None:
                return
            _sn = _simple(_t)
            if _sn:
                out.add(_sn)
            for _arg in (getattr(_t, "arguments", []) or []):
                _arg_t = getattr(_arg, "type", None)
                if _arg_t is not None:
                    _walk(_arg_t)

        _walk(field_type_node)
        if not out:
            return set()

        _base = _simple(field_type_node)
        _nested = {x for x in out if x and x != _base}
        if _nested and _base in wrapper_types:
            return _nested
        return out

    def _type_node_to_signature(self, type_node) -> str:
        if type_node is None:
            return ""
        nm = getattr(type_node, "name", None)
        if not nm:
            return ""
        base = str(nm).strip().split('.')[-1]
        args = []
        for arg in (getattr(type_node, "arguments", []) or []):
            arg_t = getattr(arg, "type", None)
            arg_sig = self._type_node_to_signature(arg_t) if arg_t is not None else ""
            if arg_sig:
                args.append(arg_sig)
        return "{}<{}>".format(base, ",".join(args)) if args else base

    def _extract_declared_contract_signatures(self, field_type_node) -> set:
        if field_type_node is None:
            return set()
        out = set()
        wrapper = self._is_wrapper_contract_type(field_type_node)
        args = list(getattr(field_type_node, "arguments", []) or [])
        if wrapper and args:
            for arg in args:
                arg_t = getattr(arg, "type", None)
                sig = self._type_node_to_signature(arg_t)
                if sig:
                    out.add(self._normalize_contract_signature(sig))
            return out
        sig_self = self._type_node_to_signature(field_type_node)
        if sig_self:
            out.add(self._normalize_contract_signature(sig_self))
        return out

    def _is_wrapper_contract_type(self, field_type_node) -> bool:
        """True when field declaration is a wrapper contract type (e.g. Instance<T>)."""
        if field_type_node is None:
            return False
        base = getattr(field_type_node, "name", None)
        if not base:
            return False
        base_simple = str(base).strip().split('.')[-1]
        return base_simple in {
            "Instance", "Provider", "Optional", "List", "Set",
            "Collection", "Iterable", "Stream"
        }

    def _collect_injected_user_qualified_fields(self, type_node) -> dict:
        """Map injected field name -> {annotations, contract_types}."""
        out = {}
        injection_ann = {"Inject", "Autowired", "Resource", "EJB"}
        direct_qualifier_ann = {"CommonValidation", "CommonValidations"}
        skip_ann = {
            "Inject", "Autowired", "Resource", "EJB", "Qualifier", "Named"
        }
        for member in getattr(type_node, "body", []) or []:
            if not isinstance(member, jt.FieldDeclaration):
                continue
            ann_names = {str(getattr(a, "name", "") or "").strip().split(".")[-1]
                         for a in (getattr(member, "annotations", []) or [])}
            # Standard path: explicit injection annotations.
            # Additive path: allow CommonValidation/CommonValidations to reuse
            # the same qualifier-contract implementation mapping.
            if not ((ann_names & injection_ann) or (ann_names & direct_qualifier_ann)):
                continue
            matched = {a for a in ann_names if a and a not in skip_ann}
            is_wrapper_contract = self._is_wrapper_contract_type(getattr(member, "type", None))
            # For wrapper-contract declarations (e.g. Instance<T>), allow
            # contract-only resolution even when qualifier annotation is absent.
            if not matched and not is_wrapper_contract:
                continue
            contract_types = self._extract_declared_contract_types(getattr(member, "type", None))
            contract_signatures = self._extract_declared_contract_signatures(getattr(member, "type", None))
            for decl in getattr(member, "declarators", []) or []:
                vname = getattr(decl, "name", None)
                if vname:
                    out[vname] = {
                        "annotations": set(matched),
                        "contract_types": set(contract_types),
                        "contract_signatures": set(contract_signatures),
                        "wrapper_contract": bool(is_wrapper_contract),
                    }
        return out

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

    def _is_enum_runtime_accessor(self, qualifier: str, member: str) -> bool:
        """True for enum runtime accessor calls like Status.OUTSTANDING.name()."""
        if not isinstance(member, str) or member not in {"name", "ordinal"}:
            return False
        if not isinstance(qualifier, str):
            return False
        q = qualifier.strip()
        if not q or "(" in q or ")" in q:
            return False
        parts = [p for p in q.split('.') if p]
        if not parts:
            return False
        tail = parts[-1]
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', tail):
            return False
        return len(parts) == 1 or (parts[0] and parts[0][0].isupper())

    def _is_enum_runtime_accessor_call(self, call: str) -> bool:
        if not isinstance(call, str):
            return False
        m = re.match(r'^\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\.([A-Za-z_]\w*)\s*\(\s*\)\s*$', call)
        if not m:
            return False
        return self._is_enum_runtime_accessor(m.group(1), m.group(2))

    # ------------------------------------------------------------------
    # Expression walker (for nested invocations)
    # ------------------------------------------------------------------

    def _collect_invocations_in_expression(
        self, expr, *,
        var_types, imports_types, autowired_fields,
        wildcard_packages, locals_from_new, params_set, package_name,
        current_class_name=None,
    ) -> set:
        calls = set()
        if expr is None:
            return calls
        try:
            if isinstance(expr, jt.MethodInvocation):
                qual = expr.qualifier or ""
                member = expr.member
                if not current_class_name:
                    current_class_name = getattr(self, "_active_method_owner", None)
                if qual and self._is_enum_runtime_accessor(qual, member):
                    return calls
                if qual == "this":
                    if current_class_name:
                        calls.add(f"{current_class_name}.{member}()")
                    else:
                        calls.add(f"{member}()")
                elif qual and ("(" in qual or ")" in qual):
                    calls.add(f"{qual}.{member}()")
                elif qual:
                    qual = self._normalize_qualifier(qual, var_types)
                    if self._keep_qualified_call(
                        qual, var_types, imports_types, autowired_fields,
                        wildcard_packages, locals_from_new, params_set, package_name,
                    ):
                        resolved_type = var_types.get(qual) or autowired_fields.get(qual) or qual
                        # FIX: emit simple class name when resolved_type is an FQN
                        if isinstance(resolved_type, str) and '.' in resolved_type and resolved_type[0].islower():
                            resolved_type = resolved_type.split('.')[-1]
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
                        current_class_name=current_class_name,
                    )
                return calls

            if isinstance(expr, jt.ClassCreator):
                ctor_type = self._simple_type_name(expr.type)
                ctor_args = getattr(expr, "arguments", []) or []
                if ctor_type and ctor_args:
                    calls.add(f"{ctor_type}.{ctor_type}()")
                for a in ctor_args:
                    calls |= self._collect_invocations_in_expression(
                        a, var_types=var_types, imports_types=imports_types,
                        autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
                        locals_from_new=locals_from_new, params_set=params_set,
                        package_name=package_name,
                        current_class_name=current_class_name,
                    )
                return calls

            # Handle nested `this.*` expressions, e.g. constructor args:
            # new StatusHistory(..., this.getPurgeDate())
            if isinstance(expr, jt.This):
                selectors = getattr(expr, "selectors", None) or []
                if not current_class_name:
                    current_class_name = getattr(self, "_active_method_owner", None)
                if not selectors:
                    return calls

                first = selectors[0]
                if isinstance(first, jt.MethodInvocation):
                    if current_class_name:
                        calls.add(f"{current_class_name}.{first.member}()")
                    else:
                        calls.add(f"{first.member}()")
                    for a in getattr(first, "arguments", []) or []:
                        calls |= self._collect_invocations_in_expression(
                            a, var_types=var_types, imports_types=imports_types,
                            autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
                            locals_from_new=locals_from_new, params_set=params_set,
                            package_name=package_name,
                            current_class_name=current_class_name,
                        )
                elif isinstance(first, jt.MemberReference):
                    field_name = first.member
                    resolved_class = (
                        autowired_fields.get(field_name)
                        or var_types.get(field_name)
                        or field_name
                    )
                    if isinstance(resolved_class, str) and '.' in resolved_class and resolved_class[0].islower():
                        resolved_class = resolved_class.split('.')[-1]
                    for sel in selectors[1:]:
                        if isinstance(sel, jt.MethodInvocation):
                            calls.add(f"{resolved_class}.{sel.member}()")
                            for a in getattr(sel, "arguments", []) or []:
                                calls |= self._collect_invocations_in_expression(
                                    a, var_types=var_types, imports_types=imports_types,
                                    autowired_fields=autowired_fields, wildcard_packages=wildcard_packages,
                                    locals_from_new=locals_from_new, params_set=params_set,
                                    package_name=package_name,
                                    current_class_name=current_class_name,
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
                        current_class_name=current_class_name,
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
                            current_class_name=current_class_name,
                        )
        except Exception:
            pass
        return calls

    # ------------------------------------------------------------------
    # Main call-finder (AST path)
    # ------------------------------------------------------------------

    def find_calls_in_method(self, type_node, method_node, code: str) -> list:
        class _OrderedCallCollector:
            def __init__(self):
                self._seen = set()
                self._items = []

            def add(self, item):
                if item in self._seen:
                    return
                self._seen.add(item)
                self._items.append(item)

            def update(self, iterable):
                for item in iterable or []:
                    self.add(item)

            def __ior__(self, iterable):
                self.update(iterable)
                return self

            def __iter__(self):
                return iter(self._items)

            def __len__(self):
                return len(self._items)

        calls = _OrderedCallCollector()
        package_name = self._get_package_name(code)
        super_type_name = self._get_super_type_name(type_node)

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

        def _generic_element_type(type_name: str) -> str:
            """Return simple element type from common generic wrappers.

            Examples:
              List<PaymentOrder> -> PaymentOrder
              Stream<Foo.Bar> -> Bar
            """
            t = str(type_name or "").strip()
            if not t:
                return ""
            m = re.match(
                r'^(?:List|Set|Collection|Iterable|Stream|Optional)\s*<\s*([^,>]+)',
                t,
            )
            if not m:
                return ""
            inner = str(m.group(1) or "").strip()
            inner = re.sub(r'<[^>]*>', '', inner)
            inner = inner.split('.')[-1].strip()
            return inner

        def _infer_lambda_param_types():
            """Infer untyped lambda params from receiver variable generic types."""
            src = self._get_method_source(code, method_node)
            if not src:
                return

            # listVar.forEach(item -> ...)
            for recv, prm in re.findall(
                r'\b([A-Za-z_]\w*)\s*\.\s*forEach\s*\(\s*\(?\s*([A-Za-z_]\w*)\s*\)?\s*->',
                src,
                flags=re.MULTILINE,
            ):
                elem = _generic_element_type(var_types.get(recv, ""))
                if elem and prm and prm not in var_types:
                    var_types[prm] = elem

            # listVar.stream().filter(x -> ...), map(x -> ...), forEach(x -> ...)
            for recv, prm in re.findall(
                r'\b([A-Za-z_]\w*)\s*\.\s*stream\s*\(\s*\)\s*(?:\.\s*[A-Za-z_]\w*\s*\([^)]*\)\s*)*\.\s*(?:filter|map|peek|flatMap|forEach)\s*\(\s*\(?\s*([A-Za-z_]\w*)\s*\)?\s*->',
                src,
                flags=re.MULTILINE,
            ):
                elem = _generic_element_type(var_types.get(recv, ""))
                if elem and prm and prm not in var_types:
                    var_types[prm] = elem

        _infer_lambda_param_types()

        _injected_user_fields = self._collect_injected_user_qualified_fields(type_node)
        _discovered_ann = set()
        for _meta in _injected_user_fields.values():
            if isinstance(_meta, dict):
                _discovered_ann.update(_meta.get("annotations", set()) or set())
            else:
                _discovered_ann.update(_meta or set())
        _ann_to_classes = self._get_user_annotation_class_index(_discovered_ann)
        _class_to_methods = self._get_project_class_method_index()
        _iface_to_impls = self._get_project_interface_impl_index()
        _class_to_contract_sigs = self._get_project_class_contract_signature_index()

        # Include for-loop element variables
        # FIX: use _effective_type_name so FQN types are preserved here too
        for _, forstmt in method_node.filter(jt.ForStatement):
            if hasattr(forstmt, "control") and hasattr(forstmt.control, "var"):
                var_decl = forstmt.control.var
                if var_decl:
                    tname = self._effective_type_name(var_decl.type)
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
        self._active_method_owner = _type_class_name

        # Track every method name the AST path emits with a resolved class prefix
        # (e.g. "RequestorValidator.validate") so the regex fallback path below
        # does not re-emit them as bare unqualified calls ("validate", "setMaxPageSize")
        # which the cleaner cannot attribute and which become spurious output rows.
        _ast_qualified_methods: set = set()

        # AST: MethodInvocation nodes
        for _, inv in method_node.filter(jt.MethodInvocation):
            qual = inv.qualifier or ""
            member = inv.member
            if qual and self._is_enum_runtime_accessor(qual, member):
                continue

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
            elif qual == "super":
                # Convert super.method(...) into ParentClass.method(...)
                # so inherited implementations can be resolved downstream.
                if super_type_name:
                    _emit_chain_segments(inv, super_type_name, calls)
                else:
                    calls.add(f"{member}()")
            elif qual == "this":
                # Direct this.method(...) in AST mode
                if _type_class_name:
                    _emit_chain_segments(inv, _type_class_name, calls)
                else:
                    calls.add(f"{member}()")
            elif _is_dynamic(qual):
                calls.add(f"{qual}.{member}()")
            else:
                # Strip "this." prefix so "this.obj" resolves the same as "obj"
                if qual.startswith("this."):
                    qual = qual[5:]

                # Generic DI qualifier rule:
                # For DI fields (@Inject/@Autowired/@Resource/@EJB) that also carry
                # qualifier annotations, emit dependencies to classes carrying
                # the same qualifier annotation.
                _inj_meta = _injected_user_fields.get(qual)
                if _inj_meta:
                    if isinstance(_inj_meta, dict):
                        _matched_ann = set(_inj_meta.get("annotations", set()) or set())
                        _contracts = {
                            str(_c).strip().split('.')[-1]
                            for _c in (_inj_meta.get("contract_types", set()) or set())
                            if str(_c or "").strip()
                        }
                        _contract_sigs = {
                            self._normalize_contract_signature(_c)
                            for _c in (_inj_meta.get("contract_signatures", set()) or set())
                            if str(_c or "").strip()
                        }
                        _use_qualified_lookup = bool(_inj_meta.get("wrapper_contract", False))
                    else:
                        _matched_ann = set(_inj_meta or set())
                        _contracts = set()
                        _contract_sigs = set()
                        _use_qualified_lookup = False

                    # Qualifier-annotation expansion is only for wrapper contract
                    # declarations (e.g. Instance<Validator<T>>). For direct field
                    # declarations (e.g. Class_A variable), resolve via declared type.
                    if _use_qualified_lookup:
                        _allowed_impl_classes = set()
                        for _contract in _contracts:
                            _allowed_impl_classes.update(_iface_to_impls.get(_contract, set()))
                            _allowed_impl_classes.update(_iface_to_impls.get(_contract + "s", set()))
                            if _contract.endswith("s"):
                                _allowed_impl_classes.update(_iface_to_impls.get(_contract[:-1], set()))

                        # Contract-only fallback when qualifier annotation is absent.
                        if not _matched_ann and (_contracts or _contract_sigs):
                            _candidate_classes = set(_allowed_impl_classes)
                            if _contract_sigs:
                                _candidate_classes.update({
                                    _cls_name
                                    for _cls_name in (_class_to_contract_sigs or {}).keys()
                                    if self._class_matches_contract_signatures(
                                        _cls_name,
                                        _contract_sigs,
                                        _class_to_contract_sigs,
                                    )
                                })
                            for _cls in sorted(_candidate_classes):
                                _methods = sorted(_class_to_methods.get(_cls, set()))
                                if not _methods:
                                    calls.add(f"{_cls}.{_cls}()")
                                    _ast_qualified_methods.add(_cls)
                                    continue
                                for _m in _methods:
                                    calls.add(f"{_cls}.{_m}()")
                                    _ast_qualified_methods.add(_m)

                        for _ann in sorted(_matched_ann):
                            for _cls in sorted(_ann_to_classes.get(_ann, set())):
                                if _contracts and (_cls not in _allowed_impl_classes and _cls not in _contracts):
                                    continue
                                if _contract_sigs and not self._class_matches_contract_signatures(
                                    _cls,
                                    _contract_sigs,
                                    _class_to_contract_sigs,
                                ):
                                    continue
                                _methods = sorted(_class_to_methods.get(_cls, set()))
                                if not _methods:
                                    # Keep a fallback edge when no methods were indexed.
                                    calls.add(f"{_cls}.{_cls}()")
                                    _ast_qualified_methods.add(_cls)
                                    continue
                                for _m in _methods:
                                    calls.add(f"{_cls}.{_m}()")
                                    _ast_qualified_methods.add(_m)

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
                    # FIX: var_types may now store a package-qualified FQN such as
                    # 'nl.rabobank.schemas.cs.rom.extendedquerypaymentorder._1.req.SearchOptions'
                    # for a parameter declared as:
                    #   final nl.rabobank.schemas...SearchOptions searchOptions
                    # Emitting the full FQN as the call root produces
                    #   'nl.rabobank.schemas...SearchOptions.isOnlyBatches()'
                    # which _enrich_call_with_path cannot parse (its regex only grabs
                    # the first dot-segment 'nl' as the class name).
                    # We must emit ONLY the simple class name (last FQN segment) so
                    # the downstream enrichment step sees 'SearchOptions.isOnlyBatches()'
                    # and can then resolve it correctly via _resolve_class_path /
                    # _resolve_fqn_path using the import-aware caller-file var_map.
                    if isinstance(resolved_type, str) and '.' in resolved_type and resolved_type[0].islower():
                        resolved_type = resolved_type.split('.')[-1]
                    _emit_chain_segments(inv, resolved_type, calls)
                elif re.fullmatch(r'[a-z_][A-Za-z0-9_]*', str(qual or '')):
                    # Keep unresolved lowercase owner calls (e.g. inherited DI fields)
                    # so downstream import/inheritance-aware resolution can map them.
                    _emit_chain_segments(inv, qual, calls)

        # ---------------------------------------------------------------
        # Handle this.field.method() calls — javalang parses these as a
        # This node with selectors, NOT as a MethodInvocation with a
        # qualifier.  The MethodInvocation loop above never sees them.
        #
        # AST shape for  this.requestorValidator.validate(...) :
        #   This(selectors=[
        #       MemberReference(member="requestorValidator"),
        #       MethodInvocation(member="validate", qualifier=None)
        #   ])
        #
        # We walk every This node in the method body, find the first
        # MemberReference selector (the field name), resolve it to its
        # declared class via autowired_fields / var_types, then emit
        # every subsequent MethodInvocation selector as a qualified call.
        # ---------------------------------------------------------------
        for _, this_node in method_node.filter(jt.This):
            selectors = getattr(this_node, "selectors", None) or []
            if not selectors:
                continue

            # Direct this.method(...) form may be represented as:
            # This(selectors=[MethodInvocation(member="method", ...)])
            # Handle it explicitly so calls like this.getPurgeDate() are emitted.
            first = selectors[0]
            if isinstance(first, jt.MethodInvocation):
                class_name = _type_class_name or getattr(type_node, "name", None)
                if class_name:
                    calls.add(f"{class_name}.{first.member}()")
                    _ast_qualified_methods.add(first.member)
                continue

            # First selector must be a MemberReference — that is the field name
            # e.g. "requestorValidator", "requestDetailsValidator"
            if not isinstance(first, jt.MemberReference):
                continue
            field_name = first.member

            # Resolve field name → declared class name
            resolved_class = (
                autowired_fields.get(field_name)
                or var_types.get(field_name)
                or field_name   # last resort: keep as-is (will be lowercase, cleaner drops it)
            )
            # FIX: same FQN → simple name conversion as in the MethodInvocation loop above.
            # autowired_fields may store a package-qualified FQN for fields declared with
            # fully-qualified types.  Emit only the simple name so _enrich_call_with_path
            # can parse and resolve the call correctly.
            if isinstance(resolved_class, str) and '.' in resolved_class and resolved_class[0].islower():
                resolved_class = resolved_class.split('.')[-1]

            # Walk remaining selectors — emit every MethodInvocation
            for sel in selectors[1:]:
                if isinstance(sel, jt.MethodInvocation):
                    calls.add(f"{resolved_class}.{sel.member}()")
                    _ast_qualified_methods.add(sel.member)
                elif isinstance(sel, jt.MemberReference):
                    # Further field chaining (rare): update resolved class via
                    # method_return_index if available, otherwise skip.
                    pass  # future: chain through method_return_index

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
            ctor_args = getattr(cc, "arguments", []) or []
            has_ctor_args = len(ctor_args) > 0
            if ctor_type in imports_types or wildcard_packages or \
               self.accept_local_new_types or self.accept_same_package:
                if has_ctor_args:
                    calls.add(f"{ctor_type}.{ctor_type}()")
            for arg in ctor_args:
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
                ctor_args = getattr(expr, "arguments", []) or []
                has_ctor_args = len(ctor_args) > 0
                if ctor_type and has_ctor_args:
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
        this_call_pat = re.compile(r'\bthis\s*\.\s*([A-Za-z_]\w*)\s*\(')
        # Class-literal proxy call pattern (dynamic capture):
        # anyReceiver.anyProxyMethod(TargetClass.class).anyMethod(...)
        # -> TargetClass.anyMethod()
        # Group 3 captures TargetClass, group 4 captures the invoked method name.
        business_object_call_pat = re.compile(
            r'\b([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\(\s*([A-Za-z_]\w*)\s*\.class\s*\)\s*\.\s*([A-Za-z_]\w*)\s*\(',
            re.MULTILINE,
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

            # Add explicit owner for class-literal proxy calls where the target
            # class is provided as AnyProxyMethod(TargetClass.class).
            for _bm in business_object_call_pat.finditer(src_clean):
                _target_class = _bm.group(3)
                _target_method = _bm.group(4)
                if _target_class and _target_method:
                    calls.add(f"{_target_class}.{_target_method}()")
                    _ast_qualified_methods.add(_target_method)

            # Deterministic fallback for AST blind spots: emit direct this.method(...)
            # from source text of this method (covers cases like this.getPurgeDate()
            # nested in constructor arguments or parser-shape differences).
            if _type_class_name:
                for _tm in this_call_pat.finditer(src_clean):
                    _mname = _tm.group(1)
                    if _mname:
                        calls.add(f"{_type_class_name}.{_mname}()")
                        _ast_qualified_methods.add(_mname)

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
                        # Known variable — replace with its resolved type name.
                        # FIX: if the stored type is a package-qualified FQN (first
                        # char lowercase, e.g. 'nl.path.SearchOptions'), use only
                        # the simple class name (last segment) for the call string.
                        # The downstream enrichment (_enrich_call_with_path) reads
                        # _build_var_map from the source file which does the FQN-to-
                        # path mapping; emitting the full FQN here would break the
                        # regex that extracts the class token from the call string.
                        if isinstance(resolved, str) and '.' in resolved and resolved[0].islower():
                            resolved = resolved.split('.')[-1]
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
                            # FIX: same FQN → simple name conversion for field types
                            if isinstance(field_type, str) and '.' in field_type and field_type[0].islower():
                                field_type = field_type.split('.')[-1]
                            chain = field_type + chain[len(leading):]
                            calls.add(chain)
                        else:
                            # Preserve unknown lowercase roots for downstream resolver.
                            calls.add(chain)
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
                # Do not emit dynamic terminals as unqualified calls.
                # Chained calls must remain chain-qualified only.
                continue

        # Some javalang builds expose super calls as a separate node type.
        if super_type_name and hasattr(jt, "SuperMethodInvocation"):
            for _, sinv in method_node.filter(jt.SuperMethodInvocation):
                member = getattr(sinv, "member", None)
                if not member:
                    continue
                calls.add(f"{super_type_name}.{member}()")
                _ast_qualified_methods.add(member)

        # ==============================================================
        # NEW PATTERNS — added additively; no existing logic modified.
        # Each section is independently guarded and appends to `calls`.
        # ==============================================================

        # ------------------------------------------------------------------
        # Pattern: CatchClause Traversal
        # Walk every catch block so method calls on the exception variable
        # (e.g. catch(Exception e) { handle(e); }) are not missed.
        # javalang node: CatchClause
        # ------------------------------------------------------------------
        for _, catch_clause in method_node.filter(jt.CatchClause):
            catch_param = getattr(catch_clause, "parameter", None)
            catch_block = getattr(catch_clause, "block", None)
            if catch_block is None:
                continue
            # Build a tiny var_map for the catch parameter so qualified calls
            # like e.getMessage() are resolved to the declared exception type.
            catch_var_types = dict(var_types)
            if catch_param is not None:
                param_name = getattr(catch_param, "name", None)
                # CatchClause parameter may have multiple types (multi-catch)
                param_types_list = getattr(catch_param, "types", None)
                if param_types_list:
                    # Use the first type for resolution (all share the same var)
                    first_type = param_types_list[0] if param_types_list else None
                    if first_type:
                        type_name = self._simple_type_name(first_type) if hasattr(first_type, "name") else str(first_type)
                        if param_name and type_name:
                            catch_var_types[param_name] = type_name
                else:
                    param_type = getattr(catch_param, "type", None)
                    type_name = self._simple_type_name(param_type) if param_type is not None else None
                    if param_name and type_name:
                        catch_var_types[param_name] = type_name
            # Recursively collect invocations inside the catch block
            for stmt in (catch_block if isinstance(catch_block, (list, tuple)) else []):
                calls |= self._collect_invocations_in_expression(
                    stmt,
                    var_types=catch_var_types,
                    imports_types=imports_types,
                    autowired_fields=autowired_fields,
                    wildcard_packages=wildcard_packages,
                    locals_from_new=locals_from_new,
                    params_set=params_set,
                    package_name=package_name,
                )

        # ------------------------------------------------------------------
        # Pattern: Multi-Catch Handling
        # Capture each exception type in a multi-catch clause.
        # javalang node: CatchClause.parameter.types (multiple)
        # e.g. catch(IOException | SQLException e) { ... }
        # ------------------------------------------------------------------
        for _, catch_clause in method_node.filter(jt.CatchClause):
            catch_param = getattr(catch_clause, "parameter", None)
            if catch_param is None:
                continue
            param_types_list = getattr(catch_param, "types", None)
            if not param_types_list or len(param_types_list) < 2:
                continue
            param_name = getattr(catch_param, "name", None)
            # Register each exception type so method calls via the variable resolve
            for exc_type in param_types_list:
                exc_type_name = self._simple_type_name(exc_type) if hasattr(exc_type, "name") else str(exc_type)
                if param_name and exc_type_name:
                    # Record mapping for potential downstream resolution
                    var_types.setdefault(param_name, exc_type_name)

        # ------------------------------------------------------------------
        # Pattern: Try-With-Resources Traversal
        # Walk resource declarations and the try body.
        # javalang node: TryStatement.resources
        # e.g. try (Connection c = getConnection()) { ... }
        # ------------------------------------------------------------------
        for _, try_stmt in method_node.filter(jt.TryStatement):
            resources = getattr(try_stmt, "resources", None) or []
            for res in resources:
                # TryResource: type + name + value (initializer)
                res_type = getattr(res, "type", None)
                res_name = getattr(res, "name", None)
                res_value = getattr(res, "value", None)
                if res_type is not None and res_name:
                    tname = self._effective_type_name(res_type)
                    if tname:
                        var_types.setdefault(res_name, tname)
                # Collect any method call inside the resource initializer
                if res_value is not None:
                    calls |= self._collect_invocations_in_expression(
                        res_value,
                        var_types=var_types,
                        imports_types=imports_types,
                        autowired_fields=autowired_fields,
                        wildcard_packages=wildcard_packages,
                        locals_from_new=locals_from_new,
                        params_set=params_set,
                        package_name=package_name,
                    )

        # ------------------------------------------------------------------
        # Pattern: Explicit this() Constructor Delegation
        # Detect constructor-to-constructor calls within the same class.
        # javalang node: ExplicitConstructorInvocation (qualifier "this")
        # e.g. this(id);
        # ------------------------------------------------------------------
        for _, eci in method_node.filter(jt.ExplicitConstructorInvocation):
            qualifier = getattr(eci, "qualifier", None) or ""
            if str(qualifier).lower() == "this" or qualifier == "":
                # this(...) delegation — emit as same-class constructor call
                class_name = getattr(type_node, "name", None)
                if class_name:
                    calls.add(f"{class_name}.{class_name}()")
                    for arg in getattr(eci, "arguments", []) or []:
                        calls |= self._collect_invocations_in_expression(
                            arg,
                            var_types=var_types,
                            imports_types=imports_types,
                            autowired_fields=autowired_fields,
                            wildcard_packages=wildcard_packages,
                            locals_from_new=locals_from_new,
                            params_set=params_set,
                            package_name=package_name,
                        )

        # ------------------------------------------------------------------
        # Pattern: Explicit super() Constructor Delegation
        # Detect constructor-to-parent-constructor lineage.
        # javalang node: ExplicitConstructorInvocation / SuperConstructorInvocation
        # e.g. super(id);
        # ------------------------------------------------------------------
        # Handle via SuperConstructorInvocation when available
        if hasattr(jt, "SuperConstructorInvocation"):
            for _, sci in method_node.filter(jt.SuperConstructorInvocation):
                if super_type_name:
                    calls.add(f"{super_type_name}.{super_type_name}()")
                for arg in getattr(sci, "arguments", []) or []:
                    calls |= self._collect_invocations_in_expression(
                        arg,
                        var_types=var_types,
                        imports_types=imports_types,
                        autowired_fields=autowired_fields,
                        wildcard_packages=wildcard_packages,
                        locals_from_new=locals_from_new,
                        params_set=params_set,
                        package_name=package_name,
                    )
        # Also handle via ExplicitConstructorInvocation with "super" qualifier
        for _, eci in method_node.filter(jt.ExplicitConstructorInvocation):
            qualifier = getattr(eci, "qualifier", None) or ""
            if str(qualifier).lower() == "super":
                if super_type_name:
                    calls.add(f"{super_type_name}.{super_type_name}()")
                for arg in getattr(eci, "arguments", []) or []:
                    calls |= self._collect_invocations_in_expression(
                        arg,
                        var_types=var_types,
                        imports_types=imports_types,
                        autowired_fields=autowired_fields,
                        wildcard_packages=wildcard_packages,
                        locals_from_new=locals_from_new,
                        params_set=params_set,
                        package_name=package_name,
                    )

        # ------------------------------------------------------------------
        # Pattern: @Override Metadata
        # Record that a method overrides a parent/interface method.
        # javalang node: MethodDeclaration.annotations
        # e.g. @Override void save()
        # This pattern does not add new calls but records the override
        # relationship so downstream resolution can prefer the child's impl.
        # ------------------------------------------------------------------
        _is_override_method = False
        for ann in getattr(method_node, "annotations", []) or []:
            ann_name = getattr(ann, "name", "") or ""
            if ann_name == "Override":
                _is_override_method = True
                break
        if _is_override_method and super_type_name:
            method_nm = getattr(method_node, "name", None)
            if method_nm:
                # Emit a resolution hint: this method overrides the parent's version
                calls.add(f"{super_type_name}.{method_nm}()")
                _ast_qualified_methods.add(method_nm)

        # ------------------------------------------------------------------
        # Pattern: Lambda Block-Body Recursion
        # Walk calls made inside a block-bodied lambda.
        # javalang node: LambdaExpression.body (BlockStatement list)
        # e.g. x -> { validate(x); save(x); }
        # ------------------------------------------------------------------
        for _, lambda_expr in method_node.filter(jt.LambdaExpression):
            lambda_body = getattr(lambda_expr, "body", None)
            if lambda_body is None:
                continue
            # Block body: list of statements
            if isinstance(lambda_body, (list, tuple)):
                for stmt in lambda_body:
                    calls |= self._collect_invocations_in_expression(
                        stmt,
                        var_types=var_types,
                        imports_types=imports_types,
                        autowired_fields=autowired_fields,
                        wildcard_packages=wildcard_packages,
                        locals_from_new=locals_from_new,
                        params_set=params_set,
                        package_name=package_name,
                    )

        # ------------------------------------------------------------------
        # Pattern: Lambda Expression-Body Recursion
        # Walk calls made inside an expression-bodied lambda.
        # javalang node: LambdaExpression.body (Expression)
        # e.g. x -> process(x)
        # ------------------------------------------------------------------
        for _, lambda_expr in method_node.filter(jt.LambdaExpression):
            lambda_body = getattr(lambda_expr, "body", None)
            if lambda_body is None:
                continue
            # Expression body: a single node (not a list)
            if not isinstance(lambda_body, (list, tuple)):
                calls |= self._collect_invocations_in_expression(
                    lambda_body,
                    var_types=var_types,
                    imports_types=imports_types,
                    autowired_fields=autowired_fields,
                    wildcard_packages=wildcard_packages,
                    locals_from_new=locals_from_new,
                    params_set=params_set,
                    package_name=package_name,
                )

        # ------------------------------------------------------------------
        # Pattern: Method References  (Type::method)
        # Dedicated handling for :: — currently a blind spot.
        # javalang node: MethodReference
        # e.g. User::getName
        # ------------------------------------------------------------------
        for _, mref in method_node.filter(jt.MethodReference):
            mref_member = getattr(mref, "member", None)
            mref_qualifier = getattr(mref, "qualifier", None)
            if mref_member and mref_qualifier:
                # qualifier may be a type name string or a ReferenceType node
                if hasattr(mref_qualifier, "name"):
                    qual_str = self._simple_type_name(mref_qualifier)
                else:
                    qual_str = str(mref_qualifier) if mref_qualifier else ""
                qual_str = qual_str.strip() if qual_str else ""
                if qual_str and qual_str[0].isupper():
                    # Static or instance method ref on a class: User::getName
                    calls.add(f"{qual_str}.{mref_member}()")
                    _ast_qualified_methods.add(mref_member)
                elif qual_str:
                    # Lowercase qualifier — try to resolve via var_types
                    resolved = var_types.get(qual_str) or autowired_fields.get(qual_str) or qual_str
                    if isinstance(resolved, str) and "." in resolved and resolved[0].islower():
                        resolved = resolved.split(".")[-1]
                    if resolved and resolved[0].isupper():
                        calls.add(f"{resolved}.{mref_member}()")
                        _ast_qualified_methods.add(mref_member)

        # ------------------------------------------------------------------
        # Pattern: Constructor Method References  (Type::new)
        # Treat ::new as a constructor reference.
        # javalang node: MethodReference with member == "new"
        # e.g. Order::new
        # ------------------------------------------------------------------
        for _, mref in method_node.filter(jt.MethodReference):
            mref_member = getattr(mref, "member", None)
            mref_qualifier = getattr(mref, "qualifier", None)
            if mref_member == "new" and mref_qualifier:
                if hasattr(mref_qualifier, "name"):
                    qual_str = self._simple_type_name(mref_qualifier)
                else:
                    qual_str = str(mref_qualifier) if mref_qualifier else ""
                qual_str = (qual_str or "").strip()
                if qual_str and qual_str[0].isupper():
                    # Constructor reference: emit as Type.Type()
                    calls.add(f"{qual_str}.{qual_str}()")

        # ------------------------------------------------------------------
        # Pattern: Anonymous-Class Body Recursion
        # Walk anonymous class bodies for method calls.
        # javalang node: ClassCreator.body
        # e.g. new Runnable() { public void run(){ save(); } }
        # ------------------------------------------------------------------
        for _, cc in method_node.filter(jt.ClassCreator):
            anon_body = getattr(cc, "body", None)
            if not anon_body:
                continue
            # body is a list of member declarations; iterate methods inside
            for anon_member in anon_body:
                if isinstance(anon_member, jt.MethodDeclaration):
                    anon_stmts = getattr(anon_member, "body", None) or []
                    for stmt in anon_stmts:
                        calls |= self._collect_invocations_in_expression(
                            stmt,
                            var_types=var_types,
                            imports_types=imports_types,
                            autowired_fields=autowired_fields,
                            wildcard_packages=wildcard_packages,
                            locals_from_new=locals_from_new,
                            params_set=params_set,
                            package_name=package_name,
                        )

        # ------------------------------------------------------------------
        # Pattern: Double-Brace Initialization
        # Traverse anonymous/initializer double-brace bodies.
        # javalang node: ClassCreator.body -> member initializer blocks
        # e.g. new X() {{ init(); }}
        # ------------------------------------------------------------------
        for _, cc in method_node.filter(jt.ClassCreator):
            anon_body = getattr(cc, "body", None)
            if not anon_body:
                continue
            for anon_member in anon_body:
                # Initializer blocks inside anonymous class bodies
                member_stmts = getattr(anon_member, "statements", None) or []
                if not isinstance(anon_member, jt.MethodDeclaration) and member_stmts:
                    for stmt in member_stmts:
                        calls |= self._collect_invocations_in_expression(
                            stmt,
                            var_types=var_types,
                            imports_types=imports_types,
                            autowired_fields=autowired_fields,
                            wildcard_packages=wildcard_packages,
                            locals_from_new=locals_from_new,
                            params_set=params_set,
                            package_name=package_name,
                        )

        # ------------------------------------------------------------------
        # Pattern: Array Initializer Traversal
        # Walk object constructions inside array initializers.
        # javalang node: ArrayInitializer.initializers
        # e.g. { new A(), new B() }
        # ------------------------------------------------------------------
        for _, arr_init in method_node.filter(jt.ArrayInitializer):
            for init_item in getattr(arr_init, "initializers", []) or []:
                calls |= self._collect_invocations_in_expression(
                    init_item,
                    var_types=var_types,
                    imports_types=imports_types,
                    autowired_fields=autowired_fields,
                    wildcard_packages=wildcard_packages,
                    locals_from_new=locals_from_new,
                    params_set=params_set,
                    package_name=package_name,
                )

        # ------------------------------------------------------------------
        # Pattern: @Resource Injection
        # Expand DI annotation handling beyond @Autowired/@Inject.
        # javalang node: FieldDeclaration.annotations (@Resource)
        # e.g. @Resource Service service;
        # This pattern supplements _collect_autowired_fields which already
        # collects ALL field declarations. Here we additionally ensure that
        # fields annotated with @Resource are tracked in var_types so that
        # subsequent method calls on them are resolved correctly.
        # ------------------------------------------------------------------
        for _, fd in type_node.filter(jt.FieldDeclaration):
            _has_resource_ann = False
            for ann in getattr(fd, "annotations", []) or []:
                ann_name = getattr(ann, "name", "") or ""
                if ann_name == "Resource":
                    _has_resource_ann = True
                    break
            if _has_resource_ann:
                tname = self._effective_type_name(fd.type)
                for decl in getattr(fd, "declarators", []) or []:
                    vname = getattr(decl, "name", None)
                    if vname and tname:
                        var_types.setdefault(vname, tname)
                        autowired_fields.setdefault(vname, tname)

        # ------------------------------------------------------------------
        # Pattern: instanceof Pattern Variable
        # Capture the pattern-variable's type for subsequent call resolution.
        # javalang node: InstanceOfExpression (pattern variable — Java 16+)
        # For Java 8 this is a no-op (pattern variables not supported), but
        # the guard keeps the code safe across adapter versions.
        # e.g. if (x instanceof Service s) s.run();
        # ------------------------------------------------------------------
        _instance_of_node_name = "InstanceOfExpression"
        if hasattr(jt, _instance_of_node_name):
            _io_cls = getattr(jt, _instance_of_node_name)
            for _, io_expr in method_node.filter(_io_cls):
                pattern_var = getattr(io_expr, "pattern_variable", None)
                io_type = getattr(io_expr, "type", None)
                if pattern_var and io_type:
                    pv_name = getattr(pattern_var, "name", None)
                    pv_type = self._effective_type_name(io_type)
                    if pv_name and pv_type:
                        var_types.setdefault(pv_name, pv_type)

        # ------------------------------------------------------------------
        # Pattern: Generic Type-Witness Calls
        # Dedicated node-level handling for type-witness method invocations.
        # javalang node: MethodInvocation.type_arguments
        # e.g. repo.<User>find(id)
        # This is handled through the existing MethodInvocation loop above,
        # but here we emit an additional pass that explicitly checks for
        # non-empty type_arguments to avoid relying only on strip_generics
        # text cleanup in the chained_pat regex path.
        # ------------------------------------------------------------------
        for _, inv in method_node.filter(jt.MethodInvocation):
            type_args = getattr(inv, "type_arguments", None)
            if not type_args:
                continue
            qual = inv.qualifier or ""
            member = inv.member
            if qual and not _is_dynamic(qual):
                if qual.startswith("this."):
                    qual = qual[5:]
                qual = self._normalize_qualifier(qual, var_types)
                if self._keep_qualified_call(
                    qual, var_types, imports_types, autowired_fields,
                    wildcard_packages, locals_from_new, params_set, package_name,
                ):
                    resolved_type = var_types.get(qual) or autowired_fields.get(qual) or qual
                    if isinstance(resolved_type, str) and "." in resolved_type and resolved_type[0].islower():
                        resolved_type = resolved_type.split(".")[-1]
                    calls.add(f"{resolved_type}.{member}()")
                    _ast_qualified_methods.add(member)
            elif not qual:
                if self._keep_unqualified_call(member, set(), set()):
                    calls.add(f"{member}()")

        # ------------------------------------------------------------------
        # Pattern: Array-Indexed Receiver
        # Resolve a method call whose receiver is an array-index expression.
        # javalang node: ArraySelector / MemberReference on indexed target
        # e.g. services[i].process()
        # The existing MethodInvocation loop already captures these when the
        # qualifier contains bracket syntax as a dynamic qualifier string.
        # Here we additionally handle ArraySelector selectors on MemberReference
        # so that the method after the index dereference is emitted.
        # ------------------------------------------------------------------
        for _, inv in method_node.filter(jt.MethodInvocation):
            qual = inv.qualifier or ""
            member = inv.member
            if not qual:
                continue
            # Dynamic qualifier containing array access (brackets in the string)
            if "[" in qual or "]" in qual:
                # Strip array index expressions to get the base variable
                base_var = re.sub(r'\[.*?\]', '', qual).strip().rstrip(".")
                if base_var:
                    resolved = var_types.get(base_var) or autowired_fields.get(base_var) or base_var
                    if isinstance(resolved, str) and "." in resolved and resolved[0].islower():
                        resolved = resolved.split(".")[-1]
                    if resolved and resolved[0].isupper():
                        calls.add(f"{resolved}.{member}()")
                        _ast_qualified_methods.add(member)

        # ------------------------------------------------------------------
        # Pattern: Cast-Wrapped Receiver
        # Use the cast's target type to resolve the receiver's class.
        # javalang node: Cast (type, expression)
        # e.g. ((Service)obj).process()
        # Collect method calls on cast expressions discovered in the AST.
        # ------------------------------------------------------------------
        for _, cast_expr in method_node.filter(jt.Cast):
            cast_type = getattr(cast_expr, "type", None)
            cast_sub = getattr(cast_expr, "expression", None)
            if cast_type is None or cast_sub is None:
                continue
            cast_type_name = self._simple_type_name(cast_type)
            if not cast_type_name or not cast_type_name[0].isupper():
                continue
            # Record cast type in var_types if sub-expression is a MemberReference
            if isinstance(cast_sub, jt.MemberReference):
                var_name = getattr(cast_sub, "member", None)
                if var_name:
                    var_types.setdefault(var_name, cast_type_name)
            # Collect any nested invocations inside the cast expression
            calls |= self._collect_invocations_in_expression(
                cast_sub,
                var_types=var_types,
                imports_types=imports_types,
                autowired_fields=autowired_fields,
                wildcard_packages=wildcard_packages,
                locals_from_new=locals_from_new,
                params_set=params_set,
                package_name=package_name,
            )

        # ------------------------------------------------------------------
        # Pattern: Static/Instance Initializer Traversal
        # Treat initializer blocks as pseudo-method contexts and collect calls.
        # javalang node: ClassDeclaration.body -> initializer block members
        # e.g. static { initialize(); }
        # Note: This is a class-level pattern; we collect calls from ALL
        # initializer blocks declared in the enclosing type_node when the
        # current method_node is the constructor (common entry point).
        # ------------------------------------------------------------------
        _is_constructor = isinstance(method_node, jt.ConstructorDeclaration)
        if _is_constructor:
            for init_member in getattr(type_node, "body", []) or []:
                # javalang represents initializer blocks as BlockStatement lists
                # attached directly to the class body (not as methods/fields).
                member_stmts = None
                if hasattr(init_member, "statements") and not isinstance(
                    init_member, (jt.MethodDeclaration, jt.ConstructorDeclaration, jt.FieldDeclaration)
                ):
                    member_stmts = getattr(init_member, "statements", None)
                if member_stmts:
                    for stmt in member_stmts:
                        calls |= self._collect_invocations_in_expression(
                            stmt,
                            var_types=var_types,
                            imports_types=imports_types,
                            autowired_fields=autowired_fields,
                            wildcard_packages=wildcard_packages,
                            locals_from_new=locals_from_new,
                            params_set=params_set,
                            package_name=package_name,
                        )

        # ------------------------------------------------------------------
        # Pattern: Inheritance Resolution
        # Build parent-class relationships and use them when resolving
        # inherited method calls.
        # javalang node: ClassDeclaration.extends
        # e.g. class Child extends Parent
        # The super_type_name variable (already set above) handles existing
        # super.method() calls. Here we additionally register the parent class
        # in var_types under the keyword "super" so downstream resolution
        # can match super-rooted calls.
        # ------------------------------------------------------------------
        if super_type_name:
            var_types.setdefault("super", super_type_name)

        # ------------------------------------------------------------------
        # Pattern: Interface -> Implementation Map
        # Resolve interface-typed variables to concrete implementations.
        # javalang node: ClassDeclaration.implements
        # e.g. class Impl implements Service
        # Record interface names implemented by the current class so that
        # calls emitted with the interface type can be resolved to the impl.
        # ------------------------------------------------------------------
        _implemented_interfaces = []
        for iface_ref in getattr(type_node, "implements", []) or []:
            iface_name = self._simple_type_name(iface_ref)
            if iface_name:
                _implemented_interfaces.append(iface_name)
        # For each implemented interface, if a local var/field has that interface
        # type, add the current class as an alternative resolution target.
        _current_class_name = getattr(type_node, "name", None)
        if _current_class_name:
            for iface_name in _implemented_interfaces:
                for var_name, var_type in list(var_types.items()):
                    if var_type == iface_name:
                        # Don't overwrite; provide the impl type as a fallback
                        var_types.setdefault(var_name + "__impl__", _current_class_name)

        # ------------------------------------------------------------------
        # Pattern: Local Class Traversal
        # Walk method-scoped (local) classes and their methods.
        # javalang node: LocalClassDeclaration (if available in this javalang)
        # e.g. class Local { void run(){ save(); } }
        # ------------------------------------------------------------------
        _local_class_node_name = "LocalClassDeclaration"
        if hasattr(jt, _local_class_node_name):
            _lc_cls = getattr(jt, _local_class_node_name)
            for _, local_cls in method_node.filter(_lc_cls):
                for local_member in getattr(local_cls, "body", []) or []:
                    if isinstance(local_member, jt.MethodDeclaration):
                        local_stmts = getattr(local_member, "body", None) or []
                        for stmt in local_stmts:
                            calls |= self._collect_invocations_in_expression(
                                stmt,
                                var_types=var_types,
                                imports_types=imports_types,
                                autowired_fields=autowired_fields,
                                wildcard_packages=wildcard_packages,
                                locals_from_new=locals_from_new,
                                params_set=params_set,
                                package_name=package_name,
                            )

        # ------------------------------------------------------------------
        # Pattern: Inner-Class Construction
        # Handle inner-class instantiation via an outer object.
        # javalang node: ClassCreator (qualifier = outer instance)
        # e.g. outer.new Inner()
        # InnerClassCreator node in javalang represents "outer.new Inner()"
        # ------------------------------------------------------------------
        if hasattr(jt, "InnerClassCreator"):
            for _, icc in method_node.filter(jt.InnerClassCreator):
                inner_type = getattr(icc, "type", None)
                if inner_type is None:
                    continue
                inner_type_name = self._simple_type_name(inner_type)
                if not inner_type_name:
                    continue
                ctor_args = getattr(icc, "arguments", []) or []
                if ctor_args:
                    calls.add(f"{inner_type_name}.{inner_type_name}()")
                for arg in ctor_args:
                    calls |= self._collect_invocations_in_expression(
                        arg,
                        var_types=var_types,
                        imports_types=imports_types,
                        autowired_fields=autowired_fields,
                        wildcard_packages=wildcard_packages,
                        locals_from_new=locals_from_new,
                        params_set=params_set,
                        package_name=package_name,
                    )
        # Also detect inner-class construction encoded as ClassCreator with a
        # non-None qualifier (some javalang versions use this representation)
        for _, cc in method_node.filter(jt.ClassCreator):
            cc_qualifier = getattr(cc, "qualifier", None)
            if cc_qualifier is None:
                continue
            # A non-None qualifier means this is an outer.new Inner() form
            ctor_type = self._simple_type_name(getattr(cc, "type", None))
            if not ctor_type:
                continue
            ctor_args = getattr(cc, "arguments", []) or []
            if ctor_args:
                calls.add(f"{ctor_type}.{ctor_type}()")
            for arg in ctor_args:
                calls |= self._collect_invocations_in_expression(
                    arg,
                    var_types=var_types,
                    imports_types=imports_types,
                    autowired_fields=autowired_fields,
                    wildcard_packages=wildcard_packages,
                    locals_from_new=locals_from_new,
                    params_set=params_set,
                    package_name=package_name,
                )

        # ==============================================================
        # END OF NEW PATTERNS
        # ==============================================================

        # Lifecycle dependency rule (generic):
        # If a class defines @PostConstruct methods, emit them as synthetic
        # dependencies for other methods in the same class so lifecycle
        # initializers are visible in lineage.
        _pc_methods = self._get_post_construct_methods(type_node)
        _cur_method_name = getattr(method_node, "name", None)
        _owner_class_name = getattr(type_node, "name", None)
        # Only attach lifecycle init calls when this method already has at
        # least one concrete dependency. This avoids false children like
        # getValidators() -> init() for no-call methods.
        if _pc_methods and _owner_class_name and _cur_method_name not in _pc_methods and len(calls) > 0 and not _is_override_method:
            for _pc_m in sorted(_pc_methods):
                calls.add(f"{_owner_class_name}.{_pc_m}()")
                _ast_qualified_methods.add(_pc_m)

        return [c for c in calls if not self._is_enum_runtime_accessor_call(c)]


    def is_system_call(self, call: str) -> bool:
        if not isinstance(call, str):
            return False
        call = call.strip()
        if not call:
            return False

        call_ng = re.sub(r'\s*&amp;lt;[^&amp;gt]+&amp;gt;\s*', '', call)
        call_ng = re.sub(r'\s*<[^>]+>\s*', '', call_ng)
        lc = call_ng.lower()

        owner_member_match = re.match(r'^\s*(.*?)\.([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*$', call_ng)
        owner_raw = (owner_member_match.group(1).strip() if owner_member_match else "")
        member_raw = (owner_member_match.group(2).strip().lower() if owner_member_match else "")
        owner_norm = owner_raw.replace('\\', '.').replace('/', '.').strip('.').lower()
        owner_simple = owner_norm.split('.')[-1] if owner_norm else ""

        ignore_owner_prefixes = [
            p.lower()
            for p in self.details.get("IGNORE_CALL_OWNER_PREFIXES", [
                "java.", "javax.", "jakarta.", "sun.", "com.sun.", "jdk.", "org.slf4j.",
            ])
            if isinstance(p, str) and p.strip()
        ]
        if owner_norm and any(owner_norm.startswith(prefix) for prefix in ignore_owner_prefixes):
            return True

        if self.details.get("suppress_enum_runtime_methods", True):
            if member_raw in {"name", "ordinal"} and owner_simple == "enum":
                return True
            if self._is_enum_runtime_accessor_call(call_ng):
                return True

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
