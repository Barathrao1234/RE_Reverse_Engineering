# method_lineage_generation_java8.py
# Java 8 compatible version.
#
# Key differences from the Java 18 version:
#   - No records, sealed classes, text blocks, switch expressions, or pattern matching.
#   - No 'var' type-inference handling.
#   - No union-type syntax in type hints (uses Optional/Tuple/List from typing module).
#   - find_matching_brace_from, find_next_open_brace_from, etc. use plain int return
#     types instead of `int | None` (Python 3.10+ union syntax removed).
#   - All `int | None` return annotations replaced with `Optional[int]`.
#   - build_type_to_path_including_nested only filters on ClassDeclaration,
#     InterfaceDeclaration, EnumDeclaration (no RecordDeclaration — Java 8).
#   - class_regex for LOC does NOT include 'record' as a keyword.
#   - tuple[int, list[str]] replaced with Tuple[int, List[str]].
#   - Everything else is functionally identical to the Java 18 version.

import os
import re
import html
import json
import javalang
import pandas as pd
from typing import Optional, Tuple, List


class LanguageAdapter:
    """
    Base interface for language-specific adapters.
    Concrete adapters (Java8Adapter, etc.) must implement these methods.
    """
    def configure(self, *, details, regex,
                  include_unqualified=True,
                  accept_local_new_types=True,
                  accept_parameter_types=True,
                  accept_same_package=True):
        self.details = details
        self.regex = regex
        self.include_unqualified = include_unqualified
        self.accept_local_new_types = accept_local_new_types
        self.accept_parameter_types = accept_parameter_types
        self.accept_same_package = accept_same_package

    def file_extension(self):
        raise NotImplementedError

    def parse_ast(self, code):
        raise NotImplementedError

    def get_declared_types(self, ast):
        raise NotImplementedError

    def get_methods_in_type(self, type_node):
        raise NotImplementedError

    def extract_method_metadata(self, method_node):
        raise NotImplementedError

    def find_calls_in_method(self, type_node, method_node, code):
        raise NotImplementedError

    def fallback_parse(self, code_raw):
        raise NotImplementedError

    def is_system_call(self, call):
        raise NotImplementedError

    def language_keywords(self):
        raise NotImplementedError

    def build_object_class_map(self, app_folder):
        raise NotImplementedError

    def build_method_return_index(self, app_folder):
        raise NotImplementedError

    def find_type_to_file_map(self, app_folder):
        raise NotImplementedError

    def extract_method_loc(self, file_path, method_name):
        raise NotImplementedError

    def extract_application_properties_from_folder(self, app_folder):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def strip_top_level_comments(code):
    """
    Remove top-level comments (// ... and /* ... */) but leave comments
    inside method/class bodies untouched.
    """
    code = re.sub(r'^\s*//.*$', '', code, flags=re.M)

    def replacer(match):
        if '{' not in match.group(0) and '}' not in match.group(0):
            return ''
        return match.group(0)

    code = re.sub(r'/\*.*?\*/', replacer, code, flags=re.S)
    return code


def is_commented_declaration(code, line_no):
    """
    Return True if the line corresponding to line_no is fully commented out.
    """
    lines = code.splitlines()
    if line_no < 0 or line_no >= len(lines):
        return False
    line = lines[line_no].strip()
    return line.startswith("//") or line.startswith("/*") or line.startswith("*")


def is_declaration_line_commented(src, decl_start_idx):
    """
    Return True if the line where decl_start_idx occurs is commented out.
    """
    line_start = src.rfind('\n', 0, decl_start_idx) + 1
    line = src[line_start: src.find('\n', line_start)]
    stripped = line.lstrip()

    if stripped.startswith("//"):
        return True

    before = src[:decl_start_idx]
    last_block_start = before.rfind("/*")
    last_block_end = before.rfind("*/")

    if last_block_start != -1 and last_block_end < last_block_start:
        return True

    return False


