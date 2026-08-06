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
from datetime import datetime
import concurrent.futures
import multiprocessing
from collections import deque

def log_time(message):
    with open("execution_log_new.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} - {message}\n")


class LanguageAdapter:
    """
    Base interface for language-specific adapters.
    Concrete adapters (Java8Adapter, etc.) must implement these methods.
    """
    def configure(self, *, details, regex,
                  include_unqualified=True,
                  accept_local_new_types=True,
                  accept_parameter_types=True,
                  accept_same_package=True,
                  file_content_cache=None,
                  raw_ast_cache=None):
        self.details = details
        self.regex = regex
        self.include_unqualified = include_unqualified
        self.accept_local_new_types = accept_local_new_types
        self.accept_parameter_types = accept_parameter_types
        self.accept_same_package = accept_same_package
        # Shared caches so adapter index-builders never re-read a file
        self._file_content_cache = file_content_cache if file_content_cache is not None else {}
        self._raw_ast_cache = raw_ast_cache if raw_ast_cache is not None else {}

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

# ---------------------------------------------------------------------------
# Module-level worker for ProcessPoolExecutor
# Must be at module level (not a closure) so it can be pickled.
# ---------------------------------------------------------------------------

def _file_worker(args):
    """
    Process one Java source file in a subprocess.
    args = (file_path, adapter_module, adapter_class, adapter_kwargs, strip_fn_src)

    Returns (list_of_row_dicts, error_dict_or_None)
    Each row dict contains an extra '_type_name', '_method_name', '_calls' key
    that the main process uses to rebuild method_map / file_map.
    """
    import importlib, html as _html, re as _re, os as _os
    file_path, adapter_module_name, adapter_class_name, adapter_kwargs = args
    file = _os.path.basename(file_path)
    local_rows = []
    local_error = None

    # Re-instantiate the adapter in this subprocess
    try:
        mod = importlib.import_module(adapter_module_name)
        AdapterCls = getattr(mod, adapter_class_name)
        adapter = AdapterCls()
        adapter.configure(**adapter_kwargs)
    except Exception as e:
        return [], {'File': file_path, 'Error': f'Adapter init failed: {e}'}

    def _strip(text):
        if not isinstance(text, str):
            return ""
        return _re.sub(
            r'//.*?$|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
            '', text, flags=_re.MULTILINE | _re.DOTALL
        )

    def _is_commented(code, line_no):
        lines = code.splitlines()
        if line_no < 0 or line_no >= len(lines):
            return False
        line = lines[line_no].strip()
        return line.startswith("//") or line.startswith("/*") or line.startswith("*")

    def _read(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="latin-1") as fh:
                return fh.read()

    null_meta = {'Annotations': 'None', 'Method_Declaration_Type': 'Default',
                  'return_type': '', 'Parameters': '', 'Parameter_Arity': None,
                  'Parameter_Types': ''}

    def append_row(type_name, type_kind, method_name, meta, call, calls_list):
        local_rows.append({
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
            '_type_name': type_name,
            '_method_name': method_name,
            '_calls': calls_list,
        })

    try:
        code_raw = _read(file_path)
        code = _html.unescape(code_raw)
        code_no_comments = _strip(code)

        ast = adapter.parse_ast(code_no_comments)
        if not ast:
            raise RuntimeError("AST parse failed")

        declared_types = list(adapter.get_declared_types(ast))

        if not declared_types:
            fb = adapter.fallback_parse(code_raw)
            type_name = fb.get('type_name', 'Unknown')
            row_type = fb.get('row_type', 'Unknown')
            filtered_calls = fb.get('filtered_calls', [])
            for call in filtered_calls or ["None"]:
                append_row(type_name, row_type, "UnknownMethod", null_meta,
                           call, filtered_calls or ["None"])
            return local_rows, None

        for type_name, type_kind, type_node in declared_types:
            for method_name, method_node in adapter.get_methods_in_type(type_node):
                try:
                    pos = method_node.position
                    if pos and _is_commented(code, pos[1] - 1):
                        continue
                except Exception:
                    pass
                meta = adapter.extract_method_metadata(method_node)
                calls = adapter.find_calls_in_method(type_node, method_node, code_no_comments)
                calls = list(dict.fromkeys(calls)) if calls else ["None"]
                for call in calls:
                    append_row(type_name, type_kind, method_name, meta, call, calls)

    except Exception as e:
        local_error = {'File': file_path, 'Error': str(e)}
        try:
            code_raw = _read(file_path)
            code = _html.unescape(code_raw)
        except Exception as e2:
            return local_rows, [local_error,
                {'File': file_path, 'Error': f"Read error in fallback: {e2}"}]

        fb = adapter.fallback_parse(code_raw)
        type_name = fb.get('type_name', 'Unknown')
        row_type = fb.get('row_type', 'Unknown')

        if 'per_method_calls' in fb and fb['per_method_calls']:
            for rec in fb['per_method_calls']:
                method = rec.get('method_name') or 'UnknownMethod'
                call = rec.get('object_call') or 'None'
                local_rows.append({
                    'file_name': file, 'class_interface_name': type_name,
                    'type': row_type, 'method_name': method,
                    'Annotations': "None", 'Method_Declaration_Type': "Default",
                    'return_type': "", 'object_call': call,
                    'Parameters': '', 'Parameter_Arity': None, 'Parameter_Types': '',
                    '_type_name': type_name, '_method_name': method, '_calls': [call],
                })
        else:
            filtered_calls = fb.get('filtered_calls', [])
            for call in filtered_calls or ["None"]:
                local_rows.append({
                    'file_name': file, 'class_interface_name': type_name,
                    'type': row_type, 'method_name': "UnknownMethod",
                    'Annotations': "None", 'Method_Declaration_Type': "Default",
                    'return_type': "", 'object_call': call,
                    'Parameters': '', 'Parameter_Arity': None, 'Parameter_Types': '',
                    '_type_name': type_name, '_method_name': "UnknownMethod",
                    '_calls': filtered_calls or ["None"],
                })
    return local_rows, local_error


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
    start_time = datetime.now()
    log_time(f"Method lineage Generation START")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    regex = data["Language"][technology]["Application"][application]["Regex_Pattern"]

    ast_results = []
    method_map = {}
    file_map = {}
    errors = []

    # -----------------------------------------------------------
    # Performance caches and project file indexes
    # -----------------------------------------------------------
    file_content_cache = {}
    raw_ast_cache = {}

    adapter.configure(
        details=details,
        regex=regex,
        include_unqualified=include_unqualified,
        accept_local_new_types=accept_local_new_types,
        accept_parameter_types=accept_parameter_types,
        accept_same_package=accept_same_package,
        file_content_cache=file_content_cache,
        raw_ast_cache=raw_ast_cache,
    )
    file_name_to_path = {}

    valid_extensions = tuple(details.get("extension", []))

    if not valid_extensions:
        valid_extensions = (adapter.file_extension(),)

    # -----------------------------------------------------------
    # Controller-first BFS: discover only reachable files
    # -----------------------------------------------------------
    # Step 1: build a cheap class-name → path index (file stem, no parsing)
    _class_to_path = {}
    for _root, _, _files in os.walk(app_folder):
        for _f in _files:
            if _f.endswith(valid_extensions):
                _stem = os.path.splitext(_f)[0]
                _abs = os.path.abspath(os.path.join(_root, _f))
                _class_to_path.setdefault(_stem, _abs)
                # XxxImpl → also register as Xxx so callers of the interface find it
                if _stem.endswith("Impl"):
                    _class_to_path.setdefault(_stem[:-4], _abs)

    def _bfs_read(path):
        try:
            with open(path, "r", encoding="utf-8") as _fh:
                return _fh.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="latin-1") as _fh:
                return _fh.read()

    def _extract_class_name_from_call(call):
        """Return the leading UpperCase class name from a call string, or None."""
        if not isinstance(call, str) or '.' not in call:
            return None
        base = call.split('.')[0].strip()
        # Only treat tokens starting with an uppercase letter as class names
        if not base or not base[0].isupper():
            return None
        return base

    _visited_paths = set()
    java_files = []          # ordered list of reachable abs paths
    _bfs_queue = deque()

    def _enqueue(path):
        abs_p = os.path.abspath(path)
        if abs_p not in _visited_paths and os.path.isfile(abs_p):
            _visited_paths.add(abs_p)
            java_files.append(abs_p)
            _bfs_queue.append(abs_p)

    # Seed from controller_files
    for _cf in (controller_files or []):
        _enqueue(_cf)

    # Step 2: BFS — parse each file, extract callees, enqueue their files
    _strip_for_bfs = lambda text: re.sub(
        r'//.*?$|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
        '', text, flags=re.MULTILINE | re.DOTALL
    )

    while _bfs_queue:
        _cur = _bfs_queue.popleft()
        try:
            _raw = _bfs_read(_cur)
        except Exception as _e:
            log_time(f"BFS: cannot read {_cur}: {_e}")
            continue

        _code = html.unescape(_raw)
        _code_clean = _strip_for_bfs(_code)
        _raw_calls = []

        try:
            _ast = adapter.parse_ast(_code_clean)
            if _ast:
                for _, _, _type_node in adapter.get_declared_types(_ast):
                    for _, _method_node in adapter.get_methods_in_type(_type_node):
                        _raw_calls.extend(
                            adapter.find_calls_in_method(_type_node, _method_node, _code_clean) or []
                        )
            else:
                raise RuntimeError("AST failed")
        except Exception:
            try:
                _fb = adapter.fallback_parse(_raw)
                for _rec in _fb.get('per_method_calls', []):
                    _c = _rec.get('object_call')
                    if _c:
                        _raw_calls.append(_c)
                for _c in _fb.get('filtered_calls', []):
                    if _c:
                        _raw_calls.append(_c)
            except Exception as _e2:
                log_time(f"BFS fallback failed for {_cur}: {_e2}")

        for _call in _raw_calls:
            _cls = _extract_class_name_from_call(_call)
            if _cls:
                _dep = _class_to_path.get(_cls)
                if _dep:
                    _enqueue(_dep)

    log_time(f"BFS complete: {len(java_files)} reachable files from {len(controller_files or [])} controller(s)")

    # Build O(1) filename → path lookup (used by LOC resolver later)
    for _fp in java_files:
        file_name_to_path.setdefault(os.path.basename(_fp).lower(), _fp)

    def read_file_cached(file_path):
        """
        Read every source file only once during one method_lineage run.
        """
        if file_path in file_content_cache:
            return file_content_cache[file_path]

        try:
            with open(file_path, "r", encoding="utf-8") as source_file:
                content = source_file.read()
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="latin-1") as source_file:
                content = source_file.read()

        file_content_cache[file_path] = content
        return content

    def parse_raw_ast_cached(file_path):
        """
        Parse the raw Java source only once.

        This cache is intentionally separate from adapter.parse_ast(),
        because the adapter receives comment/literal-stripped source.
        """
        if file_path not in raw_ast_cache:
            raw_ast_cache[file_path] = javalang.parse.parse(
                read_file_cached(file_path)
            )

        return raw_ast_cache[file_path]

    # ------------------ Pre-build indexes in parallel (threads) ------------------
    # Index builds are I/O-bound (file read) + CPU (javalang parse).
    # They run in threads alongside the ProcessPoolExecutor below.
    # They use the shared file_content_cache / raw_ast_cache injected via configure().
    _index_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    _ocm_future = _index_executor.submit(adapter.build_object_class_map, app_folder)
    _mri_future = _index_executor.submit(adapter.build_method_return_index, app_folder)

    # ------------------ Walk all files with ProcessPoolExecutor ------------------
    # ProcessPoolExecutor spawns real OS subprocesses → bypasses the GIL →
    # javalang.parse.parse() truly runs in parallel across all CPU cores.
    #
    # Workers use the module-level _file_worker function (picklable).
    # Each worker receives plain serialisable data (no shared state).
    # Results are merged back into the main process.

    # Build the adapter config dict to pass to each worker subprocess.
    # Only serialisable primitives — no in-memory caches (can't cross process boundary).
    _adapter_module   = type(adapter).__module__
    _adapter_class    = type(adapter).__name__
    _adapter_kwargs   = dict(
        details=adapter.details,
        regex=adapter.regex,
        include_unqualified=adapter.include_unqualified,
        accept_local_new_types=adapter.accept_local_new_types,
        accept_parameter_types=adapter.accept_parameter_types,
        accept_same_package=adapter.accept_same_package,
        # Caches not passed — each worker has its own private cache
    )

    _cpu = multiprocessing.cpu_count() or 4
    # Cap workers: more than cpu_count gives no benefit for CPU-bound work;
    # very large pools waste memory on 5000-file codebases.
    _max_proc_workers = min(_cpu, 16)

    _worker_args = [
        (fp, _adapter_module, _adapter_class, _adapter_kwargs)
        for fp in java_files
    ]

    # Use 'spawn' context explicitly — safer on macOS/Windows and avoids
    # fork-related deadlocks with javalang's thread-local state.
    _mp_ctx = multiprocessing.get_context('spawn')

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=_max_proc_workers,
        mp_context=_mp_ctx,
    ) as _proc_pool:
        _futures = {
            _proc_pool.submit(_file_worker, arg): arg[0]
            for arg in _worker_args
        }
        for _fut in concurrent.futures.as_completed(_futures):
            _file_path = _futures[_fut]
            _file = os.path.basename(_file_path)
            try:
                _rows, _err = _fut.result()
            except Exception as _exc:
                errors.append({'File': _file_path, 'Error': str(_exc)})
                continue

            if _err:
                if isinstance(_err, list):
                    errors.extend(_err)
                else:
                    errors.append(_err)

            for _row in _rows:
                _type_name   = _row.pop('_type_name',   _row.get('class_interface_name', 'Unknown'))
                _method_name = _row.pop('_method_name',  _row.get('method_name', 'UnknownMethod'))
                _calls       = _row.pop('_calls', [])
                # Also populate main-process file_content_cache for LOC computation
                if _file_path not in file_content_cache:
                    try:
                        file_content_cache[_file_path] = read_file_cached(_file_path)
                    except Exception:
                        pass
                file_map.setdefault(_type_name, _file)
                method_map.setdefault(_type_name, {})
                method_map[_type_name][_method_name] = _calls
                ast_results.append(_row)

        # ---- Optional chain resolution ----
    # Build an inverted index: method_name → (type, calls) for O(1) lookup
    # instead of scanning all types on every resolve_chain call (was O(N²)).
    _method_to_type = {}  # method_name → first type that owns it
    for _typ, _methods in method_map.items():
        for _mname in _methods:
            _method_to_type.setdefault(_mname, _typ)

    chain_results = []

    def resolve_chain(current, visited):
        called_method = current.split('.')[-1] if '.' in current else current
        typ = _method_to_type.get(called_method)
        if typ is not None:
            calls = method_map[typ].get(called_method)
            file_name = file_map.get(typ, 'Unknown')
            if calls:
                for call in calls:
                    chain_results.append({'File Name': file_name, 'Method Name': current, 'Object Call': call})
                    if call not in visited:
                        visited.add(call)
                        resolve_chain(call, visited)
            else:
                chain_results.append({'File Name': file_name, 'Method Name': current, 'Object Call': ''})
        else:
            chain_results.append({'File Name': 'Unknown', 'Method Name': current, 'Object Call': ''})

    for typ in method_map:
        for method in method_map[typ]:
            file_name = file_map.get(typ, 'Unknown')
            for call in method_map[typ][method]:
                chain_results.append({'File Name': file_name, 'Method Name': method, 'Object Call': call})
                resolve_chain(call, {call})

    # ---- Cleaner: system-call filtering + mapping + chain explosion ----
    def clean_and_write(df, object_class_map=None, method_return_index=None):
        # Accept pre-built indexes (built in parallel) or build on-demand
        if object_class_map is None:
            object_class_map = adapter.build_object_class_map(app_folder)
        if method_return_index is None:
            method_return_index = adapter.build_method_return_index(app_folder)

        def build_interface_to_impl_map(source_files):
            iface_to_impl = {}

            for source_file_path in source_files:
                file = os.path.basename(source_file_path)

                if not file.endswith(".java"):
                    continue

                impl_name = os.path.splitext(file)[0]

                if impl_name.endswith("Impl"):
                    iface_name = impl_name[:-4]
                    iface_to_impl[iface_name] = impl_name

            return iface_to_impl

        iface_to_impl_map = build_interface_to_impl_map(java_files)

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

            if b_no_gen in iface_to_impl_map: 
                impl_name = iface_to_impl_map[b_no_gen] 
                impl_path = type_to_path_full.get(impl_name) 
                if impl_path: 
                    iface_path = type_to_path_full.get(b_no_gen) 
                    method_in_iface = bool(method_return_index.get(b_no_gen)) 
                    method_in_impl = bool(method_return_index.get(impl_name)) 
                    if method_in_impl and not method_in_iface: 
                        return impl_name

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

        # apply(axis=1) is slow for large DataFrames — iterate records instead
        _cmc_values = [
            map_or_resolve(row)
            for row in df_clean[["object_call", "class_interface_name", "file_name"]].to_dict("records")
        ]
        df_clean["class_method_call"] = _cmc_values
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
            # Convert to list-of-dicts once — much faster than iterrows()
            records = df_clean_local.to_dict("records")
            single_seg_pat_paren = re.compile(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\([^)]*\)\s*$')
            single_seg_pat_noparen = re.compile(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*$')

            rows = []
            for row in records:
                obj_call = str(row.get("object_call", "") or "").strip()
                parent_class = str(row.get("class_interface_name", "") or "").strip()
                file_name = str(row.get("file_name", "") or "").strip()

                obj_call = normalize_keyword_rooted_call(obj_call, parent_class)
                cmc = normalize_keyword_rooted_call(str(row.get("class_method_call", "") or "").strip(), parent_class)

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

        def collect_external_import_classes(source_files, user_prefix):
            import_classes = set()
            import_pattern = re.compile(
                r'^\s*import\s+(static\s+)?([\w\.]+)\s*;',
                re.MULTILINE
            )

            for source_file_path in source_files:
                try:
                    code = read_file_cached(source_file_path)
                except Exception:
                    continue

                for _, full_import in import_pattern.findall(code):
                    if user_prefix and full_import.startswith(user_prefix):
                        continue

                    simple_name = full_import.split(".")[-1]

                    # For wildcard imports the final component is "*".
                    if simple_name and simple_name != "*":
                        import_classes.add(simple_name)

            return import_classes

        def extract_base_class(class_method_call):
            if not isinstance(class_method_call, str):
                return None
            m = re.match(r'\s*([A-Za-z_]\w*)\s*\.', class_method_call)
            return m.group(1) if m else None

        user_prefix = details.get("user_defined_generic_import", "")
        external_import_classes = collect_external_import_classes(
            java_files,
            user_prefix
        )

        df_clean_exploded["__base_class"] = df_clean_exploded["class_method_call"].apply(
            extract_base_class
        )

        df_clean_exploded = df_clean_exploded[
            ~df_clean_exploded["__base_class"].isin(external_import_classes)
        ].drop(columns="__base_class")

        # --- Enforce: if Class.method exists, drop object.method for the same call ---
        def _split_base_method(cmc):
            s = str(cmc or "").strip()
            m = re.match(
                r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\(?\s*\)?\s*$',
                s
            )
            if not m:
                return None, None
            return m.group(1), m.group(2)

        df_ex = df_clean_exploded.copy()

        split_results = [
            _split_base_method(value)
            for value in df_ex["class_method_call"].tolist()
        ]

        if split_results:
            bases, methods = zip(*split_results)
            df_ex["__base"] = bases
            df_ex["__meth"] = methods
        else:
            df_ex["__base"] = None
            df_ex["__meth"] = None

        mask_valid = df_ex['__base'].notna() & df_ex['__meth'].notna()
        df_valid = df_ex[mask_valid].copy()

        df_valid['__upper_base'] = df_valid['__base'].apply(
            lambda b: (b[0].upper() + b[1:]) if isinstance(b, str) and b else b
        )

        class_rows = df_valid[
            df_valid["__base"].str[0].str.isupper().fillna(False)
        ].copy()

        class_key_set = set(
            zip(
                class_rows['file_name'],
                class_rows['class_interface_name'],
                class_rows['method_name'],
                class_rows['__upper_base'],
                class_rows['__meth']
            )
        )

        valid_keys = list(
            zip(
                df_valid["file_name"],
                df_valid["class_interface_name"],
                df_valid["method_name"],
                df_valid["__upper_base"],
                df_valid["__meth"]
            )
        )

        lower_case_base_mask = (
            df_valid["__base"]
            .astype(str)
            .str[0]
            .str.islower()
            .fillna(False)
        )

        df_valid["__drop"] = (
            lower_case_base_mask
            & pd.Series(
                (key in class_key_set for key in valid_keys),
                index=df_valid.index
            )
        )

        df_keep_valid = df_valid[
            ~df_valid["__drop"]
        ].drop(
            columns=["__base", "__meth", "__upper_base", "__drop"]
        )

        df_rest = df_ex[~mask_valid]
        df_clean_exploded = pd.concat([df_keep_valid, df_rest], ignore_index=True)

        df_clean_exploded = df_clean_exploded.drop_duplicates(
            subset=['file_name', 'class_interface_name', 'method_name', 'class_method_call']
        )

        # FINAL FILTER — DROP NON-USER-DEFINED IMPORT CALLS (second pass)
        # Reuse external_import_classes calculated above. Do not scan the
        # complete application folder for a second time.
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

        # Use to_dict("records") — 50–100× faster than iterrows() on large DataFrames
        for row_x in df_clean_exploded[["class_method_call", "class_interface_name"]].to_dict("records"):
            cmc = str(row_x.get("class_method_call", "") or "").strip()
            parent_cls = str(row_x.get("class_interface_name", "") or "").strip()
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
        for cls, mtd in callee_pairs:
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

            
        def build_type_to_path_including_nested(source_files):
            """
            Build a type-to-file index.  Uses the shared raw_ast_cache so
            files are never parsed more than once per run.  Falls back to a
            fast regex scan for files that failed to parse with javalang
            (saves a second parse attempt per failing file).
            """
            mapping = {}

            declaration_types = (
                javalang.tree.ClassDeclaration,
                javalang.tree.InterfaceDeclaration,
                javalang.tree.EnumDeclaration,
            )

            # Regex fallback for files whose AST is unavailable
            _decl_re = re.compile(
                r'\b(?:class|interface|enum)\s+([A-Za-z_]\w*)',
                re.MULTILINE,
            )

            for fpath in source_files:
                tree = raw_ast_cache.get(fpath)  # may be None (not yet cached)
                if tree is None:
                    try:
                        tree = parse_raw_ast_cached(fpath)
                    except Exception:
                        tree = False  # parse failed
                        raw_ast_cache[fpath] = tree

                if tree and tree is not False:
                    for _, decl in tree.filter(declaration_types):
                        name = getattr(decl, "name", None)
                        if not name:
                            continue
                        mapping.setdefault(name, fpath)
                        if name.endswith("Impl"):
                            mapping.setdefault(name[:-4], fpath)
                else:
                    # AST unavailable — use regex on cached text (no extra I/O)
                    text = file_content_cache.get(fpath, "")
                    for m in _decl_re.finditer(text):
                        name = m.group(1)
                        mapping.setdefault(name, fpath)
                        if name.endswith("Impl"):
                            mapping.setdefault(name[:-4], fpath)

            return mapping

        type_to_path_full = build_type_to_path_including_nested(java_files)
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

            extension = details_cfg["extension"][0]

            if not java_file_path:
                target_filename = "{}{}".format(
                    classname,
                    extension
                ).lower()

                java_file_path = file_name_to_path.get(target_filename)

            if not java_file_path:
                impl_filename = "{}Impl{}".format(
                    classname,
                    extension
                ).lower()

                java_file_path = file_name_to_path.get(impl_filename)

            # Build the cache key after resolving the actual file path.
            # This prevents unresolved and resolved requests from using
            # different cache entries for the same method.
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
                if line_cache is not None:
                    line_cache[cache_key] = None

                print(
                    "Neither {} nor {} found".format(
                        "{}{}".format(classname, details_cfg.get('extension', '.java')),
                        "{}Impl{}".format(classname, details_cfg.get('extension', '.java'))
                    )
                )
                return None

            try:
                text = read_file_cached(java_file_path)
            except Exception:
                if line_cache is not None:
                    line_cache[cache_key] = None
                return None

            text = text.replace("\r\n", "\n").replace("\r", "\n")
            lines = text.split("\n")

            # ------------------------------------------------------------------

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

        loc_lookup = {}

        # Parallelise LOC computation — each call is independent and I/O-bound
        # (file reads hit the in-process cache after the first access).
        _unique_rows = [
            row for row in df_unique_methods.to_dict("records")
            if row["class_method_key"] not in loc_lookup
        ]

        def _compute_loc(row):
            return row["class_method_key"], extract_loc_any(row)

        _loc_workers = min(8, (multiprocessing.cpu_count() or 4))
        with concurrent.futures.ThreadPoolExecutor(max_workers=_loc_workers) as _loc_pool:
            for _key, _val in _loc_pool.map(_compute_loc, _unique_rows):
                loc_lookup.setdefault(_key, _val)

        df_unique_methods["Number_Of_Lines"] = (
            df_unique_methods["class_method_key"].map(loc_lookup)
        )
            
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

        with pd.ExcelWriter(all_methods, engine="xlsxwriter") as writer:
            df_clean_exploded.to_excel(writer,sheet_name="Cleaned_AST_Details",index=False)
            df_application_properties.to_excel(writer,sheet_name="application.properties",index=False)

        return os.path.abspath(all_methods)

    df_results = pd.DataFrame(
        ast_results,
        columns=[
            'file_name',
            'class_interface_name',
            'type',
            'method_name',
            'Annotations',
            'Method_Declaration_Type',
            'return_type',
            'object_call',
            'Parameters',
            'Parameter_Arity',
            'Parameter_Types'
        ]
    )

    # Collect pre-built index results (built in parallel with the AST loop)
    _prebuilt_ocm = _ocm_future.result()
    _prebuilt_mri = _mri_future.result()
    _index_executor.shutdown(wait=True)

    all_methods = clean_and_write(df_results, _prebuilt_ocm, _prebuilt_mri)
    end_time = datetime.now()

    elapsed = (end_time - start_time).total_seconds()
    log_time(
        f"Method Lineage Generation END | "
        f"Duration={elapsed:.3f} sec"
    )
    return all_methods