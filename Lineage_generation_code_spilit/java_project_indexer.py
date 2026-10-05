# =============================================================================
# FILE: java_project_indexer.py
# PURPOSE: Project-Level Index Builders for Java 8 codebases
#
# CONTAINS:
#   class ProjectIndexerMixin  - mixin providing:
#     _normalize_ps_ref()
#     _get_java_file_indexes()
#     _get_super_type_name()
#     _get_post_construct_methods()
#     _get_user_annotation_class_index()
#     _get_project_class_method_index()
#     _get_project_interface_impl_index()
#     _normalize_contract_signature()
#     _contract_signature_base()
#     _split_top_level_commas()
#     _get_project_class_contract_signature_index()
#     _class_matches_contract_signatures()
#     _get_package_name()
#     build_object_class_map()
#     build_method_return_index()
#     find_type_to_file_map()
#
# NOTE:
#   _is_same_package_type() is defined in CallResolverMixin (java_call_resolver.py)
#   and is resolved via MRO on the assembled JavaAdapter — not duplicated here.
#
# WHEN TO EDIT THIS FILE:
#   - Changes to whole-project scanning (class index, interface impl index)
#   - Changes to contract signature resolution
#   - Changes to object-class map or method return index builders
#   - Changes to file-to-type mapping
#
# DEPENDS ON:
#   config.py                 (compiled regex patterns via self._rx)
#   utils.py                  (utility helpers)
#   language_adapter_base.py  (LanguageAdapter base class)
#   java_ast_parser.py        (AstParserMixin — parse_ast, get_declared_types, etc.)
#   java_call_resolver.py     (CallResolverMixin — _is_same_package_type)
#
# USED BY:
#   java_adapter.py           (assembled into JavaAdapter via multiple inheritance)
# =============================================================================

import os
import re
import html
import javalang
import javalang.tree as jt
from pathlib import Path

from config import (
    re_value_dollar, re_value_spel_dollar, re_field_decl,
    re_configuration_properties, re_property_source,
    re_message_key, re_named_query_decl,
    re_any_method_first_string_arg, _re_throw_new,
    _re_unqualified_method_decl, re_method_decl,
)
from utils import strip_generics, _strip_source_comments