def _strip_comments_and_literals(text):
    if not isinstance(text, str):
        return ""
    return re.sub(
        r'//.*?$|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])\'',
        '',
        text,
        flags=re.MULTILINE | re.DOTALL
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def method_lineage(
    adapter,
    details,
    data,
    technology,
    application,
    app_folder,
    OUTPUT_DIR,
    groups,
    all_methods,
    controller_files,
    include_unqualified=True,
    accept_local_new_types=True,
    accept_parameter_types=True,
    accept_same_package=True
):
    """
    Produces Excel with three sheets:
      - Cleaned_AST_Details (Class.method exploded per chain segment)
      - Unique_Methods (overload-aware; with LOC, annotations, return type, decl type)
      - application.properties
    """
    print("method_lineage")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    regex = data["Language"][technology]["Application"][application]["Regex_Pattern"]

    adapter.configure(
        details=details,
        regex=regex,
        include_unqualified=include_unqualified,
        accept_local_new_types=accept_local_new_types,
        accept_parameter_types=accept_parameter_types,
        accept_same_package=accept_same_package
    )

    ast_results = []
    method_map = {}
    file_map = {}
    errors = []

    # ------------------ Walk all files and extract calls ------------------
    for root_dir, _, files in os.walk(app_folder):
        for file in files:
            if not file.endswith(adapter.file_extension()):
                continue
            file_path = os.path.join(root_dir, file)

            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    code_raw = f.read()

                code = html.unescape(code_raw)
                code_no_comments = _strip_comments_and_literals(code)

                ast = adapter.parse_ast(code_no_comments)
                if not ast:
                    raise RuntimeError("AST parse failed")

                declared_types = list(adapter.get_declared_types(ast))

                if not declared_types:
                    fb = adapter.fallback_parse(code_raw)
                    type_name = fb.get('type_name', 'Unknown')
                    row_type = fb.get('row_type', 'Unknown')
                    filtered_calls = fb.get('filtered_calls', [])

                    file_map[type_name] = file
                    method_map.setdefault(type_name, {})
                    synthetic_method = "UnknownMethod"
                    method_map[type_name][synthetic_method] = filtered_calls or ["None"]

                    for call in filtered_calls or ["None"]:
                        ast_results.append({
                            'file_name': file,
                            'class_interface_name': type_name,
                            'type': row_type,
                            'method_name': synthetic_method,
                            'Annotations': "None",
                            'Method_Declaration_Type': "Default",
                            'return_type': "",
                            'object_call': call,
                            'Parameters': '',
                            'Parameter_Arity': None,
                            'Parameter_Types': '',
                        })
                    continue

                for type_name, type_kind, type_node in declared_types:
                    file_map[type_name] = file
                    method_map.setdefault(type_name, {})

                    for method_name, method_node in adapter.get_methods_in_type(type_node):
                        try:
                            pos = method_node.position
                            if pos and is_commented_declaration(code, pos[1] - 1):
                                continue
                        except Exception:
                            pass

                        meta = adapter.extract_method_metadata(method_node)
                        calls = adapter.find_calls_in_method(type_node, method_node, code_no_comments)
                        calls = sorted(set(calls)) if calls else ["None"]
                        method_map[type_name][method_name] = calls

                        for call in calls:
                            ast_results.append({
                                'file_name': file,
                                'class_interface_name': type_name,
                                'type': type_kind or 'Unknown',
                                'method_name': method_name,
                                'Annotations': meta.get('Annotations', ''),
                                'Method_Declaration_Type': meta.get('Method_Declaration_Type', 'Default'),
                                'return_type': meta.get('return_type', ''),
                                'object_call': call,
                                'Parameters': meta.get('Parameters', ''),
                                'Parameter_Arity': meta.get('Parameter_Arity', None),
                                'Parameter_Types': meta.get('Parameter_Types', ''),
                            })

            except Exception as e:
                errors.append({'File': file_path, 'Error': str(e)})
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        code_raw = f.read()
                    code = html.unescape(code_raw)
                except Exception as e2:
                    errors.append({'File': file_path, 'Error': "Read error in fallback: {}".format(e2)})
                    continue

                fb = adapter.fallback_parse(code_raw)
                type_name = fb.get('type_name', 'Unknown')
                row_type = fb.get('row_type', 'Unknown')

                file_map[type_name] = file
                method_map.setdefault(type_name, {})

                if 'per_method_calls' in fb and fb['per_method_calls']:
                    for rec in fb['per_method_calls']:
                        method = rec.get('method_name') or 'UnknownMethod'
                        call = rec.get('object_call') or 'None'
                        method_map[type_name].setdefault(method, []).append(call)
                        ast_results.append({
                            'file_name': file,
                            'class_interface_name': type_name,
                            'type': row_type,
                            'method_name': method,
                            'Annotations': "None",
                            'Method_Declaration_Type': "Default",
                            'return_type': "",
                            'object_call': call,
                            'Parameters': '',
                            'Parameter_Arity': None,
                            'Parameter_Types': '',
                        })
                else:
                    filtered_calls = fb.get('filtered_calls', [])
                    synthetic_method = "UnknownMethod"
                    method_map[type_name][synthetic_method] = filtered_calls or ["None"]

                    for call in filtered_calls or ["None"]:
                        ast_results.append({
                            'file_name': file,
                            'class_interface_name': type_name,
                            'type': row_type,
                            'method_name': synthetic_method,
                            'Annotations': "None",
                            'Method_Declaration_Type': "Default",
                            'return_type': "",
                            'object_call': call,
                            'Parameters': '',
                            'Parameter_Arity': None,
                            'Parameter_Types': '',
                        })

    # ---- Optional chain resolution ----
    chain_results = []

    def resolve_chain(current, visited):
        called_method = current.split('.')[-1] if '.' in current else current
        found = False
        for typ in method_map:
            if called_method in method_map[typ]:
                calls = method_map[typ][called_method]
                file_name = file_map.get(typ, 'Unknown')
                if calls:
                    for call in calls:
                        chain_results.append({'File Name': file_name, 'Method Name': current, 'Object Call': call})
                        if call not in visited:
                            visited.add(call)
                            resolve_chain(call, visited)
                else:
                    chain_results.append({'File Name': file_name, 'Method Name': current, 'Object Call': ''})
                found = True
                break
        if not found:
            chain_results.append({'File Name': 'Unknown', 'Method Name': current, 'Object Call': ''})

    for typ in method_map:
        for method in method_map[typ]:
            file_name = file_map.get(typ, 'Unknown')
            for call in method_map[typ][method]:
                chain_results.append({'File Name': file_name, 'Method Name': method, 'Object Call': call})
                resolve_chain(call, {call})

    df_ast = pd.DataFrame(ast_results)

    # ---- Cleaner: system-call filtering + mapping + chain explosion ----
    def clean_and_write(df):
        object_class_map = adapter.build_object_class_map(app_folder)
        method_return_index = adapter.build_method_return_index(app_folder)
        lang_keywords = adapter.language_keywords()
        keyword_set = {kw.lower() for kw in lang_keywords}

        SYSTEM_METHODS = {
            m.lower()
            for m in details.get("SYSTEM_METHODS", [])
            if isinstance(m, str)
        }

        def is_system_call(call):
            return adapter.is_system_call(call)

        df_clean = df[~df["object_call"].apply(is_system_call)].copy()
        df_clean["object_call"] = df_clean["object_call"].fillna("None")

        def strip_generics(name):
            if not isinstance(name, str):
                return name
            name = re.sub(r'\s*&amp;lt;[^&amp;gt]+&amp;gt;\s*', '', name)
            name = re.sub(r'\s*<[^>]+>\s*', '', name)
            return name

        chain_suppressions = set()

        def normalize_keyword_rooted_call(s, parent_class):
            if not isinstance(s, str) or not s.strip():
                return s
            s = s.strip()
            m = re.match(r'^\s*(return|this|super|new)\s*\.\s*([A-Za-z_]\w*)(.*)$', s, flags=re.IGNORECASE)
            if m:
                meth = m.group(2)
                rest = m.group(3) or ""
                return "{}.{}{}".format(strip_generics(parent_class), meth, rest).strip()
            return s

        def _lookup_type(base, parent_class, file_name):
            if not isinstance(base, str) or base.strip() == "":
                return strip_generics(parent_class)
            b = base.strip()
            if b.lower() in keyword_set:
                return strip_generics(parent_class)

            t_scoped = object_class_map.get((str(file_name).lower(), b.lower()))
            if t_scoped:
                return strip_generics(t_scoped)

            t_global = object_class_map.get(b.lower())
            if t_global:
                return strip_generics(t_global)

            b_no_gen = strip_generics(b)
            cap = (b_no_gen[0].upper() + b_no_gen[1:]) if b_no_gen else b_no_gen
            if cap and cap in method_return_index:
                return cap

            return b_no_gen

        def map_class_method_call(obj_call, parent_class, file_name):
            if not isinstance(obj_call, str) or obj_call.strip() == "":
                return "None"

            mkw = re.match(r'^\s*(return|this|super|new)\s*\.\s*([A-Za-z_]\w*)(.*)$', obj_call, flags=re.IGNORECASE)
            if mkw:
                meth = mkw.group(2)
                rest = mkw.group(3) or ""
                return "{}.{}{}".format(strip_generics(parent_class), meth, rest)

            if "." not in obj_call:
                return obj_call

            first_dot = obj_call.find(".")
            obj = obj_call[:first_dot]
            rest = obj_call[first_dot + 1:]

            mapped_base = _lookup_type(obj, parent_class, file_name)
            return "{}.{}".format(mapped_base, rest)

        def resolve_chained_with_classes(obj_call, parent_class, file_name):
            if not isinstance(obj_call, str) or obj_call.strip() == "":
                return "None"
            first_dot = obj_call.find(".")
            if first_dot == -1 or "(" not in obj_call:
                return map_class_method_call(obj_call, parent_class, file_name)

            base = obj_call[:first_dot].strip()
            rest = obj_call[first_dot + 1:]
            methods = re.findall(r'([A-Za-z_]\w*)\s*\(', rest)
            if not methods:
                return map_class_method_call(obj_call, parent_class, file_name)

            current_class = _lookup_type(base, parent_class, file_name)

            chain_render = []
            for m in methods:
                chain_render.append("{}.{}()".format(strip_generics(current_class), m))
                ret_type = method_return_index.get(current_class, {}).get(m)
                if not ret_type or str(ret_type).lower() == 'void':
                    break
                current_class = strip_generics(str(ret_type).split('.')[-1])
            return ".".join(chain_render)

        def map_or_resolve(row):
            obj_call = row["object_call"]
            parent_cls = row["class_interface_name"]
            file_name = row["file_name"]
            if isinstance(obj_call, str) and "." in obj_call and "(" in obj_call:
                return resolve_chained_with_classes(obj_call, parent_cls, file_name)
            return map_class_method_call(obj_call, parent_cls, file_name)

        df_clean["class_method_call"] = df_clean.apply(map_or_resolve, axis=1)
        df_clean["class_method_call"] = df_clean["class_method_call"].astype(str).str.replace(
            r'\s*&amp;lt;[^&amp;gt]+&amp;gt;\s*', '', regex=True
        ).str.replace(r'\s*<[^>]+>\s*', '', regex=True)

        def derive_chain_segments(obj_call, parent_class, file_name):
            if not isinstance(obj_call, str) or obj_call.strip() == "":
                return []

            first_dot = obj_call.find(".")
            if first_dot == -1 or "(" not in obj_call:
                m = re.match(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\(', obj_call)
                if m:
                    cls, mtd = strip_generics(m.group(1)), m.group(2)
                    return ["{}.{}()".format(cls, mtd)]
                m2 = re.match(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*$', obj_call)
                if m2:
                    cls, mtd = strip_generics(m2.group(1)), m2.group(2)
                    return ["{}.{}()".format(cls, mtd)]
                return []

            base = obj_call[:first_dot].strip()
            rest = obj_call[first_dot + 1:]
            methods = re.findall(r'([A-Za-z_]\w*)\s*\(', rest)
            if not methods:
                m3 = re.match(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*$', obj_call)
                if m3:
                    cls, mtd = strip_generics(m3.group(1)), m3.group(2)
                    return ["{}.{}()".format(cls, mtd)]
                return []

            current_class = _lookup_type(base, parent_class, file_name)

            segments = []
            for mtd in methods:
                segments.append("{}.{}()".format(strip_generics(current_class), mtd))
                ret_type = method_return_index.get(current_class, {}).get(mtd)
                if not ret_type or str(ret_type).lower() == "void":
                    break
                current_class = strip_generics(str(ret_type).split(".")[-1])
            return segments

        def explode_cleaned_ast_details(df_clean_local):
            rows = []
            single_seg_pat_paren = re.compile(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\([^)]*\)\s*$')
            single_seg_pat_noparen = re.compile(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*$')

            for _, row in df_clean_local.iterrows():
                obj_call = str(row.get("object_call", "")).strip()
                parent_class = str(row.get("class_interface_name", "")).strip()
                file_name = str(row.get("file_name", "")).strip()

                obj_call = normalize_keyword_rooted_call(obj_call, parent_class)
                cmc = normalize_keyword_rooted_call(str(row.get("class_method_call", "")).strip(), parent_class)

                base_context = {
                    "file_name": row.get("file_name"),
                    "class_interface_name": strip_generics(parent_class),
                    "type": row.get("type"),
                    "method_name": row.get("method_name"),
                    "Annotations": row.get("Annotations"),
                    "Method_Declaration_Type": row.get("Method_Declaration_Type"),
                    "return_type": row.get("return_type"),
                    "Parameters": row.get("Parameters", ""),
                    "Parameter_Arity": row.get("Parameter_Arity", None),
                    "Parameter_Types": row.get("Parameter_Types", ""),
                }

                segments = derive_chain_segments(obj_call, parent_class, file_name)
                if segments:
                    for seg in segments:
                        row_dict = dict(base_context)
                        row_dict["object_call"] = seg
                        row_dict["class_method_call"] = seg
                        rows.append(row_dict)
                    continue

                m2 = single_seg_pat_paren.match(cmc)
                if m2:
                    cls, mtd = strip_generics(m2.group(1)), m2.group(2)
                    key = (base_context["file_name"], base_context["class_interface_name"], base_context["method_name"], mtd.lower())
                    if key in chain_suppressions:
                        continue
                    seg = "{}.{}()".format(cls, mtd)
                    row_dict = dict(base_context)
                    row_dict["object_call"] = seg
                    row_dict["class_method_call"] = seg
                    rows.append(row_dict)
                    continue

                m2_np = single_seg_pat_noparen.match(cmc)
                if m2_np:
                    cls, mtd = strip_generics(m2_np.group(1)), m2_np.group(2)
                    key = (base_context["file_name"], base_context["class_interface_name"], base_context["method_name"], mtd.lower())
                    if key in chain_suppressions:
                        continue
                    seg = "{}.{}()".format(cls, mtd)
                    row_dict = dict(base_context)
                    row_dict["object_call"] = seg
                    row_dict["class_method_call"] = seg
                    rows.append(row_dict)
                    continue

                row_dict = dict(base_context)
                row_dict["object_call"] = obj_call or "None"
                row_dict["class_method_call"] = cmc or obj_call or "None"
                rows.append(row_dict)

            df_out = pd.DataFrame(rows) if rows else df_clean_local.copy()
            if not df_out.empty:
                df_out = df_out.drop_duplicates()
            return df_out

        df_clean_exploded = explode_cleaned_ast_details(df_clean)

        # ============================================================
        # FINAL SYSTEM METHOD DROP (AFTER CHAIN EXPLOSION)
        # ============================================================

        def extract_method_only(call):
            if not isinstance(call, str):
                return None
            m = re.match(r'\s*[A-Za-z_]\w*\s*\.\s*([A-Za-z_]\w*)', call)
            return m.group(1).lower() if m else None

        df_clean_exploded["__method_only"] = (
            df_clean_exploded["class_method_call"]
            .astype(str)
            .apply(extract_method_only)
        )

        df_clean_exploded = df_clean_exploded[
            ~df_clean_exploded["__method_only"].isin(SYSTEM_METHODS)
        ].drop(columns="__method_only")

        # ============================================================
        # REMOVE CALLS BASED ON NON-USER-DEFINED IMPORTS
        # ============================================================

        def collect_external_import_classes(app_folder_path, user_prefix):
            import_classes = set()
            import_pattern = re.compile(r'^\s*import\s+(static\s+)?([\w\.]+)\s*;', re.MULTILINE)

            for root, _, files in os.walk(app_folder_path):
                for file in files:
                    if not file.endswith(tuple(details.get("extension", []))):
                        continue
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            code = f.read()
                    except Exception:
                        continue

                    for _, full_import in import_pattern.findall(code):
                        if full_import.startswith(user_prefix):
                            continue
                        simple_name = full_import.split('.')[-1]
                        if simple_name:
                            import_classes.add(simple_name)

            return import_classes

        def extract_base_class(class_method_call):
            if not isinstance(class_method_call, str):
                return None
            m = re.match(r'\s*([A-Za-z_]\w*)\s*\.', class_method_call)
            return m.group(1) if m else None

        user_prefix = details.get("user_defined_generic_import", "")
        external_import_classes = collect_external_import_classes(app_folder, user_prefix)

        df_clean_exploded["__base_class"] = df_clean_exploded["class_method_call"].apply(
            extract_base_class
        )

        df_clean_exploded = df_clean_exploded[
            ~df_clean_exploded["__base_class"].isin(external_import_classes)
        ].drop(columns="__base_class")

        # --- Enforce: if Class.method exists, drop object.method for the same call ---
        def _split_base_method(cmc):
            s = str(cmc or "").strip()
            m = re.match(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\(?\s*\)?\s*$', s)
            if not m:
                return None, None
            return m.group(1), m.group(2)

        df_ex = df_clean_exploded.copy()
        split_results = df_ex['class_method_call'].apply(
            lambda s: pd.Series(_split_base_method(s))
        )
        df_ex['__base'] = split_results[0]
        df_ex['__meth'] = split_results[1]

        mask_valid = df_ex['__base'].notna() & df_ex['__meth'].notna()
        df_valid = df_ex[mask_valid].copy()

        df_valid['__upper_base'] = df_valid['__base'].apply(
            lambda b: (b[0].upper() + b[1:]) if isinstance(b, str) and b else b
        )

        class_rows = df_valid[df_valid['__base'].str[0].str.isupper() == True].copy()

        class_key_set = set(
            zip(
                class_rows['file_name'],
                class_rows['class_interface_name'],
                class_rows['method_name'],
                class_rows['__upper_base'],
                class_rows['__meth']
            )
        )

        def _should_drop(row):
            base = row['__base']
            if not isinstance(base, str) or not base:
                return False
            if base[0].isupper():
                return False
            upper_base = row['__upper_base']
            key = (row['file_name'], row['class_interface_name'], row['method_name'], upper_base, row['__meth'])
            return key in class_key_set

        df_valid['__drop'] = df_valid.apply(_should_drop, axis=1)

        df_keep_valid = df_valid[df_valid['__drop'] == False].drop(columns=['__base', '__meth', '__upper_base', '__drop'])
        df_rest = df_ex[~mask_valid]
        df_clean_exploded = pd.concat([df_keep_valid, df_rest], ignore_index=True)

        df_clean_exploded = df_clean_exploded.drop_duplicates(
            subset=['file_name', 'class_interface_name', 'method_name', 'class_method_call']
        )

        # FINAL FILTER — DROP NON-USER-DEFINED IMPORT CALLS (second pass)
        user_prefix = details.get("user_defined_generic_import", "")
        external_import_classes = collect_external_import_classes(app_folder, user_prefix)

        df_clean_exploded["__base_class"] = (
            df_clean_exploded["class_method_call"]
            .astype(str)
            .apply(extract_base_class)
        )

        df_clean_exploded = df_clean_exploded[
            ~df_clean_exploded["__base_class"].isin(external_import_classes)
        ].drop(columns="__base_class")

        # ============================================================
        # Callee collection from Cleaned_AST_Details
        # ============================================================
        callee_pairs = set()

        rx_qual = re.compile(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\(\s*\)\s*$')
        rx_unq = re.compile(r'^\s*([A-Za-z_]\w*)\s*(?:\(\s*\))?\s*$')

        for _, row_x in df_clean_exploded.iterrows():
            cmc = str(row_x.get("class_method_call", "")).strip()
            parent_cls = str(row_x.get("class_interface_name", "")).strip()
            if not cmc:
                continue

            m = rx_qual.match(cmc)
            if m:
                cls = m.group(1)
                mtd = m.group(2)
                if mtd.lower() in SYSTEM_METHODS:
                    continue
                callee_pairs.add((cls, mtd))
                continue

            m2 = rx_unq.match(cmc)
            if m2:
                mtd = m2.group(1)
                if mtd.lower() in SYSTEM_METHODS:
                    continue
                if method_return_index.get(parent_cls, {}).get(mtd) is not None:
                    callee_pairs.add((parent_cls, mtd))

        # ============================================================
        # Unique_Methods (overload-aware)
        # ============================================================

        df_unique_parent = (
            df_clean
            .assign(class_method_key=lambda x: (
                x['class_interface_name'].astype(str) + "." +
                x['method_name'].astype(str) + "(" +
                x['Parameters'].fillna("").astype(str) + ")"
            ))
            .groupby(['class_interface_name', 'method_name', 'Parameters'], as_index=False)
            .agg({
                'Annotations': 'first',
                'return_type': 'first',
                'Method_Declaration_Type': 'first',
                'Parameter_Arity': 'first',
                'Parameter_Types': 'first',
                'class_method_key': 'first'
            })
        )[[
            "class_method_key",
            "class_interface_name", "method_name",
            "Parameters", "Parameter_Arity", "Parameter_Types",
            "Annotations", "return_type", "Method_Declaration_Type"
        ]]

        df_all_methods = (
            df[
                ["class_interface_name", "method_name", "Parameters", "Parameter_Arity", "Parameter_Types",
                 "Annotations", "return_type", "Method_Declaration_Type"]
            ]
            .drop_duplicates(subset=["class_interface_name", "method_name", "Parameters"])
            .dropna(subset=["class_interface_name", "method_name"])
        ).copy()

        df_all_methods["class_method_key"] = (
            df_all_methods["class_interface_name"].astype(str) + "." +
            df_all_methods["method_name"].astype(str) + "(" +
            df_all_methods["Parameters"].fillna("").astype(str) + ")"
        )

        rows_callees = []
        for cls, mtd in sorted(callee_pairs):
            rtype = method_return_index.get(cls, {}).get(mtd, "")
            rows_callees.append({
                "class_interface_name": cls,
                "method_name": mtd,
                "Parameters": "",
                "Parameter_Arity": None,
                "Parameter_Types": "",
                "Annotations": "",
                "return_type": rtype,
                "Method_Declaration_Type": "Default"
            })
        df_callee_methods = pd.DataFrame(rows_callees)
        if not df_callee_methods.empty:
            df_callee_methods["class_method_key"] = (
                df_callee_methods["class_interface_name"].astype(str) + "." +
                df_callee_methods["method_name"].astype(str) + "(" +
                df_callee_methods["Parameters"].fillna("").astype(str) + ")"
            )
        else:
            df_callee_methods = pd.DataFrame(columns=[
                "class_method_key",
                "class_interface_name", "method_name",
                "Parameters", "Parameter_Arity", "Parameter_Types",
                "Annotations", "return_type", "Method_Declaration_Type"
            ])

        df_unique_methods = pd.concat(
            [df_unique_parent, df_all_methods, df_callee_methods],
            ignore_index=True
        ).drop_duplicates(
            subset=["class_interface_name", "method_name", "Parameters"],
            keep="first"
        ).reset_index(drop=True)

        valid_kinds = {"class", "class_implements_interface", "interface"}

        valid_types_df = (
            df_clean_exploded[["class_interface_name", "type"]]
            .dropna(subset=["class_interface_name", "type"])
            .drop_duplicates()
        )

        valid_class_or_interface = set(
            valid_types_df.loc[valid_types_df["type"].str.lower().isin(valid_kinds), "class_interface_name"]
            .astype(str)
            .tolist()
        )

        df_unique_methods = df_unique_methods[
            df_unique_methods["class_interface_name"].astype(str).isin(valid_class_or_interface)
        ].reset_index(drop=True)

        # ============================================================
        # Accurate LOC computation (nested-aware + overload match)
        # Java 8 version: no 'record' in class_regex; no union-type hints
        # ============================================================

        def build_type_to_path_including_nested(java_folder):
            """
            Java 8 version: only ClassDeclaration, InterfaceDeclaration, EnumDeclaration.
            No RecordDeclaration (Java 16+).
            """
            mapping = {}
            for root, _, files in os.walk(java_folder):
                for f in files:
                    if not f.endswith(tuple(details["extension"])):
                        continue
                    fpath = os.path.join(root, f)
                    try:
                        with open(fpath, "r", encoding="utf-8") as fh:
                            code = fh.read()
                    except Exception:
                        try:
                            with open(fpath, "r", encoding="latin-1") as fh:
                                code = fh.read()
                        except Exception:
                            continue
                    try:
                        tree = javalang.parse.parse(code)
                    except Exception:
                        continue
                    # Java 8: no RecordDeclaration
                    for _, decl in tree.filter((
                        javalang.tree.ClassDeclaration,
                        javalang.tree.InterfaceDeclaration,
                        javalang.tree.EnumDeclaration,
                    )):
                        name = getattr(decl, "name", None)
                        if name:
                            mapping.setdefault(name, fpath)
            return mapping

        type_to_path_full = build_type_to_path_including_nested(app_folder)
        loc_cache = {}

        def get_method_line_count(
            details_cfg,
            java_folder,
            classname,
            methodname,
            java_file_path=None,
            line_cache=None,
            include_package_private=False,
            count_empty_lines=True,
            parameter_signature=None,
            parameter_arity=None,
            parameter_types=None
        ):
            """
            Robust LOC counter for a Java method/constructor.
            Java 8 version: class_regex excludes 'record' and 'sealed'/'non-sealed'.
            Return type annotations use plain Optional[int] (no union `|` syntax).
            """
            classname = str(classname).strip()
            methodname = str(methodname).strip()

            cache_key = (
                (java_file_path or "").lower(),
                classname.lower(),
                methodname.lower(),
                include_package_private,
                count_empty_lines,
                str(parameter_arity),
                str(parameter_types)
            )
            if line_cache is not None and cache_key in line_cache:
                return line_cache[cache_key]

            if not java_file_path:
                target_filename = "{}{}".format(classname, details_cfg.get('extension', '.java')).lower()
                for root, _, files in os.walk(java_folder):
                    for file in files:
                        if file.lower() == target_filename:
                            java_file_path = os.path.join(root, file)
                            break
                    if java_file_path:
                        break

            if not java_file_path:
                if line_cache is not None:
                    line_cache[cache_key] = None
                return None

            try:
                with open(java_file_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception:
                with open(java_file_path, "r", encoding="latin-1") as f:
                    text = f.read()

            text = text.replace("\r\n", "\n").replace("\r", "\n")
            lines = text.split("\n")

            # ------------------------------------------------------------------
            # Helpers: comment/string-aware scanning
            # ------------------------------------------------------------------

            def find_matching_brace_from(pos):
                # type: (int) -> Optional[int]
                depth = 0
                i = pos
                in_block_comment = False
                in_line_comment = False
                in_string = False
                string_char = None
                while i < len(text):
                    ch = text[i]
                    nxt = text[i + 1] if i + 1 < len(text) else ""

                    if in_block_comment:
                        if ch == "*" and nxt == "/":
                            in_block_comment = False
                            i += 2
                            continue
                        i += 1
                        continue
                    if in_line_comment:
                        if ch == "\n":
                            in_line_comment = False
                        i += 1
                        continue
                    if in_string:
                        if ch == "\\":
                            i += 2
                            continue
                        if ch == string_char:
                            in_string = False
                            string_char = None
                        i += 1
                        continue

                    if ch == "/" and nxt == "*":
                        in_block_comment = True
                        i += 2
                        continue
                    if ch == "/" and nxt == "/":
                        in_line_comment = True
                        i += 2
                        continue
                    if ch in ("'", '"'):
                        in_string = True
                        string_char = ch
                        i += 1
                        continue

                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            return i
                    i += 1
                return None

            def find_method_terminator(from_pos):
                in_block_comment = False
                in_line_comment = False
                in_string = False
                string_char = None
                i = from_pos

                while i < len(text):
                    ch = text[i]
                    nxt = text[i + 1] if i + 1 < len(text) else ""

                    if in_block_comment:
                        if ch == "*" and nxt == "/":
                            in_block_comment = False
                            i += 2
                            continue
                        i += 1
                        continue

                    if in_line_comment:
                        if ch == "\n":
                            in_line_comment = False
                        i += 1
                        continue

                    if in_string:
                        if ch == "\\":
                            i += 2
                            continue
                        if ch == string_char:
                            in_string = False
                            string_char = None
                        i += 1
                        continue

                    if ch == "/" and nxt == "*":
                        in_block_comment = True
                        i += 2
                        continue
                    if ch == "/" and nxt == "/":
                        in_line_comment = True
                        i += 2
                        continue
                    if ch in ("'", '"'):
                        in_string = True
                        string_char = ch
                        i += 1
                        continue

                    if ch in ("{", ";"):
                        return ch, i

                    i += 1

                return None, None

            def find_matching_paren_from(pos):
                # type: (int) -> Optional[int]
                i = pos
                depth = 0
                in_block_comment = in_line_comment = in_string = False
                string_char = None
                angle_depth = 0
                while i < len(text):
                    ch = text[i]
                    nxt = text[i + 1] if i + 1 < len(text) else ""

                    if in_block_comment:
                        if ch == "*" and nxt == "/":
                            in_block_comment = False
                            i += 2
                            continue
                        i += 1
                        continue
                    if in_line_comment:
                        if ch == "\n":
                            in_line_comment = False
                        i += 1
                        continue
                    if in_string:
                        if ch == "\\":
                            i += 2
                            continue
                        if ch == string_char:
                            in_string = False
                            string_char = None
                        i += 1
                        continue

                    if ch == "/" and nxt == "*":
                        in_block_comment = True
                        i += 2
                        continue
                    if ch == "/" and nxt == "/":
                        in_line_comment = True
                        i += 2
                        continue
                    if ch in ("'", '"'):
                        in_string = True
                        string_char = ch
                        i += 1
                        continue

                    if ch == "<":
                        angle_depth += 1
                        i += 1
                        continue
                    if ch == ">" and angle_depth > 0:
                        angle_depth -= 1
                        i += 1
                        continue

                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            return i
                    i += 1
                return None

            def compute_arity_and_simple_types(param_region):
                # type: (str) -> Tuple[int, List[str]]
                s = re.sub(r'@\w+(?:\([^)]*\))?', '', param_region)
                s = re.sub(r'<[^>]*>', '', s)
                s = s.replace("\r", "").replace("\n", " ")

                parts, buf, par = [], "", 0
                for ch in s:
                    if ch == "(":
                        par += 1
                        buf += ch
                    elif ch == ")":
                        par = max(0, par - 1)
                        buf += ch
                    elif ch == "," and par == 0:
                        parts.append(buf.strip())
                        buf = ""
                    else:
                        buf += ch
                if buf.strip():
                    parts.append(buf.strip())

                if len(parts) == 1 and parts[0] == "":
                    return 0, []

                types = []
                for p in parts:
                    p = p.split("=", 1)[0].strip()
                    p = p.replace("...", "[]")
                    p = re.sub(r'\b(final|volatile|transient)\b', '', p)
                    toks = re.findall(r'[A-Za-z_]\w+|\[\]', p)
                    if not toks:
                        types.append("")
                        continue
                    arr = ""
                    while toks and toks[-1] == "[]":
                        arr += "[]"
                        toks.pop()
                    if not toks:
                        types.append(arr or "")
                        continue
                    _name = toks.pop()
                    type_tok = next((t for t in reversed(toks) if t != "[]"), "")
                    types.append((type_tok or "") + arr)

                arity = 0 if (len(parts) == 1 and parts[0] == "") else len(parts)
                return arity, [t for t in types]

            # ============================================================
            # 1) Match the target class/interface/enum in the file
            #    Java 8: no 'record', no 'sealed', no 'non-sealed'
            # ============================================================
            _anno_arg = r'(?:\([^()]*(?:\([^()]*\)[^()]*)*\))?'
            _anno_prefix = r'(?:@\w+' + _anno_arg + r'[ \t]*\n?[ \t]*)*'
            # Java 8: only class / interface / enum (no record)
            class_kw = r"(?:class|interface|enum)"
            class_regex = re.compile(
                r"(?m)^[ \t]*" + _anno_prefix +
                r"(?:public|protected|private)?[ \t]*" +
                r"(?:(?:abstract|final|static|strictfp)[ \t]+)*" +
                class_kw + r"[ \t]+" + re.escape(classname) + r"\b"
            )
            class_match = class_regex.search(text)
            if not class_match:
                class_regex_fallback = re.compile(
                    _anno_prefix +
                    r"(?:public|protected|private)?[ \t]*" +
                    r"(?:(?:abstract|final|static|strictfp)[ \t]+)*" +
                    class_kw + r"[ \t]+" + re.escape(classname) + r"\b"
                )
                class_match = class_regex_fallback.search(text)
            if not class_match:
                if line_cache is not None:
                    line_cache[cache_key] = None
                return None

            class_decl_end = class_match.end()
            class_open = text.find("{", class_decl_end)
            if class_open == -1:
                if line_cache is not None:
                    line_cache[cache_key] = 1
                return 1

            class_close = find_matching_brace_from(class_open)
            if class_close is None:
                class_close = len(text) - 1

            class_block = text[class_open:class_close + 1]
            class_block_global_start = class_open
            class_block_start_line = text.count("\n", 0, class_open) + 1

            # ============================================================
            # 2) Find the method/constructor signature in the class block
            # ============================================================
            access_req = r"(?:public|private|protected)"
            access = r"(?:" + access_req + r")?" if include_package_private else access_req
            # Java 8: no 'sealed', 'non-sealed' modifiers
            modifiers = r"(?:(?:static|final|abstract|synchronized|native|strictfp|default)\b[ \t]*)*"
            methodname_esc = re.escape(methodname)

            method_decl_regex = re.compile(
                r"(?m)^[ \t]*" + access + r"[ \t]*" + modifiers +
                r"(?:<[^>]*>\s*)?" +
                r"[A-Za-z_][\w.<>\[\],\s?]*\s+" +
                methodname_esc + r"[ \t]*\(",
                re.IGNORECASE
            )

            ctor_decl_regex = re.compile(
                r"(?m)^[ \t]*" + access + r"[ \t]*" + modifiers +
                r"\b" + re.escape(classname) + r"[ \t]*\(",
                re.IGNORECASE
            )

            matches = (
                list(ctor_decl_regex.finditer(class_block))
                if methodname == classname
                else list(method_decl_regex.finditer(class_block))
            )

            if not matches:
                def _make_interface_method_regex(mname):
                    anno_arg = r'(?:\([^()]*(?:\([^()]*\)[^()]*)*\))?'
                    anno_line = r'(?:^[ \t]*@\w+' + anno_arg + r'[ \t]*(?:\n|\Z))*'
                    ret_type = r'[A-Za-z_][\w$]*(?:\s*<[^;{]*?>)?(?:\s*\[\s*\])*'
                    param = r'[^;{]*?'
                    return re.compile(
                        r"(?ms)" +
                        anno_line +
                        r"^[ \t]*(?:(?:public|protected|private|default|static|abstract)\s+)*" +
                        ret_type + r"\s+" +
                        re.escape(mname) + r"[ \t]*\(" + param + r"\)" +
                        r"(?:\s+throws\s+[^;{]+)?[ \t]*;",
                        re.IGNORECASE
                    )

                interface_match = _make_interface_method_regex(methodname).search(class_block)

                if interface_match:
                    start_line = text.count(
                        "\n", 0, class_block_global_start + interface_match.start()
                    ) + 1
                    end_line = text.count(
                        "\n", 0, class_block_global_start + interface_match.end()
                    ) + 1
                    loc = max(1, end_line - start_line + 1)
                    if line_cache is not None:
                        line_cache[cache_key] = loc
                    return loc

                if line_cache is not None:
                    line_cache[cache_key] = None
                return None

            # ============================================================
            # 3) For EACH candidate overload, compute LOC + signature info
            # ============================================================
            def compute_loc_for_match(m_match):
                sig_global_start = class_block_global_start + m_match.start()
                sig_global_end = class_block_global_start + m_match.end()
                sig_line_idx = text.count("\n", 0, sig_global_start) + 1

                def anno_block_start(signature_line_index):
                    i = signature_line_index - 2
                    if i < 0:
                        return None
                    paren_balance = 0
                    started = False
                    start_line = None
                    while i >= class_block_start_line - 1:
                        raw = lines[i]
                        line = raw.rstrip()
                        if not line.strip() and not (started and paren_balance > 0):
                            break
                        is_anno = line.lstrip().startswith("@")
                        if not started:
                            if is_anno:
                                started = True
                                start_line = i + 1
                                paren_balance = line.count("(") - line.count(")")
                            else:
                                break
                        else:
                            if is_anno or paren_balance > 0:
                                start_line = i + 1
                                paren_balance += line.count("(") - line.count(")")
                            else:
                                break
                        i -= 1
                    return start_line

                start_line_idx = anno_block_start(sig_line_idx) or sig_line_idx

                terminator, term_pos = find_method_terminator(sig_global_end)

                if terminator == ";":
                    end_line_idx = text.count("\n", 0, term_pos) + 1
                    if count_empty_lines:
                        return max(1, end_line_idx - start_line_idx + 1)
                    else:
                        segment = lines[start_line_idx - 1:end_line_idx]
                        return max(1, sum(1 for ln in segment if ln.strip()))

                if terminator != "{":
                    return 1

                brace_open_pos = term_pos
                brace_close_pos = find_matching_brace_from(brace_open_pos)
                if brace_close_pos is None:
                    brace_close_pos = len(text) - 1

                end_line_idx = text.count("\n", 0, brace_close_pos) + 1

                if count_empty_lines:
                    return max(1, end_line_idx - start_line_idx + 1)
                else:
                    segment = lines[start_line_idx - 1:end_line_idx]
                    return max(1, sum(1 for ln in segment if ln.strip()))

            candidates = []
            for m_match in matches:
                paren_open_pos = class_block_global_start + m_match.end() - 1
                paren_close_pos = find_matching_paren_from(paren_open_pos)
                if paren_close_pos is None:
                    loc = compute_loc_for_match(m_match)
                    candidates.append({"arity": None, "types": [], "loc": loc})
                    continue
                param_region = text[paren_open_pos + 1:paren_close_pos]
                m_arity, m_types = compute_arity_and_simple_types(param_region)
                loc = compute_loc_for_match(m_match)
                candidates.append({"arity": m_arity, "types": m_types, "loc": loc})

            target_arity = None
            if parameter_arity is not None:
                try:
                    target_arity = int(parameter_arity)
                except Exception:
                    target_arity = None

            target_types = [t.strip() for t in str(parameter_types or "").split(";") if t and t.strip()]

            def simple_equal(a, b):
                def norm(x):
                    x = (x or "").strip()
                    x = x.split(".")[-1]
                    x = re.sub(r'\[]+$', '[]', x)
                    return x.lower()
                return norm(a) == norm(b)

            best_loc = None
            if candidates:
                pool = candidates

                if target_arity is not None:
                    pool = [c for c in pool if c["arity"] == target_arity] or pool

                if len(pool) > 1 and target_types:
                    def score(c):
                        if not c["types"] or len(c["types"]) != len(target_types):
                            return -1
                        return sum(1 for i in range(len(target_types)) if simple_equal(c["types"][i], target_types[i]))
                    scored = [(score(c), c) for c in pool]
                    max_score = max(s for s, _ in scored)
                    pool = [c for s, c in scored if s == max_score]

                best_loc = max(c["loc"] for c in pool)

            if line_cache is not None:
                line_cache[cache_key] = best_loc
            return best_loc

        def extract_loc_any(row):
            classname = str(row["class_interface_name"]).strip()
            methodname = str(row["method_name"]).strip()

            if methodname.lower() in SYSTEM_METHODS:
                return None

            java_file_path = type_to_path_full.get(classname)

            return get_method_line_count(
                details_cfg=details,
                java_folder=app_folder,
                classname=classname,
                methodname=methodname,
                java_file_path=java_file_path,
                line_cache=loc_cache,
                include_package_private=True,
                count_empty_lines=True,
                parameter_signature=row.get("Parameters", None),
                parameter_arity=row.get("Parameter_Arity", None),
                parameter_types=row.get("Parameter_Types", None)
            )

        df_unique_methods["Number_Of_Lines"] = df_unique_methods.apply(extract_loc_any, axis=1)

        desired_cols = [
            "class_method_key",
            "class_interface_name", "method_name",
            "Parameters", "Parameter_Arity", "Parameter_Types",
            "Annotations", "return_type", "Method_Declaration_Type",
            "Number_Of_Lines",
        ]
        existing_cols = [c for c in desired_cols if c in df_unique_methods.columns]
        df_unique_methods = df_unique_methods[existing_cols].reset_index(drop=True)

        df_unique_methods.insert(0, "Method ID", ["M{}".format(str(i + 1).zfill(4)) for i in range(len(df_unique_methods))])

        def _strip_parens_preserve(s):
            if not isinstance(s, str):
                return s
            return re.sub(r'\(\s*[^)]*\)', '', s)

        def _unescape_html(s):
            if not isinstance(s, str):
                return s
            return html.unescape(s)

        for col in ['object_call', 'class_method_call', 'class_interface_name', 'return_type']:
            if col in df_clean_exploded.columns:
                df_clean_exploded[col] = df_clean_exploded[col].apply(_strip_parens_preserve).apply(_unescape_html)

        df_application_properties = adapter.extract_application_properties_from_folder(app_folder)

        if not os.path.exists(all_methods):
            with pd.ExcelWriter(all_methods, engine="openpyxl", mode="w") as writer:
                pd.DataFrame({"init": []}).to_excel(writer, sheet_name="Init", index=False)

        with pd.ExcelWriter(all_methods, engine="openpyxl", mode="a", if_sheet_exists="new") as writer:
            df_clean_exploded.to_excel(writer, sheet_name="Cleaned_AST_Details", index=False)
            df_application_properties.to_excel(writer, sheet_name="application.properties", index=False)

        return os.path.abspath(all_methods)

    all_methods = clean_and_write(pd.DataFrame(ast_results))

    # ============================================================
    # Dependency generation
    # ============================================================

    # def input_generation_for_dependency():

    #     def _strip_generics(name):
    #         if not isinstance(name, str):
    #             name = str(name)
    #         s = re.sub(r'\s*&amp;amp;lt;[^&amp;amp;gt]+&amp;amp;gt;\s*', '', name)
    #         s = re.sub(r'\s*&amp;lt;[^&amp;gt;]+&amp;gt;\s*', '', s)
    #         s = re.sub(r'\s*&lt;[^&gt;]+&gt;\s*', '', s)
    #         s = re.sub(r'\s*<[^>]+>\s*', '', s)
    #         s = s.replace('[]', '')
    #         return s.strip()

    #     def _lookup_type_dep(base, parent_class, file_name, object_class_map_dep, method_return_index_dep):
    #         b = _strip_generics(base).strip()
    #         if not b:
    #             return _strip_generics(parent_class)
    #         if b.lower() in {"this", "super", "return", "new"}:
    #             return _strip_generics(parent_class)
    #         t_scoped = object_class_map_dep.get((str(file_name).lower(), b.lower()))
    #         if t_scoped:
    #             return _strip_generics(t_scoped)
    #         t_global = object_class_map_dep.get(b.lower())
    #         if t_global:
    #             return _strip_generics(t_global)
    #         if b and b[0].isupper():
    #             return _strip_generics(b)
    #         return _strip_generics(parent_class)

    #     def _strip_comments_and_literals_dep(text):
    #         return re.sub(
    #             r'//.*?$|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])\'',
    #             '',
    #             text,
    #             flags=re.S | re.M
    #         )

    #     def _extract_braced_block(src, start_idx):
    #         if start_idx < 0 or start_idx >= len(src) or src[start_idx] != '{':
    #             return ''
    #         depth, i = 1, start_idx + 1
    #         while i < len(src) and depth > 0:
    #             c = src[i]
    #             if c == '{':
    #                 depth += 1
    #             elif c == '}':
    #                 depth -= 1
    #             i += 1
    #         return src[start_idx + 1:i - 1] if depth == 0 else ''

    #     def _interface_methods_from_source(code, type_name):
    #         m = re.search(r'\binterface\s+' + re.escape(type_name) + r'\b', code)
    #         if not m:
    #             return []
    #         brace_open = code.find('{', m.end())
    #         if brace_open == -1:
    #             return []
    #         block = _extract_braced_block(code, brace_open)
    #         if not block:
    #             return []
    #         core = _strip_comments_and_literals_dep(block)
    #         methods = []

    #         for ret_type, name in re.findall(
    #             r'(?:^|\s)'
    #             r'(?:public|protected|private|static|abstract|default|strictfp|final|native|synchronized|transient|volatile|\s)*'
    #             r'(?:&lt;[^&gt;]+&gt;\s*)?'
    #             r'([\w\.\[\]&lt;&gt;?,\s]+?)\s+'
    #             r'([A-Za-z_]\w*)\s*\([^;{]*\)\s*;',
    #             core
    #         ):
    #             methods.append((name, _strip_generics(ret_type)))

    #         for ret_type, name in re.findall(
    #             r'(?:^|\s)'
    #             r'(?:public|protected|private|static|abstract|default|strictfp|final|native|synchronized|transient|volatile|\s)*'
    #             r'(?:&lt;[^&gt;]+&gt;\s*)?'
    #             r'([\w\.\[\]&lt;&gt;?,\s]+?)\s+'
    #             r'([A-Za-z_]\w*)\s*\([^;{]*\)\s*\{',
    #             core
    #         ):
    #             methods.append((name, _strip_generics(ret_type)))

    #         seen = {}
    #         for name, rt in methods:
    #             seen.setdefault(name, rt or None)
    #         return [(n, seen[n]) for n in sorted(seen.keys())]

    #     def _class_decl_info_from_source(code, type_name):
    #         m = re.search(r'\bclass\s+' + re.escape(type_name) + r'\b', code)
    #         if not m:
    #             return (None, [])
    #         brace_open = code.find('{', m.end())
    #         if brace_open == -1:
    #             header = code[m.end(): m.end() + 300]
    #         else:
    #             header = code[m.end(): brace_open]
    #         header = _strip_comments_and_literals_dep(header)

    #         ext = None
    #         m_ext = re.search(r'\bextends\s+([A-Za-z_][\w\.\$<>?,\s]*)', header)
    #         if m_ext:
    #             ext_parts = _strip_generics(m_ext.group(1)).split()
    #             ext = ext_parts[0] if ext_parts else None
    #             if ext:
    #                 ext = ext.split('.')[-1]

    #         impls = []
    #         m_impl = re.search(r'\bimplements\s+([A-Za-z_][\w\.\$<>\?,\s,]*)', header)
    #         if m_impl:
    #             raw = m_impl.group(1)
    #             parts = [p.strip() for p in raw.split(',') if p.strip()]
    #             for p in parts:
    #                 p2 = _strip_generics(p).split('.')[-1]
    #                 if p2:
    #                     impls.append(p2)

    #         return (ext, impls)

    #     type_kind_map = {}

    #     def is_interface_type(type_name_check):
    #         return str(type_kind_map.get(_strip_generics(type_name_check), '')).lower() == 'interface'

    #     def _resolve_chained_segments(call, parent_class, file_name, object_class_map_dep, method_return_index_dep):
    #         if not isinstance(call, str):
    #             call = str(call)
    #         s = call.strip()
    #         if not s:
    #             return []

    #         m_ctor = re.match(r'^\s*([A-Za-z_]\w*)\s*\.\s*\1\s*\(\s*\)\s*$', s)
    #         if m_ctor:
    #             cls = _strip_generics(m_ctor.group(1))
    #             return ["{}.{}".format(cls, cls)]

    #         m_unq = re.match(r'^\s*([A-Za-z_]\w*)\s*(\(\s*\))?\s*$', s)
    #         if m_unq and '.' not in s:
    #             meth = m_unq.group(1)
    #             pcls = _strip_generics(parent_class)
    #             has = method_return_index_dep.get(pcls, {}).get(meth) is not None
    #             if has:
    #                 return ["{}.{}".format(pcls, meth)]
    #             if is_interface_type(pcls):
    #                 return ["{}.{}".format(pcls, meth)]
    #             return []

    #         first_dot = s.find('.')
    #         if first_dot == -1:
    #             return []

    #         base = s[:first_dot].strip()
    #         rest = s[first_dot + 1:]

    #         methods = re.findall(r'([A-Za-z_]\w*)\s*\(', rest)
    #         if not methods:
    #             m2 = re.match(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*$', s)
    #             if m2:
    #                 cls = _lookup_type_dep(m2.group(1), parent_class, file_name, object_class_map_dep, method_return_index_dep)
    #                 meth = m2.group(2)
    #                 return ["{}.{}".format(cls, meth)]
    #             return []

    #         if '(' in base or ')' in base:
    #             current_class = _strip_generics(parent_class)
    #         else:
    #             current_class = _lookup_type_dep(base, parent_class, file_name, object_class_map_dep, method_return_index_dep)

    #         segments = []
    #         for meth in methods:
    #             cls_now = _strip_generics(current_class)
    #             segments.append("{}.{}".format(cls_now, meth))
    #             ret = method_return_index_dep.get(cls_now, {}).get(meth)
    #             if not ret or str(ret).lower() == 'void':
    #                 break
    #             next_cls = _strip_generics(str(ret).split('.')[-1])
    #             current_class = next_cls

    #         return segments

    #     print("method_dependency_generating")

    #     regex = data["Language"][technology]["Application"][application]["Regex_Pattern"]
    #     adapter.configure(
    #         details=details,
    #         regex=regex,
    #         include_unqualified=include_unqualified,
    #         accept_local_new_types=accept_local_new_types,
    #         accept_parameter_types=accept_parameter_types,
    #         accept_same_package=accept_same_package
    #     )

    #     object_class_map_dep = adapter.build_object_class_map(app_folder)
    #     method_return_index_dep = adapter.build_method_return_index(app_folder)

    #     parent_children = {}
    #     class_to_file = {}
    #     implements_map = {}
    #     interface_methods_index = {}

    #     for root_dir, _, files in os.walk(app_folder):
    #         for file in files:
    #             if not file.endswith(adapter.file_extension()):
    #                 continue

    #             file_path = os.path.join(root_dir, file)
    #             try:
    #                 with open(file_path, "r", encoding="utf-8") as fh:
    #                     code_raw = fh.read()
    #             except Exception:
    #                 try:
    #                     with open(file_path, "r", encoding="latin-1") as fh:
    #                         code_raw = fh.read()
    #                 except Exception:
    #                     continue

    #             code_cleaned = strip_top_level_comments(code_raw)
    #             code = html.unescape(code_cleaned)
    #             code_for_calls = _strip_comments_and_literals_dep(code)
    #             ast = adapter.parse_ast(code)

    #             if not ast:
    #                 continue

    #             for type_name, type_kind, type_node in adapter.get_declared_types(ast):
    #                 tn = _strip_generics(type_name)
    #                 tk = str(type_kind).lower()
    #                 type_kind_map[tn] = tk

    #                 if tk == "class":
    #                     class_to_file[tn] = file
    #                     _, impls = _class_decl_info_from_source(code, tn)
    #                     if impls:
    #                         implements_map[tn] = impls

    #                 if tk == "interface":
    #                     iface_methods = _interface_methods_from_source(code, tn)
    #                     if iface_methods:
    #                         method_return_index_dep.setdefault(tn, {})
    #                         names_only = set()
    #                         for mname, rtype in iface_methods:
    #                             names_only.add(mname)
    #                             key_seed = (file, tn, mname)
    #                             parent_children.setdefault(key_seed, set())
    #                             if method_return_index_dep[tn].get(mname) is None and rtype:
    #                                 method_return_index_dep[tn][mname] = rtype
    #                         interface_methods_index[tn] = interface_methods_index.get(tn, set()) | names_only

    #                 for method_name, method_node in adapter.get_methods_in_type(type_node):
    #                     try:
    #                         def _is_commented(code_inner, line_no):
    #                             lines_inner = code_inner.splitlines()
    #                             if line_no < 0 or line_no >= len(lines_inner):
    #                                 return False
    #                             line_inner = lines_inner[line_no].strip()
    #                             return line_inner.startswith("//") or line_inner.startswith("/*") or line_inner.startswith("*")

    #                         pos = method_node.position
    #                         if pos and _is_commented(code, pos[1] - 1):
    #                             continue
    #                     except Exception:
    #                         pass

    #                     key = (file, tn, method_name)
    #                     bucket = parent_children.setdefault(key, set())

    #                     try:
    #                         raw_calls = adapter.find_calls_in_method(type_node, method_node, code_for_calls) or []
    #                     except Exception:
    #                         raw_calls = []

    #                     raw_calls = [c for c in raw_calls if isinstance(c, str) and c.strip() and not adapter.is_system_call(c)]

    #                     for c in raw_calls:
    #                         c_norm = re.sub(r"\(\s*\)", "()", c)
    #                         for seg in _resolve_chained_segments(c_norm, tn, file, object_class_map_dep, method_return_index_dep):
    #                             bucket.add(seg)

    #     for cls, ifaces in implements_map.items():
    #         file_name = class_to_file.get(cls)
    #         if not file_name:
    #             continue
    #         for iface in ifaces:
    #             method_names = set()
    #             if iface in interface_methods_index:
    #                 method_names |= interface_methods_index[iface]
    #             else:
    #                 for k in interface_methods_index.keys():
    #                     if k.split('.')[-1] == iface:
    #                         method_names |= interface_methods_index[k]
    #             if not method_names:
    #                 continue
    #             for mname in method_names:
    #                 key = (file_name, cls, mname)
    #                 parent_children.setdefault(key, set())

    #     existing_parent_keys = set(parent_children.keys())

    #     for (file_name, class_name, parent_method), children_set in list(parent_children.items()):
    #         for child in children_set:
    #             if not isinstance(child, str) or '.' not in child:
    #                 continue
    #             cls, meth = child.split('.', 1)
    #             cls = _strip_generics(cls)
    #             cls_file = class_to_file.get(cls)
    #             if not cls_file:
    #                 continue
    #             k = (cls_file, cls, meth)
    #             if k not in existing_parent_keys:
    #                 parent_children.setdefault(k, set())
    #                 existing_parent_keys.add(k)

    #     rows = []
    #     for (file_name, class_name, parent_method), children_set in sorted(parent_children.items()):
    #         children_sorted = sorted(children_set)
    #         child_joined = ", ".join(children_sorted)
    #         rows.append({
    #             "file_name": file_name,
    #             "class": class_name,
    #             "parent_method": "{}.{}".format(class_name, parent_method),
    #             "child_method": child_joined,
    #             "used/un_used": "un_used"
    #         })

    #     controllers = [
    #         os.path.splitext(os.path.basename(p))[0]
    #         for p in (controller_files or [])
    #         if isinstance(p, str) and p.strip()
    #     ]
    #     print("coverage controller_files : ", controllers)

    #     df_out = pd.DataFrame(
    #         rows, columns=["file_name", "class", "parent_method", "child_method", "used/un_used"]
    #     )

    #     if controllers:
    #         mask_mark_used = df_out["class"].isin(controllers)
    #         df_out.loc[mask_mark_used, "used/un_used"] = "used"

    #     with open(groups, "r", encoding="utf-8") as f:
    #         group_data = json.load(f)

    #     java_paths = (
    #         group_data.get("groups", {})
    #         .get("rule", {})
    #         .get("files")
    #     )

    #     rules = []
    #     if isinstance(java_paths, (list, tuple)):
    #         rules = [
    #             os.path.splitext(os.path.basename(p))[0]
    #             for p in java_paths
    #             if isinstance(p, str) and p.strip()
    #         ]
    #     elif java_paths is None:
    #         rules = []
    #     else:
    #         raise TypeError("'groups.rule.files' must be a list of strings; got {}".format(type(java_paths)))

    #     if rules:
    #         mask_mark_used = df_out["class"].isin(rules)
    #         df_out.loc[mask_mark_used, "used/un_used"] = "used"

    #     out_dir = OUTPUT_DIR or os.getcwd()
    #     os.makedirs(out_dir, exist_ok=True)

    #     if not method_dependency or not isinstance(method_dependency, str):
    #         raise ValueError("Expected a file name string for 'method_dependency'.")

    #     dependency_out_path = os.path.join(out_dir, method_dependency)
    #     print("dependency_out_path:", dependency_out_path)

    #     with pd.ExcelWriter(dependency_out_path, engine="openpyxl", mode="w") as writer:
    #         df_out.to_excel(writer, index=False, sheet_name="Sheet1")

    #     print("Dependency sheet created: {}".format(dependency_out_path))
    #     return dependency_out_path

    # dependency_input = input_generation_for_dependency()

    return all_methods