class ProjectIndexerMixin:
    """
    Mixin: project-level index builders for Java 8 codebases.
    Provides whole-project scans: class/interface/contract indexes,
    object-class maps, method return indexes, and file-to-type maps.
    All methods use self and are designed for multiple inheritance
    into JavaAdapter.
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

    def _get_java_file_indexes(self, java_folder: Path):
        """Build and cache Java file indexes to avoid repeated rglob scans."""
        folder_key = str(Path(java_folder).resolve())
        cache = getattr(self, "_java_file_index_cache", None)
        if isinstance(cache, dict) and cache.get("folder") == folder_key:
            return cache

        all_java_files = list(Path(java_folder).rglob("*.java"))
        stem_to_paths = {}
        rel_no_ext_to_path = {}

        for p in all_java_files:
            rp = p.resolve()
            stem_l = rp.stem.lower()
            stem_to_paths.setdefault(stem_l, []).append(rp)

            try:
                rel_no_ext = str(rp.relative_to(java_folder)).replace("\\", "/")
                if rel_no_ext.lower().endswith(".java"):
                    rel_no_ext = rel_no_ext[:-5]
                rel_no_ext_to_path.setdefault(rel_no_ext.lower(), rp)
            except Exception:
                pass

        cache = {
            "folder": folder_key,
            "all_java_files": all_java_files,
            "stem_to_paths": stem_to_paths,
            "rel_no_ext_to_path": rel_no_ext_to_path,
        }
        self._java_file_index_cache = cache
        return cache


    def _get_super_type_name(self, type_node) -> str:
        """Return the simple superclass name for the current type, if any."""
        try:
            parent = getattr(type_node, "extends", None)
            if parent is None:
                return None
            parent_name = self._simple_type_name(parent)
            if parent_name:
                return parent_name
            raw = getattr(parent, "name", None) or str(parent)
            if isinstance(raw, str) and raw.strip():
                return raw.strip().split('.')[-1]
        except Exception:
            pass
        return None

    def _get_post_construct_methods(self, type_node) -> set:
        """Return method names in the class that are annotated with @PostConstruct."""
        out = set()
        for member in getattr(type_node, "body", []) or []:
            if not isinstance(member, jt.MethodDeclaration):
                continue
            ann_list = getattr(member, "annotations", []) or []
            for ann in ann_list:
                ann_name = str(getattr(ann, "name", "") or "").strip()
                if ann_name in {"PostConstruct", "javax.annotation.PostConstruct", "jakarta.annotation.PostConstruct"}:
                    mname = getattr(member, "name", None)
                    if mname:
                        out.add(mname)
                    break
        return out

    def _get_user_annotation_class_index(self, ann_names: set) -> dict:
        """Map annotation simple name -> set(class simple names) from full project scan."""
        if not ann_names:
            return {}

        project_root = self.details.get("PROJECT_PATH") or self.details.get("SERVICE_PROJECT_PATH") or os.getcwd()
        root_abs = os.path.abspath(str(project_root))
        cache = getattr(self, "_user_annotation_class_index_cache", None)
        if not (isinstance(cache, dict) and cache.get("key") == root_abs):
            # Build once per project root: annotation simple name -> class names.
            # Returning subsets from this cache avoids repeated full-project scans.
            #
            # IMPORTANT:
            # Qualifier annotations used by @Inject fields are often placed on
            # producer methods/fields (not only directly above class declarations).
            # To support that CDI style, index class names when the annotation
            # appears anywhere in the class AST subtree.
            index_all = {}
            class_decl_re = re.compile(r'\bclass\s+([A-Za-z_]\w*)\b')
            ann_re = re.compile(r'@([A-Za-z_][\w\.]*)')
            wanted_lc = {str(a).strip().lower() for a in ann_names if str(a).strip()}

            for root, _, files in os.walk(project_root):
                for fn in files:
                    if not fn.endswith(self.file_extension()):
                        continue
                    fpath = os.path.join(root, fn)
                    try:
                        if fpath in self._file_content_cache:
                            src = self._file_content_cache[fpath]
                        else:
                            with open(fpath, "r", encoding="utf-8") as fh:
                                src = fh.read()
                            self._file_content_cache[fpath] = src
                    except Exception:
                        continue

                    ast_tree = None
                    try:
                        ast_tree = self.parse_ast(src)
                    except Exception:
                        ast_tree = None

                    if ast_tree is not None:
                        for _, cls in ast_tree.filter(jt.ClassDeclaration):
                            cls_name = getattr(cls, "name", None)
                            if not cls_name:
                                continue

                            ann_hits = set()

                            # Class-level annotations
                            for a in (getattr(cls, "annotations", []) or []):
                                an = str(getattr(a, "name", "") or "").strip().split(".")[-1]
                                if an:
                                    ann_hits.add(an)

                            # Member-level annotations (fields/methods/ctors)
                            for mem in (getattr(cls, "body", []) or []):
                                for a in (getattr(mem, "annotations", []) or []):
                                    an = str(getattr(a, "name", "") or "").strip().split(".")[-1]
                                    if an:
                                        ann_hits.add(an)

                            # Last-resort textual capture within file for desired
                            # qualifiers that may not be represented as expected
                            # by this javalang build.
                            if wanted_lc:
                                for raw_ann in ann_re.findall(src):
                                    ann_simple = str(raw_ann).split(".")[-1].strip()
                                    if ann_simple and ann_simple.lower() in wanted_lc:
                                        ann_hits.add(ann_simple)

                            for ann in ann_hits:
                                index_all.setdefault(ann, set()).add(cls_name)
                        continue

                    # Regex fallback when AST parse fails.
                    file_classes = [m.group(1) for m in class_decl_re.finditer(src)]
                    if not file_classes:
                        continue
                    file_anns = {x.split(".")[-1] for x in ann_re.findall(src)}
                    for ann in file_anns:
                        if wanted_lc and ann.lower() not in wanted_lc:
                            continue
                        for cls_name in file_classes:
                            index_all.setdefault(ann, set()).add(cls_name)

            cache = {"key": root_abs, "value": index_all}
            self._user_annotation_class_index_cache = cache

        index_all = cache.get("value", {}) if isinstance(cache, dict) else {}
        index = {}
        # Accept exact name and light singular/plural variants to tolerate
        # qualifier naming drift (e.g. CommonValidation/CommonValidations).
        for a in ann_names:
            a = str(a or "").strip()
            if not a:
                continue
            vals = set(index_all.get(a, set()))
            if not vals:
                if a.endswith("s"):
                    vals.update(index_all.get(a[:-1], set()))
                else:
                    vals.update(index_all.get(a + "s", set()))
            if not vals:
                a_lc = a.lower()
                for k, v in index_all.items():
                    if str(k).strip().lower() == a_lc:
                        vals.update(v)
            index[a] = vals
        return index

    def _get_project_class_method_index(self) -> dict:
        """Map class simple name -> set(method names), cached per project root."""
        project_root = self.details.get("PROJECT_PATH") or self.details.get("SERVICE_PROJECT_PATH") or os.getcwd()
        root_abs = os.path.abspath(str(project_root))
        cache = getattr(self, "_project_class_method_index_cache", None)
        if isinstance(cache, dict) and cache.get("key") == root_abs:
            return cache.get("value", {})

        out = {}
        method_name_re = re.compile(r'\b([A-Za-z_]\w*)\s*\(')

        for root, _, files in os.walk(project_root):
            for fn in files:
                if not fn.endswith(self.file_extension()):
                    continue
                fpath = os.path.join(root, fn)
                try:
                    if fpath in self._file_content_cache:
                        src = self._file_content_cache[fpath]
                    else:
                        with open(fpath, "r", encoding="utf-8") as fh:
                            src = fh.read()
                        self._file_content_cache[fpath] = src
                except Exception:
                    continue

                parsed = None
                try:
                    cached_ast = self._raw_ast_cache.get(fpath)
                    if cached_ast is False:
                        parsed = None
                    elif cached_ast is not None:
                        parsed = cached_ast
                    else:
                        parsed = javalang.parse.parse(src)
                        self._raw_ast_cache[fpath] = parsed
                except Exception:
                    self._raw_ast_cache[fpath] = False
                    parsed = None

                if parsed is not None:
                    for _, cls in parsed.filter(jt.ClassDeclaration):
                        cls_name = getattr(cls, "name", None)
                        if not cls_name:
                            continue
                        methods = out.setdefault(cls_name, set())
                        for member in getattr(cls, "body", []) or []:
                            if isinstance(member, jt.MethodDeclaration):
                                mname = getattr(member, "name", None)
                                if mname:
                                    methods.add(mname)
                    continue

                # Regex fallback: use method signatures and drop constructor name.
                cls_name = None
                mcls = re.search(r'\bclass\s+([A-Za-z_]\w*)\b', src)
                if mcls:
                    cls_name = mcls.group(1)
                if not cls_name:
                    continue
                methods = out.setdefault(cls_name, set())
                for mm in re_method_decl.finditer(src):
                    mname = mm.group(1)
                    if mname and mname != cls_name:
                        methods.add(mname)
                for mm in method_name_re.finditer(src):
                    mname = mm.group(1)
                    if mname and mname != cls_name:
                        methods.add(mname)

        self._project_class_method_index_cache = {"key": root_abs, "value": out}
        return out

    def _get_project_interface_impl_index(self) -> dict:
        """Map interface simple name -> set(implementing class simple names)."""
        project_root = self.details.get("PROJECT_PATH") or self.details.get("SERVICE_PROJECT_PATH") or os.getcwd()
        root_abs = os.path.abspath(str(project_root))
        cache = getattr(self, "_project_interface_impl_index_cache", None)
        if isinstance(cache, dict) and cache.get("key") == root_abs:
            return cache.get("value", {})

        out = {}
        impl_re = re.compile(r'\bclass\s+([A-Za-z_]\w*)\s+implements\s+([^\{]+)', re.MULTILINE)

        for root, _, files in os.walk(project_root):
            for fn in files:
                if not fn.endswith(self.file_extension()):
                    continue
                fpath = os.path.join(root, fn)
                stem = os.path.splitext(fn)[0]
                try:
                    if fpath in self._file_content_cache:
                        src = self._file_content_cache[fpath]
                    else:
                        with open(fpath, "r", encoding="utf-8") as fh:
                            src = fh.read()
                        self._file_content_cache[fpath] = src
                except Exception:
                    continue

                parsed = None
                try:
                    parsed = self.parse_ast(src)
                except Exception:
                    parsed = None

                if parsed is not None:
                    for _, cls in parsed.filter(jt.ClassDeclaration):
                        cls_name = getattr(cls, "name", None)
                        if not cls_name:
                            continue
                        for iface in (getattr(cls, "implements", []) or []):
                            iface_name = self._simple_type_name(iface)
                            if iface_name:
                                out.setdefault(iface_name, set()).add(cls_name)
                else:
                    for mm in impl_re.finditer(src or ""):
                        cls_name = (mm.group(1) or "").strip()
                        iface_csv = (mm.group(2) or "")
                        if not cls_name:
                            continue
                        for tok in iface_csv.split(','):
                            iface_name = tok.strip().split()[-1].split('.')[-1]
                            if iface_name:
                                out.setdefault(iface_name, set()).add(cls_name)

                if stem.endswith("Impl") and len(stem) > 4:
                    out.setdefault(stem[:-4], set()).add(stem)
                if stem.endswith("Implementation") and len(stem) > len("Implementation"):
                    out.setdefault(stem[:-len("Implementation")], set()).add(stem)

        self._project_interface_impl_index_cache = {"key": root_abs, "value": out}
        return out

    def _normalize_contract_signature(self, sig: str) -> str:
        s = str(sig or "").strip()
        if not s:
            return ""
        s = re.sub(r'\s+', '', s)
        s = re.sub(r'\b(?:[a-z_][A-Za-z0-9_]*\.)+([A-Za-z_][A-Za-z0-9_]*)', r'\1', s)
        return s

    def _contract_signature_base(self, sig: str) -> str:
        n = self._normalize_contract_signature(sig)
        if not n:
            return ""
        return n.split('<', 1)[0].split('.')[-1]

    def _split_top_level_commas(self, text: str) -> list:
        parts = []
        buf = []
        depth_angle = 0
        depth_paren = 0
        for ch in str(text or ""):
            if ch == '<':
                depth_angle += 1
            elif ch == '>':
                depth_angle = max(0, depth_angle - 1)
            elif ch == '(':
                depth_paren += 1
            elif ch == ')':
                depth_paren = max(0, depth_paren - 1)
            if ch == ',' and depth_angle == 0 and depth_paren == 0:
                p = "".join(buf).strip()
                if p:
                    parts.append(p)
                buf = []
                continue
            buf.append(ch)
        tail = "".join(buf).strip()
        if tail:
            parts.append(tail)
        return parts

    def _get_project_class_contract_signature_index(self) -> dict:
        """Map class simple name -> set(implemented contract signatures)."""
        project_root = self.details.get("PROJECT_PATH") or self.details.get("SERVICE_PROJECT_PATH") or os.getcwd()
        root_abs = os.path.abspath(str(project_root))
        cache = getattr(self, "_project_class_contract_signature_index_cache", None)
        if isinstance(cache, dict) and cache.get("key") == root_abs:
            return cache.get("value", {})

        out = {}
        impl_re = re.compile(r'\bclass\s+([A-Za-z_]\w*)\s+implements\s+([^\{]+)', re.MULTILINE)
        for root, _, files in os.walk(project_root):
            for fn in files:
                if not fn.endswith(".java"):
                    continue
                fp = os.path.join(root, fn)
                try:
                    txt = open(fp, "r", encoding="utf-8", errors="ignore").read()
                except Exception:
                    txt = ""
                for mm in impl_re.finditer(txt or ""):
                    cls_name = (mm.group(1) or "").strip()
                    iface_csv = (mm.group(2) or "")
                    if not cls_name:
                        continue
                    for tok in self._split_top_level_commas(iface_csv):
                        sig = self._normalize_contract_signature(tok)
                        if sig:
                            out.setdefault(cls_name, set()).add(sig)

        self._project_class_contract_signature_index_cache = {"key": root_abs, "value": out}
        return out

    def _class_matches_contract_signatures(self, cls_name: str, contract_sigs: set, class_sig_index: dict) -> bool:
        cls = str(cls_name or "").strip().split('.')[-1]
        if not cls:
            return False
        req = {self._normalize_contract_signature(x) for x in (contract_sigs or set()) if str(x or "").strip()}
        if not req:
            return True

        impl = {
            self._normalize_contract_signature(x)
            for x in (class_sig_index.get(cls, set()) or set())
            if str(x or "").strip()
        }
        if not impl:
            req_bases = {self._contract_signature_base(x) for x in req if x}
            return cls in req_bases

        for r in req:
            if '<' in r and '>' in r:
                if r in impl:
                    return True
            else:
                rb = self._contract_signature_base(r)
                if any(self._contract_signature_base(i) == rb for i in impl):
                    return True
        return False


    def _get_package_name(self, java_code: str):
        pat = html.unescape(
            self.regex.get("package", r'^\s*package\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;')
        )
        m = re.search(pat, java_code, flags=re.MULTILINE)
        return m.group(1) if m else None

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
            t2 = re.sub(r'\s*&amp;lt;[^&amp;gt]+&amp;gt;\s*', '', t)
            t2 = re.sub(r'\s*<[^>]+>\s*', '', t2)
            return t2.replace('[]', '').strip()

        def update_map(f: str, var: str, typ: str, *, source: str):
            if not typ or typ in PRIMITIVES:
                return
            # FIX: the lookup side in _enrich_call_with_path uses
            #   object_class_map.get((caller_file.lower(), cls_name.lower()))
            # where caller_file is the full absolute path stored in the file_name
            # column (set from fpath = os.path.join(root, file) in the parser worker).
            # Previously this side used f.lower() where f was the bare filename
            # ('OrderService.java'), so the scoped key never matched the lookup key
            # (which was the full path).  Use os.path.normcase(os.path.abspath(f))
            # to make both sides consistent regardless of relative/absolute form.
            key_scoped = (os.path.normcase(os.path.abspath(f)), var.lower())
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
                            tname = self._effective_type_name(fd.type)
                            for decl in getattr(fd, "declarators", []):
                                if tname and tname not in PRIMITIVES:
                                    update_map(fpath, decl.name, tname, source="ast_field")

                        for _, mnode in type_node.filter(jt.MethodDeclaration):
                            for p in getattr(mnode, "parameters", []):
                                tname = self._effective_type_name(p.type)
                                if tname and tname not in PRIMITIVES:
                                    update_map(fpath, p.name, tname, source="ast_param")
                            for _, lv in mnode.filter(jt.LocalVariableDeclaration):
                                declared_type = self._effective_type_name(lv.type)
                                for decl in getattr(lv, "declarators", []):
                                    tname = declared_type or self._infer_type_from_initializer(decl)
                                    if tname and tname not in PRIMITIVES:
                                        update_map(fpath, decl.name, tname, source="ast_local")
                            for _, forstmt in mnode.filter(jt.ForStatement):
                                if hasattr(forstmt, "control") and hasattr(forstmt.control, "var"):
                                    var_decl = forstmt.control.var
                                    if var_decl:
                                        tname = self._effective_type_name(var_decl.type)
                                        for declarator in getattr(var_decl, "declarators", []):
                                            if tname and tname not in PRIMITIVES:
                                                update_map(fpath, declarator.name, tname, source="ast_for")

                        for _, cnode in type_node.filter(jt.ConstructorDeclaration):
                            for p in getattr(cnode, "parameters", []):
                                tname = self._effective_type_name(p.type)
                                if tname and tname not in PRIMITIVES:
                                    update_map(fpath, p.name, tname, source="ast_ctor_param")
                            for _, lv in cnode.filter(jt.LocalVariableDeclaration):
                                declared_type = self._effective_type_name(lv.type)
                                for decl in getattr(lv, "declarators", []):
                                    tname = declared_type or self._infer_type_from_initializer(decl)
                                    if tname and tname not in PRIMITIVES:
                                        update_map(fpath, decl.name, tname, source="ast_ctor_local")
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

    def build_method_return_index(self, app_folder: str) -> dict:
        method_return_index = {}
        class_decl_pat = re.compile(r'\bclass\s+(\w+)\b')
        # Captures "class Foo extends Bar" — used for Case 1 inheritance walk
        class_extends_pat = re.compile(r'\bclass\s+(\w+)\s+extends\s+(\w+)')
        method_sig_pat = re.compile(
            r'(?:public|protected|private)?\s+(?:static\s+)?([\w\.<<>\[\]]+)\s+(\w+)\s*\(',
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
                                    r'(&lt;[^&gt]+&gt;|<[^>]+>)', '', base
                                )
                            method_return_index[cls_name][m.name] = rname.strip().split('.')[-1]
                        for _, c in cls.filter(jt.ConstructorDeclaration):
                            method_return_index[cls_name][c.name] = "<constructor>"

                        # Lombok accessor fallback: synthesize getter/setter names from fields
                        # so downstream code can resolve getX()/setX() to the backing field path.
                        if self._has_lombok_getter_setter(code, class_node=cls, class_name=cls_name):
                            for _, fd in cls.filter(jt.FieldDeclaration):
                                for decl in getattr(fd, "declarators", []) or []:
                                    field_name = decl.name
                                    if not field_name:
                                        continue
                                    cap = field_name[0].upper() + field_name[1:] if len(field_name) > 1 else field_name.upper()
                                    method_return_index[cls_name].setdefault(f"get{cap}", field_name)
                                    method_return_index[cls_name].setdefault(f"is{cap}", field_name)
                                    method_return_index[cls_name].setdefault(f"set{cap}", field_name)

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
                                    r'(&lt;[^&gt]+&gt;|<[^>]+>)', '', base
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
                        r'(&lt;[^&gt]+&gt;|<[^>]+>)', '', return_type
                    ).strip().split('.')[-1]
                    method_return_index[cls_name][method_name] = simple_return
                for cm in constructor_sig_pat.finditer(code):
                    ctor_name = cm.group(1)
                    if ctor_name == cls_name:
                        method_return_index[cls_name][ctor_name] = "<constructor>"

        return method_return_index

    def find_type_to_file_map(self, app_folder: str) -> dict:
        java_files_map = {}
        for root, _, files in os.walk(app_folder):
            for f in files:
                if f.endswith(self.file_extension()):
                    class_name = os.path.splitext(f)[0]
                    java_files_map[class_name] = os.path.join(root, f)
        return java_files_map
