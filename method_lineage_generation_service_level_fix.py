



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
import time
from tqdm import tqdm



def log_time(message):
    with open("execution_log_service.txt", "a", encoding="utf-8") as f:
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


def strip_generics(name):
    if not isinstance(name, str):
        return name
    name = re.sub(r'\s*&lt;[^&gt]+&gt;\s*', '', name)
    name = re.sub(r'\s*<[^>]+>\s*', '', name)
    return name




# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Module-level worker for ProcessPoolExecutor
# Must be at module level (not a closure) so it can be pickled.
# ---------------------------------------------------------------------------

_WORKER_ADAPTER = None
_WORKER_DEBUG = False


def _init_file_worker(adapter_module_name, adapter_class_name, adapter_kwargs):
    """Initialize adapter once per worker process (spawn-safe)."""
    import importlib, os as _os

    global _WORKER_ADAPTER, _WORKER_DEBUG
    mod = importlib.import_module(adapter_module_name)
    AdapterCls = getattr(mod, adapter_class_name)
    _WORKER_ADAPTER = AdapterCls()
    _WORKER_ADAPTER.configure(**adapter_kwargs)
    _WORKER_DEBUG = bool(
        ((adapter_kwargs.get("details") or {}).get("verbose_debug"))
        or _os.environ.get("LINEAGE_VERBOSE_DEBUG")
    )

def _file_worker(args):
    """
    Process one Java source file in a subprocess.
    args = (file_path, adapter_module, adapter_class, adapter_kwargs, strip_fn_src)

    Returns (list_of_row_dicts, error_dict_or_None)
    Each row dict contains an extra '_type_name', '_method_name', '_calls' key
    that the main process uses to rebuild method_map / file_map.
    """
    import importlib, html as _html, re as _re, os as _os
    global _WORKER_ADAPTER, _WORKER_DEBUG
    _worker_debug = _WORKER_DEBUG

    # Preferred path: args is just file_path and adapter is initialized once
    # per process via ProcessPoolExecutor(initializer=...).
    if isinstance(args, str):
        file_path = args
        adapter = _WORKER_ADAPTER
    else:
        # Backward-compatible fallback path (legacy tuple payload)
        file_path, adapter_module_name, adapter_class_name, adapter_kwargs = args
        _worker_debug = bool(
            ((adapter_kwargs.get("details") or {}).get("verbose_debug"))
            or _os.environ.get("LINEAGE_VERBOSE_DEBUG")
        )
        adapter = None

    file = _os.path.basename(file_path)
    local_rows = []
    local_error = None

    # Fallback init if worker initializer wasn't used.
    if adapter is None:
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

    def _strip_comments_only(text):
        if not isinstance(text, str):
            return ""
        # Keep literals intact for AST parse. Removing literals can create
        # invalid argument lists (e.g. foo("x") -> foo()).
        return _re.sub(r'//.*?$|/\*.*?\*/', '', text, flags=_re.MULTILINE | _re.DOTALL)

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
            'file_name': file_path,
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
        # Parse the original source first so javalang line positions match
        # the same text used by commented-line checks below.
        code_for_ast = code
        code_no_comments = _strip(code)

        ast = adapter.parse_ast(code_for_ast)
        if not ast:
            # Fallback: some files parse more reliably after comment stripping,
            # but position-based checks must then be treated as best-effort.
            code_for_ast = _strip_comments_only(code)
            ast = adapter.parse_ast(code_for_ast)
        if not ast:
            raise RuntimeError("AST parse failed")


        declared_types = list(adapter.get_declared_types(ast))
        if not declared_types and code_for_ast is not code_no_comments:
            _retry_ast = adapter.parse_ast(code_no_comments)
            if _retry_ast:
                _retry_types = list(adapter.get_declared_types(_retry_ast))
                if _retry_types:
                    ast = _retry_ast
                    code_for_ast = code_no_comments
                    declared_types = _retry_types
        
        if not declared_types:
            fb = adapter.fallback_parse(code_raw)
            type_name = fb.get('type_name', 'Unknown')
            row_type = fb.get('row_type', 'Unknown')
            if fb.get('per_method_calls'):
                for rec in fb['per_method_calls']:
                    method = rec.get('method_name') or 'UnknownMethod'
                    call = rec.get('object_call') or 'None'
                    append_row(type_name, row_type, method, null_meta,
                            call, [call])
            else:
                filtered_calls = fb.get('filtered_calls', [])
                for call in filtered_calls or ["None"]:
                    append_row(type_name, row_type, "UnknownMethod", null_meta,
                            call, filtered_calls or ["None"])
            return local_rows, None
        

        for type_name, type_kind, type_node in declared_types:
            for method_name, method_node in adapter.get_methods_in_type(type_node):
                try:
                    pos = method_node.position
                    if pos and _is_commented(code, pos[0] - 1):
                        continue
                except Exception:
                    pass
                try:
                    meta = adapter.extract_method_metadata(method_node)
                    # Keep source aligned with AST positions. Using stripped text can
                    # shift method-body extraction and misattribute calls (notably to constructors).
                    calls = adapter.find_calls_in_method(type_node, method_node, code_for_ast)
                    calls = list(dict.fromkeys(calls)) if calls else ["None"]
                except Exception as method_ex:
                    # Keep the method in output even if call extraction fails.
                    # Falling back the entire file hides valid methods like overrides.
                    meta = dict(null_meta)
                    calls = ["None"]
                    if _worker_debug:
                        print(
                            "[DEBUG][_file_worker][method_error] {}.{} in {} -> {}".format(
                                type_name,
                                method_name,
                                file_path,
                                method_ex,
                            )
                        )
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
                    'file_name': file_path, 'class_interface_name': type_name,
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
                    'file_name': file_path, 'class_interface_name': type_name,
                    'type': row_type, 'method_name': "UnknownMethod",
                    'Annotations': "None", 'Method_Declaration_Type': "Default",
                    'return_type': "", 'object_call': call,
                    'Parameters': '', 'Parameter_Arity': None, 'Parameter_Types': '',
                    '_type_name': type_name, '_method_name': "UnknownMethod",
                    '_calls': filtered_calls or ["None"],
                })
    return local_rows, local_error


def method_lineage(
    service_files,
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
    start_time = datetime.now()
    log_time(f"Method lineage Generation START")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    regex = data["Language"][technology]["Application"][application]["Regex_Pattern"]
    _verbose_debug = bool(details.get("verbose_debug") or os.environ.get("LINEAGE_VERBOSE_DEBUG"))

    _entry_debug_log_file = (
        os.environ.get("LINEAGE_DEBUG_LOG_FILE")
        or details.get("debug_log_file")
        or os.path.join(OUTPUT_DIR, "lineage_debug_capture.txt")
    )

    def _entry_debug_print(*args):
        text = " ".join(str(a) for a in args)
        print(*args)
        if not _verbose_debug:
            return
        try:
            with open(_entry_debug_log_file, "a", encoding="utf-8") as _fh:
                _fh.write(text + "\n")
        except Exception:
            pass

    if _verbose_debug:
        try:
            with open(_entry_debug_log_file, "w", encoding="utf-8") as _fh:
                _fh.write("[DEBUG][SESSION] started: {}\n".format(datetime.now().isoformat()))
        except Exception:
            pass
        print("controller_files : ", controller_files)
        print("method_lineage")

    ast_results = []
    method_map = {}
    file_map = {}
    errors = []

    def _as_abs_norm(path):
        if not isinstance(path, str) or not path.strip():
            return ""
        return os.path.normcase(os.path.abspath(path.strip()))

    def _split_top_level_commas(text):
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
                parts.append("".join(buf).strip())
                buf = []
            else:
                buf.append(ch)

        tail = "".join(buf).strip()
        if tail:
            parts.append(tail)
        return parts

    def _canon_type_name(type_text):
        t = str(type_text or "").strip()
        if not t:
            return ""
        t = re.sub(r'@\w+(?:\([^)]*\))?\s*', ' ', t)
        t = re.sub(r'\bfinal\b', ' ', t)
        t = t.replace("...", "[]")
        t = re.sub(r'\s*<[^>]*>\s*', '', t)
        t = re.sub(r'\s+', '', t)
        if '.' in t:
            t = t.split('.')[-1]
        return t.lower()

    def _signature_to_canon_tuple(sig_text):
        sig = str(sig_text or "").strip()
        if not sig:
            return tuple()
        out = []
        for raw in _split_top_level_commas(sig):
            tok = re.sub(r'@\w+(?:\([^)]*\))?\s*', ' ', raw)
            tok = re.sub(r'\bfinal\b', ' ', tok).strip()
            bits = tok.split()
            if len(bits) >= 2 and re.fullmatch(r'[A-Za-z_]\w*', bits[-1]):
                tok = " ".join(bits[:-1])
            out.append(_canon_type_name(tok))
        return tuple(x for x in out if x)

    def _method_meta_param_tuple(method_node):
        try:
            meta = adapter.extract_method_metadata(method_node) or {}
            param_types = str(meta.get("Parameter_Types") or "").strip()
        except Exception:
            param_types = ""
        if not param_types:
            return tuple()
        return tuple(
            _canon_type_name(p)
            for p in str(param_types).split(';')
            if str(p).strip()
        )

    def _row_param_tuple(row):
        param_types = str((row or {}).get("Parameter_Types") or "").strip()
        if not param_types:
            return tuple()
        return tuple(
            _canon_type_name(p)
            for p in param_types.split(';')
            if str(p).strip()
        )

    def _parse_entry_method_spec(raw_entry):
        s = str(raw_entry or "").strip()
        if not s:
            return None

        low = s.lower()
        marker = ".java."
        idx = low.rfind(marker)
        if idx != -1:
            file_part = s[:idx + len(".java")]
            tail = s[idx + len(marker):].strip()
            m = re.match(r'^([A-Za-z_]\w*)\s*(?:\((.*)\))?\s*$', tail)
            if not m:
                return {
                    "raw": s,
                    "is_method_entry": False,
                    "file": s,
                }
            method_name = m.group(1)
            sig = m.group(2)
            return {
                "raw": s,
                "is_method_entry": True,
                "file": file_part,
                "method": method_name,
                "params": _signature_to_canon_tuple(sig) if sig is not None else None,
                "signature_given": sig is not None,
            }

        # New supported format:
        #   C:/.../PaymentOrderService.initiatePaymentOrder
        #   C:/.../PaymentOrderService.initiatePaymentOrder(String,Long)
        m2 = re.match(r'^(.*)\.([A-Za-z_]\w*)\s*(?:\((.*)\))?\s*$', s)
        if m2:
            file_stem = str(m2.group(1) or "").strip()
            method_name = m2.group(2)
            sig = m2.group(3)
            # Only treat as method-entry when class stem has no extension,
            # to avoid misreading plain file paths like .../MyClass.java.
            base_name = os.path.basename(file_stem)
            if base_name and "." not in base_name:
                inferred_file = file_stem + ".java"
                return {
                    "raw": s,
                    "is_method_entry": True,
                    "file": inferred_file,
                    "method": method_name,
                    "params": _signature_to_canon_tuple(sig) if sig is not None else None,
                    "signature_given": sig is not None,
                }

        return {
            "raw": s,
            "is_method_entry": False,
            "file": s,
        }

    def _debug_print_methods_for_file(target_file_path):
        if not _verbose_debug:
            return
        target_norm = _as_abs_norm(target_file_path)
        target_base = os.path.basename(str(target_file_path or "")).strip().lower()
        target_class = os.path.splitext(target_base)[0]

        if not target_norm and not target_base:
            return

        def _path_base(path):
            return os.path.basename(str(path or "")).strip().lower()

        # Prefer exact path matching when present to avoid same-basename class drift
        _has_exact_ast_match = any(
            _as_abs_norm(r.get("file_name")) == target_norm
            for r in ast_results
        )

        # Raw methods directly from parsed rows for this file
        row_methods = sorted(set(
            str(r.get("method_name") or "").strip()
            for r in ast_results
            if (
                _as_abs_norm(r.get("file_name")) == target_norm
                or (
                    not _has_exact_ast_match
                    and (
                        _path_base(r.get("file_name")) == target_base
                        or str(r.get("class_interface_name") or "").strip().lower() == target_class
                    )
                )
            )
            and str(r.get("method_name") or "").strip()
        ))

        method_names = []
        class_names = []
        for _cls, _methods in method_map.items():
            _cls_file = _as_abs_norm(file_map.get(_cls, ""))
            _cls_file_base = _path_base(file_map.get(_cls, ""))
            if _cls_file == target_norm or _cls_file_base == target_base or str(_cls).strip().lower() == target_class:
                class_names.append(_cls)
                method_names.extend(list((_methods or {}).keys()))

        uniq_methods = sorted(set(m for m in method_names if isinstance(m, str) and m.strip()))

        _entry_debug_print("\n[DEBUG][ENTRY_FILE_METHODS] file:", target_file_path)
        if class_names:
            _entry_debug_print("[DEBUG][ENTRY_FILE_METHODS] classes:", sorted(set(class_names)))
        else:
            _entry_debug_print("[DEBUG][ENTRY_FILE_METHODS] classes: []")

        _entry_debug_print("[DEBUG][ENTRY_FILE_METHODS] total_methods:", len(uniq_methods))
        for _idx, _m in enumerate(uniq_methods, start=1):
            _entry_debug_print("[DEBUG][ENTRY_FILE_METHODS] {}. {}".format(_idx, _m))

        _entry_debug_print("[DEBUG][ENTRY_FILE_METHODS][ROW_SCAN] total_methods:", len(row_methods))
        for _idx, _m in enumerate(row_methods, start=1):
            _entry_debug_print("[DEBUG][ENTRY_FILE_METHODS][ROW_SCAN] {}. {}".format(_idx, _m))

        entry_errors = [
            e for e in errors
            if isinstance(e, dict) and (
                _as_abs_norm(e.get("File")) == target_norm
                or _path_base(e.get("File")) == target_base
            )
        ]
        _entry_debug_print("[DEBUG][ENTRY_FILE_METHODS][ERRORS] count:", len(entry_errors))
        for _e in entry_errors:
            _entry_debug_print("[DEBUG][ENTRY_FILE_METHODS][ERRORS]", _e)

    # â”€â”€ Single progress bar: 0 â†’ 100 across the whole pipeline â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Checkpoints (cumulative %):
    #   10  BFS discovery done
    #   60  All files parsed
    #   75  Chain resolution done
    #   90  LOC computation done
    #  100  Excel written
    _pbar = tqdm(
        total=100,
        desc="Progress",
        unit="%",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}% [{elapsed}<{remaining}] {postfix}",
        ncols=90,
        dynamic_ncols=True,
    )
    _pbar_start_ts = time.time()
    _last_pbar_refresh_ts = _pbar_start_ts

    def _elapsed_seconds_text():
        return "{}s".format(int(max(0, time.time() - _pbar_start_ts)))

    def _maybe_refresh_pbar(force=False):
        """Redraw tqdm periodically so [elapsed<remaining] keeps ticking."""
        nonlocal _last_pbar_refresh_ts
        _now = time.time()
        if force or (_now - _last_pbar_refresh_ts >= 1.0):
            _pbar.refresh()
            _last_pbar_refresh_ts = _now

    def _pbar_goto(target_pct, label):
        """Jump the bar to exactly target_pct, regardless of where it currently is."""
        delta = target_pct - _pbar.n
        if delta > 0:
            _pbar.update(delta)
        _pbar.set_postfix_str("{} | t={}".format(label, _elapsed_seconds_text()))
        _maybe_refresh_pbar(force=True)

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
    # Step 1: build indexes for class-name and FQN â†’ path resolution.
    # Class names can collide across modules (e.g. generated vs domain classes),
    # so BFS must be able to resolve by explicit import FQN as well.
    _class_to_paths = {}
    _fqn_to_paths = {}
    _class_methods = {}
    _class_kind = {}
    _class_extends = {}
    _concrete_subclasses = {}
    _interface_to_impls = {}
    _class_impl_signatures = {}
    _annotation_to_paths = {}
    _all_project_files_indexed = []
    _import_decl_re = re.compile(r'^\s*import\s+(?:static\s+)?([\w.]+)\s*;', re.MULTILINE)
    _pkg_decl_re = re.compile(r'^\s*package\s+([\w.]+)\s*;', re.MULTILINE)
    _ann_name_re = re.compile(r'@([A-Za-z_][\w\.]*)')
    _decl_type_name_re = re.compile(r'\b(?:class|interface|enum)\s+([A-Za-z_]\w*)\b')
    _bfs_qual_inv_pat = re.compile(r'([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\.\s*([A-Za-z_]\w*)\s*\(')
    _method_body_decl_re = re.compile(
        r'^[ \t]*(?:@\w+(?:\([^)]*\))?\s*)*'
        r'(?:(?:public|private|protected|static|final|abstract|synchronized|native|strictfp|default)\s+)*'
        r'(?:<[^>{;]+>\s*)?'
        r'(?:[A-Za-z_][\w$.]*(?:\s*<[^>{;]+>)?(?:\s*\[\s*\])*)\s+'
        r'([A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:throws\s+[^\{]+)?\{',
        re.MULTILINE,
    )
    _extends_decl_re = re.compile(
        r'\bclass\s+[A-Za-z_]\w*\s+extends\s+([A-Za-z_][\w.]*)',
        re.MULTILINE,
    )
    _return_qual_call_re = re.compile(
        r'\breturn\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\.\s*([A-Za-z_]\w*)\s*\(',
        re.MULTILINE,
    )
    _inject_ann_re = re.compile(r'@Inject\b')
    _inject_field_decl_re = re.compile(
        r'@Inject\b\s*'
        r'(?:(?:public|private|protected|static|final|transient|volatile)\s+)*'
        r'[A-Za-z_][\w$.<>,\[\]\?\s]*\s+'
        r'([a-z][A-Za-z0-9_]*)\s*(?:=|;|,|\))',
        re.MULTILINE,
    )

    def _add_index_path(index_map, key, path):
        if not key:
            return
        bucket = index_map.setdefault(key, [])
        if path not in bucket:
            bucket.append(path)

    def _add_annotation_path(index_map, ann_name, path):
        ann = str(ann_name or "").strip()
        if not ann:
            return
        bucket = index_map.setdefault(ann, set())
        bucket.add(path)

    def _annotation_lookup_names(ann_name):
        ann = str(ann_name or "").strip()
        if not ann:
            return []
        out = [ann]
        if ann.endswith("s"):
            out.append(ann[:-1])
        else:
            out.append(ann + "s")
        return out

    def _normalize_contract_signature(_sig):
        """Normalize contract signature to simple-type form preserving generics.

        Example:
          nl.pkg.Validator<nl.pkg.Requestor> -> Validator<Requestor>
        """
        s = str(_sig or "").strip()
        if not s:
            return ""
        s = re.sub(r'\s+', '', s)
        s = re.sub(r'\b(?:[a-z_][A-Za-z0-9_]*\.)+([A-Za-z_][A-Za-z0-9_]*)', r'\1', s)
        return s

    def _contract_signature_base(_sig):
        n = _normalize_contract_signature(_sig)
        if not n:
            return ""
        return n.split('<', 1)[0].split('.')[-1]

    def _class_matches_contract_signatures(_cls_name, _contract_sigs):
        """True when class implements contract signature(s).

        For generic contracts like Validator<Requestor>, require exact signature match.
        For non-generic contracts like Validator, match by base interface/class name.
        """
        cls = str(_cls_name or "").strip().split('.')[-1]
        if not cls:
            return False
        req = {_normalize_contract_signature(x) for x in (_contract_sigs or set()) if str(x or "").strip()}
        if not req:
            return True

        impl = {
            _normalize_contract_signature(x)
            for x in (_class_impl_signatures.get(cls, set()) or set())
            if str(x or "").strip()
        }
        if not impl:
            # Keep fallback when contract refers directly to class name.
            req_bases = {_contract_signature_base(x) for x in req if x}
            return cls in req_bases

        for r in req:
            if '<' in r and '>' in r:
                if r in impl:
                    return True
            else:
                rb = _contract_signature_base(r)
                if any(_contract_signature_base(i) == rb for i in impl):
                    return True
        return False

    def _read_index_text(path):
        try:
            with open(path, "r", encoding="utf-8") as _fh:
                return _fh.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="latin-1") as _fh:
                return _fh.read()
        except Exception:
            return ""

    def _extract_declared_methods_for_stem(src_text, class_stem):
        """Return direct method/constructor names declared in class_stem.

        Fast regex-first path; AST fallback only when regex finds nothing.
        """
        names = set()
        text = src_text or ""
        if not text or not class_stem:
            return names

        # Regex-first: permissive declaration-body detector.
        _fallback_decl_re = re.compile(
            r'^[ \t]*(?:@\w+(?:\([^)]*\))?\s*)*'
            r'(?:(?:public|private|protected|static|final|abstract|synchronized|native|strictfp|default)\s+)+'
            r'(?:<[^>{;]+>\s*)?'
            r'(?:[^\n\r;{}]+?)\s+'
            r'([A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:throws\s+[^\{]+)?\{',
            re.MULTILINE,
        )
        for _m in _fallback_decl_re.finditer(text):
            _name = _m.group(1)
            if _name and _name not in {"if", "for", "while", "switch", "catch", "return", "new"}:
                names.add(_name)
        if names:
            return names

        # AST fallback: improves correctness for uncommon complex signatures.
        try:
            _tree = javalang.parse.parse(text)
            for _t in getattr(_tree, "types", []) or []:
                if getattr(_t, "name", None) != class_stem:
                    continue
                for _member in getattr(_t, "body", []) or []:
                    if isinstance(_member, (javalang.tree.MethodDeclaration, javalang.tree.ConstructorDeclaration)):
                        _mn = getattr(_member, "name", None)
                        if _mn:
                            names.add(_mn)
                if names:
                    return names
        except Exception:
            pass
        return names

    for _root, _, _files in os.walk(app_folder):
        for _f in _files:
            if _f.endswith(valid_extensions):
                _stem = os.path.splitext(_f)[0]
                _abs = os.path.abspath(os.path.join(_root, _f))
                _all_project_files_indexed.append(_abs)

                _txt = _read_index_text(_abs)
                _pkg_match = _pkg_decl_re.search(_txt)
                _pkg_name = _pkg_match.group(1) if _pkg_match else ""

                # Index all declared top-level type names in this source file
                # so multi-class files are discoverable by class name.
                _declared_type_names = {_stem}
                for _m_decl in _decl_type_name_re.finditer(_txt or ""):
                    _nm = str(_m_decl.group(1) or "").strip()
                    if _nm:
                        _declared_type_names.add(_nm)

                for _decl_name in sorted(_declared_type_names):
                    _add_index_path(_class_to_paths, _decl_name, _abs)
                    _fqn_name = f"{_pkg_name}.{_decl_name}" if _pkg_name else _decl_name
                    _add_index_path(_fqn_to_paths, _fqn_name, _abs)

                # XxxImpl â†’ also register as Xxx so callers of the interface find it
                if _stem.endswith("Impl"):
                    _add_index_path(_class_to_paths, _stem[:-4], _abs)

                # Project-wide qualifier index: annotation simple name -> file paths
                # Used by BFS rule expansion for @Inject-qualified fields.
                _file_anns = {
                    str(_raw_ann).strip().split('.')[-1]
                    for _raw_ann in _ann_name_re.findall(_txt or "")
                    if str(_raw_ann or "").strip()
                }
                for _ann in _file_anns:
                    _add_annotation_path(_annotation_to_paths, _ann, _abs)

                for _decl_name in sorted(_declared_type_names):
                    _kind = "class"
                    if re.search(r'\binterface\s+' + re.escape(_decl_name) + r'\b', _txt or ""):
                        _kind = "interface"
                    elif re.search(r'\benum\s+' + re.escape(_decl_name) + r'\b', _txt or ""):
                        _kind = "enum"
                    _class_kind[_decl_name] = _kind

                    # Lightweight owner-resolution metadata for BFS class->method routing.
                    _declared_methods = _extract_declared_methods_for_stem(_txt, _decl_name)
                    _regex_declared = {
                        _m.group(1)
                        for _m in _method_body_decl_re.finditer(_txt or "")
                        if _m and _m.group(1)
                    }
                    if _regex_declared:
                        _declared_methods.update(_regex_declared)
                    # Merge (not overwrite) for duplicate simple class names across modules.
                    _class_methods.setdefault(_decl_name, set()).update(_declared_methods)

                    _ext_m = re.search(
                        r'\bclass\s+' + re.escape(_decl_name) + r'\s+extends\s+([A-Za-z_][A-Za-z0-9_$.]*)',
                        _txt or "",
                    )
                    if _ext_m:
                        _parent = _ext_m.group(1).split('.')[-1]
                        if _parent:
                            _class_extends[_decl_name] = _parent
                            _concrete_subclasses.setdefault(_parent, []).append(_decl_name)

                    _impl_m = re.search(
                        r'\bclass\s+' + re.escape(_decl_name) + r'\s+implements\s+([^\{]+)',
                        _txt or "",
                    )
                    if _impl_m:
                        for _iface_tok in _split_top_level_commas(_impl_m.group(1)):
                            _iface_norm = _normalize_contract_signature(_iface_tok)
                            if _iface_norm:
                                _class_impl_signatures.setdefault(_decl_name, set()).add(_iface_norm)
                            _iface_simple = _contract_signature_base(_iface_tok)
                            if _iface_simple:
                                _interface_to_impls.setdefault(_iface_simple, set()).add(_decl_name)

                if _stem.endswith("Impl") and len(_stem) > 4:
                    _interface_to_impls.setdefault(_stem[:-4], set()).add(_stem)
                if _stem.endswith("Implementation") and len(_stem) > len("Implementation"):
                    _iface_guess = _stem[:-len("Implementation")]
                    _interface_to_impls.setdefault(_iface_guess, set()).add(_stem)

    if _verbose_debug:
        print(f"[DEBUG] _class_to_path total entities : {len(_class_to_paths)}")
        print(f"[DEBUG] sample entites: ")
        for k,v in list(_class_to_paths.items())[:10]:
            print(f"  {k} -> {v[0] if isinstance(v, list) and v else v}")

    def _bfs_read(path):
        try:
            with open(path, "r", encoding="utf-8") as _fh:
                return _fh.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="latin-1") as _fh:
                return _fh.read()

    def _extract_declared_contract_types(_type_node):
        """Return candidate contract type names from a field declaration type.

        Example:
          Instance<Validator<Requestor>> -> {"Validator"}
        """
        out = set()
        wrapper_types = {
            "Instance", "Provider", "Optional", "List", "Set",
            "Collection", "Iterable", "Stream"
        }

        def _simple(_t):
            n = getattr(_t, "name", None)
            if not n:
                return None
            return str(n).strip().split('.')[-1]

        def _walk(_t, depth=0):
            if _t is None:
                return
            _sn = _simple(_t)
            if _sn:
                out.add(_sn)
            for _arg in (getattr(_t, "arguments", []) or []):
                _arg_t = getattr(_arg, "type", None)
                if _arg_t is not None:
                    _walk(_arg_t, depth + 1)

        _walk(_type_node)
        if not out:
            return set()

        _base = _simple(_type_node)
        _nested = {x for x in out if x and x != _base}
        if _nested and _base in wrapper_types:
            return _nested
        return out

    def _type_node_to_signature(_type_node):
        """Build simple contract signature from javalang type node."""
        if _type_node is None:
            return ""
        _name = getattr(_type_node, "name", None)
        if not _name:
            return ""
        _base = str(_name).strip().split('.')[-1]
        _args = []
        for _arg in (getattr(_type_node, "arguments", []) or []):
            _arg_t = getattr(_arg, "type", None)
            _arg_sig = _type_node_to_signature(_arg_t) if _arg_t is not None else ""
            if _arg_sig:
                _args.append(_arg_sig)
        if _args:
            return "{}<{}>".format(_base, ",".join(_args))
        return _base

    def _extract_declared_contract_signatures(_type_node):
        """Extract contract signatures preserving generic arguments.

        Example:
          Instance<Validator<Requestor>> -> {"Validator<Requestor>"}
        """
        if _type_node is None:
            return set()
        _base = str(getattr(_type_node, "name", "") or "").strip().split('.')[-1]
        _sigs = set()
        _wrapper = _is_wrapper_contract_type(_type_node)
        _args = list(getattr(_type_node, "arguments", []) or [])

        if _wrapper and _args:
            for _arg in _args:
                _arg_t = getattr(_arg, "type", None)
                _sig = _type_node_to_signature(_arg_t)
                if _sig:
                    _sigs.add(_normalize_contract_signature(_sig))
            return _sigs

        _sig_self = _type_node_to_signature(_type_node)
        if _sig_self:
            _sigs.add(_normalize_contract_signature(_sig_self))
        return _sigs

    def _is_wrapper_contract_type(_type_node):
        """Return True when the declared field type is a wrapper contract type.

        Example wrappers: Instance<T>, Provider<T>, Optional<T>, List<T>.
        """
        if _type_node is None:
            return False
        _base = getattr(_type_node, "name", None)
        if not _base:
            return False
        _base_simple = str(_base).strip().split('.')[-1]
        return _base_simple in {
            "Instance", "Provider", "Optional", "List", "Set",
            "Collection", "Iterable", "Stream"
        }

    def _collect_injected_qualified_fields_from_ast(_ast_obj):
        """Map injected field name -> {annotations, contract_types} for current file."""
        out = {}
        if not _ast_obj:
            return out
        _inj = {"Inject", "Autowired", "Resource", "EJB"}
        _direct_qualifier = {"CommonValidation", "CommonValidations"}
        _skip = {"Inject", "Autowired", "Resource", "EJB", "Qualifier", "Named"}
        try:
            for _, _cls in _ast_obj.filter(javalang.tree.ClassDeclaration):
                for _member in getattr(_cls, "body", []) or []:
                    if not isinstance(_member, javalang.tree.FieldDeclaration):
                        continue
                    _ann_names = {
                        str(getattr(_a, "name", "") or "").strip().split('.')[-1]
                        for _a in (getattr(_member, "annotations", []) or [])
                    }
                    # Standard path: field has an explicit injection annotation.
                    # Additive path: CommonValidation/CommonValidations should use
                    # the same qualifier-contract resolution even if @Inject is absent.
                    if not ((_ann_names & _inj) or (_ann_names & _direct_qualifier)):
                        continue
                    _qual = {_a for _a in _ann_names if _a and _a not in _skip}
                    _is_wrapper_contract = _is_wrapper_contract_type(getattr(_member, "type", None))
                    # For wrapper-contract declarations (e.g. Instance<T>), allow
                    # contract-only resolution even when no qualifier annotation exists.
                    if not _qual and not _is_wrapper_contract:
                        continue
                    _contract_types = _extract_declared_contract_types(getattr(_member, "type", None))
                    _contract_signatures = _extract_declared_contract_signatures(getattr(_member, "type", None))
                    for _decl in getattr(_member, "declarators", []) or []:
                        _vn = getattr(_decl, "name", None)
                        if _vn:
                            out[_vn] = {
                                "annotations": set(_qual),
                                "contract_types": set(_contract_types),
                                "contract_signatures": set(_contract_signatures),
                                "wrapper_contract": bool(_is_wrapper_contract),
                            }
        except Exception:
            return out
        return out

    def _collect_injected_field_names_from_source(_src_text):
        out = set()
        txt = str(_src_text or "")
        if not txt:
            return out
        for _m in _inject_field_decl_re.finditer(txt):
            _vn = str(_m.group(1) or "").strip()
            if _vn:
                out.add(_vn)
        return out

    
    _field_decl_re = re.compile(
        r'''
        (?:@\w+(?:\([^)]*\))?\s*)*                                    # annotations e.g. @Autowired
        (?:(?:private|public|protected|static|final|transient|volatile)\s+)*  # modifiers
        ([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:<[^>]+>)?) # ClassName, nested type, or fully-qualified package type
        \s+
        ([a-z][A-Za-z0-9_]*)                                            # variableName (lowercase start)
        \s*(?:[=;,):])                                                  # followed by = ; , ) or : (enhanced-for)
        ''',
        re.MULTILINE | re.VERBOSE
    )
    _invalid_decl_types = {
        str(token).lower()
        for token in (
            list(details.get("control_keywords", []))
            + ["return", "throw", "throws", "new", "this", "super"]
        )
    }

    def _build_var_map(file_content):
        """
        Scan a Java source file for all variable declarations and return
        a dict of { variable_name -> ClassName } (generics stripped).

        Handles:
          private UserService userService;
          private final UserService userService;
          final ObjectService request;
          static UserService instance;
          @Autowired OrderRepo orderRepo;
          List<User> users = new ArrayList<>();
          public MyCtrl(final ObjectService request, OrderRepo repo)
        """
        def _type_rank(_type_name):
            if not isinstance(_type_name, str) or not _type_name:
                return 0
            if "." in _type_name and _type_name[0].islower():
                return 3  # strongest: package-qualified FQN
            if "." in _type_name:
                return 2  # nested type, still more specific than simple
            return 1      # simple class name

        var_map = {}
        for m in _field_decl_re.finditer(file_content):
            raw_cls = m.group(1).split('<')[0].strip().rstrip('[]')
            raw_cls_lower = raw_cls.lower()

            # Guard against statement fragments like `return requestDetails;`
            # being misread as declarations and overwriting the real type map.
            if not raw_cls:
                continue
            if raw_cls_lower in _invalid_decl_types:
                continue
            if raw_cls[0].islower() and '.' not in raw_cls:
                continue

            parts = raw_cls.split('.') if raw_cls else []
            # For package-qualified types (first segment lowercase, e.g. nl.row.path.ClassName):
            #   store the FULL FQN so _enrich_call_with_path can route through
            #   _resolve_fqn_path and pick the correct source file unambiguously.
            # For nested types (all segments uppercase, e.g. Outer.Inner):
            #   keep outer class (first segment) â€” it owns the methods.
            if len(parts) > 1 and any(p and (p[0].islower() or p[0] == '_') for p in parts[:-1]):
                cls = raw_cls  # full FQN: 'nl.row.path.ClassName'
            else:
                cls = parts[0] if parts else raw_cls
            var = m.group(2)
            prev = var_map.get(var)
            if (not prev) or (_type_rank(cls) > _type_rank(prev)):
                var_map[var] = cls
        return var_map

    def _extract_class_from_mapped(mapped_cls):
        """Return the usable class token from a mapped type string.

        ``object_class_map`` and ``_build_var_map`` may now store either:
          - a simple class name:    'ClassName'
          - a package-qualified FQN: 'nl.path.Copy.Class'  (first char lowercase)
          - a nested type:           'OuterClass.InnerClass'  (first char uppercase)

        Rules:
          - FQN (first char lowercase, e.g. 'nl.path.Copy.Class'):
              Return the LAST segment ('Class') â€” that is the actual class name.
              The full FQN is preserved in var_map; _enrich_call_with_path's
              UpperCamelCase branch scans var_map values for a FQN whose last
              segment matches this simple name and uses it to call _resolve_fqn_path
              directly.  If we returned the full FQN here instead, the emitted call
              string would be 'nl.path.Copy.Class.method()' and _base_class_re would
              split on the first dot giving cls_name='nl' (treated as a variable) â€”
              the variable lookup would fail and the call would not be enriched.
          - Nested type (first char uppercase, e.g. 'Outer.Inner'):
              Return the FIRST segment ('Outer') â€” it owns the methods.
          - Simple name (no dots): return as-is.
        """
        if not isinstance(mapped_cls, str) or not mapped_cls:
            return mapped_cls
        c = mapped_cls.strip()
        c = re.sub(r'@\w+(?:\([^)]*\))?\s*', '', c)
        c = re.sub(
            r'^(?:(?:public|protected|private|static|final|abstract|synchronized|native|strictfp|default|transient|volatile)\s+)+',
            '',
            c,
            flags=re.IGNORECASE,
        ).strip()
        if ' ' in c:
            c = c.split()[-1]
        if '.' not in c:
            return c
        if c[0].islower():
            # Package-qualified FQN â€” return only the class name (last segment).
            # The full FQN stays in var_map and is used as a FQN-hint by
            # _enrich_call_with_path to resolve the correct source file.
            return c.split('.')[-1]
        # Nested / dotted UpperCamelCase â€” outer class owns the members
        return c.split('.')[0]

    def _extract_class_name_from_call(call, var_map=None):
        """
        Resolve a call string to the class name it targets.
        Case 1: UserService.method()  -> first token is UpperCase -> return directly
        Case 2: userService.method()  -> first token is lowercase -> look up in var_map
        Returns None for bare method() calls (same-file, no BFS needed).

        """
        if not isinstance(call, str) or '.' not in call:
            return None
        base = call.split('.')[0].strip()
        if not base:
            return None
        # Case 1: already a class name (UpperCamelCase)
        if base[0].isupper():
            return base
        # Case 2: lowercase variable â€” resolve via field/param declarations
        if var_map:
            resolved = var_map.get(base)
            if resolved:
                return resolved
        return None

    def _extract_method_block(source_text, method_name):
        """Return source slice for the first matching method declaration/body."""
        if not isinstance(source_text, str) or not source_text or not method_name:
            return ""
        sig_pat = re.compile(
            r'\b' + re.escape(method_name) + r'\s*\([^)]*\)\s*(?:throws\s+[^\{]+)?\{',
            re.MULTILINE,
        )
        m = sig_pat.search(source_text)
        if not m:
            return ""
        start = m.start()
        brace_start = source_text.find('{', m.end() - 1)
        if brace_start == -1:
            return source_text[start:m.end()]

        depth = 0
        end = None
        for i in range(brace_start, len(source_text)):
            ch = source_text[i]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end is None:
            return source_text[start:]
        return source_text[start:end + 1]

    def _parse_import_context(_source_text):
        _imports = {}
        _wildcards = []
        for _imp in _import_decl_re.findall(_source_text or ""):
            _imp = (_imp or "").strip()
            if not _imp:
                continue
            if _imp.endswith(".*"):
                _wildcards.append(_imp[:-2])
                continue
            _imports[_imp.split('.')[-1]] = _imp

        _pkg_m = _pkg_decl_re.search(_source_text or "")
        _pkg = _pkg_m.group(1) if _pkg_m else None
        return _imports, _wildcards, _pkg

    def _file_declares_member_bfs(_path, _member_name):
        if not _path or not _member_name:
            return False
        try:
            _txt = _bfs_read(_path)
        except Exception:
            return False
        _member = str(_member_name).strip()
        _pat = re.compile(
            r'\b(?:public|protected|private|static|final|abstract|synchronized|native|strictfp|default)?\s*'
            + r'[\w<>,\[\]\s]+\b' + re.escape(_member) + r'\s*\(',
            re.MULTILINE,
        )
        if _pat.search(_txt or ""):
            return True

        # Constructor declarations have no return type; check those too.
        _ctor_pat = re.compile(
            r'^\s*(?:public|protected|private)\s+'
            + re.escape(_member)
            + r'\s*\(',
            re.MULTILINE,
        )
        return bool(_ctor_pat.search(_txt or ""))

    def _choose_dep_candidate(_candidates, _caller_file=None, _caller_pkg=None, _member_name=None):
        if not _candidates:
            return None
        if len(_candidates) == 1:
            return _candidates[0]

        _caller_dir = os.path.dirname(os.path.abspath(_caller_file)) if _caller_file else ""
        _pkg_seg = None
        if isinstance(_caller_pkg, str) and _caller_pkg.strip():
            _pkg_seg = "/" + _caller_pkg.strip().replace('.', '/') + "/"

        def _score(_p):
            _pn = _p.replace('\\', '/').lower()
            _score_val = 0
            if _pkg_seg and _pkg_seg.lower() in _pn:
                _score_val += 50
            if _caller_dir and os.path.dirname(os.path.abspath(_p)) == _caller_dir:
                _score_val += 30
            if _member_name and _file_declares_member_bfs(_p, _member_name):
                _score_val += 20
            if "/src/main/java/" in _pn:
                _score_val += 5
            if "/src/test/java/" in _pn:
                _score_val -= 5
            return _score_val

        return sorted(_candidates, key=_score, reverse=True)[0]

    def _resolve_dep_path(_cls_name, _caller_imports, _caller_file=None, _caller_pkg=None, _caller_wildcards=None, _member_name=None):
        """
        Resolve class token to source file path, prioritizing caller imports
        when simple names are ambiguous across modules.
        """
        if not isinstance(_cls_name, str) or not _cls_name.strip():
            return None

        _cls_name = _cls_name.strip()

        # FQN directly from declaration map (e.g. nl.pkg.RequestDetails)
        if "." in _cls_name and _cls_name[0].islower():
            _fqn_hits = _fqn_to_paths.get(_cls_name, [])
            if _fqn_hits:
                return _choose_dep_candidate(_fqn_hits, _caller_file, _caller_pkg, _member_name)

            # Fallback: if the exact FQN key was indexed differently, resolve by
            # matching the expected package-path suffix among same simple-name files.
            _simple_fqn = _cls_name.split('.')[-1]
            _suffix_fqn = "/" + _cls_name.replace('.', '/') + adapter.file_extension()
            for _p in _class_to_paths.get(_simple_fqn, []):
                if _p.replace('\\', '/').lower().endswith(_suffix_fqn.lower()):
                    return _p

        _simple = _cls_name.split('.')[-1]

        # Explicit import in caller file wins over global simple-name lookup.
        _import_fqn = (_caller_imports or {}).get(_simple)
        if _import_fqn:
            _fqn_hits = _fqn_to_paths.get(_import_fqn, [])
            if _fqn_hits:
                return _choose_dep_candidate(_fqn_hits, _caller_file, _caller_pkg, _member_name)

            # Fallback if package indexing missed this file: suffix match.
            _suffix = "/" + _import_fqn.replace('.', '/') + adapter.file_extension()
            for _p in _class_to_paths.get(_simple, []):
                if _p.replace('\\', '/').lower().endswith(_suffix.lower()):
                    return _p

        # Wildcard imports: import a.b.* -> a.b.ClassName
        for _pkg in (_caller_wildcards or []):
            _wfqn = "{}.{}".format(_pkg, _simple)
            _wfqn_hits = _fqn_to_paths.get(_wfqn, [])
            if _wfqn_hits:
                return _choose_dep_candidate(_wfqn_hits, _caller_file, _caller_pkg, _member_name)

        _simple_hits = _class_to_paths.get(_simple, [])
        return _choose_dep_candidate(_simple_hits, _caller_file, _caller_pkg, _member_name)

    def _extract_called_member(_call):
        if not isinstance(_call, str) or '.' not in _call:
            return None
        _rest = _call.split('.', 1)[1].strip()
        if not _rest:
            return None
        _member = _rest.split('(')[0].split('.')[0].strip()
        return _member or None

    def _class_declares_method(_class_name, _method_name):
        if not _class_name or not _method_name:
            return False
        return _method_name in (_class_methods.get(_class_name) or set())

    def _resolve_owner_class_for_method(_class_name, _method_name):
        """
        Resolve concrete owner for a method starting from class/interface name:
        class itself -> extends chain -> implementing classes.
        """
        if not _class_name or not _method_name:
            return _class_name

        _start = _class_name.split('.')[-1]
        _visited = set()

        def _walk_extends(_cls):
            _cur = _cls
            _chain_seen = set()
            while _cur and _cur not in _chain_seen:
                _chain_seen.add(_cur)
                if (
                    _class_declares_method(_cur, _method_name)
                    and _class_kind.get(_cur) != "interface"
                ):
                    return _cur
                _cur = _class_extends.get(_cur)
            return None

        _owner = _walk_extends(_start)
        if _owner:
            return _owner

        # If start type is interface or abstract API, look for implementations.
        _impl_candidates = list(_interface_to_impls.get(_start, set()))
        if (_start + "Impl") in _class_to_paths:
            _impl_candidates.append(_start + "Impl")
        if (_start + "Implementation") in _class_to_paths:
            _impl_candidates.append(_start + "Implementation")

        # for _impl in _impl_candidates:
        #     if _impl in _visited:
        #         continue
        #     _visited.add(_impl)
        #     _impl_owner = _walk_extends(_impl)
        #     if _impl_owner:
        #         return _impl_owner

        # return _start

        for _impl in _impl_candidates:
            if _impl in _visited:
                continue
            _visited.add(_impl)
            _impl_owner = _walk_extends(_impl)
            if _impl_owner:
                return _impl_owner

        # Also check concrete subclasses that extend this class (abstract base pattern):
        # classA extends ClassB â†’ if ClassB.build is called, prefer ClassA.build
        for _sub in _concrete_subclasses.get(_start, []):
            if _sub in _visited:
                continue
            _visited.add(_sub)
            if _class_declares_method(_sub, _method_name):
                return _sub

        return _start

    _inherited_var_map_cache = {}

    def _build_inherited_var_map_for_file(_file_path, _source_text, _imports, _wildcards, _pkg):
        """Collect variable->type mappings from parent classes in the extends chain."""
        _cache_key = (_as_abs_norm(_file_path), hash(_source_text or ""))
        _cached = _inherited_var_map_cache.get(_cache_key)
        if _cached is not None:
            return dict(_cached)

        out = {}
        visited_paths = set()

        cur_source = _source_text or ""
        cur_file = _file_path
        cur_imports = dict(_imports or {})
        cur_wildcards = list(_wildcards or [])
        cur_pkg = _pkg

        while True:
            _m_ext = _extends_decl_re.search(cur_source)
            if not _m_ext:
                break
            _super_token = re.sub(r'<.*?>', '', (_m_ext.group(1) or "")).strip()
            if not _super_token:
                break

            _super_path = _resolve_dep_path(
                _super_token,
                cur_imports,
                _caller_file=cur_file,
                _caller_pkg=cur_pkg,
                _caller_wildcards=cur_wildcards,
                _member_name=None,
            )
            if not _super_path:
                break

            _super_abs = os.path.abspath(_super_path)
            if _super_abs in visited_paths:
                break
            visited_paths.add(_super_abs)

            try:
                _super_code = _bfs_read(_super_abs)
            except Exception:
                break
            if not _super_code:
                break

            _super_var_map = _build_var_map(_super_code)
            for _k, _v in (_super_var_map or {}).items():
                out.setdefault(_k, _v)

            cur_source = _super_code
            cur_file = _super_abs
            cur_imports, cur_wildcards, cur_pkg = _parse_import_context(_super_code)

        _inherited_var_map_cache[_cache_key] = dict(out)
        return out

    _inject_presence_cache = {}
    _inject_field_presence_cache = {}

    def _file_has_inject_annotation(_path, _class_name=None):
        if not _path:
            return False
        _target_class = str(_class_name or "").strip().split('.')[-1]
        _norm = _as_abs_norm(_path)
        _cache_key = (_norm, _target_class)
        _cached_any = _inject_presence_cache.get(_cache_key)
        if _cached_any is not None:
            return _cached_any
        try:
            _txt = _bfs_read(_path)
        except Exception:
            _txt = ""

        # Only class-level @Inject is considered valid.
        _has = False
        try:
            _tree = javalang.parse.parse(_txt or "")
        except Exception:
            _tree = None

        if _tree is not None:
            try:
                for _, _cls in _tree.filter(javalang.tree.ClassDeclaration):
                    _nm = str(getattr(_cls, "name", "") or "").strip()
                    if _target_class and _nm != _target_class:
                        continue
                    _anns = getattr(_cls, "annotations", []) or []
                    for _a in _anns:
                        _an = str(getattr(_a, "name", "") or "").strip().split('.')[-1]
                        if _an == "Inject":
                            _has = True
                            break
                    if _has:
                        break
            except Exception:
                _has = False

        # Fallback regex when AST parse fails.
        if not _has:
            if _target_class:
                _class_level_inject_pat = re.compile(
                    r'@Inject\b(?:\s*@\w+(?:\([^)]*\))?\s*)*\s*'
                    r'(?:(?:public|protected|private|abstract|final|static)\s+)*class\s+'
                    + re.escape(_target_class) +
                    r'\b',
                    re.MULTILINE,
                )
            else:
                _class_level_inject_pat = re.compile(
                    r'@Inject\b(?:\s*@\w+(?:\([^)]*\))?\s*)*\s*'
                    r'(?:(?:public|protected|private|abstract|final|static)\s+)*class\s+[A-Za-z_]\w*',
                    re.MULTILINE,
                )
            _has = bool(_class_level_inject_pat.search(_txt or ""))

        _inject_presence_cache[_cache_key] = _has
        return _has

    def _file_has_injected_field_decl(_path, _class_name=None):
        """True when target class declares at least one injected field.

        This is used for inherited-remap enqueue gating where many delegate
        classes inject collaborators on fields but do not carry class-level
        @Inject on the class declaration itself.
        """
        if not _path:
            return False
        _target_class = str(_class_name or "").strip().split('.')[-1]
        _norm = _as_abs_norm(_path)
        _cache_key = (_norm, _target_class)
        _cached = _inject_field_presence_cache.get(_cache_key)
        if _cached is not None:
            return _cached

        try:
            _txt = _bfs_read(_path)
        except Exception:
            _txt = ""

        _has = False
        try:
            _tree = javalang.parse.parse(_txt or "")
        except Exception:
            _tree = None

        if _tree is not None:
            try:
                _inj = {"Inject", "Autowired", "Resource", "EJB"}
                for _, _cls in _tree.filter(javalang.tree.ClassDeclaration):
                    _nm = str(getattr(_cls, "name", "") or "").strip()
                    if _target_class and _nm != _target_class:
                        continue
                    for _member in getattr(_cls, "body", []) or []:
                        if not isinstance(_member, javalang.tree.FieldDeclaration):
                            continue
                        _ann_names = {
                            str(getattr(_a, "name", "") or "").strip().split('.')[-1]
                            for _a in (getattr(_member, "annotations", []) or [])
                        }
                        if _ann_names & _inj:
                            _has = True
                            break
                    if _has:
                        break
            except Exception:
                _has = False

        # Regex fallback when AST parse fails.
        if not _has:
            _inj_field_re = re.compile(
                r'@(?:Inject|Autowired|Resource|EJB)\b\s*'
                r'(?:(?:public|private|protected|static|final|transient|volatile)\s+)*'
                r'[A-Za-z_][\w$.<>,\[\]\?\s]*\s+[a-z][A-Za-z0-9_]*\s*(?:=|;|,|\))',
                re.MULTILINE,
            )
            _has = bool(_inj_field_re.search(_txt or ""))

        _inject_field_presence_cache[_cache_key] = _has
        return _has

    _visited_paths = set()
    java_files = []          # ordered list of reachable abs paths
    _bfs_queue = deque()
    _entry_targets_by_file = {}
    _entry_targets_by_base = {}
    _entry_auto_sig_by_file_method = {}
    _entry_inherited_target_files = set()

    def _rebuild_entry_target_basename_index():
        _entry_targets_by_base.clear()
        for _p, _targets in (_entry_targets_by_file or {}).items():
            _bn = os.path.basename(str(_p or "")).strip().lower()
            if not _bn:
                continue
            _entry_targets_by_base.setdefault(_bn, []).extend(list(_targets or []))

    def _entry_targets_for_path(_path):
        _norm = _as_abs_norm(_path)
        _targets = _entry_targets_by_file.get(_norm, []) if _norm else []
        if _targets:
            return _targets
        _bn = os.path.basename(str(_path or "")).strip().lower()
        if not _bn:
            return []
        return _entry_targets_by_base.get(_bn, [])

    def _resolve_seed_path(path):
        if not isinstance(path, str) or not path.strip():
            return ""
        p = path.strip()
        if os.path.isabs(p):
            return p
        # Prefer project-root anchored relative path when available.
        p1 = os.path.join(app_folder, p)
        if os.path.isfile(p1):
            return p1
        # Fallback to current working directory resolution.
        return os.path.abspath(p)

    def _enqueue(path):
        abs_p = os.path.abspath(path)
        if abs_p not in _visited_paths and os.path.isfile(abs_p):
            _visited_paths.add(abs_p)
            java_files.append(abs_p)
            _bfs_queue.append(abs_p)
            if _verbose_debug:
                _entry_debug_print("[DEBUG][BFS_ENQUEUE] file={}".format(abs_p))

    def _resolve_inherited_entry_target(seed_file_path, target_method):
        """Map inherited method entry to the nearest superclass file declaring it."""
        if not seed_file_path or not target_method:
            return None

        try:
            seed_abs = os.path.abspath(str(seed_file_path))
        except Exception:
            return None

        seed_stem = os.path.splitext(os.path.basename(seed_abs))[0]
        if not seed_stem:
            return None

        parent = _class_extends.get(seed_stem)
        seen = set()
        while parent and parent not in seen:
            seen.add(parent)
            parent_simple = str(parent).split('.')[-1]
            parent_candidates = _class_to_paths.get(parent_simple, []) or []
            parent_file = _choose_dep_candidate(parent_candidates, seed_abs, None, target_method)
            if parent_file and _file_declares_member_bfs(parent_file, target_method):
                return os.path.abspath(parent_file)
            parent = _class_extends.get(parent_simple)
        return None

    # Seed BFS from both sources. Supports plain file entries and
    # method-scoped entries like: C:/x/y/MyClass.java.myMethod(String,int)
    _seed_files = []
    _seed_specs = []
    for _src in (service_files or []):
        if isinstance(_src, str) and _src.strip():
            _seed_specs.append(_parse_entry_method_spec(_src))
    for _src in (controller_files or []):
        if isinstance(_src, str) and _src.strip():
            _seed_specs.append(_parse_entry_method_spec(_src))

    for _spec in _seed_specs:
        if not _spec:
            continue
        _resolved_seed_file = _resolve_seed_path(_spec.get("file"))
        _seed_files.append(_resolved_seed_file)
        if _spec.get("is_method_entry"):
            _norm = _as_abs_norm(_resolved_seed_file)
            if _norm:
                _entry_targets_by_file.setdefault(_norm, []).append(
                    {
                        "method": _spec.get("method"),
                        "params": _spec.get("params"),
                        "signature_given": bool(_spec.get("signature_given")),
                    }
                )

                _seed_target_method = str(_spec.get("method") or "").strip()
                if _seed_target_method and not _file_declares_member_bfs(_resolved_seed_file, _seed_target_method):
                    _owner_file = _resolve_inherited_entry_target(_resolved_seed_file, _seed_target_method)
                    _owner_norm = _as_abs_norm(_owner_file)
                    if _owner_norm and _owner_norm != _norm:
                        # Mark inherited only when a different owner file is
                        # actually resolved. This avoids false inherited
                        # widening when method declaration detection is noisy.
                        _entry_inherited_target_files.add(_norm)
                        if _verbose_debug:
                            _entry_debug_print(
                                "[DEBUG][ENTRY_INHERITED_REMAP] seed_file={} method={} owner_file={}".format(
                                    _resolved_seed_file,
                                    _seed_target_method,
                                    _owner_file,
                                )
                            )
                        _entry_targets_by_file.setdefault(_owner_norm, []).append(
                            {
                                "method": _seed_target_method,
                                "params": _spec.get("params"),
                                "signature_given": bool(_spec.get("signature_given")),
                            }
                        )

    _rebuild_entry_target_basename_index()

    # Preserve order, remove duplicates by normalized absolute path.
    _seen_seed_norm = set()
    _seed_files_dedup = []
    for _sf in _seed_files:
        _norm = _as_abs_norm(_sf)
        if _norm and _norm not in _seen_seed_norm:
            _seen_seed_norm.add(_norm)
            _seed_files_dedup.append(_sf)

    if _verbose_debug:
        _entry_debug_print(
            "[DEBUG][BFS_SEEDS] service_files={}, controller_files={}, merged={}, method_scoped_files={}"
            .format(
                len(service_files or []),
                len(controller_files or []),
                len(_seed_files_dedup),
                len(_entry_targets_by_file),
            )
        )

    for _cf in _seed_files_dedup:
        if _verbose_debug:
            _entry_debug_print(f"[DEBUG] controller path exists: {os.path.isfile(_cf)} -> {_cf}")
        _enqueue(_cf)

    # Step 2: BFS â€” parse each file, extract callees, enqueue their files
    _strip_for_bfs = lambda text: re.sub(
        r'//.*?$|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
        '', text, flags=re.MULTILINE | re.DOTALL
    )

    _bfs_processed = 0
    _bfs_last_ui = 0

    def _update_bfs_progress():
        """Advance visible progress within the BFS window (8% -> 10%)."""
        nonlocal _bfs_last_ui
        _total_seen = len(_visited_paths)
        if _total_seen <= 0:
            return
        _ratio = float(_bfs_processed) / float(max(_total_seen, 1))
        _ratio = min(max(_ratio, 0.0), 1.0)
        _target = 8 + int(_ratio * 2)
        if _target > _bfs_last_ui:
            _bfs_last_ui = _target
            _pbar_goto(_target, f"BFS: discovering files... ({_bfs_processed}/{_total_seen})")

    _pbar.set_postfix_str("BFS: discovering files...")
    while _bfs_queue:
        _cur = _bfs_queue.popleft()
        if _verbose_debug:
            _entry_debug_print("[DEBUG][BFS_VISIT] file={}".format(_cur))
        _bfs_processed += 1
        _update_bfs_progress()
        try:
            _raw = _bfs_read(_cur)
        except Exception as _e:
            log_time(f"BFS: cannot read {_cur}: {_e}")
            _update_bfs_progress()
            continue

        _code = html.unescape(_raw)
        _code_clean = _strip_for_bfs(_code)
        _raw_calls = []

        _cur_norm = _as_abs_norm(_cur)
        _cur_targets = _entry_targets_for_path(_cur)

        def _ast_method_matches_entry_scope(_method_name, _method_node):
            if not _cur_targets:
                return True
            if _cur_norm in _entry_inherited_target_files:
                return True

            _actual_name = str(_method_name or "").strip()
            _actual_params = _method_meta_param_tuple(_method_node)
            for _t in _cur_targets:
                _target_name = str(_t.get("method") or "").strip()
                if _actual_name != _target_name:
                    continue
                _target_params = _t.get("params")
                if _target_params is not None:
                    if tuple(_target_params) == tuple(_actual_params):
                        return True
                    continue
                _key = (_cur_norm, _target_name)
                _selected = _entry_auto_sig_by_file_method.get(_key)
                if _selected is None:
                    _entry_auto_sig_by_file_method[_key] = tuple(_actual_params)
                    return True
                if tuple(_selected) == tuple(_actual_params):
                    return True
            return False

        def _collect_calls_from_ast(_ast_obj, _source_text):
            if not _ast_obj:
                return
            _declared = []
            for _, _, _type_node in adapter.get_declared_types(_ast_obj):
                for _method_name, _method_node in adapter.get_methods_in_type(_type_node):
                    _declared.append((_method_name, _method_node, _type_node))

            if _cur_targets and _cur_norm not in _entry_inherited_target_files:
                _target_names = {
                    str(_t.get("method") or "").strip()
                    for _t in _cur_targets
                    if str(_t.get("method") or "").strip()
                }
                _declared_names = {
                    str(_mn or "").strip()
                    for _mn, _, _ in _declared
                    if str(_mn or "").strip()
                }
                if _target_names and not (_declared_names & _target_names):
                    # Do not auto-widen inherited scope here. Inherited remap is
                    # handled during seed parsing when owner file is explicit.
                    pass

            for _method_name, _method_node, _type_node in _declared:
                if not _ast_method_matches_entry_scope(_method_name, _method_node):
                    continue
                for _c in (adapter.find_calls_in_method(_type_node, _method_node, _source_text) or []):
                    _raw_calls.append((_c, _method_name))
                _method_src = _extract_method_block(_source_text, _method_name)
                if _method_src:
                    for _mret in _return_qual_call_re.finditer(_method_src):
                        _owner = (_mret.group(1) or "").strip()
                        _member = (_mret.group(2) or "").strip()
                        if _owner and _member:
                            _raw_calls.append(("{}.{}()".format(_owner, _member), _method_name))

        try:
            _ast = adapter.parse_ast(_code_clean)
            if _ast:
                _collect_calls_from_ast(_ast, _code_clean)
            else:
                raise RuntimeError("AST failed")
        except Exception:
            try:
                _fb = adapter.fallback_parse(_raw)
                for _rec in _fb.get('per_method_calls', []):
                    _c = _rec.get('object_call')
                    if _c:
                        _raw_calls.append((_c, _rec.get('method_name') or 'UnknownMethod'))
                for _c in _fb.get('filtered_calls', []):
                    if _c:
                        _raw_calls.append((_c, 'UnknownMethod'))
            except Exception as _e2:
                log_time(f"BFS fallback failed for {_cur}: {_e2}")

        # AST-only safety pass on raw source: catches cases where stripped-source
        # parsing drops context but raw parse still succeeds.
        try:
            _ast_raw = adapter.parse_ast(_code)
        except Exception:
            _ast_raw = None
        if _ast_raw:
            _before = len(_raw_calls)
            _collect_calls_from_ast(_ast_raw, _code)
            if _verbose_debug and len(_raw_calls) > _before:
                _entry_debug_print(
                    "[DEBUG][BFS_AST_RAW] extra_calls_added={} file={}".format(
                        len(_raw_calls) - _before,
                        _cur,
                    )
                )

        _injected_qualified_fields = {}
        if _ast_raw:
            _injected_qualified_fields = _collect_injected_qualified_fields_from_ast(_ast_raw)
        elif _ast:
            _injected_qualified_fields = _collect_injected_qualified_fields_from_ast(_ast)
        _injected_field_names = _collect_injected_field_names_from_source(_code)

        # Build variable->class map for this file so lowercase object names
        # (e.g. userService -> UserService, request -> ObjectService) are resolved.
        _var_map = _build_var_map(_code)
        _method_varmap_cache = {}
        _expand_call_cache = {}
        _call_resolution_cache = {}
        _seen_bfs_pairs = set()

        def _get_method_var_map(_method_name):
            if not _method_name or _method_name == 'UnknownMethod':
                return _file_scope_var_map
            if _method_name in _method_varmap_cache:
                return _method_varmap_cache[_method_name]

            _method_text = _extract_method_block(_code, _method_name)
            _local_map = _build_var_map(_method_text) if _method_text else {}

            # Method-local declarations must override file-level/field declarations
            # when variable names collide (e.g. builder in multiple methods).
            _merged = dict(_file_scope_var_map)
            _merged.update(_local_map)
            _method_varmap_cache[_method_name] = _merged
            return _merged

        _caller_imports, _caller_wildcards, _caller_pkg = _parse_import_context(_code)
        _inherited_var_map = _build_inherited_var_map_for_file(
            _cur,
            _code,
            _caller_imports,
            _caller_wildcards,
            _caller_pkg,
        )
        _file_scope_var_map = dict(_inherited_var_map)
        _file_scope_var_map.update(_var_map)

        def _expand_bfs_calls(_call_text):
            """Return outer call + nested qualified calls as independent entries.

            Example:
              A.setX(B.getY()) -> ["A.setX(B.getY())", "B.getY()"]
            """
            if not isinstance(_call_text, str):
                return []
            s = _call_text.strip()
            if not s:
                return []
            _cached = _expand_call_cache.get(s)
            if _cached is not None:
                return _cached
            out = [s]
            first_seen = False
            seen = set([s])
            for _m in _bfs_qual_inv_pat.finditer(s):
                if not first_seen:
                    first_seen = True
                    continue
                _owner = (_m.group(1) or "").strip()
                _member = (_m.group(2) or "").strip()
                if not _owner or not _member:
                    continue
                _nested = "{}.{}()".format(_owner, _member)
                if _nested not in seen:
                    seen.add(_nested)
                    out.append(_nested)
            _expand_call_cache[s] = out
            return out

        def _extract_simple_call_arg_tokens(_call_text):
            """Best-effort extraction of top-level call argument tokens.

            Returns simple identifier tokens only (optionally prefixed with this.).
            """
            if not isinstance(_call_text, str):
                return []
            _s = _call_text.strip()
            if not _s:
                return []

            _open = _s.find('(')
            if _open == -1:
                return []

            _depth = 0
            _close = -1
            for _i in range(_open, len(_s)):
                _ch = _s[_i]
                if _ch == '(':
                    _depth += 1
                elif _ch == ')':
                    _depth -= 1
                    if _depth == 0:
                        _close = _i
                        break
            if _close <= _open:
                return []

            _inside = _s[_open + 1:_close]
            _parts = _split_top_level_commas(_inside)
            _out = []
            for _p in _parts:
                _tok = str(_p or "").strip()
                if not _tok:
                    continue
                if re.fullmatch(r'(?:this\.)?[A-Za-z_][A-Za-z0-9_]*', _tok):
                    _out.append(_tok)
            return _out

        for _call, _parent_method in _raw_calls:
            _maybe_refresh_pbar()
            _scoped_var_map = _get_method_var_map(_parent_method)
            for _call_one in _expand_bfs_calls(_call):
                _pair_key = (_parent_method, _call_one)
                if _pair_key in _seen_bfs_pairs:
                    continue
                _seen_bfs_pairs.add(_pair_key)
                _cls = _extract_class_name_from_call(_call_one, _scoped_var_map)
                # print(f"[BFS] call={_call_one!r:50} -> class={_cls}")
                if _cls:
                    _member = _extract_called_member(_call_one)
                    _owner_cls = _cls
                    _res_key = (_cls, _member, _cur)
                    _dep = _call_resolution_cache.get(_res_key)

                    # Additive rule: for obj.method() where obj is an injected field,
                    # use qualifier annotation(s) + declaration contract type(s), then
                    # enqueue only annotated classes that implement those contracts.
                    _owner_tok = _call_one.split('.', 1)[0].strip() if '.' in _call_one else ""
                    _owner_tok = _owner_tok[5:] if _owner_tok.startswith("this.") else _owner_tok
                    if _owner_tok and _owner_tok in _injected_qualified_fields:
                        _inj_meta = _injected_qualified_fields.get(_owner_tok, {})
                        if isinstance(_inj_meta, dict):
                            _inj_ann = set(_inj_meta.get("annotations", set()))
                            _inj_contracts = {
                                str(_c).strip().split('.')[-1]
                                for _c in (_inj_meta.get("contract_types", set()) or set())
                                if str(_c or "").strip()
                            }
                            _inj_contract_sigs = {
                                _normalize_contract_signature(_c)
                                for _c in (_inj_meta.get("contract_signatures", set()) or set())
                                if str(_c or "").strip()
                            }
                            _use_qualified_lookup = bool(_inj_meta.get("wrapper_contract", False))
                        else:
                            _inj_ann = set(_inj_meta or set())
                            _inj_contracts = set()
                            _inj_contract_sigs = set()
                            _use_qualified_lookup = False

                        # Qualifier-annotation expansion is only for wrapper contract
                        # declarations (e.g. Instance<Validator<T>>). For direct field
                        # declarations (e.g. Class_A variable), call resolution should go
                        # straight to the declared class via var-map/imports.
                        if _use_qualified_lookup:
                            _allowed_impl_classes = set()
                            for _contract in _inj_contracts:
                                _allowed_impl_classes.update(_interface_to_impls.get(_contract, set()))
                                _allowed_impl_classes.update(_interface_to_impls.get(_contract + "s", set()))
                                if _contract.endswith("s"):
                                    _allowed_impl_classes.update(_interface_to_impls.get(_contract[:-1], set()))

                            # Contract-only fallback: when no qualifier annotation is
                            # present, still expand by strict contract/implements match.
                            if not _inj_ann and (_inj_contracts or _inj_contract_sigs):
                                _candidate_classes = set(_allowed_impl_classes)
                                if _inj_contract_sigs:
                                    _candidate_classes.update({
                                        _cls_name
                                        for _cls_name in (_class_impl_signatures or {}).keys()
                                        if _class_matches_contract_signatures(_cls_name, _inj_contract_sigs)
                                    })
                                for _cand_cls in sorted(_candidate_classes):
                                    _cand_path = _resolve_owner_file_for_chain(
                                        _cand_cls,
                                        _cur,
                                        member_name=None,
                                    )
                                    if not _cand_path:
                                        _cand_path = _choose_dep_candidate(
                                            list(_class_to_paths.get(_cand_cls, [])),
                                            _cur,
                                            _caller_pkg,
                                            None,
                                        )
                                    if _cand_path and _cand_path != _cur:
                                        _enqueue(_cand_path)

                            for _ann in sorted(_inj_ann):
                                for _ann_key in _annotation_lookup_names(_ann):
                                    for _ann_path in sorted(_annotation_to_paths.get(_ann_key, set())):
                                        if _ann_path and _ann_path != _cur:
                                            if _inj_contracts:
                                                _ann_cls = os.path.splitext(os.path.basename(_ann_path))[0]
                                                if (
                                                    _ann_cls not in _allowed_impl_classes
                                                    and _ann_cls not in _inj_contracts
                                                ):
                                                    continue
                                            if _inj_contract_sigs:
                                                _ann_cls = os.path.splitext(os.path.basename(_ann_path))[0]
                                                if not _class_matches_contract_signatures(_ann_cls, _inj_contract_sigs):
                                                    continue
                                            _enqueue(_ann_path)

                    # If declaration already gives a package-qualified FQN (e.g.
                    # nl.rabobank.schemas...SearchOptions), resolve that exact file first.
                    # This avoids collapsing to an ambiguous simple owner name.
                    if _dep is None and isinstance(_cls, str) and "." in _cls and _cls[0].islower():
                        _dep = _resolve_dep_path(
                            _cls,
                            _caller_imports,
                            _caller_file=_cur,
                            _caller_pkg=_caller_pkg,
                            _caller_wildcards=_caller_wildcards,
                            _member_name=_member,
                        )

                    if _dep is None:
                        _owner_cls = _resolve_owner_class_for_method(_cls, _member) if _member else _cls
                        _dep = _resolve_dep_path(
                            _owner_cls,
                            _caller_imports,
                            _caller_file=_cur,
                            _caller_pkg=_caller_pkg,
                            _caller_wildcards=_caller_wildcards,
                            _member_name=_member,
                        )
                    if _dep is None and _owner_cls != _cls:
                        _dep = _resolve_dep_path(
                            _cls,
                            _caller_imports,
                            _caller_file=_cur,
                            _caller_pkg=_caller_pkg,
                            _caller_wildcards=_caller_wildcards,
                            _member_name=_member,
                        )
                    _call_resolution_cache[_res_key] = _dep
                    if _dep:
                        _debug_calls = {"searchCriteria.setPaymentInformationIdentification", "requestSearchCriteria.getPaymentInformationIdentification"}  # method names to watch
                        if _verbose_debug and any(_call_one.startswith(d) for d in _debug_calls):
                            print(f"[BFS] {_call_one} -> {_dep} (owner={_owner_cls}, parent_method={_parent_method})")
                        _enqueue(_dep)

                    # Additive BFS rule: when injected fields are passed as
                    # arguments (without direct qualifier call), enqueue their
                    # owning class files so their methods become parse-reachable.
                    # Example: paymentHandler.validate(..., paymentOrderValidatorDelegate, ...)
                    for _arg_tok in _extract_simple_call_arg_tokens(_call_one):
                        _arg_name = _arg_tok[5:].strip() if _arg_tok.startswith("this.") else _arg_tok
                        if not _arg_name or (
                            _arg_name not in _injected_qualified_fields
                            and _arg_name not in _injected_field_names
                        ):
                            continue
                        _mapped = _scoped_var_map.get(_arg_name)
                        if not _mapped:
                            continue
                        _arg_cls = _extract_class_from_mapped(_mapped)
                        _arg_cls = strip_generics(str(_arg_cls or "").split('.')[-1]).strip()
                        if not _arg_cls:
                            continue
                        _arg_dep = _resolve_dep_path(
                            _arg_cls,
                            _caller_imports,
                            _caller_file=_cur,
                            _caller_pkg=_caller_pkg,
                            _caller_wildcards=_caller_wildcards,
                            _member_name=None,
                        )
                        if _arg_dep and _arg_dep != _cur:
                            _enqueue(_arg_dep)
                            if _verbose_debug and _arg_name == "paymentOrderValidatorDelegate":
                                _entry_debug_print(
                                    "[DEBUG][BFS_ARG_ENQUEUE] tok={} cls={} dep={} caller_file={}".format(
                                        _arg_name,
                                        _arg_cls,
                                        _arg_dep,
                                        _cur,
                                    )
                                )

                    # When call ownership resolves to a parent/interface method,
                    # keep the declared class path as well so delegate classes
                    # (e.g. RequestorValidatorDelegate) are still parsed even when
                    # the resolved member is inherited (e.g. AbstractValidator.validate).
                    if _owner_cls and _cls and str(_owner_cls) != str(_cls):
                        _declared_dep = _resolve_dep_path(
                            _cls,
                            _caller_imports,
                            _caller_file=_cur,
                            _caller_pkg=_caller_pkg,
                            _caller_wildcards=_caller_wildcards,
                            _member_name=_member,
                        )
                        if _declared_dep and os.path.abspath(_declared_dep) != os.path.abspath(_dep):
                            _declared_kind = str(_class_kind.get(str(_cls), "") or "").lower()
                            _is_contract_owner = _declared_kind in {"interface", "annotation"}
                            _is_entry_target_class_file = bool(
                                _entry_targets_by_file.get(_as_abs_norm(_declared_dep), [])
                            )

                            # Do not force class-level @Inject for declared
                            # contract owners (e.g. Enricher.enrich in foreach).
                            # The injection may live on wrapper/list fields in
                            # the delegate class, not on the interface itself.
                            _allow_declared_owner = (
                                _is_entry_target_class_file
                                or
                                _is_contract_owner
                                or _file_has_inject_annotation(_declared_dep, _cls)
                                or _file_has_injected_field_decl(_declared_dep, _cls)
                            )

                            if _allow_declared_owner:
                                if _verbose_debug:
                                    if _is_contract_owner:
                                        _entry_debug_print(
                                            "[DEBUG][ENTRY_INHERITED_REMAP][ALLOW_CONTRACT_OWNER] call={} declared_owner={} resolved_owner={} declared_file={} resolved_file={}".format(
                                                _call_one,
                                                _cls,
                                                _owner_cls,
                                                _declared_dep,
                                                _dep,
                                            )
                                        )
                                    elif _is_entry_target_class_file:
                                        _entry_debug_print(
                                            "[DEBUG][ENTRY_INHERITED_REMAP][ALLOW_ENTRY_TARGET_CLASS] call={} declared_owner={} resolved_owner={} declared_file={} resolved_file={}".format(
                                                _call_one,
                                                _cls,
                                                _owner_cls,
                                                _declared_dep,
                                                _dep,
                                            )
                                        )
                                    elif _file_has_injected_field_decl(_declared_dep, _cls):
                                        _entry_debug_print(
                                            "[DEBUG][ENTRY_INHERITED_REMAP][ALLOW_INJECTED_FIELD_OWNER] call={} declared_owner={} resolved_owner={} declared_file={} resolved_file={}".format(
                                                _call_one,
                                                _cls,
                                                _owner_cls,
                                                _declared_dep,
                                                _dep,
                                            )
                                        )
                                    else:
                                        _entry_debug_print(
                                            "[DEBUG][ENTRY_INHERITED_REMAP] call={} declared_owner={} resolved_owner={} declared_file={} resolved_file={}".format(
                                                _call_one,
                                                _cls,
                                                _owner_cls,
                                                _declared_dep,
                                                _dep,
                                            )
                                        )
                                _enqueue(_declared_dep)
                            elif _verbose_debug:
                                _entry_debug_print(
                                    "[DEBUG][ENTRY_INHERITED_REMAP][SKIP_NO_INJECT] call={} declared_owner={} declared_file={}".format(
                                        _call_one,
                                        _cls,
                                        _declared_dep,
                                    )
                                )

                    # Receiver fallback (inheritance rule): when owner resolution
                    # maps a lower-case receiver call to an inherited owner method,
                    # enqueue Class_1 only if:
                    #   1) Class_1 extends Class_2 (resolved owner), and
                    #   2) Class_1 source contains @Inject.
                    if _owner_cls and _cls and str(_owner_cls) != str(_cls):
                        _owner_tok_local = _owner_tok
                        if _owner_tok_local and _owner_tok_local[:1].islower():
                            _declared_dep = _resolve_dep_path(
                                _cls,
                                _caller_imports,
                                _caller_file=_cur,
                                _caller_pkg=_caller_pkg,
                                _caller_wildcards=_caller_wildcards,
                                _member_name=_member,
                            )
                            if _declared_dep and _declared_dep != _cur:
                                _cls_simple = strip_generics(str(_cls or "").split('.')[-1]).strip()
                                _owner_simple = strip_generics(str(_owner_cls or "").split('.')[-1]).strip()

                                # Walk parent chain to confirm Class_1 extends Class_2
                                # (directly or indirectly).
                                _is_descendant = False
                                _seen_parents = set()
                                _cursor = _cls_simple
                                while _cursor and _cursor not in _seen_parents:
                                    _seen_parents.add(_cursor)
                                    _par = _class_extends.get(_cursor)
                                    if not _par:
                                        break
                                    _par_simple = strip_generics(str(_par).split('.')[-1]).strip()
                                    if _par_simple == _owner_simple:
                                        _is_descendant = True
                                        break
                                    _cursor = _par_simple

                                _has_inject = _file_has_inject_annotation(_declared_dep, _cls)
                                if _is_descendant and _has_inject:
                                    _enqueue(_declared_dep)
                                    if _verbose_debug:
                                        _entry_debug_print(
                                            "[DEBUG][BFS_ARG_ENQUEUE] tok={} cls={} dep={} caller_file={}".format(
                                                _owner_tok_local,
                                                _cls,
                                                _declared_dep,
                                                _cur,
                                            )
                                        )
                                elif _verbose_debug:
                                    _entry_debug_print(
                                        "[DEBUG][BFS_ARG_ENQUEUE][SKIP_RULE] tok={} cls={} owner={} extends_owner={} has_inject={} dep={} caller_file={}".format(
                                            _owner_tok_local,
                                            _cls,
                                            _owner_cls,
                                            _is_descendant,
                                            _has_inject,
                                            _declared_dep,
                                            _cur,
                                        )
                                    )
        _update_bfs_progress()
    # â”€â”€ Checkpoint 10% â”€â”€
    _pbar_goto(10, f"BFS done: {len(java_files)} files found")


    # Build O(1) filename â†’ path lookup (used by LOC resolver later)
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
    # ProcessPoolExecutor spawns real OS subprocesses â†’ bypasses the GIL â†’
    # javalang.parse.parse() truly runs in parallel across all CPU cores.
    #
    # Workers use the module-level _file_worker function (picklable).
    # Each worker receives plain serialisable data (no shared state).
    # Results are merged back into the main process.

    # Build the adapter config dict to pass to each worker subprocess.
    # Only serialisable primitives â€” no in-memory caches (can't cross process boundary).
    _adapter_module   = type(adapter).__module__
    _adapter_class    = type(adapter).__name__
    _adapter_kwargs   = dict(
        details=adapter.details,
        regex=adapter.regex,
        include_unqualified=adapter.include_unqualified,
        accept_local_new_types=adapter.accept_local_new_types,
        accept_parameter_types=adapter.accept_parameter_types,
        accept_same_package=adapter.accept_same_package,
        # Caches not passed â€” each worker has its own private cache
    )

    _cpu = multiprocessing.cpu_count() or 4
    # Cap workers: more than cpu_count gives no benefit for CPU-bound work;
    # very large pools waste memory on 5000-file codebases.
    _max_proc_workers = min(_cpu, 16)

    _worker_args = list(java_files)

    def _row_matches_entry_targets(_row):
        _row_file = (_row or {}).get("file_name")
        _row_file_norm = _as_abs_norm(_row_file)
        _targets = _entry_targets_for_path(_row_file)
        if not _targets:
            return True

        _mname = str((_row or {}).get("method_name") or "").strip()
        _mparams = _row_param_tuple(_row)
        for _t in _targets:
            _target_name = str(_t.get("method") or "").strip()
            if _mname != _target_name:
                continue
            _target_params = _t.get("params")
            if _target_params is not None:
                if tuple(_target_params) == tuple(_mparams):
                    return True
                continue
            _key = (_row_file_norm, _target_name)
            _selected = _entry_auto_sig_by_file_method.get(_key)
            if _selected is None:
                _entry_auto_sig_by_file_method[_key] = tuple(_mparams)
                return True
            if tuple(_selected) == tuple(_mparams):
                return True
        return False

    def _rows_have_any_target_match(_rows, _row_file_norm):
        """Return True when at least one parsed row matches the scoped entry target."""
        _targets = _entry_targets_for_path(_row_file_norm)
        if not _targets:
            return True

        for _row in _rows or []:
            _mname = str((_row or {}).get("method_name") or "").strip()
            _mparams = _row_param_tuple(_row)
            for _t in _targets:
                _target_name = str(_t.get("method") or "").strip()
                if _mname != _target_name:
                    continue
                _target_params = _t.get("params")
                if _target_params is not None and tuple(_target_params) != tuple(_mparams):
                    continue
                return True
        return False

    def _recover_rows_in_main_process(_file_path):
        """Best-effort row recovery when a worker returns no rows without errors."""
        _rows = []
        _null_meta = {
            'Annotations': 'None',
            'Method_Declaration_Type': 'Default',
            'return_type': '',
            'Parameters': '',
            'Parameter_Arity': None,
            'Parameter_Types': '',
        }
        try:
            _code_raw = read_file_cached(_file_path)
            _code = html.unescape(_code_raw)
            _ast_obj = adapter.parse_ast(_code)
            if not _ast_obj:
                _code_no_comments = re.sub(r'//.*?$|/\*.*?\*/', '', _code, flags=re.MULTILINE | re.DOTALL)
                _ast_obj = adapter.parse_ast(_code_no_comments)
                if not _ast_obj:
                    return _rows
                _code = _code_no_comments

            _declared_types = list(adapter.get_declared_types(_ast_obj) or [])
            for _type_name, _type_kind, _type_node in _declared_types:
                for _method_name, _method_node in adapter.get_methods_in_type(_type_node):
                    try:
                        _meta = adapter.extract_method_metadata(_method_node)
                        _calls = adapter.find_calls_in_method(_type_node, _method_node, _code)
                        _calls = list(dict.fromkeys(_calls)) if _calls else ["None"]
                    except Exception:
                        _meta = dict(_null_meta)
                        _calls = ["None"]

                    for _call in _calls:
                        _rows.append({
                            'file_name': _file_path,
                            'class_interface_name': _type_name,
                            'type': _type_kind or 'Unknown',
                            'method_name': _method_name,
                            'Annotations': _meta.get('Annotations', ''),
                            'Method_Declaration_Type': _meta.get('Method_Declaration_Type', 'Default'),
                            'return_type': _meta.get('return_type', ''),
                            'object_call': _call,
                            'Parameters': _meta.get('Parameters', ''),
                            'Parameter_Arity': _meta.get('Parameter_Arity', None),
                            'Parameter_Types': _meta.get('Parameter_Types', ''),
                            '_type_name': _type_name,
                            '_method_name': _method_name,
                            '_calls': _calls,
                        })
        except Exception:
            return []
        return _rows

    # Use 'spawn' context explicitly â€” safer on macOS/Windows and avoids
    # fork-related deadlocks with javalang's thread-local state.
    _mp_ctx = multiprocessing.get_context('spawn')

    _pbar.set_postfix_str(f"Parsing {len(java_files)} files...")
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=_max_proc_workers,
        mp_context=_mp_ctx,
        initializer=_init_file_worker,
        initargs=(_adapter_module, _adapter_class, _adapter_kwargs),
    ) as _proc_pool:
        _total_files = len(_worker_args)
        _parse_done = 0
        _futures = {
            _proc_pool.submit(_file_worker, _fp): _fp
            for _fp in _worker_args
        }
        for _fut in concurrent.futures.as_completed(_futures):
            _file_path = _futures[_fut]
            _file = os.path.basename(_file_path)
            _parse_done += 1
            # Proportional advance within 10% â†’ 60% window
            _target = 10 + int(_parse_done / max(_total_files, 1) * 50)
            _pbar_goto(_target, f"Parsing: {_file} ({_parse_done}/{_total_files})")
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

            if not _rows and not _err:
                _rows = _recover_rows_in_main_process(_file_path)
                if _verbose_debug and _rows:
                    _entry_debug_print(
                        "[DEBUG][ROW_RECOVERY] recovered_rows={} file={}".format(
                            len(_rows),
                            _file_path,
                        )
                    )

            _file_norm = _as_abs_norm(_file_path)
            _targets_for_file = _entry_targets_for_path(_file_path)
            _allow_all_rows_for_file = False
            if (
                _targets_for_file
                and _file_norm not in _entry_inherited_target_files
                and not _rows_have_any_target_match(_rows, _file_norm)
            ):
                # Strict scoped-entry behavior: if a method-scoped entry did not
                # match any parsed row for this file, keep filtering active instead
                # of widening to every method in the file.
                _allow_all_rows_for_file = False
                if _verbose_debug:
                    _entry_debug_print(
                        "[DEBUG][ENTRY_SCOPE_FALLBACK] no direct target match; keeping scope filter "
                        "file={} targets={}".format(_file_path, [t.get("method") for t in _targets_for_file])
                    )

            for _row in _rows:
                if not _allow_all_rows_for_file and not _row_matches_entry_targets(_row):
                    continue
                _type_name   = _row.pop('_type_name',   _row.get('class_interface_name', 'Unknown'))
                _method_name = _row.pop('_method_name',  _row.get('method_name', 'UnknownMethod'))
                _calls       = _row.pop('_calls', [])
                file_map.setdefault(_type_name, _file_path)
                method_map.setdefault(_type_name, {})
                method_map[_type_name][_method_name] = _calls
                ast_results.append(_row)

    # â”€â”€ Checkpoint 60% â”€â”€
    _pbar_goto(60, f"Parsing done: {len(java_files)} files")
    if _verbose_debug:
        print(f"[DEBUG] java_files found by BFS : {len(java_files)}")
        print(f"[DEBUG] ast_results rows : {len(ast_results)}")
        print(f"[DEBUG] method_map classes : {len(method_map)}")
        print(f"[DEBUG] errors from parsing : {len(errors)}")

    # Debug probe file for reporting only (does not affect BFS seeds).
    _debug_probe_file = (
        os.environ.get("LINEAGE_DEBUG_ENTRY_POINT")
        or details.get("debug_entry_point")
        or (_seed_files_dedup[0] if len(_seed_files_dedup) == 1 else None)
    )
    if _debug_probe_file:
        if _verbose_debug:
            _probe_norm = _as_abs_norm(_debug_probe_file)
            _seed_norms = {_as_abs_norm(x) for x in _seed_files_dedup}
            _java_norms = {_as_abs_norm(x) for x in java_files}
            _entry_target_keys = set(_entry_targets_by_file.keys())
            _entry_debug_print(
                "[DEBUG][ENTRY_PROBE_STATUS] file={} in_seed_files={} in_bfs_java_files={} has_entry_target={}".format(
                    _debug_probe_file,
                    _probe_norm in _seed_norms,
                    _probe_norm in _java_norms,
                    _probe_norm in _entry_target_keys,
                )
            )
            if _probe_norm in _entry_targets_by_file:
                _entry_debug_print(
                    "[DEBUG][ENTRY_PROBE_TARGETS] {}".format(
                        [
                            "{}({})".format(
                                str(t.get("method") or ""),
                                ",".join(t.get("params") or []) if t.get("params") is not None else "auto",
                            )
                            for t in (_entry_targets_by_file.get(_probe_norm) or [])
                        ]
                    )
                )
        _debug_print_methods_for_file(_debug_probe_file)

    _early_dbg_file  = os.environ.get("LINEAGE_DEBUG_ENTRY_POINT") or details.get("debug_entry_point") or ""
    _early_dbg_method = os.environ.get("LINEAGE_DEBUG_METHOD_NAME") or details.get("debug_method_name") or ""
    print("_early_dbg_file : ",_early_dbg_file)
    print("_early_dbg_method : ",_early_dbg_method)
    if _early_dbg_file and _early_dbg_method:
        _early_base  = os.path.basename(_early_dbg_file).strip().lower()
        _early_class = os.path.splitext(_early_base)[0].lower()
        _early_method = _early_dbg_method.strip().lower()
        _matched_rows = [
            r for r in ast_results
            if (
                os.path.basename(str(r.get("file_name") or "")).strip().lower() == _early_base
                or str(r.get("class_interface_name") or "").strip().lower() == _early_class
            )
            and str(r.get("method_name") or "").strip().lower() == _early_method
        ]
        print(f"[DEBUG][EARLY_METHOD_PROBE] file={_early_dbg_file} method={_early_dbg_method}")
        print(f"[DEBUG][EARLY_METHOD_PROBE] matched_rows={len(_matched_rows)}")

        for _r in _matched_rows:
            print(f"  class={_r.get('class_interface_name')}  method={_r.get('method_name')}  call={_r.get('object_call')}")
        if not _matched_rows:
            print(f"[DEBUG][EARLY_METHOD_PROBE] *** method NOT found in ast_results — AST parse produced no rows for this file ***")
 
    # ---- Optional chain resolution ----   
 
    # ---- Optional chain resolution ----
    # Build an inverted index: method_name â†’ (type, calls) for O(1) lookup
    # instead of scanning all types on every resolve_chain call (was O(NÂ²)).
    _method_to_type = {}  # method_name â†’ first type that owns it
    for _typ, _methods in method_map.items():
        for _mname in _methods:
            _method_to_type.setdefault(_mname, _typ)

    chain_results = []

    def resolve_chain(current, visited):
        # Support tokens like:
        #   ExtendedQueryPaymentOrderQueryBuilder.build()
        #   ExtendedQueryPaymentOrderQueryBuilder.build(entityManager)
        # by extracting both owner class and bare method name.
        cur = str(current or "").strip()
        m = re.match(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*(?:\(|$)', cur)

        owner = m.group(1) if m else None
        called_method = m.group(2) if m else (cur.split('.')[-1] if '.' in cur else cur)
        called_method = re.sub(r'\s*\(.*\)\s*$', '', str(called_method or '')).strip()

        typ = None
        if owner:
            # If the call already has an explicit owner (Class.method()), do not
            # fall back to global method-name lookup. Falling back here can map
            # library calls (e.g. Boolean.parseBoolean) to unrelated project
            # methods with the same name.
            if owner in method_map and called_method in method_map.get(owner, {}):
                typ = owner
        elif called_method:
            typ = _method_to_type.get(called_method)

        if typ is not None and called_method:
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

    _pbar.set_postfix_str("Resolving call chains...")
    if _entry_targets_by_file:
        _chain_roots = [
            _r for _r in ast_results
            if _row_matches_entry_targets(_r)
            and bool(_entry_targets_for_path(_r.get("file_name")))
        ]
        _chain_total = max(len(_chain_roots), 1)
        _chain_done = 0
        for _r in _chain_roots:
            _chain_done += 1
            _target = 60 + int(_chain_done / _chain_total * 15)
            _pbar_goto(_target, f"Chains: root {_chain_done}/{_chain_total}")
            _method = _r.get("method_name") or "UnknownMethod"
            _call = _r.get("object_call") or ""
            _file_name = _r.get("file_name") or "Unknown"
            chain_results.append({'File Name': _file_name, 'Method Name': _method, 'Object Call': _call})
            if isinstance(_call, str) and _call.strip() and _call != "None":
                resolve_chain(_call, {_call})
    else:
        _chain_total = max(len(method_map), 1)
        _chain_done = 0
        for typ in method_map:
            _chain_done += 1
            _target = 60 + int(_chain_done / _chain_total * 15)
            _pbar_goto(_target, f"Chains: {typ[:30]} ({_chain_done}/{_chain_total})")
            for method in method_map[typ]:
                file_name = file_map.get(typ, 'Unknown')
                for call in method_map[typ][method]:
                    chain_results.append({'File Name': file_name, 'Method Name': method, 'Object Call': call})
                    resolve_chain(call, {call})

    # â”€â”€ Checkpoint 75% â”€â”€
    _pbar_goto(75, "Chain resolution done")

    # Precomputed continuation index: (OwnerClass, method) -> child calls
    # Used later during post-processing so synthetic rows can continue into
    # their child-call lineage as well.
    _chain_children_index = {}
    for _typ, _mname, _child_call in chain_results:
        _k = (str(_typ or "").strip(), str(_mname or "").strip())
        if not _k[0] or not _k[1]:
            continue
        _chain_children_index.setdefault(_k, set()).add(str(_child_call or "").strip())

    # ---- Cleaner: system-call filtering + mapping + chain explosion ----
    def clean_and_write(df, object_class_map=None, method_return_index=None):
        _pbar_goto(76, "Post-processing: initializing...")
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
        remove_builtin_calls = bool(details.get("remove_builtin_calls"))
        keep_unresolved_owner_calls = bool(details.get("keep_unresolved_owner_calls", True))
        relax_method_scope_lowercase_lookup = bool(details.get("relax_method_scope_lowercase_lookup", True))
        CHAIN_DEBUG = False

        def _dbg(msg):
            if CHAIN_DEBUG:
                print(f"[CHAIN_DEBUG] {msg}")

        def is_system_call(call):
            return adapter.is_system_call(call)

        if remove_builtin_calls:
            df_clean = df[~df["object_call"].apply(is_system_call)].copy()
        else:
            df_clean = df.copy()
        df_clean["object_call"] = df_clean["object_call"].fillna("None")

        def strip_generics(name):
            if not isinstance(name, str):
                return name
            name = re.sub(r'\s*&lt;[^&gt]+&gt;\s*', '', name)
            name = re.sub(r'\s*<[^>]+>\s*', '', name)
            return name

        _generic_container_types = {
            "List", "Set", "Collection", "Iterable", "Optional", "Page",
            "Slice", "Stream", "ResponseEntity"
        }
        _known_container_classes = _generic_container_types | {"Map", "Pair", "Tuple", "Tuple2", "Tuple3"}
        _collection_like_methods = {
            "get", "getFirst", "getLast", "iterator", "stream", "parallelStream"
        }

        def _top_level_generic_args(type_name):
            """Split top-level generic arguments while preserving nested generics."""
            if not isinstance(type_name, str):
                return []
            raw = html.unescape(type_name).strip()
            if "<" not in raw or ">" not in raw:
                return []
            i0 = raw.find("<")
            i1 = raw.rfind(">")
            if i0 == -1 or i1 == -1 or i1 <= i0:
                return []
            inner = raw[i0 + 1:i1]
            return [p.strip() for p in _split_top_level_commas(inner) if str(p).strip()]

        def _pair_like_accessor_result_type(type_name, accessor_method):
            """Return generic argument selected by pair/tuple accessor names."""
            if not isinstance(type_name, str) or not isinstance(accessor_method, str):
                return None
            raw = html.unescape(type_name).strip()
            if not raw:
                return None
            base = strip_generics(raw.split("<", 1)[0].split(".")[-1])
            if base not in {"Pair", "Tuple", "Tuple2", "Tuple3"}:
                return None

            args = _top_level_generic_args(raw)
            if not args:
                return None

            m = accessor_method.strip()
            index_map = {
                "getValue0": 0,
                "getValue1": 1,
                "getValue2": 2,
                "getFirst": 0,
                "getSecond": 1,
                "getThird": 2,
                "getLeft": 0,
                "getRight": 1,
                "getKey": 0,
                "getValue": 1,
            }
            idx = index_map.get(m)
            if idx is None or idx >= len(args):
                return None
            return strip_generics(args[idx])

        def _well_known_accessor_exists(owner_class_name, method_name):
            """Allow common library/container accessors without project source files."""
            o = strip_generics(str(owner_class_name or "").split(".")[-1])
            m = str(method_name or "").strip()
            if not o or not m:
                return False
            if o in {"List", "Set", "Collection", "Iterable", "Page", "Slice"} and m in {
                "get", "size", "isEmpty", "iterator", "stream", "parallelStream", "contains", "add", "remove"
            }:
                return True
            if o == "Map" and m in {
                "get", "getOrDefault", "containsKey", "containsValue", "keySet", "values", "entrySet", "put", "remove"
            }:
                return True
            if o == "Optional" and m in {"get", "isPresent", "orElse", "orElseGet", "orElseThrow", "map", "filter"}:
                return True
            if o in {"Stream"} and m in {"filter", "map", "flatMap", "collect", "forEach", "findFirst", "anyMatch"}:
                return True
            if o in {"Pair", "Tuple", "Tuple2", "Tuple3"} and re.fullmatch(r'getValue\d+|getFirst|getSecond|getThird|getLeft|getRight|getKey|getValue', m):
                return True
            return False

        def _container_and_element_type(type_name):
            """Return (container_simple, element_simple_or_none) for generic returns."""
            if not isinstance(type_name, str):
                return None, None
            raw = html.unescape(type_name).strip()
            if not raw:
                return None, None

            raw = re.sub(r'\s+', '', raw)
            base = raw.split('<', 1)[0]
            base_simple = strip_generics(base.split('.')[-1]) if base else None
            if not base_simple:
                return None, None

            if '<' not in raw or '>' not in raw:
                return base_simple, None

            inner = raw[raw.find('<') + 1: raw.rfind('>')]
            tokens = re.findall(r'[A-Za-z_][A-Za-z0-9_$.]*', inner)
            skip = {
                "String", "Integer", "Long", "Boolean", "Double", "Float", "Short", "Byte",
                "Character", "Object", "Map", "List", "Set", "Collection", "Iterable", "Optional",
                "Page", "Slice", "Stream", "ResponseEntity", "Void"
            }
            elem = None
            for tok in reversed(tokens):
                simple = tok.split('.')[-1]
                if simple and simple not in skip and simple[0].isupper():
                    elem = simple
                    break
            if elem is None and tokens:
                elem = tokens[0].split('.')[-1]
            return base_simple, strip_generics(elem) if elem else None

        def _extract_class_literal_target(call_text, method_name):
            """Extract X from methodName(..., X.class, ...)."""
            if not isinstance(call_text, str) or not isinstance(method_name, str):
                return None
            pat = re.compile(
                r'\.' + re.escape(method_name) +
                r'\s*\([^)]*?([A-Za-z_][A-Za-z0-9_$.]*)\s*\.class[^)]*\)',
                re.MULTILINE,
            )
            m = pat.search(call_text)
            if not m:
                return None
            return strip_generics((m.group(1) or "").split('.')[-1])

        def _unwrap_generic_return_type(type_name):
            """Return effective chain type from a declared/given return type.

            Examples:
              ReturnType -> ReturnType
              List<ReturnType> -> ReturnType
              Optional<ReturnType> -> ReturnType
              ResponseEntity<List<ReturnType>> -> ReturnType
            """
            if not isinstance(type_name, str):
                return None
            raw = html.unescape(type_name).strip()
            if not raw:
                return None

            raw = re.sub(r'\s+', '', raw)
            base = raw.split('<', 1)[0]
            base_simple = base.split('.')[-1]

            if '<' not in raw or '>' not in raw:
                return strip_generics(base_simple)

            # For non-container generics, keep existing behavior and use outer type.
            if base_simple not in _generic_container_types:
                return strip_generics(base_simple)

            # Extract deepest user-like type token from generic args.
            inner = raw[raw.find('<') + 1: raw.rfind('>')]
            tokens = re.findall(r'[A-Za-z_][A-Za-z0-9_$.]*', inner)
            skip = {
                "String", "Integer", "Long", "Boolean", "Double", "Float", "Short", "Byte",
                "Character", "Object", "Map", "List", "Set", "Collection", "Iterable", "Optional",
                "Page", "Slice", "Stream", "ResponseEntity", "Void"
            }
            for tok in reversed(tokens):
                simple = tok.split('.')[-1]
                if simple and simple not in skip and simple[0].isupper():
                    return simple

            # Fallback to first token if no user-like class was found.
            if tokens:
                return tokens[0].split('.')[-1]
            return strip_generics(base_simple)

        def _get_declared_return_type_from_file(owner_file, method_name):
            """Best-effort read of declared return type for a method in a source file."""
            if not owner_file or not method_name or not os.path.isfile(owner_file):
                return None
            try:
                txt = file_content_cache.get(owner_file) or read_file_cached(owner_file)
            except Exception:
                return None
            if not txt:
                return None

            # Match declaration line up to method name, keeping generic return type text.
            decl_re = re.compile(
                r'^[ \t]*(?:@\w+(?:\([^)]*\))?\s*)*'
                r'(?:(?:public|protected|private|static|final|abstract|synchronized|native|strictfp|default)\s+)+'
                r'(?:<[^>{;]+>\s*)?'
                r'([A-Za-z_][\w$.]*(?:\s*<[^>{}]+>)?(?:\s*\[\s*\])*)\s+'
                + re.escape(method_name)
                + r'\s*\(',
                re.MULTILINE,
            )
            m = decl_re.search(txt)
            if not m:
                return None
            return (m.group(1) or "").strip()

        def _debug_entry_stage(df_stage, stage_label, call_col="class_method_call"):
            if not _verbose_debug:
                return
            if df_stage is None or getattr(df_stage, "empty", True):
                return

            _entry_file_dbg = _debug_probe_file or ""
            _method_dbg = (
                os.environ.get("LINEAGE_DEBUG_METHOD_NAME")
                or details.get("debug_method_name")
                or ""
            )
            _entry_file_dbg = str(_entry_file_dbg).strip()
            _method_dbg = str(_method_dbg).strip().lower()
            if not _entry_file_dbg or not _method_dbg:
                return

            _entry_norm = _as_abs_norm(_entry_file_dbg)
            _entry_base = os.path.basename(_entry_file_dbg).strip().lower()
            _entry_class = os.path.splitext(_entry_base)[0]

            def _path_base(_p):
                return os.path.basename(str(_p or "")).strip().lower()

            if "method_name" not in df_stage.columns:
                return

            _method_mask = (
                df_stage["method_name"].astype(str).str.strip().str.lower() == _method_dbg
            )

            _file_mask_exact = pd.Series([False] * len(df_stage), index=df_stage.index)
            _file_mask_fallback = pd.Series([False] * len(df_stage), index=df_stage.index)
            if "file_name" in df_stage.columns:
                _file_norm = df_stage["file_name"].astype(str).apply(_as_abs_norm)
                _file_mask_exact = (_file_norm == _entry_norm)
                _file_mask_fallback = (df_stage["file_name"].astype(str).apply(_path_base) == _entry_base)

            _src_mask_exact = pd.Series([False] * len(df_stage), index=df_stage.index)
            _src_mask_fallback = pd.Series([False] * len(df_stage), index=df_stage.index)
            if "__source_file_name" in df_stage.columns:
                _src_norm = df_stage["__source_file_name"].astype(str).apply(_as_abs_norm)
                _src_mask_exact = (_src_norm == _entry_norm)
                _src_mask_fallback = (df_stage["__source_file_name"].astype(str).apply(_path_base) == _entry_base)

            _class_mask = pd.Series([False] * len(df_stage), index=df_stage.index)
            if "class_interface_name" in df_stage.columns:
                _class_mask = (
                    df_stage["class_interface_name"].astype(str).str.strip().str.lower() == _entry_class
                )

            _has_exact_file_hit = bool((_file_mask_exact | _src_mask_exact).any())
            _file_mask = _file_mask_exact if _has_exact_file_hit else (_file_mask_exact | _file_mask_fallback)
            _src_mask = _src_mask_exact if _has_exact_file_hit else (_src_mask_exact | _src_mask_fallback)
            _class_mask = pd.Series([False] * len(df_stage), index=df_stage.index) if _has_exact_file_hit else _class_mask

            _probe_mask = _method_mask & (_file_mask | _src_mask | _class_mask)
            _probe_df = df_stage[_probe_mask]

            _sample = []
            if call_col in _probe_df.columns:
                _sample = sorted(set(
                    str(_v).strip()
                    for _v in _probe_df[call_col].dropna().tolist()
                    if str(_v).strip()
                ))[:12]

            _entry_debug_print("\n[DEBUG][ENTRY_STAGE] stage:", stage_label)
            _entry_debug_print("[DEBUG][ENTRY_STAGE] method_only_count:", int(_method_mask.sum()))
            _entry_debug_print("[DEBUG][ENTRY_STAGE] file_or_source_or_class_count:", int((_file_mask | _src_mask | _class_mask).sum()))
            _entry_debug_print("[DEBUG][ENTRY_STAGE] row_count:", len(_probe_df))
            _entry_debug_print("[DEBUG][ENTRY_STAGE] sample_call_count:", len(_sample))
            for _idx, _call in enumerate(_sample, start=1):
                _entry_debug_print("[DEBUG][ENTRY_STAGE] {}. {}".format(_idx, _call))

        _debug_entry_stage(df_clean, "S1_after_object_call_system_filter", call_col="object_call")

        def _build_super_dispatch_context_map(df_source):
            """
            Build parent-method -> concrete subclass context from rows that call super.method(...).
            Example: ExtendedX.build -> super.build maps (AbstractX, build) -> {ExtendedX}.
            Also supports adapter-normalized parent calls:
            ExtendedX.build -> AbstractX.build(...)
            """
            out = {}
            if df_source is None or df_source.empty:
                return out

            for _row in df_source[["class_interface_name", "method_name", "object_call"]].to_dict("records"):
                _caller_cls = strip_generics(str(_row.get("class_interface_name") or "")).strip()
                _caller_m = str(_row.get("method_name") or "").strip()
                _oc = str(_row.get("object_call") or "").strip()
                if not _caller_cls or not _caller_m or not _oc:
                    continue

                _parent = strip_generics(method_return_index.get(_caller_cls, {}).get("__extends__"))
                if not _parent:
                    continue

                _super_method = None

                # Raw super call: super.build(...)
                _m_super = re.match(r'^\s*super\s*\.\s*([A-Za-z_]\w*)\s*(?:\(|$)', _oc)
                if _m_super:
                    _super_method = _m_super.group(1)
                else:
                    # Adapter-normalized parent call: AbstractX.build(...)
                    _m_parent = re.match(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*(?:\(|$)', _oc)
                    if _m_parent:
                        _owner = strip_generics(_m_parent.group(1))
                        _meth = _m_parent.group(2)
                        if _owner == _parent:
                            _super_method = _meth

                if _super_method != _caller_m:
                    continue

                _k = (_parent, _super_method)
                out.setdefault(_k, set()).add(_caller_cls)

            return out

        _super_dispatch_context = _build_super_dispatch_context_map(df_clean)

        _entry_dispatch_class_hint = ""
        _entry_dispatch_file_hint = _debug_probe_file
        if _entry_dispatch_file_hint:
            _entry_dispatch_class_hint = os.path.splitext(os.path.basename(str(_entry_dispatch_file_hint)))[0]

        def _choose_dispatch_target(_cands):
            """
            Choose one concrete dispatch class from candidate set.
            Prefer explicit entry-class hint when available; else choose only
            when unambiguous.
            """
            if not _cands:
                return None
            _cand_set = set(_cands)
            if _entry_dispatch_class_hint and _entry_dispatch_class_hint in _cand_set:
                return _entry_dispatch_class_hint
            if len(_cand_set) == 1:
                return next(iter(_cand_set))
            return None

        chain_suppressions = set()

        # def normalize_keyword_rooted_call(s, parent_class):
        #     if not isinstance(s, str) or not s.strip():
        #         return s
        #     s = s.strip()
        #     m = re.match(r'^\s*(return|this|super|new)\s*\.\s*([A-Za-z_]\w*)(.*)$', s, flags=re.IGNORECASE)
        #     if m:
        #         meth = m.group(2)
        #         rest = m.group(3) or ""
        #         return "{}.{}{}".format(strip_generics(parent_class), meth, rest).strip()
        #     return s

        def normalize_keyword_rooted_call(s, parent_class):
            if not isinstance(s, str) or not s.strip():
                return s
            s = s.strip()
            m = re.match(r'^\s*(return|this|super|new)\s*\.\s*([A-Za-z_]\w*)(.*)$', s, flags=re.IGNORECASE)
            if m:
                keyword = m.group(1).lower()
                meth = m.group(2)
                rest = m.group(3) or ""
                if keyword == "super":
                    _pc = strip_generics(parent_class)
                    _super_cls = _resolve_super_owner_class(_pc, meth)
                    return "{}.{}{}".format(_super_cls, meth, rest).strip()
                return "{}.{}{}".format(strip_generics(parent_class), meth, rest).strip()
            return s

        # ------------------------------------------------------------------
        # Case 1 helper â€” inheritance walk
        # ------------------------------------------------------------------
        # Walk the extends chain stored in method_return_index["__extends__"]
        # to find the first ancestor class that actually declares the method.
        # Returns the owning class name, or class_name itself when not found.
        def _resolve_class_for_method(class_name, method_name, _visited=None, prefer_concrete=True, fallback_class_name=None):
            def _decap_java_bean(name):
                if not name:
                    return ""
                if len(name) >= 2 and name[0].isupper() and name[1].isupper():
                    i = 0
                    n = len(name)
                    while i < n and name[i].isupper():
                        i += 1
                    if i > 1 and i < n and name[i].islower():
                        i -= 1
                    return name[:i].lower() + name[i:]
                return name[0].lower() + name[1:]

            def _accessor_field_candidates(mname):
                mname = str(mname or "").strip()
                if not mname:
                    return []
                stem = ""
                if mname.startswith("get") and len(mname) > 3:
                    stem = mname[3:]
                elif mname.startswith("set") and len(mname) > 3:
                    stem = mname[3:]
                elif mname.startswith("is") and len(mname) > 2:
                    stem = mname[2:]
                if not stem:
                    return []

                cands = []
                c1 = _decap_java_bean(stem)
                cands.append(c1)
                if stem not in cands:
                    cands.append(stem)
                c2 = stem[:1].lower() + stem[1:] if stem else stem
                if c2 and c2 not in cands:
                    cands.append(c2)
                return [c for c in cands if c]

            def _class_has_lombok_accessor_field(owner_class, mname):
                owner = strip_generics(str(owner_class or "")).strip()
                if not owner:
                    return False
                owner_simple = owner.split('.')[-1]
                if not owner_simple:
                    return False

                field_names = _accessor_field_candidates(mname)
                if not field_names:
                    return False

                paths = _class_to_paths.get(owner_simple, [])
                if not paths:
                    return False

                field_alt = "|".join(re.escape(x) for x in field_names)
                if not field_alt:
                    return False

                class_hdr_pat = re.compile(
                    r'(?ms)(.*?)\bclass\s+' + re.escape(owner_simple) + r'\b[^\{]*\{'
                )
                field_pat = re.compile(
                    r'(?m)^[ \t]*(?:@\w+(?:\([^)]*\))?\s*)*'
                    r'(?:(?:public|private|protected)\s+)?'
                    r'(?:(?:static|final|transient|volatile)\s+)*'
                    r'[A-Za-z_][\w$.<>,\[\]? \t]*\s+'
                    r'(?:' + field_alt + r')\s*(?:=|;)',
                    re.IGNORECASE,
                )

                for _p in paths:
                    try:
                        _txt = file_content_cache.get(_p) or read_file_cached(_p)
                    except Exception:
                        continue
                    if not _txt:
                        continue

                    m_cls = class_hdr_pat.search(_txt)
                    if not m_cls:
                        continue

                    class_header = m_cls.group(1) or ""
                    # Apply rule only when both Lombok annotations exist on class.
                    if not (re.search(r'@Getter\b', class_header) and re.search(r'@Setter\b', class_header)):
                        continue

                    class_open = _txt.find("{", m_cls.end() - 1)
                    if class_open == -1:
                        continue
                    class_tail = _txt[class_open:]

                    if field_pat.search(class_tail):
                        return True

                return False

            if not class_name or not method_name:
                return class_name
            if _visited is None:
                _visited = set()
            if class_name in _visited:
                return class_name          # cycle guard
            _visited.add(class_name)
            entry = method_return_index.get(class_name, {})
            if not entry:
                return class_name

            def _is_descendant(child_cls, ancestor_cls):
                child = strip_generics(child_cls) if isinstance(child_cls, str) else child_cls
                anc = strip_generics(ancestor_cls) if isinstance(ancestor_cls, str) else ancestor_cls
                if not child or not anc or child == anc:
                    return False
                seen = set()
                cur = child
                while cur and cur not in seen:
                    seen.add(cur)
                    parent = method_return_index.get(cur, {}).get("__extends__")
                    if not parent:
                        break
                    if strip_generics(parent) == anc:
                        return True
                    cur = parent
                return False

            if not prefer_concrete:
                # super.method(): walk parent hierarchy only (no child override preference)
                if method_name in entry:
                    return class_name
                if _class_has_lombok_accessor_field(class_name, method_name):
                    return class_name
                parent = entry.get("__extends__")
                if parent and parent != class_name:
                    return _resolve_class_for_method(parent, method_name, _visited, prefer_concrete=False, fallback_class_name=fallback_class_name)
                return class_name

            declared_here = _class_declares_method(class_name, method_name)

            # Some indexes include inherited signatures on the child type.
            # For lineage ownership we want the declaring owner (e.g. AbstractQueryBuilder.build).
            if method_name in entry and not declared_here:
                # Lombok accessor ownership must be checked before extends fallback:
                # if child has @Getter/@Setter + matching field, keep child owner.
                if _class_has_lombok_accessor_field(class_name, method_name):
                    return class_name
                parent = entry.get("__extends__")
                if parent and parent != class_name:
                    return _resolve_class_for_method(
                        parent,
                        method_name,
                        _visited,
                        prefer_concrete=False,
                        fallback_class_name=fallback_class_name,
                    )

            if method_name in entry:
                # Context-aware concrete preference:
                # if a fallback class (e.g. subclass where super.method() originated)
                # is known and is a descendant of class_name, prefer that branch first.
                fb = strip_generics(fallback_class_name) if isinstance(fallback_class_name, str) else None
                if fb and fb != class_name and _is_descendant(fb, class_name):
                    fb_owner = _resolve_class_for_method(
                        fb,
                        method_name,
                        _visited.copy(),
                        prefer_concrete=True,
                        fallback_class_name=None,
                    )
                    if (
                        fb_owner
                        and method_name in method_return_index.get(fb_owner, {})
                        and _class_declares_method(fb_owner, method_name)
                    ):
                        return fb_owner

                # Prefer concrete subclass overrides when present.
                # To avoid cross-branch false positives, only auto-pick when unique.
                sub_candidates = []
                for sub in _concrete_subclasses.get(class_name, []):
                    if sub in _visited:
                        continue
                    sub_owner = _resolve_class_for_method(
                        sub,
                        method_name,
                        _visited.copy(),
                        prefer_concrete=prefer_concrete,
                        fallback_class_name=None,
                    )
                    if (
                        sub_owner
                        and method_name in method_return_index.get(sub_owner, {})
                        and _class_declares_method(sub_owner, method_name)
                    ):
                        if sub_owner not in sub_candidates:
                            sub_candidates.append(sub_owner)

                if len(sub_candidates) == 1:
                    return sub_candidates[0]

                # If this is an interface/API type with known impl, prefer concrete owner.
                _impl_candidates = []
                _named_impl = iface_to_impl_map.get(class_name)
                if _named_impl:
                    _impl_candidates.append(_named_impl)
                for _c in sorted(_interface_to_impls.get(class_name, set()) or set()):
                    if _c and _c not in _impl_candidates:
                        _impl_candidates.append(_c)
                _conv_impl = class_name + "Impl"
                if _conv_impl in _class_to_paths and _conv_impl not in _impl_candidates:
                    _impl_candidates.append(_conv_impl)

                for impl_name in _impl_candidates:
                    impl_owner = _resolve_class_for_method(
                        impl_name,
                        method_name,
                        _visited.copy(),
                        prefer_concrete=prefer_concrete,
                        fallback_class_name=fallback_class_name,
                    )
                    if impl_owner != impl_name or method_name in method_return_index.get(impl_name, {}):
                        return impl_owner

                # If class_name only has declaration metadata (e.g. abstract signature)
                # but no concrete body, keep class_name when multiple concrete owners exist.
                if not declared_here and len(sub_candidates) == 1:
                    return sub_candidates[0]

                return class_name          # declared here

            if _class_has_lombok_accessor_field(class_name, method_name):
                return class_name

            # If method not declared on the current type, check mapped implementation.
            _impl_candidates = []
            _named_impl = iface_to_impl_map.get(class_name)
            if _named_impl:
                _impl_candidates.append(_named_impl)
            for _c in sorted(_interface_to_impls.get(class_name, set()) or set()):
                if _c and _c not in _impl_candidates:
                    _impl_candidates.append(_c)
            _conv_impl = class_name + "Impl"
            if _conv_impl in _class_to_paths and _conv_impl not in _impl_candidates:
                _impl_candidates.append(_conv_impl)

            for impl_name in _impl_candidates:
                impl_owner = _resolve_class_for_method(
                    impl_name,
                    method_name,
                    _visited.copy(),
                    prefer_concrete=prefer_concrete,
                    fallback_class_name=fallback_class_name,
                )
                if impl_owner != impl_name or method_name in method_return_index.get(impl_name, {}):
                    return impl_owner
            parent = entry.get("__extends__")
            if parent and parent != class_name:
                return _resolve_class_for_method(parent, method_name, _visited, prefer_concrete=prefer_concrete, fallback_class_name=fallback_class_name)
            return class_name              # not found â€” keep original

        _resolve_class_for_method_impl = _resolve_class_for_method
        _resolve_class_for_method_cache = {}

        def _resolve_class_for_method(class_name, method_name, _visited=None, prefer_concrete=True, fallback_class_name=None):
            # Cache only root calls. Recursive calls pass _visited.
            if _visited is not None:
                return _resolve_class_for_method_impl(
                    class_name,
                    method_name,
                    _visited,
                    prefer_concrete=prefer_concrete,
                    fallback_class_name=fallback_class_name,
                )

            _key = (
                strip_generics(str(class_name or "")).strip(),
                str(method_name or "").strip(),
                bool(prefer_concrete),
                strip_generics(str(fallback_class_name or "")).strip(),
            )
            _cached = _resolve_class_for_method_cache.get(_key)
            if _cached is not None:
                return _cached

            _out = _resolve_class_for_method_impl(
                class_name,
                method_name,
                _visited=None,
                prefer_concrete=prefer_concrete,
                fallback_class_name=fallback_class_name,
            )
            _resolve_class_for_method_cache[_key] = _out
            return _out

        def _resolve_super_owner_class(caller_class, method_name):
            """
            Resolve super.method() owner from caller's direct parent, then ancestors.
            """
            cc = strip_generics(caller_class) if isinstance(caller_class, str) else caller_class
            if not cc or not method_name:
                return cc
            parent = method_return_index.get(cc, {}).get("__extends__")
            if not parent:
                return cc
            return _resolve_class_for_method(parent, method_name, prefer_concrete=False)

        def _is_direct_super_of_caller(candidate_class, caller_class):
            """
            True when candidate_class is the direct superclass of caller_class.
            For explicit/super-rooted calls we should keep the parent owner
            instead of re-resolving back to a concrete child override.
            """
            cand = strip_generics(candidate_class) if isinstance(candidate_class, str) else candidate_class
            caller = strip_generics(caller_class) if isinstance(caller_class, str) else caller_class
            if not cand or not caller:
                return False
            parent = method_return_index.get(caller, {}).get("__extends__")
            if not parent:
                return False
            return strip_generics(parent) == cand

        _caller_varmap_cache = {}
        _caller_inherited_varmap_cache = {}
        _resolve_owner_class_name_cache = {}

        def _resolve_owner_class_name(owner_token, caller_file):
            """
            Convert variable token (e.g. requestDetails) -> class (RequestDetails)
            using caller file var map/object_class_map/import-aware fallback.
            """
            if not isinstance(owner_token, str):
                return owner_token
            tok = strip_generics(owner_token).strip()
            if not tok:
                return tok

            # FQN owner tokens should be preserved as-is.
            # Example: nl.rabobank.a.b.C should not be heuristically remapped.
            if "." in tok and tok[0].islower():
                return tok

            _cache_key = (
                tok,
                os.path.normcase(os.path.abspath(str(caller_file))) if caller_file else "",
            )
            _cached = _resolve_owner_class_name_cache.get(_cache_key)
            if _cached is not None:
                return _cached

            # Already class-like
            if tok[0].isupper():
                _resolve_owner_class_name_cache[_cache_key] = tok
                return tok

            cfile = str(caller_file or "")
            ckey = os.path.normcase(os.path.abspath(cfile)) if cfile else ""

            # 1) var map from caller source
            vmap = _caller_varmap_cache.get(ckey)
            if vmap is None:
                try:
                    ctext = file_content_cache.get(cfile) or read_file_cached(cfile)
                except Exception:
                    ctext = ""
                vmap = _build_var_map(ctext or "")
                _caller_varmap_cache[ckey] = vmap

            mapped = vmap.get(tok)
            if mapped:
                _out = _extract_class_from_mapped(strip_generics(mapped))
                _resolve_owner_class_name_cache[_cache_key] = _out
                return _out

            # 1b) inherited field map from extends chain
            ivmap = _caller_inherited_varmap_cache.get(ckey)
            if ivmap is None:
                _imports = _file_to_imports_early.get(ckey, {})
                _wildcards = _file_to_wildcards_early.get(ckey, [])
                _pkg = _file_to_package_early.get(ckey)
                ivmap = _build_inherited_var_map_for_file(cfile, ctext or "", _imports, _wildcards, _pkg)
                _caller_inherited_varmap_cache[ckey] = ivmap

            mapped = (ivmap or {}).get(tok)
            if mapped:
                _out = _extract_class_from_mapped(strip_generics(mapped))
                _resolve_owner_class_name_cache[_cache_key] = _out
                return _out

            # 2) object_class_map
            mapped = (
                object_class_map.get((ckey, tok.lower()))
                or object_class_map.get(tok.lower())
            )
            if mapped:
                _out = _extract_class_from_mapped(strip_generics(mapped))
                _resolve_owner_class_name_cache[_cache_key] = _out
                return _out

            # 3) heuristic capitalize
            _out = tok[0].upper() + tok[1:]
            _resolve_owner_class_name_cache[_cache_key] = _out
            return _out

        _return_decl_re_cache = {}

        def _get_return_type(class_name, method_name, _visited=None, caller_file=None, fallback_class_name=None):
            """
            1) method_return_index (inherits via __extends__)
            2) source-regex fallback (works even when AST/index is missing)
            """
            if not class_name or not method_name:
                return None

            _needs_file_context = bool(caller_file and _is_ambiguous_simple_type_name(class_name))

            def _clean_ret(rt):
                if not rt:
                    return None
                rt = strip_generics(str(rt)).strip()
                if not rt:
                    return None
                if rt.lower() in ("void", "<constructor>"):
                    return None
                return rt

            # ---- Fast path: index + inheritance
            if _visited is None:
                _visited = set()
            if not _needs_file_context and class_name not in _visited:
                _visited.add(class_name)
                entry = method_return_index.get(class_name, {})
                if method_name in entry:
                    ret = _clean_ret(entry.get(method_name))
                    if ret:
                        return ret
                parent = entry.get("__extends__")
                if parent and parent != class_name:
                    ret = _get_return_type(parent, method_name, _visited, caller_file=caller_file)
                    if ret:
                        return ret

            # ---- Fallback: read source directly (AST-independent)
            _decl_re = _return_decl_re_cache.get(method_name)
            if _decl_re is None:
                _decl_re = re.compile(
                    r'^[ \t]*(?:@\w+(?:\([^)]*\))?\s*)*'
                    r'(?:(?:public|protected|private|static|final|abstract|synchronized|native|strictfp|default)\s+)*'
                    r'(?:<[^>{;]+>\s*)?'
                    r'([A-Za-z_][\w$.]*(?:\s*<[^>{;]+>)?(?:\s*\[\s*\])*)\s+'
                    + re.escape(method_name)
                    + r'\s*\(',
                    re.MULTILINE
                )
                _return_decl_re_cache[method_name] = _decl_re

            _ctx_candidates = _resolve_type_paths_from_caller(class_name, caller_file) if caller_file else []
            _global_candidates = list(type_to_path_full_early.get(class_name, []))
            candidates = list(_ctx_candidates)
            for _p in _global_candidates:
                if _p not in candidates:
                    candidates.append(_p)

            for fpath in candidates:
                text = file_content_cache.get(fpath) or ""
                if not text:
                    try:
                        text = read_file_cached(fpath)
                    except Exception:
                        continue
                m = _decl_re.search(text)
                if not m:
                    continue
                ret = _clean_ret(m.group(1))
                if ret:
                    _dbg(f"_get_return_type[FALLBACK]: {class_name}.{method_name} -> {ret} ({fpath})")
                    return ret

            # Ambiguous simple class names should only fall back to global index
            # after caller-context candidates were exhausted.
            if _needs_file_context and class_name not in _visited:
                _visited.add(class_name)
                entry = method_return_index.get(class_name, {})
                if method_name in entry:
                    ret = _clean_ret(entry.get(method_name))
                    if ret:
                        return ret
                parent = entry.get("__extends__")
                if parent and parent != class_name:
                    ret = _get_return_type(parent, method_name, _visited, caller_file=caller_file)
                    if ret:
                        return ret

            # Super-call fallback:
            # If lookup in parent/owner class misses, retry in the subclass where
            # super.method() originated (e.g. ClassA when owner is ClassB).
            fb = strip_generics(fallback_class_name) if isinstance(fallback_class_name, str) else None
            if fb and fb != class_name:
                _dbg(f"_get_return_type[FALLBACK-CLASS]: {class_name}.{method_name} -> try {fb}")
                return _get_return_type(fb, method_name, _visited=_visited, caller_file=caller_file, fallback_class_name=None)

            _dbg(f"_get_return_type[MISS]: {class_name}.{method_name} caller={caller_file}")
            return None

        _get_return_type_impl = _get_return_type
        _get_return_type_cache = {}
        _get_return_type_miss = object()

        def _get_return_type(class_name, method_name, _visited=None, caller_file=None, fallback_class_name=None):
            # Cache only root calls; recursion carries _visited.
            if _visited is not None:
                return _get_return_type_impl(
                    class_name,
                    method_name,
                    _visited=_visited,
                    caller_file=caller_file,
                    fallback_class_name=fallback_class_name,
                )

            _key = (
                strip_generics(str(class_name or "")).strip(),
                str(method_name or "").strip(),
                os.path.normcase(os.path.abspath(str(caller_file))) if caller_file else "",
                strip_generics(str(fallback_class_name or "")).strip(),
            )
            _cached = _get_return_type_cache.get(_key, _get_return_type_miss)
            if _cached is not _get_return_type_miss:
                return _cached

            _out = _get_return_type_impl(
                class_name,
                method_name,
                _visited=None,
                caller_file=caller_file,
                fallback_class_name=fallback_class_name,
            )
            _get_return_type_cache[_key] = _out
            return _out
        _method_decl_re_cache = {}

        def _method_exists_in_class(class_name, method_name, caller_file=None, fallback_class_name=None):
            """
            Check whether method_name exists in class_name:
            1) method_return_index (fastest)
            2) import-aware source-file scan for duplicate simple class names
            """
            if not class_name or not method_name:
                return False

            _needs_file_context = bool(caller_file and _is_ambiguous_simple_type_name(class_name))
            if not _needs_file_context:
                owning = _resolve_class_for_method(class_name, method_name)
                if method_name in method_return_index.get(owning, {}):
                    return True

            if method_name not in _method_decl_re_cache:
                _method_decl_re_cache[method_name] = re.compile(
                    r'^[ \t]*(?:@\w+(?:\([^)]*\))?\s*)*'
                    r'(?:(?:public|protected|private|static|final|abstract|synchronized|native|strictfp|default)\s+)*'
                    r'(?:<[^>{;]+>\s*)?'
                    # Reject statement keywords so lines like
                    #   return createPaymentTypeInformation(...)
                    # are not misread as method declarations.
                    r'(?!(?:return|throw|new|if|for|while|switch|catch|case|do|try|else|assert|break|continue|yield)\b)'
                    r'(?:[A-Za-z_][\w$.]*(?:\s*<[^>{;]+>)?(?:\s*\[\s*\])*)\s+'
                    + re.escape(method_name)
                    + r'\s*\(',
                    re.MULTILINE
                )
            pat = _method_decl_re_cache[method_name]

            _ctx_candidates = _resolve_type_paths_from_caller(class_name, caller_file) if caller_file else []
            _global_candidates = list(type_to_path_full_early.get(class_name, []))
            candidates = list(_ctx_candidates)
            for _p in _global_candidates:
                if _p not in candidates:
                    candidates.append(_p)

            for fpath in candidates:
                text = file_content_cache.get(fpath) or ""
                if not text:
                    try:
                        text = read_file_cached(fpath)
                    except Exception:
                        continue
                if pat.search(text):
                    _dbg(f"_method_exists_in_class: FOUND {class_name}.{method_name} in {fpath}")
                    return True

            # Lombok accessor fallback: treat getter/setter/isX as existing on
            # this class when matching Lombok accessor annotation exists and
            # a matching field is present.
            owning_simple = strip_generics(str(class_name or "")).split('.')[-1]
            if owning_simple:
                field_names = []
                mname = str(method_name or "").strip()
                _needs_getter = bool((mname.startswith("get") and len(mname) > 3) or (mname.startswith("is") and len(mname) > 2))
                _needs_setter = bool(mname.startswith("set") and len(mname) > 3)
                if mname.startswith("get") and len(mname) > 3:
                    stem = mname[3:]
                elif mname.startswith("set") and len(mname) > 3:
                    stem = mname[3:]
                elif mname.startswith("is") and len(mname) > 2:
                    stem = mname[2:]
                else:
                    stem = ""

                if stem:
                    if len(stem) >= 2 and stem[0].isupper() and stem[1].isupper():
                        i = 0
                        n = len(stem)
                        while i < n and stem[i].isupper():
                            i += 1
                        if i > 1 and i < n and stem[i].islower():
                            i -= 1
                        field_names.append(stem[:i].lower() + stem[i:])
                    else:
                        field_names.append(stem[:1].lower() + stem[1:])
                    if stem not in field_names:
                        field_names.append(stem)

                if field_names:
                    field_alt = "|".join(re.escape(x) for x in field_names)
                    class_hdr_pat = re.compile(
                        r'(?ms)(.*?)\bclass\s+' + re.escape(owning_simple) + r'\b[^\{]*\{'
                    )
                    class_level_getter_pat = re.compile(r'@(?:lombok\.)?(?:Getter|Data|Value)\b')
                    class_level_setter_pat = re.compile(r'@(?:lombok\.)?(?:Setter|Data)\b')
                    field_pat = re.compile(
                        r'(?m)^[ \t]*(?:(?:public|private|protected)\s+)?'
                        r'(?:(?:static|final|transient|volatile)\s+)*'
                        r'[A-Za-z_][\w$.<>,\[\]? \t]*\s+'
                        r'(?:' + field_alt + r')\s*(?:=|;)',
                        re.IGNORECASE,
                    )
                    field_getter_pat = re.compile(
                        r'(?ms)@(?:lombok\.)?Getter\b[^\n\r]*\n[ \t]*(?:(?:public|private|protected)\s+)?'
                        r'(?:(?:static|final|transient|volatile)\s+)*[A-Za-z_][\w$.<>,\[\]? \t]*\s+'
                        r'(?:' + field_alt + r')\s*(?:=|;)',
                        re.IGNORECASE,
                    )
                    field_setter_pat = re.compile(
                        r'(?ms)@(?:lombok\.)?Setter\b[^\n\r]*\n[ \t]*(?:(?:public|private|protected)\s+)?'
                        r'(?:(?:static|final|transient|volatile)\s+)*[A-Za-z_][\w$.<>,\[\]? \t]*\s+'
                        r'(?:' + field_alt + r')\s*(?:=|;)',
                        re.IGNORECASE,
                    )

                    for fpath in candidates:
                        text = file_content_cache.get(fpath) or ""
                        if not text:
                            try:
                                text = read_file_cached(fpath)
                            except Exception:
                                continue
                        if not text:
                            continue

                        m_cls = class_hdr_pat.search(text)
                        if not m_cls:
                            continue
                        class_header = m_cls.group(1) or ""
                        class_open = text.find("{", m_cls.end() - 1)
                        if class_open == -1:
                            continue
                        class_tail = text[class_open:]

                        _has_accessor = False
                        if _needs_getter:
                            _has_accessor = bool(class_level_getter_pat.search(class_header) or field_getter_pat.search(class_tail))
                        elif _needs_setter:
                            _has_accessor = bool(class_level_setter_pat.search(class_header) or field_setter_pat.search(class_tail))
                        else:
                            _has_accessor = bool(class_level_getter_pat.search(class_header) or class_level_setter_pat.search(class_header))

                        if _has_accessor and field_pat.search(class_tail):
                            _dbg(f"_method_exists_in_class: LOMBOK {class_name}.{method_name} via field in {fpath}")
                            return True

            # Super-call fallback: if not found on the parent/owner side, check
            # the subclass where super.method() was invoked.
            fb = strip_generics(fallback_class_name) if isinstance(fallback_class_name, str) else None
            if fb and fb != class_name:
                _dbg(f"_method_exists_in_class[FALLBACK-CLASS]: {class_name}.{method_name} -> try {fb}")
                return _method_exists_in_class(fb, method_name, caller_file=caller_file, fallback_class_name=None)

            _dbg(f"_method_exists_in_class: MISS {class_name}.{method_name} caller={caller_file} candidates={len(candidates)}")
            return False

        _method_exists_in_class_impl = _method_exists_in_class
        _method_exists_in_class_cache = {}

        def _method_exists_in_class(class_name, method_name, caller_file=None, fallback_class_name=None):
            _key = (
                strip_generics(str(class_name or "")).strip(),
                str(method_name or "").strip(),
                os.path.normcase(os.path.abspath(str(caller_file))) if caller_file else "",
                strip_generics(str(fallback_class_name or "")).strip(),
            )
            _cached = _method_exists_in_class_cache.get(_key)
            if _cached is not None:
                return _cached

            _out = _method_exists_in_class_impl(
                class_name,
                method_name,
                caller_file=caller_file,
                fallback_class_name=fallback_class_name,
            )
            _method_exists_in_class_cache[_key] = _out
            return _out

        def _super_fallback_class(owner_class, caller_class):
            """Return caller_class when owner_class is caller's direct parent (super path)."""
            oc = strip_generics(owner_class) if isinstance(owner_class, str) else owner_class
            cc = strip_generics(caller_class) if isinstance(caller_class, str) else caller_class
            if not oc or not cc:
                return None
            parent = method_return_index.get(cc, {}).get("__extends__")
            if parent and strip_generics(parent) == oc:
                return cc
            return None


        # ------------------------------------------------------------------
        # Case 2 helper â€” field-access chain resolution
        # ------------------------------------------------------------------
        # Resolves a dot-path that may mix field names and method calls,
        # e.g. "obj1.repo.dao.save()" where obj1, repo, dao are variables/
        # fields (no parens) and only save() is the actual method call.
        # Returns (resolved_class, trailing_method_name_or_None).
        def _resolve_field_chain(token_path, parent_class, file_name, caller_method_name=None):
            # Strip the trailing "methodName" off the path (the part before "("
            # has already been passed in, so we just split off the last token).
            m_trail = re.match(r'^(.*?)\.([A-Za-z_]\w*)\s*$', token_path, re.DOTALL)
            if m_trail:
                prefix_path = m_trail.group(1)
                trailing_method = m_trail.group(2)
            else:
                prefix_path = token_path
                trailing_method = None

            # Direct package-qualified owner (e.g. org.foo.Bar.method()) should
            # be treated as a class token, not as nested field dereferences.
            _pp = str(prefix_path or "").strip()
            _fqn_prefix = re.match(r'^[a-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$', _pp)
            if _fqn_prefix:
                _simple_owner = _pp.split('.')[-1]
                resolved_class = _normalize_owner_class_for_member(_simple_owner, trailing_method)
                return resolved_class, trailing_method

            tokens = [t.strip() for t in prefix_path.split('.') if t.strip()]
            current_class = None
            for i, tok in enumerate(tokens):
                if i == 0:
                    # First token: go through the full _lookup_type resolution
                    # (handles object_class_map, iface_to_impl, etc.)
                    current_class = _lookup_type(
                        tok,
                        parent_class,
                        file_name,
                        caller_method_name=caller_method_name,
                    )
                    # Strict unresolved-owner behavior: if the chain root cannot
                    # be resolved to a project class, do not fallback to caller class.
                    # This avoids false positives like:
                    #   Jwts.builder(...)  -> RaboJwtBuilder.builder
                    #   Instant.now()      -> RaboJwtBuilder.now
                    if not current_class:
                        return None, None
                else:
                    # Subsequent tokens: treat as a field on current_class.
                    # Try object_class_map (scoped then global), then
                    # method_return_index return-type as a last resort.
                    _fkey = os.path.normcase(os.path.abspath(file_name)) if file_name else ""
                    resolved = (
                        object_class_map.get((_fkey, tok.lower()))
                        or object_class_map.get(tok.lower())
                    )
                    if resolved:
                        current_class = strip_generics(resolved)
                    else:
                        ret = method_return_index.get(current_class, {}).get(tok)
                        if ret and str(ret).lower() not in ('void', '<constructor>'):
                            current_class = strip_generics(str(ret).split('.')[-1])
                        # else: best effort â€” keep current_class

            # Keep strict mode for unresolved roots: never synthesize caller class.
            resolved_class = current_class
            if not resolved_class:
                return None, None
            resolved_class = _normalize_owner_class_for_member(resolved_class, trailing_method)
            return resolved_class, trailing_method

        def _normalize_owner_class_for_member(class_name, member_name=None):
            if not isinstance(class_name, str):
                return class_name
            cls = strip_generics(class_name).strip()
            if not cls:
                return cls

            # For package-qualified owners, keep the FQN as authoritative.
            # Do not decompose it into simple-name candidates.
            if "." in cls and cls[0].islower():
                return cls

            member = (member_name or "").strip()
            if not member:
                return cls

            candidates = []
            seen = set()

            def _add(c):
                if not isinstance(c, str):
                    return
                c = strip_generics(c).strip()
                if c and c not in seen:
                    seen.add(c)
                    candidates.append(c)

            parts = [p.strip() for p in cls.split('.') if p.strip()]

            # IMPORTANT:
            # For nested types A.B.C, prefer enclosing owners first: A.B, A, then C, B, then full A.B.C
            # This resolves builder-variable cases to logical owner class.
            if len(parts) > 1:
                for i in range(len(parts) - 1, 0, -1):
                    _add(".".join(parts[:i]))
                for p in reversed(parts):
                    if p and p[0].isupper():
                        _add(p)
                _add(cls)  # full nested type as fallback
            else:
                _add(cls)

            for cand in candidates:
                owner = _resolve_class_for_method(cand, member, fallback_class_name=class_name)
                if owner and member in method_return_index.get(owner, {}):
                    return owner

            return cls


        _early_method_varmap_cache = {}

        def _get_method_local_var_map(file_name, method_name):
            """Method-local var map used by early chain mapping to avoid file-scope collisions."""
            if not file_name or not method_name:
                return {}
            _key = (os.path.normcase(os.path.abspath(file_name)), str(method_name))
            _cached = _early_method_varmap_cache.get(_key)
            if _cached is not None:
                return _cached

            _txt = file_content_cache.get(file_name) or ""
            if not _txt:
                try:
                    _txt = read_file_cached(file_name)
                except Exception:
                    _txt = ""

            _block = _extract_method_block(_txt, method_name) if _txt else ""
            _map = _build_var_map(_block) if _block else {}
            _early_method_varmap_cache[_key] = _map
            return _map

        def _class_exists_in_project(_class_name):
            """True when class source exists at least once in scanned project files."""
            if not isinstance(_class_name, str):
                return False
            _cls = strip_generics(_class_name).strip()
            if not _cls:
                return False

            # For package-qualified owners, try FQN first.
            if "." in _cls and _cls[0].islower():
                _fqn_hits = _fqn_to_paths.get(_cls, [])
                if _fqn_hits:
                    return True
                _cls = _cls.split(".")[-1]

            _candidates = []
            _seen = set()

            def _add(_name):
                if not isinstance(_name, str):
                    return
                _n = _name.strip()
                if _n and _n not in _seen:
                    _seen.add(_n)
                    _candidates.append(_n)

            _add(_cls)
            if "." in _cls:
                _parts = [p for p in _cls.split(".") if p]
                if _parts:
                    _add(_parts[0])
                    _add(_parts[-1])

            for _cand in _candidates:
                if _class_to_paths.get(_cand):
                    return True
            return False

        _lookup_inherited_varmap_cache = {}

        def _lookup_type(base, parent_class, file_name, caller_method_name=None):
            if not isinstance(base, str) or base.strip() == "":
                return strip_generics(parent_class)
            b = base.strip()
            b = re.sub(r'@\w+(?:\([^)]*\))?\s*', '', b)
            b = re.sub(
                r'^(?:(?:public|protected|private|static|final|abstract|synchronized|native|strictfp|default|transient|volatile)\s+)+',
                '',
                b,
                flags=re.IGNORECASE,
            ).strip()
            if b.lower() in keyword_set:
                return strip_generics(parent_class)

            # Class-like owner tokens (UpperCamelCase) must not be looked up as
            # lowercase variables. Otherwise a class token such as Authorisation
            # can collide with a variable named `authorisation` and drift to
            # Authorisation1.
            if isinstance(b, str) and b[:1].isupper():
                b_no_gen = strip_generics(b)
                if _class_exists_in_project(b_no_gen):
                    return b_no_gen

            # Prefer declarations from the current method before file-scoped hints.
            # This prevents collisions when the same variable name appears in
            # multiple methods with different types (e.g. builder.build(...)).
            _method_vmap = _get_method_local_var_map(file_name, caller_method_name)
            _method_hit = _method_vmap.get(b)
            if _method_hit:
                _mapped = strip_generics(_method_hit)
                # Keep package-qualified FQNs (e.g. nl.a.b.ClassName) so later
                # path enrichment can resolve the exact source file.
                if isinstance(_mapped, str) and "." in _mapped and _mapped[0].islower():
                    return _mapped if _class_exists_in_project(_mapped) else None
                _simple = _extract_class_from_mapped(_mapped)
                return _simple if _class_exists_in_project(_simple) else None

            # Method-scope guard for lowercase object roots:
            # if the method-local map exists but this lowercase token is not
            # declared in the current method, do NOT fall back to file-scoped
            # maps (which can bleed in a type from another method with the same
            # variable name).
            if _method_vmap and isinstance(b, str) and b[:1].islower() and not relax_method_scope_lowercase_lookup:
                return None

            _ocm_scoped_key = os.path.normcase(os.path.abspath(file_name)) if file_name else ""
            t_scoped = None
            if isinstance(b, str) and b[:1].islower():
                t_scoped = object_class_map.get((_ocm_scoped_key, b.lower()))
            if t_scoped:
                _mapped = strip_generics(t_scoped)
                if isinstance(_mapped, str) and "." in _mapped and _mapped[0].islower():
                    return _mapped if _class_exists_in_project(_mapped) else None
                _simple = _extract_class_from_mapped(_mapped)
                return _simple if _class_exists_in_project(_simple) else None

            # Inherited fields declared in parent classes (extends chain).
            if isinstance(b, str) and b[:1].islower() and _ocm_scoped_key:
                _ivmap = _lookup_inherited_varmap_cache.get(_ocm_scoped_key)
                if _ivmap is None:
                    _caller_text = file_content_cache.get(file_name) or read_file_cached(file_name)
                    _imports = _file_to_imports_early.get(_ocm_scoped_key, {})
                    _wildcards = _file_to_wildcards_early.get(_ocm_scoped_key, [])
                    _pkg = _file_to_package_early.get(_ocm_scoped_key)
                    _ivmap = _build_inherited_var_map_for_file(
                        file_name,
                        _caller_text,
                        _imports,
                        _wildcards,
                        _pkg,
                    )
                    _lookup_inherited_varmap_cache[_ocm_scoped_key] = _ivmap
                _mapped_inherited = (_ivmap or {}).get(b)
                if _mapped_inherited:
                    _mapped = strip_generics(_mapped_inherited)
                    if isinstance(_mapped, str) and "." in _mapped and _mapped[0].islower():
                        return _mapped if _class_exists_in_project(_mapped) else None
                    _simple = _extract_class_from_mapped(_mapped)
                    return _simple if _class_exists_in_project(_simple) else None

            # Strict guard for unresolved lowercase variable roots:
            # do not use global/simple-name fallbacks that can bind to the wrong
            # class when duplicate simple names exist across packages.
            if isinstance(b, str) and b[:1].islower():
                return None

            # t_global = object_class_map.get(b.lower())
            # if t_global:
            #     return strip_generics(t_global).split('.')[0]

            b_no_gen = strip_generics(b)
            cap = (b_no_gen[0].upper() + b_no_gen[1:]) if b_no_gen else b_no_gen
            if cap and cap in method_return_index and _class_exists_in_project(cap):
                return cap

            if b_no_gen in iface_to_impl_map:
                impl_name = iface_to_impl_map[b_no_gen]
                impl_path = type_to_path_full.get(impl_name)
                if impl_path:
                    iface_path = type_to_path_full.get(b_no_gen)
                    method_in_iface = bool(method_return_index.get(b_no_gen))
                    method_in_impl = bool(method_return_index.get(impl_name))
                    if method_in_impl and not method_in_iface:
                        return impl_name if _class_exists_in_project(impl_name) else None

            return b_no_gen if _class_exists_in_project(b_no_gen) else None

        def map_class_method_call(obj_call, parent_class, file_name, caller_method_name=None):
            if not isinstance(obj_call, str) or obj_call.strip() == "":
                return "None"

            # Normalize enum-constant method calls like:
            #   Response.Status.OK.getStatusCode()
            # by dropping the enum constant segment (OK) so owner resolution
            # stays on Response.Status instead of drifting to a project class
            # named Ok.
            _enum_const_call = re.match(
                r'^\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\.([A-Z][A-Z0-9_]*)\.([A-Za-z_]\w*)\s*(\(.*\))\s*$',
                obj_call,
            )
            if _enum_const_call:
                _owner_chain = _enum_const_call.group(1)
                _method_name = _enum_const_call.group(3)
                _suffix = _enum_const_call.group(4) or "()"
                obj_call = "{}.{}{}".format(_owner_chain, _method_name, _suffix)

            mkw = re.match(r'^\s*(return|this|super|new)\s*\.\s*([A-Za-z_]\w*)(.*)$', obj_call, flags=re.IGNORECASE)
            if mkw:
                keyword = mkw.group(1).lower()
                meth = mkw.group(2)
                rest = mkw.group(3) or ""
                if keyword == "super":
                    _pc = strip_generics(parent_class)
                    _super_cls = _resolve_super_owner_class(_pc, meth)
                    return "{}.{}{}".format(_super_cls, meth, rest)
                return "{}.{}{}".format(strip_generics(parent_class), meth, rest)

            if "." not in obj_call:
                _mu = re.match(r'^\s*([A-Za-z_]\w*)\s*(\(.*\))?\s*$', obj_call)
                if not _mu:
                    return obj_call

                _mname = _mu.group(1)
                _suffix = _mu.group(2) or "()"
                _caller_norm = os.path.normcase(os.path.abspath(file_name)) if file_name else ""
                _imp_map = _file_to_imports_early.get(_caller_norm, {})

                # Resolve explicit static import: import static a.b.C.member;
                _member_fqn = _imp_map.get(_mname)
                if isinstance(_member_fqn, str) and _member_fqn.count(".") >= 1:
                    _owner_fqn = _member_fqn.rsplit('.', 1)[0]
                    _owner_candidates = list(_fqn_to_paths_early.get(_owner_fqn, []) or [])

                    # Filesystem fallback for sibling modules not pre-indexed.
                    if not _owner_candidates and file_name:
                        _rel_java = os.path.join(*_owner_fqn.split(".")) + ".java"
                        _caller_abs = os.path.abspath(file_name)
                        _src_marker = os.path.join("src", "main", "java")
                        _marker_pos = os.path.normpath(_caller_abs).lower().find(_src_marker.lower())
                        if _marker_pos != -1:
                            _module_root = os.path.normpath(_caller_abs)[:_marker_pos].rstrip("\\/")
                            _parent_root = os.path.dirname(_module_root)
                            _probe_roots = []
                            if _module_root:
                                _probe_roots.append(_module_root)
                            if _parent_root and os.path.isdir(_parent_root):
                                for _sib in os.listdir(_parent_root):
                                    _sib_root = os.path.join(_parent_root, _sib)
                                    if os.path.isdir(_sib_root):
                                        _probe_roots.append(_sib_root)
                            for _root in _probe_roots:
                                _cand = os.path.join(_root, "src", "main", "java", _rel_java)
                                if os.path.isfile(_cand):
                                    _owner_candidates.append(_cand)

                    for _p in _owner_candidates:
                        if os.path.isfile(_p) and _file_declares_member_bfs(_p, _mname):
                            return "{}.{}{}".format(os.path.splitext(_p)[0], _mname, _suffix)

                # Same-class unqualified call fallback (declared or Lombok-generated).
                # Example: getPurgeDate() inside PaymentOrder.setStatus should map to
                # PaymentOrder.getPurgeDate() even when there is no explicit method body.
                _parent_simple = strip_generics(parent_class)
                if _method_exists_in_class(
                    _parent_simple,
                    _mname,
                    caller_file=file_name,
                    fallback_class_name=_parent_simple,
                ):
                    return "{}.{}{}".format(_parent_simple, _mname, _suffix)

                return obj_call

            # Import-aware external type guard:
            # If the call root is an explicitly imported type that does not
            # belong to project sources, do not attempt project owner mapping.
            # This avoids false remaps such as Response.Status.OK.getStatusCode
            # -> .../Ok.getStatusCode.
            _root_tok_m = re.match(r'^\s*([A-Za-z_]\w*)\.', obj_call)
            if _root_tok_m and file_name:
                _root_tok = _root_tok_m.group(1)
                _caller_norm = os.path.normcase(os.path.abspath(file_name))
                _imp_map = _file_to_imports_early.get(_caller_norm, {})
                _root_fqn = _imp_map.get(_root_tok)
                if isinstance(_root_fqn, str) and _root_fqn.strip():
                    # Explicitly imported but not project-owned => external/built-in.
                    if not _fqn_to_paths_early.get(_root_fqn):
                        return "None"

            # Keep package-qualified owner tokens intact (e.g. org.foo.Bar.method)
            # instead of splitting at the first dot (which yields "org").
            _fqn_owner_match = re.match(
                r'^\s*([a-z_][A-Za-z0-9_]*(?:\.[a-z_][A-Za-z0-9_]*)*\.[A-Z][A-Za-z0-9_]*)\.(.+)$',
                obj_call,
            )
            if _fqn_owner_match:
                mapped_base = _fqn_owner_match.group(1).strip()
                rest = _fqn_owner_match.group(2).strip()
                _root_from_lower_var = False
            else:
                first_dot = obj_call.find(".")
                obj = obj_call[:first_dot]
                rest = obj_call[first_dot + 1:]
                _root_from_lower_var = bool(obj and obj[:1].islower())
                mapped_base = _lookup_type(obj, parent_class, file_name, caller_method_name=caller_method_name)

            method_token = rest.split('(')[0].split('.')[0].strip()
            if method_token:
                # For lowercase variable roots (object instances), the variable's
                # declared/imported type is authoritative. Do not normalize/remap
                # to a concrete subclass by method heuristics.
                if not _root_from_lower_var:
                    mapped_base = _normalize_owner_class_for_member(mapped_base, method_token)
                    # Keep explicit FQN owner as-is; avoid collapsing to a simple
                    # class that may collide in another package.
                    if not (isinstance(mapped_base, str) and "." in mapped_base and mapped_base[0].islower()):
                        _prefer_concrete_owner = not _is_direct_super_of_caller(mapped_base, parent_class)
                        mapped_base = _resolve_class_for_method(
                            mapped_base,
                            method_token,
                            prefer_concrete=_prefer_concrete_owner,
                            fallback_class_name=strip_generics(parent_class),
                        )

            if not mapped_base or not _class_exists_in_project(mapped_base):
                return obj_call if keep_unresolved_owner_calls else "None"

            return "{}.{}".format(mapped_base, rest)

        def _choose_best_candidate_early(candidates, caller_file, member_name=None):
            if not candidates:
                return None
            if len(candidates) == 1:
                return candidates[0]

            _cands = list(candidates)
            if member_name:
                _declared = [_c for _c in _cands if _file_declares_member_bfs(_c, member_name)]
                if len(_declared) == 1:
                    return _declared[0]
                if _declared:
                    _cands = _declared

            if caller_file:
                try:
                    _caller_dir = os.path.dirname(os.path.abspath(caller_file))

                    def _score(_p):
                        try:
                            _p_dir = os.path.dirname(os.path.abspath(_p))
                            _common = os.path.commonpath([_caller_dir, _p_dir])
                            return (len(_common), -len(str(_p or "")))
                        except Exception:
                            return (0, -len(str(_p or "")))

                    _cands = sorted(_cands, key=_score, reverse=True)
                except Exception:
                    pass

            return _cands[0]

        def _decap_java_bean_name(name):
            if not isinstance(name, str) or not name:
                return ""
            if len(name) >= 2 and name[0].isupper() and name[1].isupper():
                i = 0
                n = len(name)
                while i < n and name[i].isupper():
                    i += 1
                if i > 1 and i < n and name[i].islower():
                    i -= 1
                return name[:i].lower() + name[i:]
            return name[0].lower() + name[1:]

        def _accessor_field_candidates_local(method_name):
            m = str(method_name or "").strip()
            stem = ""
            if m.startswith("get") and len(m) > 3:
                stem = m[3:]
            elif m.startswith("is") and len(m) > 2:
                stem = m[2:]
            elif m.startswith("set") and len(m) > 3:
                stem = m[3:]
            if not stem:
                return []
            cands = []
            c1 = _decap_java_bean_name(stem)
            if c1:
                cands.append(c1)
            if stem and stem not in cands:
                cands.append(stem)
            c2 = stem[:1].lower() + stem[1:] if stem else ""
            if c2 and c2 not in cands:
                cands.append(c2)
            return cands

        def _infer_return_type_from_accessor_field(owner_class, accessor_method, caller_file):
            """Best-effort return type inference for accessor chains when index misses.

            Example: Req.getRequestor().getRequestorId()
            If getRequestor return type is absent in method_return_index, infer it from
            Req field declaration: private Requestor requestor;
            """
            method_name = str(accessor_method or "").strip()
            if not method_name:
                return None
            if not (method_name.startswith("get") or method_name.startswith("is") or method_name.startswith("set")):
                return None

            owner = strip_generics(str(owner_class or "")).strip()
            if not owner:
                return None

            owner_file = _resolve_owner_file_for_chain(owner, caller_file, member_name=method_name)
            if not owner_file:
                # _resolve_class_path is defined later in this function.
                # Use an early-safe path resolution fallback here.
                try:
                    _imports = {}
                    _wildcards = []
                    _pkg = None
                    if caller_file and os.path.isfile(caller_file):
                        _caller_src = file_content_cache.get(caller_file) or read_file_cached(caller_file)
                        _imports, _wildcards, _pkg = _parse_import_context(_caller_src)
                    owner_file = _resolve_dep_path(
                        owner,
                        _imports,
                        _caller_file=caller_file,
                        _caller_pkg=_pkg,
                        _caller_wildcards=_wildcards,
                        _member_name=method_name,
                    )
                except Exception:
                    owner_file = None

                if not owner_file:
                    _cands = list(_class_to_paths.get(str(owner).split('.')[-1], []))
                    owner_file = _choose_dep_candidate(_cands, caller_file, None, method_name)
            if not owner_file or not os.path.isfile(owner_file):
                return None

            txt = file_content_cache.get(owner_file)
            if txt is None:
                try:
                    txt = read_file_cached(owner_file)
                except Exception:
                    txt = ""
            if not txt:
                return None

            names = _accessor_field_candidates_local(method_name)
            if not names:
                return None
            alt = "|".join(re.escape(x) for x in names if x)
            if not alt:
                return None

            # Java field declaration: [mods] Type fieldName [= ...];
            fld_re = re.compile(
                r'(?m)^[ \t]*(?:(?:public|protected|private)\s+)?'
                r'(?:(?:static|final|transient|volatile)\s+)*'
                r'([A-Za-z_][\w$.]*(?:\s*<[^>{}]+>)?(?:\s*\[\s*\])*)\s+'
                r'(?:' + alt + r')\s*(?:=|;)',
            )

            m = fld_re.search(txt)
            if not m:
                return None

            raw_t = strip_generics(m.group(1) or "").strip()
            if not raw_t:
                return None

            raw_t = raw_t.replace("[]", "").strip()
            if not raw_t:
                return None

            if "." in raw_t and raw_t[0].islower():
                return raw_t
            return raw_t.split(".")[-1]

        def resolve_chained_with_classes(obj_call, parent_class, file_name, caller_method_name=None):
            if not isinstance(obj_call, str) or obj_call.strip() == "":
                return "None"
            first_dot = obj_call.find(".")
            if first_dot == -1 or "(" not in obj_call:
                return map_class_method_call(obj_call, parent_class, file_name, caller_method_name=caller_method_name)

            first_paren = obj_call.find("(")
            prefix_before_call = obj_call[:first_paren]
            suffix_after_prefix = obj_call[first_paren:]

            current_class, first_method = _resolve_field_chain(
                prefix_before_call, parent_class, file_name, caller_method_name=caller_method_name
            )
            if not first_method:
                return map_class_method_call(obj_call, parent_class, file_name, caller_method_name=caller_method_name)

            remaining_methods = re.findall(r'\.([A-Za-z_]\w*)\s*\(', suffix_after_prefix)
            if not remaining_methods and ")." in suffix_after_prefix:
                _tail_after_first = suffix_after_prefix.split(")", 1)[1]
                remaining_methods = re.findall(r'\.([A-Za-z_]\w*)\s*(?:\(|(?=\.|\s*$))', _tail_after_first)
            methods = [first_method] + remaining_methods

            _super_rooted = bool(re.match(r'^\s*super\s*\.', obj_call, flags=re.IGNORECASE))
            _current_ctx_file = file_name
            _ret_decl_re2_cache = {}
            chain_render = []
            _pending_chain_element_type = None
            for i, m in enumerate(methods):
                owning_class = _normalize_owner_class_for_member(current_class, m)
                _keep_direct_super_owner = (i == 0 and _is_direct_super_of_caller(owning_class, parent_class))
                owning_class = _resolve_class_for_method(
                    strip_generics(owning_class),
                    m,
                    prefer_concrete=not ((_super_rooted and i == 0) or _keep_direct_super_owner),
                    fallback_class_name=strip_generics(parent_class),
                )
                # Chain rule: method2+ owner comes from method1 return-type context.
                _owner_token = strip_generics(owning_class)
                if i > 0 and _current_ctx_file:
                    _owner_token = os.path.splitext(os.path.abspath(_current_ctx_file))[0]
                chain_render.append("{}.{}()".format(_owner_token, m))

                if i == len(methods) - 1:
                    break

                next_m = methods[i + 1]

                # Step 1: index lookup (inheritance-aware)
                owner_for_ret = _resolve_owner_class_name(owning_class, _current_ctx_file)
                owner_file_for_ret = _resolve_owner_file_for_chain(
                    owner_for_ret,
                    _current_ctx_file,
                    member_name=m,
                )
                super_fb_cls = _super_fallback_class(owner_for_ret, parent_class)
                ret_type = _get_return_type(
                    owner_for_ret,
                    m,
                    caller_file=owner_file_for_ret,
                    fallback_class_name=super_fb_cls,
                )
                if not ret_type:
                    ret_type = _infer_return_type_from_accessor_field(
                        owner_for_ret,
                        m,
                        owner_file_for_ret or _current_ctx_file or file_name,
                    )

                # If index says a container type (e.g. List), inspect source signature
                # to unwrap generic element type for chained method resolution.
                declared_ret = _get_declared_return_type_from_file(owner_file_for_ret, m)
                _container_base, _container_elem = _container_and_element_type(declared_ret or ret_type)
                ret_type_for_chain = _unwrap_generic_return_type(declared_ret) if declared_ret else _unwrap_generic_return_type(ret_type)

                _pair_elem = _pair_like_accessor_result_type(declared_ret or ret_type, next_m)
                if _pair_elem:
                    ret_type_for_chain = _container_base or "Pair"
                    _pending_chain_element_type = _pair_elem

                # Preserve container context for accessors such as get(0).
                if next_m in _collection_like_methods and _container_base in (_generic_container_types | {"Map"}):
                    ret_type_for_chain = _container_base
                    if _container_base == "Map":
                        _map_args = _top_level_generic_args(declared_ret or ret_type)
                        _pending_chain_element_type = strip_generics(_map_args[1]) if len(_map_args) > 1 else _container_elem
                    else:
                        _pending_chain_element_type = _container_elem

                # Factory/proxy pattern: x.getBusinessObject(Target.class).method(...)
                # When declared return is generic/erased, use class-literal target.
                if not ret_type_for_chain:
                    _class_lit_target = _extract_class_literal_target(obj_call, m)
                    if _class_lit_target:
                        ret_type_for_chain = _class_lit_target

                _dbg(f"resolve_chain: owner={owning_class}, owner_for_ret={owner_for_ret}, method_1={m}, return(index)={ret_type}, method_2={next_m}, file={file_name}")
                if ret_type_for_chain:
                    next_class = strip_generics(str(ret_type_for_chain).split('.')[-1])
                    next_type_paths = _resolve_type_paths_for_chain_return(
                        ret_type_for_chain,
                        owner_file_for_ret,
                        fallback_file=_current_ctx_file,
                        member_name=next_m,
                    )

                    ok = False
                    if next_type_paths:
                        for _ntp in next_type_paths:
                            _next_simple = os.path.splitext(os.path.basename(_ntp))[0]
                            if _method_exists_in_class(
                                _next_simple,
                                next_m,
                                caller_file=_ntp,
                                fallback_class_name=super_fb_cls,
                            ):
                                current_class = _next_simple
                                _current_ctx_file = _ntp
                                ok = True
                                break
                        if not ok:
                            # Keep owner-file return-type context authoritative for
                            # chained method2 even when method discovery is incomplete.
                            _best_ntp = _choose_best_candidate_early(next_type_paths, owner_file_for_ret, member_name=next_m) or next_type_paths[0]
                            current_class = os.path.splitext(os.path.basename(_best_ntp))[0]
                            _current_ctx_file = _best_ntp
                            ok = True
                    else:
                        ok = _method_exists_in_class(
                            next_class,
                            next_m,
                            caller_file=owner_file_for_ret,
                            fallback_class_name=super_fb_cls,
                        )
                        if not ok and _well_known_accessor_exists(next_class, next_m):
                            ok = True
                        if ok:
                            current_class = next_class
                            _current_ctx_file = owner_file_for_ret

                    _dbg(f"resolve_chain: next_class={next_class}, method_2={next_m}, exists={ok}")
                    if ok:
                        # After List.get(...), switch context to known element type.
                        if _pending_chain_element_type and m in _collection_like_methods:
                            current_class = _pending_chain_element_type
                            _pending_chain_element_type = None
                        continue
                    break

                # Step 2: file-based return type extraction
                ret_from_file = None
                _ret_decl_re2 = _ret_decl_re2_cache.get(m)
                if _ret_decl_re2 is None:
                    _ret_decl_re2 = re.compile(
                        r'\b([A-Za-z_]\w*(?:<[^>]+>)?)\s+' + re.escape(m) + r'\s*\(',
                        re.MULTILINE
                    )
                    _ret_decl_re2_cache[m] = _ret_decl_re2
                _owner_key = strip_generics(owning_class)
                _owner_file_candidates = list(type_to_path_full_early.get(_owner_key, []))
                if not _owner_file_candidates:
                    _owner_file_candidates = _resolve_type_paths_from_caller(
                        _owner_key,
                        _current_ctx_file or file_name,
                    )
                if not _owner_file_candidates and isinstance(_owner_key, str) and "." in _owner_key:
                    _owner_file_candidates = _resolve_type_paths_from_caller(
                        _owner_key.split(".")[-1],
                        _current_ctx_file or file_name,
                    )

                for fpath in _owner_file_candidates:
                    text = file_content_cache.get(fpath) or ""
                    if not text:
                        try:
                            text = read_file_cached(fpath)
                        except Exception:
                            continue
                    fm = _ret_decl_re2.search(text)
                    if fm:
                        candidate = strip_generics(fm.group(1))
                        if candidate.lower() not in ('void', 'public', 'private',
                                                     'protected', 'static', 'final',
                                                     'return', 'new', 'boolean',
                                                     'int', 'long', 'double', 'float',
                                                     'string', 'object'):
                            ret_from_file = candidate
                            break

                if ret_from_file and _method_exists_in_class(
                    _unwrap_generic_return_type(ret_from_file) or ret_from_file,
                    next_m,
                    caller_file=owner_file_for_ret,
                    fallback_class_name=super_fb_cls,
                ):
                    _dbg(f"resolve_chain: return(file)={ret_from_file}, method_2={next_m}, exists=True")
                    current_class = _unwrap_generic_return_type(ret_from_file) or ret_from_file
                    _current_ctx_file = owner_file_for_ret
                    continue
                _dbg(f"resolve_chain: STOP owner={owning_class}, method_1={m}, method_2={next_m}, return(file)={ret_from_file}")
                break
            return ".".join(chain_render)

        _map_class_method_call_impl = map_class_method_call
        _map_class_method_call_cache = {}
        _map_class_method_call_miss = object()

        def map_class_method_call(obj_call, parent_class, file_name, caller_method_name=None):
            _key = (
                str(obj_call or "").strip(),
                strip_generics(str(parent_class or "")).strip(),
                os.path.normcase(os.path.abspath(str(file_name))) if file_name else "",
                str(caller_method_name or "").strip(),
            )
            _cached = _map_class_method_call_cache.get(_key, _map_class_method_call_miss)
            if _cached is not _map_class_method_call_miss:
                return _cached
            _out = _map_class_method_call_impl(
                obj_call,
                parent_class,
                file_name,
                caller_method_name=caller_method_name,
            )
            _map_class_method_call_cache[_key] = _out
            return _out

        _resolve_chained_with_classes_impl = resolve_chained_with_classes
        _resolve_chained_with_classes_cache = {}
        _resolve_chained_with_classes_miss = object()

        def resolve_chained_with_classes(obj_call, parent_class, file_name, caller_method_name=None):
            _key = (
                str(obj_call or "").strip(),
                strip_generics(str(parent_class or "")).strip(),
                os.path.normcase(os.path.abspath(str(file_name))) if file_name else "",
                str(caller_method_name or "").strip(),
            )
            _cached = _resolve_chained_with_classes_cache.get(_key, _resolve_chained_with_classes_miss)
            if _cached is not _resolve_chained_with_classes_miss:
                return _cached
            _out = _resolve_chained_with_classes_impl(
                obj_call,
                parent_class,
                file_name,
                caller_method_name=caller_method_name,
            )
            _resolve_chained_with_classes_cache[_key] = _out
            return _out


        def map_or_resolve(row):
            obj_call = row["object_call"]
            parent_cls = row["class_interface_name"]
            file_name = row["file_name"]
            method_name = row.get("method_name")
            if isinstance(obj_call, str) and "." in obj_call and "(" in obj_call:
                # Fast path: resolve with chain walker only when expression is
                # truly chained (a.b().c()) or has deep field path before call.
                _first_paren = obj_call.find("(")
                _prefix = obj_call[:_first_paren] if _first_paren != -1 else obj_call
                _needs_chain = (
                    _prefix.count(".") > 1
                    or bool(re.search(r'\)\s*\.', obj_call))
                )
                if _needs_chain:
                    return resolve_chained_with_classes(obj_call, parent_cls, file_name, caller_method_name=method_name)
            return map_class_method_call(obj_call, parent_cls, file_name, caller_method_name=method_name)

        # ------------------------------------------------------------------
        # Build type_to_path_full EARLY so derive_chain_segments can use it
        # to resolve method_2's class when method_return_index misses.
        # ------------------------------------------------------------------
        # Reuse pre-built class indexes from the initial app scan.
        # Avoid re-parsing every Java file here; this block runs after 75% and
        # dominates runtime on large codebases.
        _all_project_files_early = list(_all_project_files_indexed)
        type_to_path_full_early = {k: list(v) for k, v in _class_to_paths.items()}
        if _verbose_debug:
            print(f"[DEBUG] _all_project_files_early count: {len(_all_project_files_early)}")
            print(f"[DEBUG] type_to_path_full_early count: {len(type_to_path_full_early)}")
            print(f"[DEBUG] RequestDetails in type_to_path_full_early: {type_to_path_full_early.get('RequestDetails')}")
            print(f"[DEBUG] SearchPeriod in type_to_path_full_early: {type_to_path_full_early.get('SearchPeriod')}")

            print(f"[DEBUG] app_folder = {app_folder}")
            print(f"[DEBUG] The 2 files found:")
            for _fp in _all_project_files_early:
                print(f" {_fp}")
        _import_re_early = re.compile(r'^\s*import\s+(?:static\s+)?([\w.*]+)\s*;', re.MULTILINE)
        _pkg_re_early = re.compile(r'^\s*package\s+([\w.]+)\s*;', re.MULTILINE)

        _fqn_to_paths_early = {k: list(v) for k, v in _fqn_to_paths.items()}
        _file_to_imports_early = {}
        _file_to_wildcards_early = {}
        _file_to_package_early = {}
        _resolve_type_paths_cache = {}
        _resolve_owner_file_cache = {}
        _pbar_goto(79, "Post-processing: indexing caller context...")

        # Caller import/package context is only needed for files present in the
        # current lineage run, not for the entire repository.
        for _fp in java_files:
            _txt = file_content_cache.get(_fp)
            if _txt is None:
                try:
                    _txt = read_file_cached(_fp)
                except Exception:
                    _txt = ""

            _pkg_m = _pkg_re_early.search(_txt or "")
            _pkg = _pkg_m.group(1) if _pkg_m else ""
            _stem = os.path.splitext(os.path.basename(_fp))[0]
            _fqn = "{}.{}".format(_pkg, _stem) if _pkg else _stem
            _fqn_to_paths_early.setdefault(_fqn, [])
            if _fp not in _fqn_to_paths_early[_fqn]:
                _fqn_to_paths_early[_fqn].append(_fp)

            _imp_map = {}
            _wild = []
            for _imp in _import_re_early.findall(_txt or ""):
                _imp = (_imp or "").strip()
                if not _imp:
                    continue
                if _imp.endswith(".*"):
                    _wild.append(_imp[:-2])
                else:
                    _imp_map[_imp.split(".")[-1]] = _imp

            _nfp = os.path.normcase(os.path.abspath(_fp))
            _file_to_imports_early[_nfp] = _imp_map
            _file_to_wildcards_early[_nfp] = _wild
            _file_to_package_early[_nfp] = _pkg

        def _ensure_early_caller_context(file_path):
            if not file_path:
                return
            _nfp = os.path.normcase(os.path.abspath(file_path))
            if _nfp in _file_to_imports_early and _nfp in _file_to_package_early:
                return

            _txt = file_content_cache.get(file_path)
            if _txt is None:
                try:
                    _txt = read_file_cached(file_path)
                except Exception:
                    _txt = ""

            _pkg_m = _pkg_re_early.search(_txt or "")
            _pkg = _pkg_m.group(1) if _pkg_m else ""
            _stem = os.path.splitext(os.path.basename(file_path))[0]
            _fqn = "{}.{}".format(_pkg, _stem) if _pkg else _stem
            _fqn_to_paths_early.setdefault(_fqn, [])
            if file_path not in _fqn_to_paths_early[_fqn]:
                _fqn_to_paths_early[_fqn].append(file_path)

            _imp_map = {}
            _wild = []
            for _imp in _import_re_early.findall(_txt or ""):
                _imp = (_imp or "").strip()
                if not _imp:
                    continue
                if _imp.endswith(".*"):
                    _wild.append(_imp[:-2])
                else:
                    _imp_map[_imp.split(".")[-1]] = _imp

            _file_to_imports_early[_nfp] = _imp_map
            _file_to_wildcards_early[_nfp] = _wild
            _file_to_package_early[_nfp] = _pkg

        def _resolve_type_paths_from_caller(simple_type_name, caller_file):
            if not simple_type_name:
                return []

            s = strip_generics(str(simple_type_name)).strip()
            if "." in s:
                s = s.split(".")[-1]

            if caller_file:
                _ensure_early_caller_context(caller_file)
            caller_norm = os.path.normcase(os.path.abspath(caller_file)) if caller_file else ""
            _tp_key = (s, caller_norm)
            _tp_cached = _resolve_type_paths_cache.get(_tp_key)
            if _tp_cached is not None:
                return list(_tp_cached)
            out = []

            imp_map = _file_to_imports_early.get(caller_norm, {})
            fqn = imp_map.get(s)
            if fqn:
                for _p in _fqn_to_paths_early.get(fqn, []):
                    if _p not in out:
                        out.append(_p)

            caller_pkg = _file_to_package_early.get(caller_norm, "")
            if caller_pkg:
                same_pkg_fqn = "{}.{}".format(caller_pkg, s)
                for _p in _fqn_to_paths_early.get(same_pkg_fqn, []):
                    if _p not in out:
                        out.append(_p)

            for _pkg in _file_to_wildcards_early.get(caller_norm, []):
                wfqn = "{}.{}".format(_pkg, s)
                for _p in _fqn_to_paths_early.get(wfqn, []):
                    if _p not in out:
                        out.append(_p)

            for _p in type_to_path_full_early.get(s, []):
                if _p not in out:
                    out.append(_p)

            _resolve_type_paths_cache[_tp_key] = tuple(out)
            return out

        def _resolve_fqn_path_early(fqn, caller_file, member_name=None):
            if not isinstance(fqn, str):
                return None
            fqn = fqn.strip()
            if not fqn:
                return None

            candidates = list(_fqn_to_paths_early.get(fqn, []))
            if not candidates:
                return None
            if len(candidates) == 1:
                return candidates[0]

            if member_name:
                _declared = [_p for _p in candidates if _file_declares_member_bfs(_p, member_name)]
                if len(_declared) == 1:
                    return _declared[0]
                if _declared:
                    candidates = _declared

            if caller_file:
                try:
                    _caller_dir = os.path.dirname(os.path.abspath(caller_file))

                    def _score(_p):
                        try:
                            _p_dir = os.path.dirname(os.path.abspath(_p))
                            _common = os.path.commonpath([_caller_dir, _p_dir])
                            return (len(_common), -len(str(_p or "")))
                        except Exception:
                            return (0, -len(str(_p or "")))

                    candidates = sorted(candidates, key=_score, reverse=True)
                except Exception:
                    pass
            _best = candidates[0]
            if _best:
                return _best

            # Filesystem fallback for imports not present in early index.
            rel_java = os.path.join(*fqn.split(".")) + ".java"
            caller_abs = os.path.abspath(caller_file) if caller_file else ""
            src_marker = os.path.join("src", "main", "java")

            probe_roots = []
            if caller_abs:
                norm = os.path.normpath(caller_abs)
                marker_pos = norm.lower().find(src_marker.lower())
                if marker_pos != -1:
                    module_root = norm[:marker_pos].rstrip("\\/")
                    if module_root and module_root not in probe_roots:
                        probe_roots.append(module_root)
                    parent_root = os.path.dirname(module_root)
                    if parent_root and os.path.isdir(parent_root):
                        for sib in os.listdir(parent_root):
                            sib_root = os.path.join(parent_root, sib)
                            if os.path.isdir(sib_root) and sib_root not in probe_roots:
                                probe_roots.append(sib_root)

            for root in probe_roots:
                candidate = os.path.join(root, "src", "main", "java", rel_java)
                if os.path.isfile(candidate):
                    if member_name and not _file_declares_member_bfs(candidate, member_name):
                        continue
                    return candidate

            return None

        def _resolve_type_paths_for_chain_return(return_type_name, owner_file, fallback_file=None, member_name=None):
            """Resolve method1 return type for method2 using method1 owner context first."""
            if not return_type_name:
                return []

            rt = strip_generics(str(return_type_name)).strip()
            if not rt:
                return []

            simple = rt.split(".")[-1]
            out = []
            owner_norm = os.path.normcase(os.path.abspath(owner_file)) if owner_file else ""
            if owner_file:
                _ensure_early_caller_context(owner_file)

            def _append_unique(_paths):
                for _p in (_paths or []):
                    if _p and _p not in out:
                        out.append(_p)

            if "." in rt and rt[0].islower():
                _fqn_hit = _resolve_fqn_path_early(rt, owner_file, member_name=member_name)
                if _fqn_hit:
                    _append_unique([_fqn_hit])

            # Import in method1 owner file has strict precedence for method2 owner.
            _imp_map = _file_to_imports_early.get(owner_norm, {})
            _imp_fqn = _imp_map.get(simple)
            if not _imp_fqn:
                _sl = simple.lower()
                for _k, _v in _imp_map.items():
                    if isinstance(_k, str) and _k.lower() == _sl:
                        _imp_fqn = _v
                        break
            if _imp_fqn:
                _imp_hit = _resolve_fqn_path_early(_imp_fqn, owner_file, member_name=member_name)
                if not _imp_hit:
                    _imp_hit = _resolve_fqn_path_early(_imp_fqn, owner_file, member_name=None)
                if not _imp_hit:
                    # Deterministic fallback: match import FQN package-path suffix
                    # among same simple-name files from the project index.
                    _suffix = os.path.normcase(
                        _imp_fqn.replace('.', os.sep) + adapter.file_extension()
                    )
                    _suffix_hits = []
                    for _cand in type_to_path_full_early.get(simple, []) or []:
                        _cand_norm = os.path.normcase(str(_cand or ""))
                        if _cand_norm.endswith(_suffix):
                            _suffix_hits.append(_cand)
                    if _suffix_hits:
                        _imp_hit = _choose_best_candidate_early(
                            _suffix_hits,
                            owner_file,
                            member_name=member_name,
                        ) or _suffix_hits[0]
                if not _imp_hit and owner_file:
                    # Final deterministic hint: synthesize import-based owner path
                    # from method1 owner module root so method2 is not dropped.
                    _rel_java = os.path.join(*_imp_fqn.split(".")) + adapter.file_extension()
                    _owner_abs = os.path.abspath(owner_file)
                    _src_marker = os.path.join("src", "main", "java")
                    _mark_pos = os.path.normpath(_owner_abs).lower().find(_src_marker.lower())
                    if _mark_pos != -1:
                        _module_root = _owner_abs[:_mark_pos].rstrip("\\/")
                        if _module_root:
                            _imp_hit = os.path.join(_module_root, "src", "main", "java", _rel_java)
                if _imp_hit:
                    _append_unique([_imp_hit])
                    return out
                # Explicit import exists but unresolved in this scan: do not drift.
                return []

            _append_unique(_resolve_type_paths_from_caller(simple, owner_file))

            # Fallback to caller context only when type name is not ambiguous.
            if fallback_file and fallback_file != owner_file and not _is_ambiguous_simple_type_name(simple):
                _append_unique(_resolve_type_paths_from_caller(simple, fallback_file))

            if member_name:
                _member_hits = [_p for _p in out if _file_declares_member_bfs(_p, member_name)]
                if _member_hits:
                    return _member_hits
            return out

        def _is_ambiguous_simple_type_name(type_name):
            if not isinstance(type_name, str) or not type_name.strip():
                return keep_unresolved_owner_calls
            _simple = strip_generics(type_name).strip().split('.')[-1]
            return len(type_to_path_full_early.get(_simple, [])) > 1

        def _resolve_owner_file_for_chain(owner_class_name, caller_file, member_name=None):
            """
            Resolve owner class to a concrete file path using caller context.
            Used while traversing chained return types so duplicate simple class
            names are resolved in the owner's package/import scope.
            """
            _of_key = (
                strip_generics(str(owner_class_name or "")).strip(),
                os.path.normcase(os.path.abspath(str(caller_file))) if caller_file else "",
                str(member_name or "").strip(),
            )
            _of_cached = _resolve_owner_file_cache.get(_of_key)
            if _of_cached is not None:
                return _of_cached

            def _choose_owner_candidate(_candidates):
                if not _candidates:
                    return None
                if len(_candidates) == 1:
                    return _candidates[0]
                if not caller_file:
                    return _candidates[0]
                try:
                    _caller_dir = os.path.dirname(os.path.abspath(caller_file))
                except Exception:
                    return _candidates[0]

                def _score(_p):
                    try:
                        _pd = os.path.dirname(os.path.abspath(_p))
                        # Prefer same/nearby package path as caller.
                        _common = os.path.commonpath([_caller_dir, _pd])
                        _common_len = len(_common)
                    except Exception:
                        _common_len = 0
                    return (_common_len, -len(str(_p or "")))

                return sorted(_candidates, key=_score, reverse=True)[0]

            owner = strip_generics(str(owner_class_name or "")).strip()
            if not owner:
                _resolve_owner_file_cache[_of_key] = caller_file
                return caller_file

            owner_candidates = _resolve_type_paths_from_caller(owner, caller_file)
            if owner_candidates:
                chosen = _choose_owner_candidate(owner_candidates)
                _out = chosen or owner_candidates[0]
                _resolve_owner_file_cache[_of_key] = _out
                return _out

            owner_simple = owner.split(".")[-1]
            fallback_candidates = type_to_path_full_early.get(owner_simple, [])
            if fallback_candidates:
                chosen = _choose_owner_candidate(fallback_candidates)
                _out = chosen or fallback_candidates[0]
                _resolve_owner_file_cache[_of_key] = _out
                return _out

            _resolve_owner_file_cache[_of_key] = caller_file
            return caller_file
        # apply(axis=1) is slow for large DataFrames â€” iterate records instead.
        # Use chunked progress updates so elapsed/remaining keeps refreshing
        # while this long step is running.
        _pbar_goto(82, "Post-processing: resolving call ownership...")
        df_clean = df_clean.reset_index(drop=True)
        df_clean["__insert_rank"] = df_clean.index.astype(float)
        _rows_for_resolution = df_clean[["object_call", "class_interface_name", "file_name", "method_name"]].to_dict("records")
        _total_resolution_rows = len(_rows_for_resolution)
        _cmc_values = []
        _resolution_cache = {}
        _cache_hits = 0
        _chunk = 1000
        for _idx, _row in enumerate(_rows_for_resolution, start=1):
            _row["__insert_rank"] = float(_idx - 1)
            _key = (
                str(_row.get("object_call") or ""),
                str(_row.get("class_interface_name") or ""),
                str(_row.get("file_name") or ""),
                str(_row.get("method_name") or ""),
            )
            _cached = _resolution_cache.get(_key)
            if _cached is None:
                _cached = map_or_resolve(_row)
                _resolution_cache[_key] = _cached
            else:
                _cache_hits += 1

            _cmc_values.append(_cached)
            _maybe_refresh_pbar()
            if _total_resolution_rows and (_idx % _chunk == 0 or _idx == _total_resolution_rows):
                _pct = 82 + int((_idx / _total_resolution_rows) * 3)
                _pbar_goto(
                    min(_pct, 85),
                    f"Post-processing: resolving call ownership... ({_idx}/{_total_resolution_rows}) cache_hits={_cache_hits}",
                )
        df_clean["class_method_call"] = _cmc_values
        df_clean["class_method_call"] = df_clean["class_method_call"].astype(str).str.replace(
            r'\s*&lt;[^&gt]+&gt;\s*', '', regex=True
        ).str.replace(r'\s*<[^>]+>\s*', '', regex=True)

        # Entry-scoped strict mode: in method-targeted runs, keep additive
        # expansions anchored to explicit entry methods only.
        _strict_entry_scope = bool(
            details.get("strict_entry_method_scope", bool(_entry_targets_by_file))
        )

        def _is_row_in_entry_scope(_row_obj):
            if not _strict_entry_scope:
                return True
            if not _entry_targets_by_file:
                return True
            if not _row_matches_entry_targets(_row_obj):
                return False
            return bool(_entry_targets_for_path((_row_obj or {}).get("file_name")))

        _rows_for_additive = [
            _r for _r in _rows_for_resolution
            if _is_row_in_entry_scope(_r)
        ]
        _cmc_pairs_for_additive = [
            (_r, _c)
            for _r, _c in zip(_rows_for_resolution, _cmc_values)
            if _is_row_in_entry_scope(_r)
        ]
        _entry_scope_caller_keys = {
            (
                _as_abs_norm(_r.get("file_name")),
                str(_r.get("class_interface_name") or "").strip(),
                str(_r.get("method_name") or "").strip(),
            )
            for _r in _rows_for_additive
        }

        # ------------------------------------------------------------------
        # Requested additive rule:
        # If obj resolves to class1, method is not present in class1 but resolves
        # to parent class2, and class1 has @Inject / CommonValidations /
        # @PostConstruct markers, then cover all methods of class1.
        # ------------------------------------------------------------------
        _marker_cache = {}

        def _split_owner_member_simple(call_str):
            if not isinstance(call_str, str):
                return "", ""
            s = call_str.strip()
            if not s or s.lower() == "none":
                return "", ""
            m = re.match(r'^\s*(.*)\.([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*$', s)
            if not m:
                return "", ""
            return (m.group(1) or "").strip(), (m.group(2) or "").strip()

        def _owner_simple(owner_token):
            o = str(owner_token or "").strip().replace('\\', '/')
            if not o:
                return ""
            tail = o.rsplit('/', 1)[-1]
            if "." in tail:
                tail = tail.rsplit('.', 1)[-1]
            return strip_generics(tail)

        def _class_has_requested_markers(class_name, owner_class, caller_file):
            k = (str(class_name or ""), str(owner_class or ""), str(caller_file or ""))
            cached = _marker_cache.get(k)
            if cached is not None:
                return cached

            c = strip_generics(str(class_name or "").split('.')[-1]).strip()
            owner = strip_generics(str(owner_class or "").split('.')[-1]).strip()
            if not c:
                _marker_cache[k] = False
                return False

            # Resolve class source file using only helpers that are already
            # defined at this stage of clean_and_write.
            p = None
            try:
                _imports = {}
                _wildcards = []
                _pkg = None
                if caller_file and os.path.isfile(caller_file):
                    _caller_src = file_content_cache.get(caller_file) or read_file_cached(caller_file)
                    _imports, _wildcards, _pkg = _parse_import_context(_caller_src)

                p = _resolve_dep_path(
                    c,
                    _imports,
                    _caller_file=caller_file,
                    _caller_pkg=_pkg,
                    _caller_wildcards=_wildcards,
                    _member_name=None,
                )
            except Exception:
                p = None

            if not p:
                _cands = list(_class_to_paths.get(c, []))
                p = _choose_dep_candidate(_cands, caller_file, None, None)

            if not p or not os.path.isfile(p):
                _marker_cache[k] = False
                return False
            try:
                txt = file_content_cache.get(p) or read_file_cached(p)
            except Exception:
                txt = ""
            if not txt:
                _marker_cache[k] = False
                return False

            has_markers = bool(re.search(r'@PostConstruct\b', txt))
            if not has_markers:
                _marker_cache[k] = False
                return False

            # If class directly/indirectly extends the resolved owner class,
            # marker presence in the child class is sufficient for expansion.
            def _is_descendant_of_owner(_child, _owner):
                _child = strip_generics(str(_child or "").split('.')[-1]).strip()
                _owner = strip_generics(str(_owner or "").split('.')[-1]).strip()
                if not _child or not _owner or _child == _owner:
                    return False
                _seen = set()
                _cur = _child
                while _cur and _cur not in _seen:
                    _seen.add(_cur)
                    _par = _class_extends.get(_cur)
                    if not _par:
                        return False
                    _par_simple = strip_generics(str(_par).split('.')[-1]).strip()
                    if _par_simple == _owner:
                        return True
                    _cur = _par_simple
                return False

            if _is_descendant_of_owner(c, owner):
                _marker_cache[k] = True
                return True

            # Require that ClassA markers are tied to the resolved owner class
            # before expanding to all ClassA methods.
            if not owner:
                _marker_cache[k] = has_markers
                return has_markers

            try:
                _class1_ast = _bfs_parse(p)
            except Exception:
                _class1_ast = None

            owner_linked = False

            # ClassA has an injected field whose contract maps to owner class.
            if _class1_ast:
                _inj_fields = _collect_injected_qualified_fields_from_ast(_class1_ast)
                for _meta in (_inj_fields or {}).values():
                    if not isinstance(_meta, dict):
                        continue

                    _contracts = {
                        str(_c).strip().split('.')[-1]
                        for _c in (_meta.get("contract_types", set()) or set())
                        if str(_c or "").strip()
                    }
                    if owner in _contracts:
                        owner_linked = True
                        break

                    for _contract in _contracts:
                        _impls = set(_interface_to_impls.get(_contract, set()))
                        _impls.update(_interface_to_impls.get(_contract + "s", set()))
                        if _contract.endswith("s"):
                            _impls.update(_interface_to_impls.get(_contract[:-1], set()))
                        if owner in _impls:
                            owner_linked = True
                            break
                    if owner_linked:
                        break

                    _ann = set(_meta.get("annotations", set()) or set())
                    for _ann_name in _ann:
                        for _ann_key in _annotation_lookup_names(_ann_name):
                            for _ann_path in _annotation_to_paths.get(_ann_key, set()):
                                _ann_cls = os.path.splitext(os.path.basename(_ann_path))[0]
                                if _ann_cls == owner:
                                    owner_linked = True
                                    break
                            if owner_linked:
                                break
                        if owner_linked:
                            break
                    if owner_linked:
                        break

            # Fallback textual hint for CommonValidation-style usage.
            if not owner_linked:
                if re.search(r'\bCommonValidation(?:s)?\b', txt) and re.search(r'\b' + re.escape(owner) + r'\b', txt):
                    owner_linked = True

            _marker_cache[k] = owner_linked
            return owner_linked

        _extra_rows = []
        _impl_rows_seen = set()
        # For entry-targeted runs, keep owner-view rows enabled by default so
        # synthetic delegate expansions remain visible under the delegate file.
        _emit_owner_view_rows = bool(
            details.get("emit_owner_view_rows", bool(_entry_targets_by_file))
        )

        # Strict rule: methods with no direct call must not produce synthetic children.
        _direct_call_count_by_caller = {}
        for _r in _rows_for_additive:
            _k = (
                _as_abs_norm(_r.get("file_name")),
                str(_r.get("class_interface_name") or "").strip(),
                str(_r.get("method_name") or "").strip(),
            )
            _oc = str(_r.get("object_call") or "").strip()
            if _oc and _oc.lower() != "none":
                _direct_call_count_by_caller[_k] = _direct_call_count_by_caller.get(_k, 0) + 1

        # Recursive continuation can explode scope if left unconstrained.
        # Keep it enabled by default, but in strict entry mode limit default
        # depth to direct child-only expansion so parent method scopes stay clean.
        _allow_child_continuation = bool(
            details.get("allow_child_continuation", True)
        )
        _default_continuation_depth = 0 if _strict_entry_scope else 8

        def _append_dual_rows(_seed_row, _cmc, _owner_class, _owner_method, _caller_file, _owner_file, _offset=0.001):
            """Append caller-view and owner-view rows for the same resolved call."""
            _m = str(_owner_method or "").strip()
            if not _m:
                return
            _base_rank = float(_seed_row.get("__insert_rank", 0.0))
            _rank = _base_rank + float(_offset)

            # Caller perspective row (keeps originating file context).
            _caller = dict(_seed_row)
            _caller["file_name"] = _caller_file or _caller.get("file_name")
            # Preserve caller class/method context so entry-method debug/output
            # still includes synthetic inherited rows under the caller method.
            _caller["object_call"] = _cmc
            _caller["class_method_call"] = _cmc
            _caller["__source_file_name"] = _caller_file or _caller.get("file_name")
            _caller["__synthetic_origin"] = "additive_caller_view"
            _caller["__insert_rank"] = _rank
            _extra_rows.append(_caller)

            if not _emit_owner_view_rows:
                return

            # Owner perspective row (points to owning class source file).
            _owner = dict(_seed_row)
            if _owner_file:
                _owner["file_name"] = _owner_file
            # Owner-view rows must carry owner class/method context so
            # entry filtering and downstream expansion can resolve the
            # actual target method body (e.g. delegate init()).
            _owner["class_interface_name"] = _owner_class
            _owner["method_name"] = _m
            _owner["object_call"] = _cmc
            _owner["class_method_call"] = _cmc
            _owner["__source_file_name"] = _caller_file or _seed_row.get("file_name")
            _owner["__synthetic_origin"] = "additive_owner_view"
            _owner["__insert_rank"] = _rank + 0.0001
            _extra_rows.append(_owner)

        def _append_injected_impl_method_rows(_seed_row, _class1_name, _class1_src_path, _obj_field_name):
            if not _class1_src_path or not os.path.isfile(_class1_src_path):
                return
            try:
                _class1_ast = _bfs_parse(_class1_src_path)
            except Exception:
                _class1_ast = None
            if not _class1_ast:
                return

            _inj_fields = _collect_injected_qualified_fields_from_ast(_class1_ast)
            _meta = (_inj_fields or {}).get(_obj_field_name)
            if not _meta:
                return
            for _meta in [_meta]:
                if isinstance(_meta, dict):
                    _inj_ann = set(_meta.get("annotations", set()) or set())
                    _inj_contracts = {
                        str(_c).strip().split('.')[-1]
                        for _c in (_meta.get("contract_types", set()) or set())
                        if str(_c or "").strip()
                    }
                else:
                    _inj_ann = set(_meta or set())
                    _inj_contracts = set()

                _allowed_impl_classes = set()
                for _contract in _inj_contracts:
                    _allowed_impl_classes.update(_interface_to_impls.get(_contract, set()))
                    _allowed_impl_classes.update(_interface_to_impls.get(_contract + "s", set()))
                    if _contract.endswith("s"):
                        _allowed_impl_classes.update(_interface_to_impls.get(_contract[:-1], set()))

                for _ann in sorted(_inj_ann):
                    for _ann_key in _annotation_lookup_names(_ann):
                        for _ann_path in sorted(_annotation_to_paths.get(_ann_key, set())):
                            _ann_cls = os.path.splitext(os.path.basename(_ann_path))[0]
                            if _inj_contracts:
                                if _ann_cls not in _allowed_impl_classes and _ann_cls not in _inj_contracts:
                                    continue
                            for _impl_m in sorted(_class_methods.get(_ann_cls, set())):
                                if not isinstance(_impl_m, str) or not _impl_m.strip():
                                    continue
                                _k = (
                                    os.path.normcase(os.path.abspath(_class1_src_path)),
                                    _class1_name,
                                    _ann_cls,
                                    _impl_m.strip(),
                                )
                                if _k in _impl_rows_seen:
                                    continue
                                _impl_rows_seen.add(_k)
                                _extra_impl = dict(_seed_row)
                                _extra_impl["file_name"] = _ann_path
                                _extra_impl["class_interface_name"] = _ann_cls
                                _extra_impl["method_name"] = _impl_m.strip()
                                _extra_impl["__source_file_name"] = _class1_src_path
                                _extra_impl["object_call"] = "{}.{}()".format(_ann_cls, _impl_m.strip())
                                _extra_impl["class_method_call"] = "{}.{}()".format(_ann_cls, _impl_m.strip())
                                _extra_impl["__synthetic_origin"] = "additive_impl_expand"
                                _extra_rows.append(_extra_impl)

        def _split_owner_member_call(_call_text):
            if not isinstance(_call_text, str):
                return "", ""
            _s = _call_text.strip()
            if not _s or _s.lower() == "none":
                return "", ""
            _m = re.match(r'^\s*(.*)\.([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*$', _s)
            if not _m:
                return "", ""
            return (str(_m.group(1) or "").strip(), str(_m.group(2) or "").strip())

        def _expand_child_continuation(_seed_row, _seed_cmc, _seed_owner_file=None):
            """Recursively expand child calls from precomputed chain index."""
            if not _allow_child_continuation:
                return

            _caller_file = str((_seed_row or {}).get("file_name") or "").strip()
            _caller_method = str((_seed_row or {}).get("method_name") or "").strip()
            _caller_class = str((_seed_row or {}).get("class_interface_name") or "").strip()

            _owner_tok, _member = _split_owner_member_call(_seed_cmc)
            _owner_cls = _owner_simple(_owner_tok)
            if not _owner_cls or not _member:
                return

            _entry_owner_file = str(_seed_owner_file or "").strip()
            if not _entry_owner_file:
                _entry_owner_file = _resolve_owner_file_for_chain(
                    _owner_cls,
                    _caller_file,
                    member_name=_member,
                )
                if not _entry_owner_file:
                    _entry_owner_file = _choose_dep_candidate(
                        list(_class_to_paths.get(_owner_cls, [])),
                        _caller_file,
                        None,
                        _member,
                    )

            _stack = [(_owner_cls, _member, 0, _entry_owner_file or _caller_file)]
            _visited_nodes = set()
            _added_rows = set()
            _max_depth = int(
                details.get("synthetic_continuation_depth", _default_continuation_depth)
                or _default_continuation_depth
            )
            _children_fallback_cache = {}

            def _extract_children_from_method_source(_owner_cls_name, _owner_method_name, _owner_file_path):
                """Fallback child-call extraction from method source when chain index misses."""
                _ck = (
                    str(_owner_cls_name or "").strip(),
                    str(_owner_method_name or "").strip(),
                    os.path.normcase(os.path.abspath(str(_owner_file_path))) if _owner_file_path else "",
                )
                _cached = _children_fallback_cache.get(_ck)
                if _cached is not None:
                    return _cached

                out = set()
                if not _owner_file_path or not os.path.isfile(_owner_file_path):
                    _children_fallback_cache[_ck] = out
                    return out

                try:
                    _src = file_content_cache.get(_owner_file_path) or read_file_cached(_owner_file_path)
                except Exception:
                    _src = ""
                if not _src:
                    _children_fallback_cache[_ck] = out
                    return out

                _mblock = _extract_method_block(_src, _owner_method_name)
                if not _mblock:
                    _children_fallback_cache[_ck] = out
                    return out

                _txt = _strip_comments_and_literals(_mblock)
                if not _txt:
                    _children_fallback_cache[_ck] = out
                    return out

                _qual_pat = re.compile(r'([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\.\s*([A-Za-z_]\w*)\s*\(')
                _unqual_pat = re.compile(r'\b([A-Za-z_]\w*)\s*\(')
                _skip = set(keyword_set) | {
                    "if", "for", "while", "switch", "catch", "new", "return", "throw", "super", "this", "try", "do"
                }

                for mm in _qual_pat.finditer(_txt):
                    _q = str(mm.group(1) or "").strip()
                    _m = str(mm.group(2) or "").strip()
                    if not _q or not _m:
                        continue
                    out.add("{}.{}()".format(_q, _m))

                for mm in _unqual_pat.finditer(_txt):
                    _m = str(mm.group(1) or "").strip()
                    if not _m or _m in _skip or _m == _owner_method_name:
                        continue
                    out.add("{}.{}()".format(_owner_cls_name, _m))

                _children_fallback_cache[_ck] = out
                return out

            while _stack:
                _cur_owner, _cur_member, _depth, _cur_owner_file = _stack.pop()
                if _depth > _max_depth:
                    continue
                _node = (_cur_owner, _cur_member)
                if _node in _visited_nodes:
                    continue
                _visited_nodes.add(_node)

                _children = _chain_children_index.get((_cur_owner, _cur_member), set())
                if not _children:
                    _children = _extract_children_from_method_source(
                        _cur_owner,
                        _cur_member,
                        _cur_owner_file,
                    )
                if not _children:
                    continue

                for _child_call in sorted(_children):
                    if not isinstance(_child_call, str) or not _child_call.strip():
                        continue

                    _mapped_child = map_class_method_call(
                        _child_call,
                        _cur_owner,
                        _cur_owner_file or _caller_file,
                        caller_method_name=_cur_member,
                    )
                    _ch_owner_tok, _ch_member = _split_owner_member_call(_mapped_child)
                    _ch_owner_cls = _owner_simple(_ch_owner_tok)
                    if not _ch_owner_cls or not _ch_member:
                        continue

                    # Keep continuation rows scoped to the currently expanded
                    # owner method to avoid cross-method leakage back into the
                    # original seed caller (e.g. sendToUpf <- afterCompletion children).
                    _continuation_seed = dict(_seed_row or {})
                    _continuation_seed["class_interface_name"] = _cur_owner
                    _continuation_seed["method_name"] = _cur_member
                    if _cur_owner_file:
                        _continuation_seed["file_name"] = _cur_owner_file

                    _row_key = (
                        _as_abs_norm(_cur_owner_file or _caller_file),
                        _cur_owner,
                        _cur_member,
                        _mapped_child,
                    )
                    if _row_key in _added_rows:
                        continue
                    _added_rows.add(_row_key)

                    _owner_file = _resolve_owner_file_for_chain(
                        _ch_owner_cls,
                        _cur_owner_file or _caller_file,
                        member_name=_ch_member,
                    )
                    if not _owner_file:
                        _owner_file = _choose_dep_candidate(
                            list(_class_to_paths.get(_ch_owner_cls, [])),
                            _cur_owner_file or _caller_file,
                            None,
                            _ch_member,
                        )

                    _append_dual_rows(
                        _continuation_seed,
                        _mapped_child,
                        _ch_owner_cls,
                        _ch_member,
                        _cur_owner_file or _caller_file,
                        _owner_file,
                        _offset=0.500 + (_depth * 0.01),
                    )

                    _stack.append((_ch_owner_cls, _ch_member, _depth + 1, _owner_file or _cur_owner_file or _caller_file))

        for _row, _cmc in _cmc_pairs_for_additive:
            _obj_call = str(_row.get("object_call") or "").strip()
            _parent_class = str(_row.get("class_interface_name") or "").strip()
            _caller_file = str(_row.get("file_name") or "").strip()
            _caller_method = str(_row.get("method_name") or "").strip()

            _caller_key = (
                _as_abs_norm(_caller_file),
                _parent_class,
                _caller_method,
            )
            if _direct_call_count_by_caller.get(_caller_key, 0) <= 0:
                continue

            if not _obj_call or _obj_call.lower() == "none":
                continue

            _m_obj = re.match(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\(', _obj_call)
            if not _m_obj:
                continue
            _obj_root = _m_obj.group(1)
            _called_member = _m_obj.group(2)

            _class1 = _obj_root if _obj_root[:1].isupper() else _lookup_type(
                _obj_root,
                _parent_class,
                _caller_file,
                caller_method_name=_caller_method,
            )
            _class1 = strip_generics(str(_class1 or "").split('.')[-1]).strip()
            if not _class1:
                continue
            # Rule is declaration-based for class1: inherited methods should
            # still trigger class1 expansion when not declared in class1.
            _in_child_declared = _class_declares_method(_class1, _called_member)
            if _in_child_declared:
                continue

            # Resolve class2 directly from class1 via inheritance/interface maps.
            _class2 = _resolve_class_for_method(
                _class1,
                _called_member,
                _visited=None,
                prefer_concrete=False,
                fallback_class_name=strip_generics(_parent_class),
            )
            _class2 = strip_generics(str(_class2 or "").split('.')[-1]).strip()
            if not _class2 or _class2 == _class1:
                continue

            _in_owner = _method_exists_in_class(
                _class2,
                _called_member,
                caller_file=_caller_file,
                fallback_class_name=strip_generics(_parent_class),
            )
            if not _in_owner:
                continue
            if not _class_has_requested_markers(_class1, _class2, _caller_file):
                continue

            # Resolve paths with caller import context first to avoid
            # same-simple-name collisions across modules.
            _class1_path = _resolve_owner_file_for_chain(
                _class1,
                _caller_file,
                member_name=None,
            )
            if not _class1_path:
                _class1_path = _choose_dep_candidate(list(_class_to_paths.get(_class1, [])), _caller_file, None, None)

            _caller_norm = _as_abs_norm(_caller_file)
            _class1_norm = _as_abs_norm(_class1_path)
            _is_same_class1_file_caller = bool(_caller_norm and _class1_norm and _caller_norm == _class1_norm)

            _class2_path = _resolve_owner_file_for_chain(
                _class2,
                _caller_file,
                member_name=_called_member,
            )
            if not _class2_path:
                _class2_path = _choose_dep_candidate(list(_class_to_paths.get(_class2, [])), _caller_file, None, _called_member)

            # Ensure the inherited-owner call itself is present in both caller and owner views.
            _owner_cmc = "{}.{}()".format(_class2, _called_member)
            _append_dual_rows(
                _row,
                _owner_cmc,
                _class2,
                _called_member,
                _caller_file,
                _class2_path,
                _offset=0.001,
            )

            # Avoid self-file cross-method pollution such as
            # RequestorValidatorDelegate.getValidators -> RequestorValidatorDelegate.init.
            # Expansion is intended for caller-to-callee propagation across classes.
            if _is_same_class1_file_caller:
                if _verbose_debug:
                    _entry_debug_print(
                        "[DEBUG][ENTRY_EXPAND_CLASS1][SKIP_SELF_FILE] caller_file={} class1={} owner={} called_member={}".format(
                            _caller_file,
                            _class1,
                            _class2,
                            _called_member,
                        )
                    )
                continue

            _class1_declared_methods = set(_class_methods.get(_class1, set()) or set())
            if (not _class1_declared_methods) and _class1_path and os.path.isfile(_class1_path):
                try:
                    _class1_src_text = file_content_cache.get(_class1_path) or read_file_cached(_class1_path)
                except Exception:
                    _class1_src_text = ""
                if _class1_src_text:
                    try:
                        _class1_declared_methods = set(
                            _extract_declared_methods_for_stem(_class1_src_text, _class1) or set()
                        )
                    except Exception:
                        _class1_declared_methods = set()

            if _verbose_debug:
                _entry_debug_print(
                    "[DEBUG][ENTRY_EXPAND_CLASS1] caller_file={} class1={} owner={} called_member={} class1_methods_count={}".format(
                        _caller_file,
                        _class1,
                        _class2,
                        _called_member,
                        len(_class1_declared_methods),
                    )
                )

            _method_insert_offset = 0.002
            for _mname in sorted(_class1_declared_methods):
                if not isinstance(_mname, str) or not _mname.strip():
                    continue
                _m = _mname.strip()
                _cmc = "{}.{}()".format(_class1, _m)
                _append_dual_rows(
                    _row,
                    _cmc,
                    _class1,
                    _m,
                    _caller_file,
                    _class1_path,
                    _offset=_method_insert_offset,
                )
                _method_insert_offset += 0.001

            _append_injected_impl_method_rows(_row, _class1, _class1_path, _obj_root)

            # Continue child-call lineage for current resolved call.
            _expand_child_continuation(_row, _cmc)

        # ------------------------------------------------------------------
        # Additive constructor rule: same-file class expansion
        # ------------------------------------------------------------------
        # Scenario:
        #   ABC.sendToUpf -> new HTTPSender(...)
        #   class HTTPSender { ... }
        # If constructor target class is declared in the same Java file,
        # emit ABC.sendToUpf -> HTTPSender.method() for declared methods.
        # If no same-file class declaration exists, keep existing behavior.
        _same_file_ctor_seen = set()
        _same_file_class_decl_cache = {}

        def _same_file_declares_class(_file_path, _class_name):
            _key = (_as_abs_norm(_file_path), str(_class_name or "").strip())
            _cached = _same_file_class_decl_cache.get(_key)
            if _cached is not None:
                return _cached
            if not _file_path or not os.path.isfile(_file_path) or not _class_name:
                _same_file_class_decl_cache[_key] = False
                return False
            try:
                _src = file_content_cache.get(_file_path) or read_file_cached(_file_path)
            except Exception:
                _src = ""
            if not _src:
                _same_file_class_decl_cache[_key] = False
                return False
            _pat = re.compile(r'\bclass\s+' + re.escape(str(_class_name).strip()) + r'\b')
            _has = bool(_pat.search(_src))
            _same_file_class_decl_cache[_key] = _has
            return _has

        def _extract_ctor_target_class(_obj_call_text, _cmc_text):
            _obj = str(_obj_call_text or "").strip()
            _cmc_s = str(_cmc_text or "").strip()

            _m_new = re.search(r'\bnew\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(', _obj)
            if _m_new:
                return strip_generics(str(_m_new.group(1) or "").split('.')[-1]).strip()

            # Fallback when object_call is normalized: ClassX.ClassX()
            _m_cmc = re.match(r'^\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*$', _cmc_s)
            if _m_cmc and _m_cmc.group(1) == _m_cmc.group(2):
                return strip_generics(str(_m_cmc.group(1) or "")).strip()
            return ""

        _ctor_expand_debug_count = 0
        for _row, _cmc in _cmc_pairs_for_additive:
            _caller_file = str(_row.get("file_name") or "").strip()
            _caller_method = str(_row.get("method_name") or "").strip()
            _parent_class = str(_row.get("class_interface_name") or "").strip()
            _obj_call = str(_row.get("object_call") or "").strip()
            if not _caller_file or not _caller_method:
                continue

            _ctor_cls = _extract_ctor_target_class(_obj_call, _cmc)
            if not _ctor_cls:
                continue
            if not _same_file_declares_class(_caller_file, _ctor_cls):
                continue

            try:
                _caller_src = file_content_cache.get(_caller_file) or read_file_cached(_caller_file)
            except Exception:
                _caller_src = ""
            if not _caller_src:
                continue

            _declared_methods = set(_extract_declared_methods_for_stem(_caller_src, _ctor_cls) or set())
            if not _declared_methods:
                continue

            _method_offset = 0.240
            _emitted = 0
            for _mname in sorted(_declared_methods):
                if not isinstance(_mname, str) or not _mname.strip():
                    continue
                _m = _mname.strip()
                # Skip constructor self-symbol; expand callable methods.
                if _m == _ctor_cls:
                    continue

                _dupe_key = (
                    _as_abs_norm(_caller_file),
                    _parent_class,
                    _caller_method,
                    _ctor_cls,
                    _m,
                    "same_file_ctor",
                )
                if _dupe_key in _same_file_ctor_seen:
                    continue
                _same_file_ctor_seen.add(_dupe_key)

                _cmc_emit = "{}.{}()".format(_ctor_cls, _m)
                _append_dual_rows(
                    _row,
                    _cmc_emit,
                    _ctor_cls,
                    _m,
                    _caller_file,
                    _caller_file,
                    _offset=_method_offset,
                )
                _method_offset += 0.001
                _emitted += 1
                _expand_child_continuation(_row, _cmc_emit, _caller_file)

            if _verbose_debug and _emitted > 0:
                _ctor_expand_debug_count += 1
                _entry_debug_print(
                    "[DEBUG][CTOR_SAME_FILE_EXPAND] caller_file={} class={} method={} ctor_target={} emitted_rows={}".format(
                        _caller_file,
                        _parent_class,
                        _caller_method,
                        _ctor_cls,
                        _emitted,
                    )
                )

        # ------------------------------------------------------------------
        # New additive case: Arrays.asList(...).iterator() field expansion
        # ------------------------------------------------------------------
        # Scenario:
        #   sortedPaymentOrderValidators = new SortedValidatorList(Arrays.asList(
        #       validator_vp_validatePayment,
        #       validatorROO_ipo_327,
        #       ...
        #   ).iterator());
        #
        # If a field variable listed in Arrays.asList resolves to a project class,
        # emit synthetic Class.method() rows so lineage includes those validator
        # classes even when the methods are invoked later via iterator traversal.
        _arrays_list_case_seen = set()

        def _extract_balanced_parenthesized(_txt, _open_idx):
            if not isinstance(_txt, str) or _open_idx < 0 or _open_idx >= len(_txt) or _txt[_open_idx] != '(':
                return ""
            _depth = 0
            _end = None
            for _i in range(_open_idx, len(_txt)):
                _ch = _txt[_i]
                if _ch == '(':
                    _depth += 1
                elif _ch == ')':
                    _depth -= 1
                    if _depth == 0:
                        _end = _i
                        break
            if _end is None:
                return ""
            return _txt[_open_idx:_end + 1]

        def _extract_arrays_aslist_arguments(_method_src):
            _args = []
            if not isinstance(_method_src, str) or not _method_src.strip():
                return _args

            # Support both patterns:
            # 1) Arrays.asList(a, b).iterator()
            # 2) new SortedValidatorList<>(Arrays.asList(a.iterator(), b.iterator()))
            if not re.search(r'Arrays\s*\.\s*asList\s*\(', _method_src, re.MULTILINE):
                return _args

            _aslist_open_pat = re.compile(r'Arrays\s*\.\s*asList\s*\(', re.MULTILINE)
            for _m_as in _aslist_open_pat.finditer(_method_src):
                _open_idx = _m_as.end() - 1
                _paren_block = _extract_balanced_parenthesized(_method_src, _open_idx)
                if not _paren_block:
                    continue
                _inside = _paren_block[1:-1]
                for _tok in _split_top_level_commas(_inside):
                    _tok_s = str(_tok or "").strip()
                    if not _tok_s:
                        continue
                    # Keep only simple variable-like tokens from asList entries.
                    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', _tok_s):
                        _args.append(_tok_s)
                        continue
                    # New: support entries like var.iterator() inside asList.
                    _m_it = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*iterator\s*\(\s*\)', _tok_s)
                    if _m_it:
                        _args.append(_m_it.group(1))
                        continue
                    _m_this_it = re.fullmatch(r'this\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*iterator\s*\(\s*\)', _tok_s)
                    if _m_this_it:
                        _args.append(_m_this_it.group(1))
            return _args

        def _extract_iterator_owner_vars(_method_src):
            """Extract owner vars from direct iterator calls (var.iterator()).

            Supports init wiring patterns like:
              new SortedEnricherList<>(paymentOrderEnrichers.iterator())
              this.requestorValidators.iterator()
            """
            _out = []
            _txt = str(_method_src or "")
            if not _txt.strip():
                return _out

            _it_pat = re.compile(
                r'(?:\bthis\s*\.\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*iterator\s*\(\s*\)',
                re.MULTILINE,
            )
            for _m in _it_pat.finditer(_txt):
                _vn = str(_m.group(1) or "").strip()
                if _vn:
                    _out.append(_vn)
            return _out

        def _extract_first_generic_sig(_sig_text):
            _s = _normalize_contract_signature(_sig_text)
            if not _s or '<' not in _s or '>' not in _s:
                return ""
            _lt = _s.find('<')
            _gt = _s.rfind('>')
            if _lt < 0 or _gt <= _lt:
                return ""
            _inside = _s[_lt + 1:_gt]
            _parts = _split_top_level_commas(_inside)
            if not _parts:
                return ""
            return _normalize_contract_signature(_parts[0])

        def _build_injected_var_name_set_local(_src_text):
            out = set()
            txt = str(_src_text or "")
            if not txt:
                return out
            _inj_decl_pat = re.compile(
                r'@Inject\b\s*'
                r'(?:(?:public|private|protected|static|final|transient|volatile)\s+)*'
                r'[A-Za-z_][\w$.<>,\[\]\?\s]*\s+'
                r'([a-z][A-Za-z0-9_]*)\s*(?:=|;|,|\))',
                re.MULTILINE,
            )
            for mm in _inj_decl_pat.finditer(txt):
                _vn = str(mm.group(1) or "").strip()
                if _vn:
                    out.add(_vn)
            return out

        def _extract_field_decl_type_for_var(_src_text, _var_name):
            """Best-effort field type extraction for a specific variable name."""
            _txt = str(_src_text or "")
            _vn = str(_var_name or "").strip()
            if not _txt or not _vn:
                return ""
            _pat = re.compile(
                r'(?:@\w+(?:\([^)]*\))?\s*)*'
                r'(?:(?:public|private|protected|static|final|transient|volatile)\s+)*'
                r'([A-Za-z_][\w$.]*(?:\s*<[^;=\n]+>)?)\s+'
                + re.escape(_vn)
                + r'\s*(?:=|;)',
                re.MULTILINE,
            )
            _m = _pat.search(_txt)
            if not _m:
                return ""
            return _normalize_contract_signature(_m.group(1) or "")

        def _extract_field_annotations_for_var(_src_text, _var_name):
            """Return annotation simple names attached to the field declaration."""
            _txt = str(_src_text or "")
            _vn = str(_var_name or "").strip()
            if not _txt or not _vn:
                return set()

            _pat = re.compile(
                r'((?:@\w+(?:\([^)]*\))?\s*)*)'
                r'(?:(?:public|private|protected|static|final|transient|volatile)\s+)*'
                r'[A-Za-z_][\w$.]*(?:\s*<[^;=\n]+>)?\s+'
                + re.escape(_vn)
                + r'\s*(?:=|;)',
                re.MULTILINE,
            )
            _m = _pat.search(_txt)
            if not _m:
                return set()

            _ann_block = str(_m.group(1) or "")
            _out = set()
            for _raw in re.findall(r'@([A-Za-z_][\w.]*)', _ann_block):
                _nm = str(_raw or "").strip().split('.')[-1]
                if _nm:
                    _out.add(_nm)
            return _out

        def _find_impl_classes_for_declared_sig(_sig):
            _s = _normalize_contract_signature(_sig)
            if not _s:
                return []
            _base = _contract_signature_base(_s)
            if not _base:
                return []
            _cands = set(_interface_to_impls.get(_base, set()) or set())
            _matched = []
            for _cls in sorted(_cands):
                if _class_matches_contract_signatures(_cls, {_s}):
                    _matched.append(_cls)
            return _matched

        def _extract_signature_type_args(_sig):
            """Return simple generic argument tokens from a contract signature.

            Example:
              Validator<Requestor> -> {"Requestor"}
              Mapper<Key, Value>   -> {"Key", "Value"}
            """
            _s = _normalize_contract_signature(_sig)
            if not _s or '<' not in _s or '>' not in _s:
                return set()
            _lt = _s.find('<')
            _gt = _s.rfind('>')
            if _lt < 0 or _gt <= _lt:
                return set()
            _inside = _s[_lt + 1:_gt]
            _out = set()
            for _tok in _split_top_level_commas(_inside):
                _t = _normalize_contract_signature(_tok)
                if not _t:
                    continue
                _base = _contract_signature_base(_t)
                if _base:
                    _out.add(_base)
            return _out

        def _class_decl_uses_type_args(_cls_name, _type_args):
            """Best-effort check that class declaration references requested type args.

            This enables a bounded fallback when interface->impl indexing cannot infer
            inherited generic contracts (e.g. class extends AbstractValidator<Requestor>).
            """
            _cls = str(_cls_name or "").strip().split('.')[-1]
            _args = {str(a or "").strip().split('.')[-1] for a in (_type_args or set()) if str(a or "").strip()}
            if not _cls:
                return False
            if not _args:
                return True

            _paths = list(_class_to_paths.get(_cls, []) or [])
            for _p in _paths:
                if not _p or not os.path.isfile(_p):
                    continue
                try:
                    _txt = file_content_cache.get(_p) or read_file_cached(_p)
                except Exception:
                    _txt = ""
                if not _txt:
                    continue

                _decl_match = re.search(
                    r'\bclass\s+' + re.escape(_cls) + r'\b[^\{]*\{',
                    _txt,
                    re.MULTILINE,
                )
                _decl_txt = _decl_match.group(0) if _decl_match else _txt
                for _arg in _args:
                    if re.search(r'\b' + re.escape(_arg) + r'\b', _decl_txt):
                        return True
            return False

        def _class_has_any_annotation(_cls_name, _ann_names):
            """Best-effort class annotation presence check by indexed files."""
            _cls = str(_cls_name or "").strip().split('.')[-1]
            if not _cls:
                return False
            _want = {str(a or "").strip() for a in (_ann_names or set()) if str(a or "").strip()}
            if not _want:
                return False
            _paths = list(_class_to_paths.get(_cls, []) or [])
            for _p in _paths:
                if not _p or not os.path.isfile(_p):
                    continue
                try:
                    _txt = file_content_cache.get(_p) or read_file_cached(_p)
                except Exception:
                    _txt = ""
                if not _txt:
                    continue
                for _ann in _want:
                    if re.search(r'@(?:[A-Za-z_][\w]*\.)?' + re.escape(_ann) + r'\b', _txt):
                        return True
            return False

        def _class_likely_contract_implementor(_cls_name, _contract_base):
            """Heuristic contract check for qualifier-based fallback.

            Used only when strict generic contract matching returns no classes.
            """
            _cls = str(_cls_name or "").strip().split('.')[-1]
            _base = str(_contract_base or "").strip().split('.')[-1]
            if not _cls:
                return False

            _paths = list(_class_to_paths.get(_cls, []) or [])
            for _p in _paths:
                if not _p or not os.path.isfile(_p):
                    continue
                try:
                    _txt = file_content_cache.get(_p) or read_file_cached(_p)
                except Exception:
                    _txt = ""
                if not _txt:
                    continue

                # Favor explicit inheritance/implements hints first.
                if _base:
                    if re.search(r'\bimplements\b[^\{;]*\b' + re.escape(_base) + r'\b', _txt):
                        return True
                    if re.search(r'\bextends\b[^\{;]*\b' + re.escape(_base) + r'\b', _txt):
                        return True

                # Validator-style classes frequently extend abstract validators.
                if _base == "Validator" and re.search(r'\b(?:implements|extends)\b[^\{;]*\b(?:Validator|AbstractValidator)\b', _txt):
                    return True

                # Last-resort narrow heuristic.
                if _base == "Validator" and re.search(r'\bclass\s+' + re.escape(_cls) + r'\b', _txt):
                    if "Validator" in _cls:
                        return True

            return False

        def _find_contract_candidates_by_shape(_decl_sig):
            """Fallback candidate discovery when direct interface indexes are sparse.

            Example:
              Validator<Requestor> -> subclasses of AbstractValidator/Validator
              constrained by Requestor type-arg usage when available.
            """
            _sig = _normalize_contract_signature(_decl_sig)
            if not _sig:
                return []

            _base = _contract_signature_base(_sig)
            _sig_args = _extract_signature_type_args(_sig)
            _cands = set()

            # Direct index hints when present.
            _cands.update(_interface_to_impls.get(_base, set()) or set())

            # Shape fallback for abstract/delegate ecosystems.
            _validator_like_bases = {_base}
            if _base == "Validator":
                _validator_like_bases.update({"AbstractValidator"})

            for _cls_name, _parent in (_class_extends or {}).items():
                _par = str(_parent or "").split('.')[-1]
                if _par in _validator_like_bases:
                    _cands.add(_cls_name)

            _out = []
            for _cls in sorted(_cands):
                if not _class_likely_contract_implementor(_cls, _base):
                    continue
                if _sig_args and not _class_decl_uses_type_args(_cls, _sig_args):
                    continue
                _out.append(_cls)
            return _out

        _abstract_class_cache = {}

        def _is_abstract_project_class(_cls_name, _cls_file=None):
            _cls = str(_cls_name or "").strip().split('.')[-1]
            if not _cls:
                return False
            if _cls in _abstract_class_cache:
                return _abstract_class_cache.get(_cls, False)

            _paths = []
            if _cls_file:
                _paths.append(_cls_file)
            _paths.extend(list(_class_to_paths.get(_cls, []) or []))

            _is_abs = False
            for _p in _paths:
                if not _p or not os.path.isfile(_p):
                    continue
                try:
                    _txt = file_content_cache.get(_p) or read_file_cached(_p)
                except Exception:
                    _txt = ""
                if not _txt:
                    continue
                if re.search(r'\babstract\s+class\s+' + re.escape(_cls) + r'\b', _txt):
                    _is_abs = True
                    break

            _abstract_class_cache[_cls] = _is_abs
            return _is_abs

        _caller_seed_row = {}
        for _row in _rows_for_additive:
            _k = (
                _as_abs_norm(_row.get("file_name")),
                str(_row.get("class_interface_name") or "").strip(),
                str(_row.get("method_name") or "").strip(),
            )
            if _k not in _caller_seed_row:
                _caller_seed_row[_k] = _row

        # Also seed from synthetic owner-view rows produced earlier (e.g. Class1
        # expansion), so delegate init methods discovered via inherited remap can
        # still run ARRAYS iterator expansion.
        for _row in (_extra_rows or []):
            _k = (
                _as_abs_norm(_row.get("file_name")),
                str(_row.get("class_interface_name") or "").strip(),
                str(_row.get("method_name") or "").strip(),
            )
            if not _k[0] or not _k[2]:
                continue
            if _k in _caller_seed_row:
                continue
            _caller_seed_row[_k] = {
                "file_name": _row.get("file_name"),
                "class_interface_name": _row.get("class_interface_name"),
                "method_name": _row.get("method_name"),
                "object_call": _row.get("object_call", "None"),
                "class_method_call": _row.get("class_method_call", "None"),
                "__insert_rank": float(_row.get("__insert_rank", 0.0) or 0.0),
            }

        # Fallback seeds from pre-filter rows: methods that only contain calls
        # removed by early system-call filtering (e.g. iterator/asList wiring)
        # would otherwise never enter additive expansion.
        if isinstance(df, pd.DataFrame) and not df.empty:
            _seed_cols = ["file_name", "class_interface_name", "method_name"]
            if set(_seed_cols).issubset(df.columns):
                for _rec in df[_seed_cols].drop_duplicates().to_dict("records"):
                    _k = (
                        _as_abs_norm(_rec.get("file_name")),
                        str(_rec.get("class_interface_name") or "").strip(),
                        str(_rec.get("method_name") or "").strip(),
                    )
                    if _k in _caller_seed_row:
                        continue
                    if not _k[0] or not _k[2]:
                        continue
                    _caller_seed_row[_k] = {
                        "file_name": _rec.get("file_name"),
                        "class_interface_name": _rec.get("class_interface_name"),
                        "method_name": _rec.get("method_name"),
                        "object_call": "None",
                        "class_method_call": "None",
                        "__insert_rank": 0.0,
                    }

        _arrays_case_method_offset = {}
        _declared_classes_for_path_cache = {}

        def _declared_classes_for_path(_path):
            _p_norm = _as_abs_norm(_path)
            _cached = _declared_classes_for_path_cache.get(_p_norm)
            if _cached is not None:
                return _cached
            _out = set()
            if not _p_norm:
                _declared_classes_for_path_cache[_p_norm] = _out
                return _out
            for _cls_name, _paths in (_class_to_paths or {}).items():
                for _cp in (_paths or []):
                    if _as_abs_norm(_cp) == _p_norm:
                        _out.add(str(_cls_name or "").strip().split('.')[-1])
                        break
            # Fallback to file stem when declared types were not indexed.
            if not _out:
                _out.add(os.path.splitext(os.path.basename(str(_path or "")))[0])
            _declared_classes_for_path_cache[_p_norm] = _out
            return _out

        for _caller_key, _seed_row in _caller_seed_row.items():
            if _strict_entry_scope and _caller_key not in _entry_scope_caller_keys:
                # Keep explicit entry-target methods in scope even when they
                # had no direct resolved rows after early filtering.
                _caller_file_for_scope = str((_seed_row or {}).get("file_name") or "")
                _caller_method_for_scope = str((_seed_row or {}).get("method_name") or "").strip()
                _entry_methods_for_file = {
                    str(_m or "").strip().lower()
                    for _m in (_entry_targets_for_path(_caller_file_for_scope) or set())
                    if str(_m or "").strip()
                }
                if not (
                    _caller_method_for_scope
                    and _caller_method_for_scope.lower() in _entry_methods_for_file
                ):
                    continue

            _caller_file_norm, _parent_class, _caller_method = _caller_key
            if not _caller_file_norm or not _caller_method:
                continue

            _caller_file = str(_seed_row.get("file_name") or "").strip()
            if not _caller_file:
                continue
            try:
                _caller_src = file_content_cache.get(_caller_file) or read_file_cached(_caller_file)
            except Exception:
                _caller_src = ""
            if not _caller_src:
                continue

            _method_src = _extract_method_block(_caller_src, _caller_method)
            if not _method_src:
                continue

            _listed_vars = _extract_arrays_aslist_arguments(_method_src)
            _listed_vars.extend(_extract_iterator_owner_vars(_method_src))
            if _listed_vars:
                _listed_vars = list(dict.fromkeys(_listed_vars))
            if not _listed_vars:
                continue

            try:
                _caller_ast = _bfs_parse(_caller_file)
            except Exception:
                _caller_ast = None
            _injected_field_meta = _collect_injected_qualified_fields_from_ast(_caller_ast) if _caller_ast else {}

            def _qualifier_allowed_classes(_qualifiers):
                _allowed = set()
                for _ann in sorted(set(_qualifiers or set())):
                    for _ann_key in _annotation_lookup_names(_ann):
                        for _ann_path in sorted(_annotation_to_paths.get(_ann_key, set()) or set()):
                            _declared = _declared_classes_for_path(_ann_path)
                            for _ann_cls in sorted(_declared):
                                if _ann_cls:
                                    _allowed.add(_ann_cls)
                return _allowed

            _class_var_map = _build_var_map(_caller_src)
            _method_var_map = _build_var_map(_method_src)
            _var_map = dict(_class_var_map or {})
            _var_map.update(_method_var_map or {})
            _inject_vars = set()
            _inject_vars.update(_build_injected_var_name_set_local(_caller_src))
            _inject_vars.update(_build_injected_var_name_set_local(_method_src))

            _method_offset = _arrays_case_method_offset.get(_caller_key, 0.100)
            _arrays_emit_count = 0
            _arrays_emit_classes = set()
            _arrays_gate_debug_lines = []

            for _var_name in _listed_vars:
                _inj_meta = (_injected_field_meta or {}).get(_var_name) or {}
                _inj_ann = set((_inj_meta or {}).get("annotations", set()) or set())
                _inj_contract_sigs = set((_inj_meta or {}).get("contract_signatures", set()) or set())

                # Source fallback when AST metadata is missing/incomplete for field wrappers.
                if not _inj_contract_sigs:
                    _decl_field_type = _extract_field_decl_type_for_var(_caller_src, _var_name)
                    if _decl_field_type:
                        _decl_inner = _extract_first_generic_sig(_decl_field_type)
                        if _decl_inner:
                            _inj_contract_sigs.add(_normalize_contract_signature(_decl_inner))
                if not _inj_ann:
                    _inj_ann = _extract_field_annotations_for_var(_caller_src, _var_name)

                _ann_allowed_classes = _qualifier_allowed_classes(_inj_ann)
                _var_gate_lines = []

                _mapped = _var_map.get(_var_name)
                _target_classes = []
                _mapped_sig = _normalize_contract_signature(str(_mapped or ""))
                _mapped_base = _contract_signature_base(_mapped_sig)

                _resolved_cls = _extract_class_from_mapped(_mapped)
                _resolved_cls = strip_generics(str(_resolved_cls or "").split('.')[-1]).strip()
                if _verbose_debug and _parent_class.endswith("Delegate"):
                    _var_gate_lines.append(
                        "var={} mapped={} mapped_sig={} resolved_cls={} qualifiers={} ann_allowed_classes={} contract_sigs={}".format(
                            _var_name,
                            str(_mapped or ""),
                            _mapped_sig,
                            _resolved_cls,
                            ",".join(sorted(_inj_ann)) if _inj_ann else "",
                            ",".join(sorted(_ann_allowed_classes)) if _ann_allowed_classes else "",
                            ",".join(sorted(_inj_contract_sigs)) if _inj_contract_sigs else "",
                        )
                    )

                if _resolved_cls and _class_exists_in_project(_resolved_cls):
                    _owner_file = _resolve_owner_file_for_chain(
                        _resolved_cls,
                        _caller_file,
                        member_name=None,
                    )
                    if not _owner_file:
                        _owner_file = _choose_dep_candidate(
                            list(_class_to_paths.get(_resolved_cls, [])),
                            _caller_file,
                            None,
                            None,
                        )

                    # Arrays.asList-origin variables are explicit dependency wiring
                    # sites (e.g. validatorROO_ipo_363). Expand directly to their
                    # concrete class methods even when the target class itself does
                    # not carry class-level @Inject.
                    if _owner_file:
                        _target_classes.append((_resolved_cls, _owner_file))
                        if _verbose_debug and _parent_class.endswith("Delegate"):
                            _var_gate_lines.append(
                                "direct_resolve class={} owner_file={}".format(_resolved_cls, _owner_file)
                            )
                    elif _verbose_debug and _parent_class.endswith("Delegate"):
                        _var_gate_lines.append(
                            "direct_resolve class={} owner_file_missing".format(_resolved_cls)
                        )
                else:
                    # Wrapper contract case, e.g. Instance<Validator<Requestor>>.
                    # Use declared generic contract to resolve implementation classes,
                    # gated by injected-field metadata when available (AST).
                    # Regex fallback is kept for parse-failure scenarios.
                    if _inj_meta or (_var_name in _inject_vars):
                        _decl_sigs = []
                        if _inj_contract_sigs:
                            _decl_sigs.extend(sorted(_inj_contract_sigs))
                        elif _mapped_base in {"Instance", "List", "Set", "Collection", "Iterable"}:
                            _decl_sig = _extract_first_generic_sig(_mapped_sig)
                            if _decl_sig:
                                _decl_sigs.append(_decl_sig)

                        for _decl_sig in _decl_sigs:
                            _impl_candidates = _find_impl_classes_for_declared_sig(_decl_sig)
                            if _verbose_debug and _parent_class.endswith("Delegate"):
                                _var_gate_lines.append(
                                    "decl_sig={} impl_candidates_initial={}".format(
                                        _decl_sig,
                                        ",".join(sorted(_impl_candidates)) if _impl_candidates else "",
                                    )
                                )
                            if not _impl_candidates:
                                _impl_candidates = _find_contract_candidates_by_shape(_decl_sig)
                                if _verbose_debug and _parent_class.endswith("Delegate"):
                                    _var_gate_lines.append(
                                        "decl_sig={} impl_candidates_shape={}".format(
                                            _decl_sig,
                                            ",".join(sorted(_impl_candidates)) if _impl_candidates else "",
                                        )
                                    )
                            if not _impl_candidates and _ann_allowed_classes:
                                # Fallback for inherited generic contracts that may not
                                # appear in direct "implements" indexes.
                                _sig_args = _extract_signature_type_args(_decl_sig)
                                _impl_candidates = [
                                    _cls for _cls in sorted(_ann_allowed_classes)
                                    if _class_decl_uses_type_args(_cls, _sig_args)
                                    and _class_likely_contract_implementor(_cls, _contract_signature_base(_decl_sig))
                                ]
                            if not _impl_candidates and _ann_allowed_classes:
                                # Last bounded fallback: qualifier-matched classes that
                                # still look like implementors of the declared contract.
                                _decl_base = _contract_signature_base(_decl_sig)
                                _shape_matches = [
                                    _cls for _cls in sorted(_ann_allowed_classes)
                                    if _class_likely_contract_implementor(_cls, _decl_base)
                                ]
                                _impl_candidates = _shape_matches
                            if not _impl_candidates:
                                continue

                            # Strict wrapper rule: class must satisfy declared generic
                            # contract and share the injected field's qualifier.
                            if _ann_allowed_classes:
                                _before_qualifier_candidates = list(_impl_candidates)
                                _impl_candidates = [
                                    _cls for _cls in _impl_candidates
                                    if _cls in _ann_allowed_classes
                                ]
                                if (not _impl_candidates) and _before_qualifier_candidates:
                                    # Qualifier metadata is incomplete for some CDI setups;
                                    # keep contract-matching candidates as bounded fallback.
                                    _impl_candidates = _before_qualifier_candidates
                                    if _verbose_debug and _parent_class.endswith("Delegate"):
                                        _var_gate_lines.append(
                                            "decl_sig={} qualifier_filter_empty_fallback=contract_candidates".format(_decl_sig)
                                        )
                                elif _verbose_debug and _parent_class.endswith("Delegate"):
                                    _var_gate_lines.append(
                                        "decl_sig={} impl_candidates_after_qualifier={}".format(
                                            _decl_sig,
                                            ",".join(sorted(_impl_candidates)) if _impl_candidates else "",
                                        )
                                    )
                            _impl_iter = _impl_candidates
                            if not _impl_iter:
                                continue

                            for _impl_cls in _impl_iter:
                                _impl_file = _resolve_owner_file_for_chain(
                                    _impl_cls,
                                    _caller_file,
                                    member_name=None,
                                )
                                if not _impl_file:
                                    _impl_file = _choose_dep_candidate(
                                        list(_class_to_paths.get(_impl_cls, [])),
                                        _caller_file,
                                        None,
                                        None,
                                    )
                                _target_classes.append((_impl_cls, _impl_file))
                        if _verbose_debug and _parent_class.endswith("Delegate") and not _decl_sigs:
                            _var_gate_lines.append("wrapper_mode no_declared_contract_signatures")

                if _verbose_debug and _parent_class.endswith("Delegate") and not _target_classes:
                    _var_gate_lines.append("var={} target_classes_empty".format(_var_name))

                for _target_cls, _owner_file in _target_classes:
                    if not _target_cls:
                        continue
                    if _is_abstract_project_class(_target_cls, _owner_file):
                        if _verbose_debug and _parent_class.endswith("Delegate"):
                            _var_gate_lines.append("skip_abstract class={}".format(_target_cls))
                        continue
                    _declared_methods = set(_class_methods.get(_target_cls, set()) or set())
                    if (not _declared_methods) and _owner_file and os.path.isfile(_owner_file):
                        try:
                            _owner_src = file_content_cache.get(_owner_file) or read_file_cached(_owner_file)
                        except Exception:
                            _owner_src = ""
                        if _owner_src:
                            try:
                                _declared_methods = set(
                                    _extract_declared_methods_for_stem(_owner_src, _target_cls) or set()
                                )
                            except Exception:
                                _declared_methods = set()

                    for _mname in sorted(_declared_methods):
                        if not isinstance(_mname, str) or not _mname.strip():
                            continue
                        _m = _mname.strip()
                        _dupe_key = (
                            _caller_file_norm,
                            _parent_class,
                            _caller_method,
                            _target_cls,
                            _m,
                        )
                        if _dupe_key in _arrays_list_case_seen:
                            continue
                        _arrays_list_case_seen.add(_dupe_key)
                        _cmc = "{}.{}()".format(_target_cls, _m)
                        _append_dual_rows(
                            _seed_row,
                            _cmc,
                            _target_cls,
                            _m,
                            _caller_file,
                            _owner_file,
                            _offset=_method_offset,
                        )
                        _method_offset += 0.001
                        _arrays_emit_count += 1
                        _arrays_emit_classes.add(_target_cls)

                        # Continue child-call lineage for synthetic class.method rows.
                        _expand_child_continuation(_seed_row, _cmc, _owner_file)

                if _verbose_debug and _parent_class.endswith("Delegate"):
                    _arrays_gate_debug_lines.extend(_var_gate_lines)

            if _verbose_debug and (_arrays_emit_count > 0 or _parent_class.endswith("Delegate")):
                _entry_debug_print(
                    "[DEBUG][ARRAYS_EXPAND] caller_file={} class={} method={} listed_vars={} emitted_rows={} emitted_classes={}".format(
                        _caller_file,
                        _parent_class,
                        _caller_method,
                        ",".join(_listed_vars) if _listed_vars else "",
                        _arrays_emit_count,
                        ",".join(sorted(_arrays_emit_classes)) if _arrays_emit_classes else "",
                    )
                )
            if _verbose_debug and _parent_class.endswith("Delegate") and _arrays_emit_count == 0 and _arrays_gate_debug_lines:
                _entry_debug_print(
                    "[DEBUG][ARRAYS_GATE] caller_file={} class={} method={} listed_vars={}".format(
                        _caller_file,
                        _parent_class,
                        _caller_method,
                        ",".join(_listed_vars) if _listed_vars else "",
                    )
                )
                for _gate_line in _arrays_gate_debug_lines:
                    _entry_debug_print("[DEBUG][ARRAYS_GATE] {}".format(_gate_line))

            _arrays_case_method_offset[_caller_key] = _method_offset

        # ------------------------------------------------------------------
        # New additive case: method-argument object expansion
        # ------------------------------------------------------------------
        # For each call in the current method, inspect argument tokens.
        # If an argument is a variable whose declared type resolves to a project
        # class, emit synthetic Class.method() rows for that argument class.
        _arg_object_case_seen = set()

        def _extract_call_arguments(_call_text):
            if not isinstance(_call_text, str):
                return []
            _s = _call_text.strip()
            if not _s or '(' not in _s:
                return []
            _open = _s.find('(')
            _blk = _extract_balanced_parenthesized(_s, _open)
            if not _blk or len(_blk) < 2:
                return []
            _inside = _blk[1:-1]
            if not _inside.strip():
                return []
            return _split_top_level_commas(_inside)

        def _extract_call_arguments_from_method_source(_method_src_text, _obj_call_text):
            """Fallback: extract arguments from invocation text in method source.

            Some rows carry normalized object_call like Owner.method() with empty
            args; this fallback recovers real argument tokens from source.
            """
            _src = str(_method_src_text or "")
            _obj = str(_obj_call_text or "").strip()
            if not _src or not _obj or '.' not in _obj:
                return []

            _owner_hint = _obj.split('.', 1)[0].strip()
            if _owner_hint.startswith("this."):
                _owner_hint = _owner_hint[5:].strip()
            _tail = _obj.split('.', 1)[1]
            _method_hint = _tail.split('(', 1)[0].strip()
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', _method_hint or ""):
                return []

            _pat = None
            if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', _owner_hint or ""):
                _pat = re.compile(
                    r'(?:\bthis\s*\.\s*)?'
                    + re.escape(_owner_hint)
                    + r'\s*\.\s*'
                    + re.escape(_method_hint)
                    + r'\s*\(',
                    re.MULTILINE,
                )
            else:
                _pat = re.compile(
                    r'\b[A-Za-z_][A-Za-z0-9_]*\s*\.\s*'
                    + re.escape(_method_hint)
                    + r'\s*\(',
                    re.MULTILINE,
                )

            def _collect_args(_regex):
                _vals = []
                for _m in _regex.finditer(_src):
                    _open = _m.end() - 1
                    _blk = _extract_balanced_parenthesized(_src, _open)
                    if not _blk or len(_blk) < 2:
                        continue
                    _inside = _blk[1:-1]
                    if not _inside.strip():
                        continue
                    for _a in _split_top_level_commas(_inside):
                        _tok = str(_a or "").strip()
                        if _tok:
                            _vals.append(_tok)
                return _vals

            _out = _collect_args(_pat)

            # Second pass: owner-agnostic method match when owner token in
            # object_call is normalized to class name but source uses a variable.
            if not _out:
                _pat_any_owner = re.compile(
                    r'\b[A-Za-z_][A-Za-z0-9_]*\s*\.\s*'
                    + re.escape(_method_hint)
                    + r'\s*\(',
                    re.MULTILINE,
                )
                _out = _collect_args(_pat_any_owner)

            if not _out:
                return []
            _dedup = []
            _seen = set()
            for _a in _out:
                if _a in _seen:
                    continue
                _seen.add(_a)
                _dedup.append(_a)
            return _dedup

        def _build_injected_var_name_set(_src_text):
            """Collect variable names declared with @Inject in the given source text."""
            out = set()
            txt = str(_src_text or "")
            if not txt:
                return out
            _inj_decl_pat = re.compile(
                r'@Inject\b\s*'
                r'(?:(?:public|private|protected|static|final|transient|volatile)\s+)*'
                r'[A-Za-z_][\w$.<>,\[\]\?\s]*\s+'
                r'([a-z][A-Za-z0-9_]*)\s*(?:=|;|,|\))',
                re.MULTILINE,
            )
            for mm in _inj_decl_pat.finditer(txt):
                _vn = str(mm.group(1) or "").strip()
                if _vn:
                    out.add(_vn)
            return out

        _arg_case_method_offset = {}
        for _row in _rows_for_additive:
            _obj_call = str((_row or {}).get("object_call") or "").strip()
            if not _obj_call or _obj_call.lower() == "none":
                continue

            _caller_file = str((_row or {}).get("file_name") or "").strip()
            _parent_class = str((_row or {}).get("class_interface_name") or "").strip()
            _caller_method = str((_row or {}).get("method_name") or "").strip()
            _caller_key = (
                _as_abs_norm(_caller_file),
                _parent_class,
                _caller_method,
            )
            if _direct_call_count_by_caller.get(_caller_key, 0) <= 0:
                continue
            if not _caller_file or not _caller_method:
                continue

            try:
                _caller_src = file_content_cache.get(_caller_file) or read_file_cached(_caller_file)
            except Exception:
                _caller_src = ""
            if not _caller_src:
                continue

            _method_src = _extract_method_block(_caller_src, _caller_method)
            if not _method_src:
                continue

            _class_var_map = _build_var_map(_caller_src)
            _method_var_map = _build_var_map(_method_src)
            _var_map = dict(_class_var_map or {})
            _var_map.update(_method_var_map or {})
            _inject_vars = set()
            _inject_vars.update(_build_injected_var_name_set(_caller_src))
            _inject_vars.update(_build_injected_var_name_set(_method_src))
            try:
                _caller_ast = _bfs_parse(_caller_file)
            except Exception:
                _caller_ast = None
            _arg_injected_field_meta = _collect_injected_qualified_fields_from_ast(_caller_ast) if _caller_ast else {}

            _args = _extract_call_arguments(_obj_call)
            if not _args:
                _args = _extract_call_arguments_from_method_source(_method_src, _obj_call)
            if not _args:
                continue
            if _verbose_debug and _caller_method == "transformValidateEnrichStore":
                _entry_debug_print(
                    "[DEBUG][ARG_EXPAND] caller={} obj_call={} args={}".format(
                        _caller_method,
                        _obj_call,
                        ",".join(str(a) for a in _args),
                    )
                )

            _method_offset = _arg_case_method_offset.get(_caller_key, 0.300)
            for _arg in _args:
                _tok = str(_arg or "").strip()
                if not _tok:
                    continue

                # Keep only plain variable tokens (optionally this.var).
                if _tok.startswith("this."):
                    _tok = _tok[5:].strip()
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', _tok):
                    continue

                _mapped = _var_map.get(_tok)
                if not _mapped:
                    _inj_meta = (_arg_injected_field_meta or {}).get(_tok) or {}
                    _inj_contract_types = {
                        str(_c or "").strip().split('.')[-1]
                        for _c in (_inj_meta.get("contract_types", set()) or set())
                        if str(_c or "").strip()
                    }
                    if _inj_contract_types:
                        # Fallback for field tokens passed as args when regex var-map
                        # misses their declared type in method/body context.
                        _mapped = sorted(_inj_contract_types)[0]
                if not _mapped:
                    if _verbose_debug and _caller_method == "transformValidateEnrichStore":
                        _entry_debug_print("[DEBUG][ARG_EXPAND][SKIP_NO_MAP] tok={}".format(_tok))
                    continue

                _resolved_cls = _extract_class_from_mapped(_mapped)
                _resolved_cls = strip_generics(str(_resolved_cls or "").split('.')[-1]).strip()
                if not _resolved_cls:
                    continue
                if not _class_exists_in_project(_resolved_cls):
                    continue

                _owner_file = _resolve_owner_file_for_chain(
                    _resolved_cls,
                    _caller_file,
                    member_name=None,
                )
                if not _owner_file:
                    _owner_file = _choose_dep_candidate(
                        list(_class_to_paths.get(_resolved_cls, [])),
                        _caller_file,
                        None,
                        None,
                    )

                # Strict gate for argument-object expansion:
                # only continue when the EXACT resolved class declaration
                # (from import/package-resolved path) has class-level @Inject.
                _arg_declared_injected = (_tok in _inject_vars) or (_tok in (_arg_injected_field_meta or {}))
                if (not _arg_declared_injected) and (
                    not _owner_file or not _file_has_inject_annotation(_owner_file, _resolved_cls)
                ):
                    if _verbose_debug and _caller_method == "transformValidateEnrichStore":
                        _entry_debug_print(
                            "[DEBUG][ARG_EXPAND][SKIP_INJECT_GATE] tok={} cls={} owner_file={}".format(
                                _tok,
                                _resolved_cls,
                                _owner_file,
                            )
                        )
                    continue

                _declared_methods = set(_class_methods.get(_resolved_cls, set()) or set())
                if (not _declared_methods) and _owner_file and os.path.isfile(_owner_file):
                    try:
                        _owner_src = file_content_cache.get(_owner_file) or read_file_cached(_owner_file)
                    except Exception:
                        _owner_src = ""
                    if _owner_src:
                        try:
                            _declared_methods = set(
                                _extract_declared_methods_for_stem(_owner_src, _resolved_cls) or set()
                            )
                        except Exception:
                            _declared_methods = set()

                for _mname in sorted(_declared_methods):
                    if not isinstance(_mname, str) or not _mname.strip():
                        continue
                    _m = _mname.strip()
                    _dupe_key = (
                        _caller_key[0],
                        _parent_class,
                        _caller_method,
                        _resolved_cls,
                        _m,
                        _tok,
                    )
                    if _dupe_key in _arg_object_case_seen:
                        continue
                    _arg_object_case_seen.add(_dupe_key)
                    _cmc = "{}.{}()".format(_resolved_cls, _m)
                    if _verbose_debug and _caller_method == "transformValidateEnrichStore":
                        _entry_debug_print(
                            "[DEBUG][ARG_EXPAND][EMIT] tok={} cmc={} caller_file={}".format(
                                _tok,
                                _cmc,
                                _caller_file,
                            )
                        )
                    _append_dual_rows(
                        _row,
                        _cmc,
                        _resolved_cls,
                        _m,
                        _caller_file,
                        _owner_file,
                        _offset=_method_offset,
                    )
                    _method_offset += 0.001

                    # Continue child-call lineage for argument-object synthetic rows.
                    _expand_child_continuation(_row, _cmc, _owner_file)

            _arg_case_method_offset[_caller_key] = _method_offset

        # ------------------------------------------------------------------
        # New additive case: interface-generic variable call expansion
        # ------------------------------------------------------------------
        # Example:
        #   for (Enricher<PaymentOrder> enricher : sortedEnrichers) {
        #       paymentOrder = enricher.enrich(paymentOrder);
        #   }
        # For owner variable 'enricher', use its declared signature
        # Enricher<PaymentOrder>, find classes implementing this signature,
        # then emit all methods from those classes.
        _iface_generic_seen = set()

        def _build_var_signature_map(_src_text):
            """Return var->declared type signature map preserving generics."""
            out = {}
            txt = str(_src_text or "")
            if not txt:
                return out

            # Enhanced-for variable: for (Type var : iterable)
            _for_decl_pat = re.compile(
                r'\bfor\s*\(\s*'
                r'([A-Za-z_][\w$.]*(?:\s*<[^\n\r\{\};\)]*>)?)\s+'
                r'([a-z][A-Za-z0-9_]*)\s*:\s*',
                re.MULTILINE,
            )
            for mm in _for_decl_pat.finditer(txt):
                _typ = _normalize_contract_signature(mm.group(1))
                _var = (mm.group(2) or "").strip()
                if _typ and _var:
                    out[_var] = _typ

            # Local/field/parameter-like declaration fragments in method text.
            _decl_pat = re.compile(
                r'(?:(?:final|volatile|transient)\s+)*'
                r'([A-Za-z_][\w$.]*(?:\s*<[^\n\r;=\),]*>)?)\s+'
                r'([a-z][A-Za-z0-9_]*)\s*(?:=|;|,|\)|:)',
                re.MULTILINE,
            )
            for mm in _decl_pat.finditer(txt):
                _typ = _normalize_contract_signature(mm.group(1))
                _var = (mm.group(2) or "").strip()
                if _typ and _var and _var not in out:
                    out[_var] = _typ
            return out

        def _find_impl_classes_for_signature(_sig):
            """Resolve implementation classes for declared signature.

            If signature has generics, require exact implemented signature match.
            """
            _s = _normalize_contract_signature(_sig)
            if not _s:
                return []
            _base = _contract_signature_base(_s)
            if not _base:
                return []

            _cands = set(_interface_to_impls.get(_base, set()) or set())
            # Include direct index scan fallback by class-implements signatures.
            for _cls, _impl_sigs in (_class_impl_signatures or {}).items():
                _impl_norm = {
                    _normalize_contract_signature(x)
                    for x in (_impl_sigs or set())
                    if str(x or "").strip()
                }
                if not _impl_norm:
                    continue
                if '<' in _s and '>' in _s:
                    if _s in _impl_norm:
                        _cands.add(_cls)
                else:
                    if any(_contract_signature_base(x) == _base for x in _impl_norm):
                        _cands.add(_cls)

            _out = []
            for _cls in sorted(_cands):
                _impl_norm = {
                    _normalize_contract_signature(x)
                    for x in (_class_impl_signatures.get(_cls, set()) or set())
                    if str(x or "").strip()
                }
                if '<' in _s and '>' in _s:
                    if _s in _impl_norm:
                        _out.append(_cls)
                else:
                    if not _impl_norm or any(_contract_signature_base(x) == _base for x in _impl_norm):
                        _out.append(_cls)
            return _out

        def _build_decl_signature_map(_src_text):
            """Build var -> declared type signature map preserving generics."""
            out = {}
            txt = str(_src_text or "")
            if not txt:
                return out

            _decl_pat = re.compile(
                r'(?:(?:public|private|protected|static|final|transient|volatile)\s+)*'
                r'([A-Za-z_][\w$.]*(?:\s*<[^\n\r;=\),]*>)?)\s+'
                r'([a-z][A-Za-z0-9_]*)\s*(?:=|;|,|\)|:)',
                re.MULTILINE,
            )
            for mm in _decl_pat.finditer(txt):
                _typ = _normalize_contract_signature(mm.group(1))
                _var = (mm.group(2) or "").strip()
                if _typ and _var and _var not in out:
                    out[_var] = _typ
            return out

        def _wrapper_base_from_sig(_sig):
            _s = _normalize_contract_signature(_sig)
            if not _s:
                return ""
            return _contract_signature_base(_s)

        def _extract_first_generic_arg_sig(_sig):
            _s = _normalize_contract_signature(_sig)
            if not _s or '<' not in _s or '>' not in _s:
                return ""
            _inner = _s.split('<', 1)[1].rsplit('>', 1)[0].strip()
            if not _inner:
                return ""
            _parts = _split_top_level_commas(_inner)
            return _normalize_contract_signature(_parts[0]) if _parts else ""

        def _build_foreach_binding_map(_method_src, _file_src):
           
            out = {}
            _method_decl_map = _build_decl_signature_map(_method_src)
            _file_decl_map = _build_decl_signature_map(_file_src)
            _for_pat = re.compile(
                r'\bfor\s*\(\s*'
                r'([A-Za-z_][\w$.]*(?:\s*<[^\n\r\{\};\)]*>)?)\s+'
                r'([a-z][A-Za-z0-9_]*)\s*:\s*'
                r'([^\)]+)\)',
                re.MULTILINE,
            )
            for mm in _for_pat.finditer(str(_method_src or "")):
                _elem_sig = _normalize_contract_signature(mm.group(1))
                _owner_var = (mm.group(2) or "").strip()
                _iter_expr = (mm.group(3) or "").strip()
                _iter_var = _iter_expr
                if _iter_var.startswith("this."):
                    _iter_var = _iter_var[5:].strip()
                # keep only plain iterable variable names
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', _iter_var or ""):
                    _iter_var = ""

                _iter_sig = ""
                if _iter_var:
                    _iter_sig = _method_decl_map.get(_iter_var) or _file_decl_map.get(_iter_var) or ""

                if _owner_var and _elem_sig:
                    out[_owner_var] = {
                        "element_sig": _elem_sig,
                        "iterable_name": _iter_var,
                        "iterable_sig": _normalize_contract_signature(_iter_sig),
                    }
            return out

        _iface_case_method_offset = {}
        for _row in _rows_for_additive:
            _obj_call = str((_row or {}).get("object_call") or "").strip()
            if not _obj_call or _obj_call.lower() == "none" or "." not in _obj_call:
                continue

            _owner_var = _obj_call.split('.', 1)[0].strip()
            if _owner_var.startswith("this."):
                _owner_var = _owner_var[5:].strip()
            _is_owner_var = bool(re.fullmatch(r'[a-z][A-Za-z0-9_]*', _owner_var or ""))
            _is_owner_type = bool(re.fullmatch(r'[A-Z][A-Za-z0-9_]*', _owner_var or ""))
            if not (_is_owner_var or _is_owner_type):
                continue

            _caller_file = str((_row or {}).get("file_name") or "").strip()
            _parent_class = str((_row or {}).get("class_interface_name") or "").strip()
            _caller_method = str((_row or {}).get("method_name") or "").strip()
            _caller_key = (
                _as_abs_norm(_caller_file),
                _parent_class,
                _caller_method,
            )
            if _direct_call_count_by_caller.get(_caller_key, 0) <= 0:
                continue
            if not _caller_file or not _caller_method:
                continue

            try:
                _caller_src = file_content_cache.get(_caller_file) or read_file_cached(_caller_file)
            except Exception:
                _caller_src = ""
            if not _caller_src:
                continue

            _method_src = _extract_method_block(_caller_src, _caller_method)
            if not _method_src:
                continue

            _foreach_bindings = _build_foreach_binding_map(_method_src, _caller_src)

            _sig_map = _build_var_signature_map(_method_src)
            _decl_sig = _sig_map.get(_owner_var)
            # If owner token was already normalized to interface/class name
            # (e.g. Enricher.enrich()), recover declared generic signature from
            # method-scope variable declarations sharing this base.
            if not _decl_sig and _is_owner_type:
                _owner_base = _contract_signature_base(_owner_var)
                _cands = []
                for _v_sig in (_sig_map or {}).values():
                    _v_norm = _normalize_contract_signature(_v_sig)
                    if _contract_signature_base(_v_norm) == _owner_base:
                        _cands.append(_v_norm)
                if _cands:
                    _decl_sig = next((s for s in _cands if '<' in s and '>' in s), _cands[0])
            if not _decl_sig:
                continue

            _foreach_meta = _foreach_bindings.get(_owner_var)
            if not _foreach_meta and _is_owner_type:
                # Owner may be normalized to type token (Enricher) while
                # foreach map is keyed by variable name (enricher).
                _decl_norm = _normalize_contract_signature(_decl_sig)
                _decl_base = _contract_signature_base(_decl_norm)
                _cand = []
                for _meta in (_foreach_bindings or {}).values():
                    _elem = _normalize_contract_signature((_meta or {}).get("element_sig", ""))
                    if not _elem:
                        continue
                    if _elem == _decl_norm or _contract_signature_base(_elem) == _decl_base:
                        _cand.append(_meta)
                if _cand:
                    _exact = [m for m in _cand if _normalize_contract_signature((m or {}).get("element_sig", "")) == _decl_norm]
                    _foreach_meta = (_exact[0] if _exact else _cand[0])
            _iterable_sig = _normalize_contract_signature((_foreach_meta or {}).get("iterable_sig", ""))
            _element_sig = _normalize_contract_signature((_foreach_meta or {}).get("element_sig", _decl_sig))
            _iterable_base = _wrapper_base_from_sig(_iterable_sig)

            _impl_classes = _find_impl_classes_for_signature(_element_sig)
            if not _impl_classes:
                continue

            _method_offset = _iface_case_method_offset.get(_caller_key, 0.700)
            for _impl_cls in _impl_classes:
                if not _class_exists_in_project(_impl_cls):
                    continue

                _owner_file = _resolve_owner_file_for_chain(
                    _impl_cls,
                    _caller_file,
                    member_name=None,
                )
                if not _owner_file:
                    _owner_file = _choose_dep_candidate(
                        list(_class_to_paths.get(_impl_cls, [])),
                        _caller_file,
                        None,
                        None,
                    )

                _declared_methods = set(_class_methods.get(_impl_cls, set()) or set())
                if (not _declared_methods) and _owner_file and os.path.isfile(_owner_file):
                    try:
                        _owner_src = file_content_cache.get(_owner_file) or read_file_cached(_owner_file)
                    except Exception:
                        _owner_src = ""
                    if _owner_src:
                        try:
                            _declared_methods = set(
                                _extract_declared_methods_for_stem(_owner_src, _impl_cls) or set()
                            )
                        except Exception:
                            _declared_methods = set()

                # For List<Enricher<...>> in foreach, emit only enrich().
                # For Instance<...>, keep existing logic (all methods).
                if _iterable_base == "List":
                    _declared_methods = {m for m in _declared_methods if str(m or "").strip() == "enrich"}

                for _mname in sorted(_declared_methods):
                    if not isinstance(_mname, str) or not _mname.strip():
                        continue
                    _m = _mname.strip()
                    _dupe_key = (
                        _caller_key[0],
                        _parent_class,
                        _caller_method,
                        _impl_cls,
                        _m,
                        _owner_var,
                        _decl_sig,
                    )
                    if _dupe_key in _iface_generic_seen:
                        continue
                    _iface_generic_seen.add(_dupe_key)

                    _cmc = "{}.{}()".format(_impl_cls, _m)
                    _append_dual_rows(
                        _row,
                        _cmc,
                        _impl_cls,
                        _m,
                        _caller_file,
                        _owner_file,
                        _offset=_method_offset,
                    )
                    _method_offset += 0.001

                    # For List<Enricher<...>> foreach, keep focused roots at
                    # enrich() implementers and avoid deep child-call explosion.
                    if _iterable_base != "List":
                        _expand_child_continuation(_row, _cmc, _owner_file)

            _iface_case_method_offset[_caller_key] = _method_offset

        if _extra_rows:
            df_clean = pd.concat([df_clean, pd.DataFrame(_extra_rows)], ignore_index=True)
            if "__insert_rank" in df_clean.columns:
                df_clean = df_clean.sort_values(["__insert_rank"], kind="mergesort").reset_index(drop=True)
            df_clean = df_clean.drop_duplicates(
                subset=["file_name", "class_interface_name", "method_name", "class_method_call"],
                keep="first",
            ).reset_index(drop=True)
        _pbar_goto(85, "Post-processing: call ownership done")

        # Early strict filter: if owner class/path cannot be resolved to a real
        # project source file, drop the row before chain explosion.
        _owner_resolve_pre_cache = {}

        def _split_owner_member_pre(call_str):
            if not isinstance(call_str, str):
                return "", ""
            s = call_str.strip()
            if not s or s.lower() == "none":
                return "", ""
            m = re.match(r'^\s*(.*)\.([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*$', s)
            if not m:
                return "", ""
            return (m.group(1) or "").strip(), (m.group(2) or "").strip()

        def _is_project_resolved_pre(call_str, caller_file):
            owner, member = _split_owner_member_pre(call_str)
            # Unqualified call support (e.g. getBase64Sha256Hash(...)):
            # keep when declared in caller file or imported via static import.
            if not owner and isinstance(call_str, str):
                _m_unq = re.match(r'^\s*([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*$', call_str.strip())
                if _m_unq:
                    _mname = _m_unq.group(1)
                    _caller_abs = os.path.abspath(str(caller_file)) if caller_file else ""
                    if _caller_abs and _file_declares_member_bfs(_caller_abs, _mname):
                        return True

                    _imports_map = _file_to_imports_early.get(_caller_abs, {}) if _caller_abs else {}
                    _wildcards = _file_to_wildcards_early.get(_caller_abs, []) if _caller_abs else []

                    # import static a.b.C.member;
                    _fqn_member = _imports_map.get(_mname)
                    if isinstance(_fqn_member, str) and _fqn_member.count('.') >= 1:
                        _owner_fqn = _fqn_member.rsplit('.', 1)[0]
                        for _p in _fqn_to_paths_early.get(_owner_fqn, []) or []:
                            if os.path.isfile(_p) and _file_declares_member_bfs(_p, _mname):
                                return True
                        # Keep row for late-stage path enrichment even when owner
                        # file lives outside the current pre-indexed module set.
                        return True

                    # import static a.b.C.*;
                    for _wfqn in _wildcards or []:
                        for _p in _fqn_to_paths_early.get(_wfqn, []) or []:
                            if os.path.isfile(_p) and _file_declares_member_bfs(_p, _mname):
                                return True

                return False

            if not owner:
                return keep_unresolved_owner_calls

            # Chained owner expressions (e.g. Type.getA().getB()) are valid
            # intermediate forms before chain explosion. Resolving them as a
            # simple class token here is impossible and caused false drops at S2.
            # Let later chain/existence stages validate these rows.
            if "(" in owner or ")" in owner:
                return True

            cache_key = (
                owner,
                member,
                os.path.normcase(os.path.abspath(str(caller_file))) if caller_file else "",
            )
            cached = _owner_resolve_pre_cache.get(cache_key)
            if cached is not None:
                return cached

            # Already path-enriched owner
            if "/" in owner or "\\" in owner:
                owner_java = owner if owner.lower().endswith(adapter.file_extension()) else (owner + adapter.file_extension())
                out = os.path.isfile(owner_java)
                _owner_resolve_pre_cache[cache_key] = out
                return out

            owner_simple = owner.split(".")[-1] if "." in owner else owner
            candidates = []

            for p in _resolve_type_paths_from_caller(owner, caller_file) or []:
                if p and p not in candidates:
                    candidates.append(p)
            for p in _resolve_type_paths_from_caller(owner_simple, caller_file) or []:
                if p and p not in candidates:
                    candidates.append(p)
            for p in type_to_path_full_early.get(owner_simple, []) or []:
                if p and p not in candidates:
                    candidates.append(p)

            candidates = [p for p in candidates if os.path.isfile(p)]
            if not candidates:
                _owner_resolve_pre_cache[cache_key] = bool(keep_unresolved_owner_calls and member)
                return bool(keep_unresolved_owner_calls and member)

            if member:
                candidates_with_member = [p for p in candidates if _file_declares_member_bfs(p, member)]
                if candidates_with_member:
                    _owner_resolve_pre_cache[cache_key] = True
                    return True

            _owner_resolve_pre_cache[cache_key] = bool(candidates)
            return bool(candidates)

        if not df_clean.empty and "class_method_call" in df_clean.columns:
            _pre_mask = [
                _is_project_resolved_pre(_cmc, _file)
                for _cmc, _file in zip(
                    df_clean["class_method_call"].tolist(),
                    df_clean["file_name"].tolist(),
                )
            ]
            df_clean = df_clean.loc[_pre_mask].copy()
        _debug_entry_stage(df_clean, "S2_after_pre_owner_filter", call_col="class_method_call")
        _pbar_goto(86, "Post-processing: dropped unresolved owners")

        def _import_implies_project_owner(owner_name, caller_file):
            """Best-effort check: owner is a caller-imported project type."""
            owner = strip_generics(str(owner_name or "")).strip()
            if not owner:
                return False
            owner_simple = owner.split(".")[-1]
            caller_abs = os.path.abspath(str(caller_file)) if caller_file else ""
            if not caller_abs:
                return False

            imp_map = _file_to_imports_early.get(caller_abs, {})
            fqn = imp_map.get(owner_simple)
            if not fqn:
                owner_lc = owner_simple.lower()
                for _k, _v in imp_map.items():
                    if str(_k).lower() == owner_lc:
                        fqn = _v
                        break
            if not isinstance(fqn, str) or not fqn.strip():
                return False

            user_pref = str(details.get("user_defined_generic_import") or "")
            user_pref2 = str(details.get("user_defined_import") or "")
            if user_pref and not fqn.startswith(user_pref):
                if user_pref2 and not fqn.startswith(user_pref2):
                    return False

            hits = _fqn_to_paths_early.get(fqn, []) or []
            if any(os.path.isfile(_p) for _p in hits):
                return True

            # Fallback through caller-aware resolver when index misses the exact FQN.
            return bool(_resolve_type_paths_from_caller(owner_simple, caller_file))


        def derive_chain_segments(obj_call, parent_class, file_name, caller_method_name=None):
            if not isinstance(obj_call, str) or obj_call.strip() == "":
                return []

            first_dot = obj_call.find(".")
            if first_dot == -1 or "(" not in obj_call:
                m = re.match(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\(', obj_call)
                if m:
                    cls, mtd = strip_generics(m.group(1)), m.group(2)
                    if not _method_exists_in_class(
                        cls,
                        mtd,
                        caller_file=file_name,
                        fallback_class_name=strip_generics(parent_class),
                    ):
                        return []
                    # Case 1: walk extends for single-segment calls
                    owning = _resolve_class_for_method(
                        cls,
                        mtd,
                        prefer_concrete=not _is_direct_super_of_caller(cls, parent_class),
                        fallback_class_name=strip_generics(parent_class),
                    )
                    return ["{}.{}()".format(owning, mtd)]
                m2 = re.match(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*$', obj_call)
                if m2:
                    cls, mtd = strip_generics(m2.group(1)), m2.group(2)
                    if not _method_exists_in_class(
                        cls,
                        mtd,
                        caller_file=file_name,
                        fallback_class_name=strip_generics(parent_class),
                    ):
                        return []
                    owning = _resolve_class_for_method(
                        cls,
                        mtd,
                        prefer_concrete=not _is_direct_super_of_caller(cls, parent_class),
                        fallback_class_name=strip_generics(parent_class),
                    )
                    return ["{}.{}()".format(owning, mtd)]
                return []

            # Case 2: resolve field-access chain before the first "("
            first_paren = obj_call.find("(")
            prefix_before_call = obj_call[:first_paren]
            suffix_after_prefix = obj_call[first_paren:]

            current_class, first_method = _resolve_field_chain(
                prefix_before_call,
                parent_class,
                file_name,
                caller_method_name=caller_method_name,
            )
            if not first_method:
                # Recovery path: when field-chain parsing misses, resolve
                # lowercase receiver variable via method/file var map and keep
                # imported project owners instead of dropping lineage.
                _m_var_call = re.match(
                    r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\(',
                    obj_call,
                )
                if _m_var_call:
                    _owner_tok = strip_generics(_m_var_call.group(1))
                    _member_tok = _m_var_call.group(2)
                    _owner_cls = _owner_tok
                    if _owner_tok and _owner_tok[:1].islower():
                        _mvmap = {}
                        _caller_norm = os.path.normcase(os.path.abspath(file_name)) if file_name else ""
                        _ctext = file_content_cache.get(file_name, "")
                        if not _ctext and file_name:
                            try:
                                _ctext = read_file_cached(file_name)
                            except Exception:
                                _ctext = ""
                        if _ctext and caller_method_name:
                            _mblock = _extract_method_block(_ctext, caller_method_name)
                            if _mblock:
                                _mvmap = _build_var_map(_mblock)
                        _owner_decl = _mvmap.get(_owner_tok)
                        if not _owner_decl:
                            _fvmap = _caller_varmap_cache.get(_caller_norm)
                            if _fvmap is None:
                                _fvmap = _build_var_map(_ctext or "")
                                _caller_varmap_cache[_caller_norm] = _fvmap
                            _owner_decl = (_fvmap or {}).get(_owner_tok)
                        if _owner_decl:
                            _owner_cls = _extract_class_from_mapped(strip_generics(_owner_decl)) or _owner_cls

                    if _method_exists_in_class(
                        _owner_cls,
                        _member_tok,
                        caller_file=file_name,
                        fallback_class_name=strip_generics(parent_class),
                    ) or _import_implies_project_owner(_owner_cls, file_name):
                        return ["{}.{}()".format(_owner_cls, _member_tok)]

                m3 = re.match(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*$', obj_call)
                if m3:
                    cls, mtd = strip_generics(m3.group(1)), m3.group(2)
                    if not _method_exists_in_class(
                        cls,
                        mtd,
                        caller_file=file_name,
                        fallback_class_name=strip_generics(parent_class),
                    ):
                        return []
                    owning = _resolve_class_for_method(
                        cls,
                        mtd,
                        prefer_concrete=not _is_direct_super_of_caller(cls, parent_class),
                        fallback_class_name=strip_generics(parent_class),
                    )
                    return ["{}.{}()".format(owning, mtd)]
                return []

            remaining_methods = re.findall(r'\.([A-Za-z_]\w*)\s*\(', suffix_after_prefix)
            if not remaining_methods and ")." in suffix_after_prefix:
                _tail_after_first = suffix_after_prefix.split(")", 1)[1]
                remaining_methods = re.findall(r'\.([A-Za-z_]\w*)\s*(?:\(|(?=\.|\s*$))', _tail_after_first)
            methods = [first_method] + remaining_methods

            _super_rooted = bool(re.match(r'^\s*super\s*\.', obj_call, flags=re.IGNORECASE))
            _current_ctx_file = file_name
            segments = []
            _pending_chain_element_type = None
            for i, mtd in enumerate(methods):
                owning_class = _normalize_owner_class_for_member(current_class, mtd)
                _keep_direct_super_owner = (i == 0 and _is_direct_super_of_caller(owning_class, parent_class))
                owning_class = _resolve_class_for_method(
                    strip_generics(owning_class),
                    mtd,
                    prefer_concrete=not ((_super_rooted and i == 0) or _keep_direct_super_owner),
                    fallback_class_name=strip_generics(parent_class),
                )
                # Chain rule: method2+ owner comes from method1 return-type context.
                _owner_token = strip_generics(owning_class)
                if i > 0 and _current_ctx_file:
                    _owner_token = os.path.splitext(os.path.abspath(_current_ctx_file))[0]
                segments.append("{}.{}()".format(_owner_token, mtd))

                # No next method â€” nothing more to resolve
                if i == len(methods) - 1:
                    break

                next_mtd = methods[i + 1]

                # Step 1: try method_return_index (inheritance-aware)
                owner_for_ret = _resolve_owner_class_name(owning_class, _current_ctx_file)
                owner_file_for_ret = _resolve_owner_file_for_chain(
                    owner_for_ret,
                    _current_ctx_file,
                    member_name=mtd,
                )
                super_fb_cls = _super_fallback_class(owner_for_ret, parent_class)
                ret_type = _get_return_type(
                    owner_for_ret,
                    mtd,
                    caller_file=owner_file_for_ret,
                    fallback_class_name=super_fb_cls,
                )
                if not ret_type:
                    ret_type = _infer_return_type_from_accessor_field(
                        owner_for_ret,
                        mtd,
                        owner_file_for_ret or _current_ctx_file or file_name,
                    )

                declared_ret = _get_declared_return_type_from_file(owner_file_for_ret, mtd)
                _container_base, _container_elem = _container_and_element_type(declared_ret or ret_type)
                _pair_elem = _pair_like_accessor_result_type(declared_ret or ret_type, next_mtd)
                if _pair_elem:
                    ret_type = _container_base or "Pair"
                    _pending_chain_element_type = _pair_elem
                elif next_mtd in _collection_like_methods and _container_base in (_generic_container_types | {"Map"}):
                    ret_type = _container_base
                    if _container_base == "Map":
                        _map_args = _top_level_generic_args(declared_ret or ret_type)
                        _pending_chain_element_type = strip_generics(_map_args[1]) if len(_map_args) > 1 else _container_elem
                    else:
                        _pending_chain_element_type = _container_elem
                elif declared_ret:
                    ret_type = _unwrap_generic_return_type(declared_ret)

                if not ret_type:
                    _class_lit_target = _extract_class_literal_target(obj_call, mtd)
                    if _class_lit_target:
                        ret_type = _class_lit_target

                _dbg(f"derive_segments: owner={owning_class}, owner_for_ret={owner_for_ret}, method_1={mtd}, return(index)={ret_type}, method_2={next_mtd}, file={file_name}")
                if ret_type:
                    next_class = strip_generics(str(ret_type).split(".")[-1])

                    # Collection unwrap fix:
                    # List<OnlinePaymentOrderInitiation>.get(...)
                    # should resolve the next owner as OnlinePaymentOrderInitiation,
                    # not List.
                    if (
                        _pending_chain_element_type
                        and mtd in _collection_like_methods
                    ):
                        next_class = strip_generics(_pending_chain_element_type)
                        _pending_chain_element_type = None

                    next_type_paths = _resolve_type_paths_for_chain_return(
                        next_class,
                        owner_file_for_ret,
                        fallback_file=_current_ctx_file,
                        member_name=next_mtd,
                    )

                    ok = False
                    if next_type_paths:
                        for _ntp in next_type_paths:
                            _next_simple = os.path.splitext(os.path.basename(_ntp))[0]
                            if _method_exists_in_class(
                                _next_simple,
                                next_mtd,
                                caller_file=_ntp,
                                fallback_class_name=super_fb_cls,
                            ):
                                current_class = _next_simple
                                _current_ctx_file = _ntp
                                ok = True
                                break

                        if not ok:
                            # Keep owner-file return-type context authoritative for
                            # chained method2 even when method discovery is incomplete.
                            _best_ntp = (
                                _choose_best_candidate_early(
                                    next_type_paths,
                                    owner_file_for_ret,
                                    member_name=next_mtd,
                                )
                                or next_type_paths[0]
                            )
                            current_class = os.path.splitext(
                                os.path.basename(_best_ntp)
                            )[0]
                            _current_ctx_file = _best_ntp
                            ok = True

                    else:
                        ok = _method_exists_in_class(
                            next_class,
                            next_mtd,
                            caller_file=owner_file_for_ret,
                            fallback_class_name=super_fb_cls,
                        )

                        if not ok and _well_known_accessor_exists(next_class, next_mtd):
                            ok = True

                        if ok:
                            current_class = next_class
                            _current_ctx_file = owner_file_for_ret

                    _dbg(
                        f"derive_segments: next_class={next_class}, "
                        f"method_2={next_mtd}, exists={ok}"
                    )

                    # Step 2: confirm next_mtd exists in next_class
                    if ok:
                        continue
                    # next_class doesn't have the method â€” stop chain
                    break

                # Step 2 fallback: index missing return type â€” scan the source file
                # for the declaration: "public ReturnType methodName("
                ret_from_file = None
                _ret_decl_re = re.compile(
                    r'\b([A-Za-z_]\w*(?:<[^>]+>)?)\s+' + re.escape(mtd) + r'\s*\(',
                    re.MULTILINE
                )
                _owner_key = strip_generics(owning_class)
                _owner_file_candidates = list(type_to_path_full_early.get(_owner_key, []))
                if not _owner_file_candidates:
                    _owner_file_candidates = _resolve_type_paths_from_caller(
                        _owner_key,
                        _current_ctx_file or file_name,
                    )
                if not _owner_file_candidates and isinstance(_owner_key, str) and "." in _owner_key:
                    _owner_file_candidates = _resolve_type_paths_from_caller(
                        _owner_key.split(".")[-1],
                        _current_ctx_file or file_name,
                    )

                for fpath in _owner_file_candidates:
                    text = file_content_cache.get(fpath) or ""
                    if not text:
                        try:
                            text = read_file_cached(fpath)
                        except Exception:
                            continue
                    fm = _ret_decl_re.search(text)
                    if fm:
                        candidate = strip_generics(fm.group(1))
                        if candidate.lower() not in ('void', 'public', 'private',
                                                     'protected', 'static', 'final',
                                                     'return', 'new', 'boolean',
                                                     'int', 'long', 'double', 'float',
                                                     'string', 'object'):
                            ret_from_file = candidate
                            break

                if ret_from_file:
                    # Verify next_mtd actually lives in ret_from_file's class
                    ok2 = _method_exists_in_class(
                        ret_from_file,
                        next_mtd,
                        caller_file=owner_file_for_ret,
                        fallback_class_name=super_fb_cls,
                    )
                    _dbg(f"derive_segments: return(file)={ret_from_file}, method_2={next_mtd}, exists={ok2}")
                    if ok2:
                        current_class = ret_from_file
                        _current_ctx_file = owner_file_for_ret
                        continue

                # Cannot determine the next class â€” stop chain
                _dbg(f"derive_segments: STOP owner={owning_class}, method_1={mtd}, method_2={next_mtd}, return(file)={ret_from_file}")

                break
            return segments

        def explode_cleaned_ast_details(df_clean_local):
            # Convert to list-of-dicts once â€” much faster than iterrows()
            records = df_clean_local.to_dict("records")
            single_seg_pat_paren = re.compile(r'^\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\.\s*([A-Za-z_]\w*)\s*\([^)]*\)\s*$')
            single_seg_pat_noparen = re.compile(r'^\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\.\s*([A-Za-z_]\w*)\s*$')
            qual_inv_pat = re.compile(r'([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\.\s*([A-Za-z_]\w*)\s*\(')

            def _extract_nested_qualified_calls(_call_text):
                """Return inner qualified invocations from a composite call string.

                Example:
                  A.setX(B.getY()) -> ["B.getY()"]
                """
                if not isinstance(_call_text, str):
                    return []
                s = _call_text.strip()
                if not s:
                    return []

                found = []
                first_seen = False
                seen = set()
                for _m in qual_inv_pat.finditer(s):
                    # Skip the outer/root invocation and keep nested ones.
                    if not first_seen:
                        first_seen = True
                        continue
                    _owner = (_m.group(1) or "").strip()
                    _member = (_m.group(2) or "").strip()
                    if not _owner or not _member:
                        continue
                    _sig = "{}.{}()".format(_owner, _member)
                    if _sig not in seen:
                        seen.add(_sig)
                        found.append(_sig)
                return found

            def _is_valid_callable_cmc(_cmc_raw, _caller_file, _fallback_cls):
                """True only when cmc has an owner.member shape and member is a real method/ctor."""
                if not isinstance(_cmc_raw, str):
                    return False
                _s = _cmc_raw.strip()
                if not _s or _s.lower() == "none":
                    return False

                _m = re.match(r'^\s*(.*)\.([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*$', _s)
                if not _m:
                    return False

                _owner = (_m.group(1) or "").strip()
                _member = (_m.group(2) or "").strip()
                if not _owner or not _member:
                    return False

                _check_file = _caller_file
                if os.sep in _owner or (os.sep != "/" and "/" in _owner):
                    _owner_file = _owner + adapter.file_extension()
                    _owner_cls = os.path.splitext(os.path.basename(_owner))[0]
                    if os.path.isfile(_owner_file):
                        _check_file = _owner_file
                else:
                    _owner_cls = strip_generics(_owner).split('.')[-1]

                return _method_exists_in_class(
                    _owner_cls,
                    _member,
                    caller_file=_check_file,
                    fallback_class_name=_fallback_cls,
                )

            rows = []
            for row_idx, row in enumerate(records):
                obj_call = str(row.get("object_call", "") or "").strip()
                parent_class = str(row.get("class_interface_name", "") or "").strip()
                file_name = str(row.get("file_name", "") or "").strip()
                caller_method_name = str(row.get("method_name", "") or "").strip()

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
                    "__source_object_call": obj_call,
                    "__row_order": row_idx,
                }

                segments = derive_chain_segments(
                    obj_call,
                    parent_class,
                    file_name,
                    caller_method_name=caller_method_name,
                )
                if segments:
                    for seg_idx, seg in enumerate(segments):
                        row_dict = dict(base_context)
                        row_dict["object_call"] = seg
                        row_dict["class_method_call"] = seg
                        row_dict["__segment_order"] = seg_idx
                        rows.append(row_dict)
                    _nested = _extract_nested_qualified_calls(obj_call)
                    _base_seg = len(segments)
                    for _n_idx, _n_call in enumerate(_nested, start=1):
                        row_dict = dict(base_context)
                        row_dict["object_call"] = _n_call
                        row_dict["class_method_call"] = _n_call
                        row_dict["__segment_order"] = _base_seg + _n_idx
                        rows.append(row_dict)
                    continue

                m2 = single_seg_pat_paren.match(cmc)
                if m2:
                    cls, mtd = strip_generics(m2.group(1)), m2.group(2)
                    _caller_file_for_check = base_context.get("file_name")
                    _fallback_cls = strip_generics(base_context.get("class_interface_name"))
                    if not _method_exists_in_class(
                        cls,
                        mtd,
                        caller_file=_caller_file_for_check,
                        fallback_class_name=_fallback_cls,
                    ):
                        continue
                    key = (base_context["file_name"], base_context["class_interface_name"], base_context["method_name"], mtd.lower())
                    if key in chain_suppressions:
                        continue
                    seg = "{}.{}()".format(cls, mtd)
                    row_dict = dict(base_context)
                    # Keep original object-call root (e.g. req.setId()) so later
                    # enrichment can resolve owner from the correct variable type.
                    row_dict["object_call"] = obj_call or seg
                    row_dict["class_method_call"] = seg
                    row_dict["__segment_order"] = 0
                    rows.append(row_dict)
                    _nested = _extract_nested_qualified_calls(obj_call)
                    for _n_idx, _n_call in enumerate(_nested, start=1):
                        row_dict = dict(base_context)
                        row_dict["object_call"] = _n_call
                        row_dict["class_method_call"] = _n_call
                        row_dict["__segment_order"] = _n_idx
                        rows.append(row_dict)
                    continue

                m2_np = single_seg_pat_noparen.match(cmc)
                if m2_np:
                    cls, mtd = strip_generics(m2_np.group(1)), m2_np.group(2)
                    _caller_file_for_check = base_context.get("file_name")
                    _fallback_cls = strip_generics(base_context.get("class_interface_name"))
                    if not _method_exists_in_class(
                        cls,
                        mtd,
                        caller_file=_caller_file_for_check,
                        fallback_class_name=_fallback_cls,
                    ):
                        continue
                    key = (base_context["file_name"], base_context["class_interface_name"], base_context["method_name"], mtd.lower())
                    if key in chain_suppressions:
                        continue
                    seg = "{}.{}()".format(cls, mtd)
                    row_dict = dict(base_context)
                    # Keep original object-call root (e.g. req.setId()) so later
                    # enrichment can resolve owner from the correct variable type.
                    row_dict["object_call"] = obj_call or seg
                    row_dict["class_method_call"] = seg
                    row_dict["__segment_order"] = 0
                    rows.append(row_dict)
                    _nested = _extract_nested_qualified_calls(obj_call)
                    for _n_idx, _n_call in enumerate(_nested, start=1):
                        row_dict = dict(base_context)
                        row_dict["object_call"] = _n_call
                        row_dict["class_method_call"] = _n_call
                        row_dict["__segment_order"] = _n_idx
                        rows.append(row_dict)
                    continue

                row_dict = dict(base_context)
                _cmc_fallback = cmc or obj_call or "None"
                _caller_file_for_check = base_context.get("file_name")
                _fallback_cls = strip_generics(base_context.get("class_interface_name"))
                if not _is_valid_callable_cmc(_cmc_fallback, _caller_file_for_check, _fallback_cls):
                    continue
                row_dict["object_call"] = obj_call or "None"
                row_dict["class_method_call"] = _cmc_fallback
                row_dict["__segment_order"] = 0
                rows.append(row_dict)

                _nested = _extract_nested_qualified_calls(obj_call)
                for _n_idx, _n_call in enumerate(_nested, start=1):
                    row_dict = dict(base_context)
                    row_dict["object_call"] = _n_call
                    row_dict["class_method_call"] = _n_call
                    row_dict["__segment_order"] = _n_idx
                    rows.append(row_dict)

            df_out = pd.DataFrame(rows) if rows else df_clean_local.copy()
            if not df_out.empty:
                df_out = df_out.drop_duplicates()
                if {"__row_order", "__segment_order"}.issubset(df_out.columns):
                    df_out = df_out.sort_values(
                        ["__row_order", "__segment_order"],
                        kind="mergesort",
                    ).reset_index(drop=True)
            return df_out

        df_clean_exploded = explode_cleaned_ast_details(df_clean)
        _debug_entry_stage(df_clean_exploded, "S3_after_explode", call_col="class_method_call")

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

        def _is_imported_project_getter_call(_cmc, _caller_file, _method_only):
            if not isinstance(_cmc, str) or not isinstance(_method_only, str):
                return False
            _m = _method_only.strip().lower()
            if not (_m.startswith("get") or _m.startswith("is")):
                return False

            _mc = re.match(r'^\s*(.*)\.([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*$', _cmc.strip())
            if not _mc:
                return False
            _owner_raw = (_mc.group(1) or "").strip().replace('\\\\', '/')
            if not _owner_raw:
                return False

            _owner_tail = _owner_raw.rsplit('/', 1)[-1]
            _owner_simple = _owner_tail.rsplit('.', 1)[-1] if '.' in _owner_tail else _owner_tail
            if not _owner_simple or not _owner_simple[:1].isupper():
                return False

            return _import_implies_project_owner(_owner_simple, _caller_file)

        if remove_builtin_calls:
            _builtin_mask = df_clean_exploded["__method_only"].isin(SYSTEM_METHODS)
            _protected_mask = pd.Series([False] * len(df_clean_exploded), index=df_clean_exploded.index)
            if {"class_method_call", "file_name", "__method_only"}.issubset(df_clean_exploded.columns):
                _protected_mask = pd.Series(
                    (
                        _is_imported_project_getter_call(_cmc, _file, _m)
                        for _cmc, _file, _m in zip(
                            df_clean_exploded["class_method_call"].tolist(),
                            df_clean_exploded["file_name"].tolist(),
                            df_clean_exploded["__method_only"].tolist(),
                        )
                    ),
                    index=df_clean_exploded.index,
                )
            df_clean_exploded = df_clean_exploded[
                ~(_builtin_mask & ~_protected_mask)
            ].drop(columns="__method_only")
        else:
            df_clean_exploded = df_clean_exploded.drop(columns="__method_only")
        _debug_entry_stage(df_clean_exploded, "S4_after_final_system_filter", call_col="class_method_call")

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
            s = str(class_method_call).strip()
            m = re.match(r'^\s*(.*)\.([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*$', s)
            if not m:
                return None
            owner = m.group(1).strip()
            if not owner:
                return None
            owner = owner.replace('\\', '/')
            owner_tail = owner.rsplit('/', 1)[-1]
            if '.' in owner_tail:
                owner_tail = owner_tail.rsplit('.', 1)[-1]
            return owner_tail or None

        def has_existing_owner_path(class_method_call):
            """True when call owner is already a concrete on-disk source path."""
            if not isinstance(class_method_call, str):
                return False
            s = str(class_method_call).strip()
            m = re.match(r'^\s*(.*)\.([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*$', s)
            if not m:
                return False
            owner = (m.group(1) or "").strip()
            if not owner:
                return False
            if "/" not in owner and "\\" not in owner:
                return False
            owner_java = owner if owner.lower().endswith(adapter.file_extension()) else (owner + adapter.file_extension())
            cached = _owner_path_exists_cache.get(owner_java)
            if cached is not None:
                return cached
            exists = os.path.isfile(owner_java)
            _owner_path_exists_cache[owner_java] = exists
            return exists

        def is_unenriched_simple_class_call(class_method_call):
            """True for unresolved simple owners like SearchCriteria.setX().

            These calls should survive pre-enrichment external-import filtering so
            later path resolution can disambiguate duplicate simple type names.
            """
            if not isinstance(class_method_call, str):
                return False
            s = str(class_method_call).strip()
            return bool(
                re.match(
                    r'^\s*[A-Z][A-Za-z0-9_$]*\.[A-Za-z_][A-Za-z0-9_]*\s*(?:\([^)]*\))?\s*$',
                    s,
                )
            )

        def is_project_owned_base(base_class):
            """
            True when base_class is resolvable to a source file in the current
            project scan (including Impl aliases and method-return index types).
            """
            if not isinstance(base_class, str):
                return False
            b = base_class.strip()
            if not b:
                return False
            return b in _project_owned_types

        user_prefix = details.get("user_defined_generic_import", "")
        _owner_path_exists_cache = {}
        _project_owned_types = set(_class_to_paths.keys()) | set(method_return_index.keys())
        _project_owned_types.update(
            k[:-4] for k in _class_to_paths.keys()
            if isinstance(k, str) and k.endswith("Impl") and len(k) > 4
        )
        _project_owned_types.update(
            k[:-len("Implementation")] for k in _class_to_paths.keys()
            if isinstance(k, str) and k.endswith("Implementation") and len(k) > len("Implementation")
        )
        external_import_classes = collect_external_import_classes(
            java_files,
            user_prefix
        )

        df_clean_exploded["__base_class"] = df_clean_exploded["class_method_call"].apply(
            extract_base_class
        )

        if remove_builtin_calls:
            _is_external = df_clean_exploded["__base_class"].isin(external_import_classes)
            _is_project_owned = df_clean_exploded["__base_class"].apply(is_project_owned_base)
            _has_owner_path = df_clean_exploded["class_method_call"].apply(has_existing_owner_path)
            _is_unenriched_simple = df_clean_exploded["class_method_call"].apply(is_unenriched_simple_class_call)
            df_clean_exploded = df_clean_exploded[
                ~(_is_external & ~_is_project_owned & ~_has_owner_path & ~_is_unenriched_simple)
            ].drop(columns="__base_class")
        else:
            df_clean_exploded = df_clean_exploded.drop(columns="__base_class")
        _debug_entry_stage(df_clean_exploded, "S5_after_external_filter_pass1", call_col="class_method_call")

        # Optional focused debug target method name.
        _debug_method_name = (
            os.environ.get("LINEAGE_DEBUG_METHOD_NAME")
            or details.get("debug_method_name")
            or ""
        )
        _debug_method_name = str(_debug_method_name).strip()

        # --- Enforce: if Class.method exists, drop object.method for the same call ---
        def _split_base_method(cmc):
            s = str(cmc or "").strip()
            m = re.match(
                r'^\s*(.*)\.([A-Za-z_]\w*)\s*\(?\s*\)?\s*$',
                s
            )
            if not m:
                return None, None
            return m.group(1).strip(), m.group(2)

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

        def _owner_key_norm(base):
            if not isinstance(base, str):
                return base
            s = base.strip().replace('\\', '/')
            if not s:
                return s
            tail = s.rsplit('/', 1)[-1]
            if '.' in tail:
                tail = tail.rsplit('.', 1)[-1]
            return tail

        df_valid['__upper_base'] = df_valid['__base'].apply(_owner_key_norm)

        class_rows = df_valid[
            df_valid["__upper_base"].astype(str).str[:1].str.isupper().fillna(False)
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
            df_valid["__upper_base"]
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
        if {"__row_order", "__segment_order"}.issubset(df_clean_exploded.columns):
            df_clean_exploded = df_clean_exploded.sort_values(
                ["__row_order", "__segment_order"],
                kind="mergesort",
            ).reset_index(drop=True)
        _debug_entry_stage(df_clean_exploded, "S6_after_lowercase_owner_dedupe", call_col="class_method_call")

        df_clean_exploded = df_clean_exploded.drop_duplicates(
            subset=['file_name', 'class_interface_name', 'method_name', 'class_method_call']
        )

        # FINAL FILTER â€” DROP NON-USER-DEFINED IMPORT CALLS (second pass)
        # Reuse external_import_classes calculated above. Do not scan the
        # complete application folder for a second time.
        df_clean_exploded["__base_class"] = (
            df_clean_exploded["class_method_call"]
            .astype(str)
            .apply(extract_base_class)
        )

        if remove_builtin_calls:
            _is_external = df_clean_exploded["__base_class"].isin(external_import_classes)
            _is_project_owned = df_clean_exploded["__base_class"].apply(is_project_owned_base)
            _has_owner_path = df_clean_exploded["class_method_call"].apply(has_existing_owner_path)
            _is_unenriched_simple = df_clean_exploded["class_method_call"].apply(is_unenriched_simple_class_call)
            df_clean_exploded = df_clean_exploded[
                ~(_is_external & ~_is_project_owned & ~_has_owner_path & ~_is_unenriched_simple)
            ].drop(columns="__base_class")
        else:
            df_clean_exploded = df_clean_exploded.drop(columns="__base_class")
        _debug_entry_stage(df_clean_exploded, "S7_after_external_filter_pass2", call_col="class_method_call")

        # Drop synthetic self-anchor rows like Class.init -> Class.init that are
        # introduced by owner-view expansion and do not represent a real callee.
        def _cm_owner_member_simple(_cmc):
            s = str(_cmc or "").strip()
            if not s:
                return "", ""
            m = re.match(r'^\s*(.*)\.([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*$', s)
            if not m:
                return "", ""
            owner = str(m.group(1) or "").strip().replace('\\\\', '/')
            member = str(m.group(2) or "").strip()
            owner_tail = owner.rsplit('/', 1)[-1]
            owner_simple = owner_tail.rsplit('.', 1)[-1] if '.' in owner_tail else owner_tail
            return owner_simple, member

        def _caller_class_simple(_class_iface):
            s = str(_class_iface or "").strip().replace('\\\\', '/')
            if not s:
                return ""
            tail = s.rsplit('/', 1)[-1]
            return tail.rsplit('.', 1)[-1] if '.' in tail else tail

        _owner_member_pairs = df_clean_exploded["class_method_call"].apply(_cm_owner_member_simple)
        _cm_owner_simple = _owner_member_pairs.apply(lambda x: str(x[0] or "").strip().lower())
        _cm_member = _owner_member_pairs.apply(lambda x: str(x[1] or "").strip().lower())
        _caller_simple = df_clean_exploded["class_interface_name"].apply(_caller_class_simple).astype(str).str.strip().str.lower()
        _method_name_norm = df_clean_exploded["method_name"].astype(str).str.strip().str.lower()

        _self_anchor_mask = (
            (_cm_owner_simple == _caller_simple)
            & (_cm_member == _method_name_norm)
            & _cm_member.ne("")
        )

        _type_empty_mask = (
            df_clean_exploded["type"].isna()
            | df_clean_exploded["type"].astype(str).str.strip().str.lower().isin({"", "none", "nan"})
        )
        _obj_empty_mask = (
            df_clean_exploded["object_call"].isna()
            | df_clean_exploded["object_call"].astype(str).str.strip().str.lower().isin({"", "none", "nan"})
        )

        _synthetic_origin_mask = (
            df_clean_exploded.get("__synthetic_origin", pd.Series(index=df_clean_exploded.index, dtype=object))
            .astype(str)
            .str.strip()
            .str.len()
            .gt(0)
        )
        _drop_self_anchor = _self_anchor_mask & _synthetic_origin_mask
        if _verbose_debug and bool(_drop_self_anchor.any()):
            _entry_debug_print(
                "[DEBUG][SELF_ANCHOR_FILTER] dropped_rows={} synthetic_only=True broad_match_count={} empty_type_or_obj_count={}".format(
                    int(_drop_self_anchor.sum()),
                    int(_self_anchor_mask.sum()),
                    int((_type_empty_mask | _obj_empty_mask).sum()),
                )
            )
        if bool(_drop_self_anchor.any()):
            df_clean_exploded = df_clean_exploded[~_drop_self_anchor].copy()

        _pbar_goto(88, "Post-processing: preparing LOC...")

        # ============================================================
        # Callee collection from Cleaned_AST_Details
        # ============================================================
        callee_pairs = set()

        rx_qual = re.compile(r'^\s*(.*)\.([A-Za-z_]\w*)\s*\(\s*\)\s*$')
        rx_unq = re.compile(r'^\s*([A-Za-z_]\w*)\s*(?:\(\s*\))?\s*$')

        # Use to_dict("records") â€” 50â€“100Ã— faster than iterrows() on large DataFrames
        for row_x in df_clean_exploded[["class_method_call", "class_interface_name"]].to_dict("records"):
            cmc = str(row_x.get("class_method_call", "") or "").strip()
            parent_cls = str(row_x.get("class_interface_name", "") or "").strip()
            if not cmc:
                continue

            m = rx_qual.match(cmc)
            if m:
                cls = m.group(1).strip()
                cls = cls.replace('\\', '/')
                cls_tail = cls.rsplit('/', 1)[-1]
                if '.' in cls_tail:
                    cls = cls_tail.rsplit('.', 1)[-1]
                else:
                    cls = cls_tail
                mtd = m.group(2)
                if remove_builtin_calls and mtd.lower() in SYSTEM_METHODS:
                    continue
                callee_pairs.add((cls, mtd))
                continue

            m2 = rx_unq.match(cmc)
            if m2:
                mtd = m2.group(1)
                if remove_builtin_calls and mtd.lower() in SYSTEM_METHODS:
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

            Returns: dict of  simple_name -> [path1, path2, ...]
            """
            mapping = {}

            def _add(name, fpath):
                mapping.setdefault(name, [])
                if fpath not in mapping[name]:
                    mapping[name].append(fpath)

            declaration_types = (
                javalang.tree.ClassDeclaration,
                javalang.tree.InterfaceDeclaration,
                javalang.tree.EnumDeclaration,
            )

            _decl_re = re.compile(
                r'\b(?:class|interface|enum)\s+([A-Za-z_]\w*)',
                re.MULTILINE,
            )

            for fpath in source_files:
                # Ensure text cache exists even for files outside BFS set
                if fpath not in file_content_cache:
                    try:
                        _ = read_file_cached(fpath)
                    except Exception:
                        file_content_cache[fpath] = ""

                tree = raw_ast_cache.get(fpath)
                if tree is None:
                    try:
                        tree = parse_raw_ast_cached(fpath)
                    except Exception:
                        tree = False
                        raw_ast_cache[fpath] = tree

                if tree and tree is not False:
                    for _, decl in tree.filter(declaration_types):
                        name = getattr(decl, "name", None)
                        if not name:
                            continue
                        _add(name, fpath)
                        if name.endswith("Impl"):
                            _add(name[:-4], fpath)
                else:
                    # IMPORTANT: use cached text that was loaded above
                    text = file_content_cache.get(fpath, "")
                    for m in _decl_re.finditer(text):
                        name = m.group(1)
                        _add(name, fpath)
                        if name.endswith("Impl"):
                            _add(name[:-4], fpath)

            return mapping

        # Build type_to_path_full from ALL project files (not just BFS-reachable ones).
        # BFS may miss files that are callee targets not reachable from the seed
        # controllers. Those classes still appear in class_method_call and need
        # their file path resolved. Scanning by extension is fast; the function
        # already uses its regex fallback for files with no cached AST.
        _all_project_files = list(_all_project_files_early)

        type_to_path_full = build_type_to_path_including_nested(_all_project_files)

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
            _anno_arg = r'(?:\([^)]*\))'
            _anno_prefix = r'(?:@\w+' + _anno_arg + r'[ \t]*\n?[ \t]*)*'
            # Java 8: only class / interface / enum (no record)
            class_kw = r"(?:class|interface|enum)"
            class_regex = re.compile(
                r"(?m)^[ \t]*" + _anno_prefix +
                r"(?:public|protected|private)?[ \t]*" +
                r"(?:(?:abstract|final|static|strictfp|default)\s+)*" +
                class_kw + r"[ \t]+" + re.escape(classname) + r"\b"
            )
            class_match = class_regex.search(text)
            if not class_match:
                class_regex_fallback = re.compile(
                    _anno_prefix +
                    r"(?:public|protected|private)?[ \t]*" +
                    r"(?:(?:abstract|final|static|strictfp|default)\s+)*" +
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
                def _has_lombok_on_class():
                    class_header = text[class_match.start():class_open]
                    return bool(re.search(r'@(?:Getter|Setter)\b', class_header))

                def _decap_java_bean(name):
                    if not name:
                        return ""
                    if len(name) >= 2 and name[0].isupper() and name[1].isupper():
                        i = 0
                        n = len(name)
                        while i < n and name[i].isupper():
                            i += 1
                        if i > 1 and i < n and name[i].islower():
                            i -= 1
                        return name[:i].lower() + name[i:]
                    return name[0].lower() + name[1:]

                def _accessor_field_candidates(mname):
                    mname = str(mname or "").strip()
                    if not mname:
                        return []
                    stem = ""
                    if mname.startswith("get") and len(mname) > 3:
                        stem = mname[3:]
                    elif mname.startswith("set") and len(mname) > 3:
                        stem = mname[3:]
                    if not stem:
                        return []

                    cands = []
                    c1 = _decap_java_bean(stem)
                    cands.append(c1)
                    if stem not in cands:
                        cands.append(stem)
                    c2 = stem[:1].lower() + stem[1:] if stem else stem
                    if c2 and c2 not in cands:
                        cands.append(c2)
                    return [c for c in cands if c]

                def _find_field_block_loc_from_accessor(mname):
                    if not _has_lombok_on_class():
                        return None

                    arity = None
                    try:
                        arity = None if parameter_arity is None else int(parameter_arity)
                    except Exception:
                        arity = None

                    if mname.startswith("set"):
                        if arity not in (None, 1):
                            return None
                    elif mname.startswith("get") or mname.startswith("is"):
                        if arity not in (None, 0):
                            return None
                    else:
                        return None

                    field_names = _accessor_field_candidates(mname)
                    if not field_names:
                        return None

                    field_alt = "|".join(re.escape(n) for n in field_names)
                    if not field_alt:
                        return None

                    field_pat = re.compile(
                        r'(?m)^[ \t]*(?:(?:public|private|protected)\s+)?'
                        r'(?:(?:static|final|transient|volatile)\s+)*'
                        r'[A-Za-z_][\w$.<>,\[\]? \t]*\s+'
                        r'(' + field_alt + r')\s*(?:=|;)',
                        re.IGNORECASE,
                    )

                    fm = field_pat.search(class_block)
                    if not fm:
                        return None

                    field_global_start = class_block_global_start + fm.start()
                    field_line_idx = text.count("\n", 0, field_global_start) + 1

                    def _field_anno_block_start(sig_idx):
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

                    start_line_idx = _field_anno_block_start(field_line_idx) or field_line_idx
                    end_line_idx = field_line_idx
                    if count_empty_lines:
                        return max(1, end_line_idx - start_line_idx + 1)
                    segment = lines[start_line_idx - 1:end_line_idx]
                    return max(1, sum(1 for ln in segment if ln.strip()))

                def _make_interface_method_regex(mname):
                    anno_arg = r'(?:\([^)]*\))'
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

                lombok_loc = _find_field_block_loc_from_accessor(methodname)
                if lombok_loc is not None:
                    if line_cache is not None:
                        line_cache[cache_key] = lombok_loc
                    return lombok_loc

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
                    while i >= 0:
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

            if remove_builtin_calls and methodname.lower() in SYSTEM_METHODS:
                return None

            # After enrichment, class_interface_name is a path WITHOUT extension
            # e.g. "/abs/path_1/Order" or "path_2/Payment".
            # Detect by presence of a path separator.
            if os.sep in classname or "/" in classname:
                # Re-attach the source extension to get the actual file path
                extension = details.get("extension", [".java"])[0]
                java_file_path = classname + extension
                # Simple class name is the final component (stem)
                classname = os.path.basename(classname)
            else:
                candidates = type_to_path_full.get(classname, [])
                java_file_path = candidates[0] if candidates else None

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

        # Parallelise LOC computation â€” each call is independent and I/O-bound
        # (file reads hit the in-process cache after the first access).
        _unique_rows = [
            row for row in df_unique_methods.to_dict("records")
            if row["class_method_key"] not in loc_lookup
        ]

        def _compute_loc(row):
            return row["class_method_key"], extract_loc_any(row)

        _pbar.set_postfix_str(f"Computing LOC for {len(_unique_rows)} methods...")
        _loc_workers = min(8, (multiprocessing.cpu_count() or 4))
        _loc_total = max(len(_unique_rows), 1)
        _loc_done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=_loc_workers) as _loc_pool:
            for _key, _val in _loc_pool.map(_compute_loc, _unique_rows):
                loc_lookup.setdefault(_key, _val)
                _loc_done += 1
                _maybe_refresh_pbar()
                _target = 75 + int(_loc_done / _loc_total * 15)
                _pbar_goto(_target, f"LOC: {_loc_done}/{_loc_total} methods")

        # â”€â”€ Checkpoint 90% â”€â”€
        _pbar_goto(90, "LOC done")

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

        # ============================================================
        # IMPORT-BASED PATH RESOLUTION
        # ============================================================
        # type_to_path_full now maps  ClassName -> [path1, path2, ...]
        # For disambiguation we need two more indexes:
        #   fqn_to_path   : "com.example.OrderService" -> "/abs/path/OrderService.java"
        #   file_to_imports: "/abs/caller.java"        -> {"OrderService": "com.example.OrderService"}
        # ============================================================

        _import_re = re.compile(
            r'^\s*import\s+(?:static\s+)?([\w.*]+)\s*;',
            re.MULTILINE
        )
        _pkg_re = re.compile(r'^\s*package\s+([\w.]+)\s*;', re.MULTILINE)

        def _read_cached(fpath):
            text = file_content_cache.get(fpath)
            if text is None:
                try:
                    with open(fpath, "r", encoding="utf-8") as _fh:
                        text = _fh.read()
                except UnicodeDecodeError:
                    try:
                        with open(fpath, "r", encoding="latin-1") as _fh:
                            text = _fh.read()
                    except Exception:
                        text = ""
                except Exception:
                    text = ""
                file_content_cache[fpath] = text
            return text or ""

        # ----- Reuse prebuilt indexes from earlier stage (avoid rescanning) -----
        fqn_to_path = {}
        for _fqn, _paths in (_fqn_to_paths_early or {}).items():
            if _paths:
                fqn_to_path.setdefault(_fqn, _paths[0])

        file_to_imports = dict(_file_to_imports_early or {})
        file_to_wildcards = dict(_file_to_wildcards_early or {})

        _fi_lower = {os.path.normcase(os.path.abspath(k)): v for k, v in file_to_imports.items()}
        _fw_lower = {os.path.normcase(os.path.abspath(k)): v for k, v in file_to_wildcards.items()}
        _fc_lower = {
            os.path.normcase(os.path.abspath(k)): v
            for k, v in file_content_cache.items()
            if isinstance(k, str)
        }

        project_fqn_to_paths = {
            _fqn: list(_paths)
            for _fqn, _paths in (_fqn_to_paths_early or {}).items()
        }

        _simple_to_paths_ci = {}
        for _simple, _paths in type_to_path_full.items():
            _simple_to_paths_ci.setdefault(str(_simple).lower(), [])
            for _p in _paths:
                if _p not in _simple_to_paths_ci[str(_simple).lower()]:
                    _simple_to_paths_ci[str(_simple).lower()].append(_p)

        # Fallback index from real file stems across the full scanned project.
        # This catches cases where type extraction missed a declaration but the
        # source file still exists (including generated-sources trees).
        _stem_to_paths_ci = {}
        for _p in _all_project_files:
            _stem = os.path.splitext(os.path.basename(_p))[0].lower()
            _stem_to_paths_ci.setdefault(_stem, [])
            if _p not in _stem_to_paths_ci[_stem]:
                _stem_to_paths_ci[_stem].append(_p)

        def _iter_candidate_paths(simple_name):
            if not simple_name:
                return []
            s = str(simple_name).strip()
            candidates = list(type_to_path_full.get(s, []))
            for _p in _simple_to_paths_ci.get(s.lower(), []):
                if _p not in candidates:
                    candidates.append(_p)
            # Always include stem matches so duplicates are considered even when
            # type_to_path_full kept only one winner for a simple class name.
            for _p in _stem_to_paths_ci.get(s.lower(), []):
                if _p not in candidates:
                    candidates.append(_p)

            # If adapter emits suffixed names (e.g. Foo1), also try canonical Foo.
            s_nosuffix = re.sub(r'\d+$', '', s)
            if s_nosuffix and s_nosuffix != s:
                for _p in type_to_path_full.get(s_nosuffix, []):
                    if _p not in candidates:
                        candidates.append(_p)
                for _p in _simple_to_paths_ci.get(s_nosuffix.lower(), []):
                    if _p not in candidates:
                        candidates.append(_p)
            return candidates

        _file_declares_member_cache = {}

        def _file_declares_member(file_path, member_name):
            if not file_path or not member_name:
                return False
            _cache_key = (
                os.path.normcase(os.path.abspath(str(file_path))),
                str(member_name).strip(),
            )
            _cached = _file_declares_member_cache.get(_cache_key)
            if _cached is not None:
                return _cached
            try:
                _txt = _read_cached(file_path)
            except Exception:
                _file_declares_member_cache[_cache_key] = False
                return False
            if not _txt:
                _file_declares_member_cache[_cache_key] = False
                return False
            _member_esc = re.escape(str(member_name).strip())
            # Consider only real method bodies, not interface signatures.
            _pat = re.compile(
                r'^[ \t]*(?:@\w+(?:\([^)]*\))?\s*)*'
                r'(?:(?:public|private|protected|static|final|abstract|synchronized|native|strictfp|default)\s+)*'
                r'(?:<[^>{;]+>\s*)?'
                r'(?:[A-Za-z_][\w$.]*(?:\s*<[^>{;]+>)?(?:\s*\[\s*\])*)\s+'
                + _member_esc +
                r'\s*\([^;{}]*\)\s*(?:throws\s+[^\{]+)?\{',
                re.MULTILINE,
            )
            if _pat.search(_txt):
                _file_declares_member_cache[_cache_key] = True
                return True

            # Constructor declarations: modifiers + ClassName(...)
            _ctor_pat = re.compile(
                r'^[ \t]*(?:@\w+(?:\([^)]*\))?\s*)*'
                r'(?:(?:public|private|protected)\s+)'
                + _member_esc +
                r'\s*\([^;{}]*\)\s*(?:throws\s+[^\{]+)?\{',
                re.MULTILINE,
            )
            _out = bool(_ctor_pat.search(_txt))
            _file_declares_member_cache[_cache_key] = _out
            return _out

        def _resolve_impl_path_for_member(simple_name, caller_file, member_name=None):
            if not simple_name:
                return None
            impl_name = iface_to_impl_map.get(simple_name)
            if not impl_name:
                return None

            impl_candidates = _iter_candidate_paths(impl_name)
            impl_best = _choose_best_candidate(impl_candidates, caller_file, member_name=member_name)
            if impl_best:
                return impl_best

            _suffix = ".{}".format(impl_name)
            _suffix_hits = [_fpath for _fqn, _fpath in fqn_to_path.items() if _fqn.endswith(_suffix)]
            impl_best = _choose_best_candidate(_suffix_hits, caller_file, member_name=member_name)
            if impl_best:
                return impl_best

            stem_hits = list(_stem_to_paths_ci.get(str(impl_name).lower(), []))
            return _choose_best_candidate(stem_hits, caller_file, member_name=member_name)

        def _resolve_same_folder_owner_for_member(base_path, caller_file, member_name=None):
            """
            Fallback for interfaces/abstract APIs: if no mapped impl is found,
            scan sibling source files under the same folder/package and pick the
            first concrete class that declares the requested member.
            """
            if not base_path or not member_name:
                return None
            try:
                base_abs = os.path.abspath(base_path)
                base_norm = os.path.normcase(base_abs)
                base_dir = os.path.dirname(base_abs)
                if not os.path.isdir(base_dir):
                    return None
            except Exception:
                return None

            _candidates = []
            try:
                for _name in os.listdir(base_dir):
                    _p = os.path.join(base_dir, _name)
                    if not os.path.isfile(_p):
                        continue
                    if os.path.normcase(os.path.abspath(_p)) == base_norm:
                        continue
                    if not any(_name.endswith(_ext) for _ext in valid_extensions):
                        continue
                    if _file_declares_member(_p, member_name):
                        _candidates.append(_p)
            except Exception:
                return None

            return _choose_best_candidate(_candidates, caller_file, member_name=member_name)

        def _choose_best_candidate(candidates, caller_file, member_name=None):
            if not candidates:
                return None
            if len(candidates) == 1:
                return candidates[0]

            caller_abs = os.path.normcase(os.path.abspath(caller_file)) if caller_file else None
            caller_dir = os.path.dirname(caller_abs) if caller_abs else None

            # Prefer candidates that likely declare the requested member.
            # This is a best-effort text check and avoids expensive reparsing.
            if member_name:
                _declared = []
                for _c in candidates:
                    if _file_declares_member(_c, member_name):
                        _declared.append(_c)
                if len(_declared) == 1:
                    return _declared[0]
                if _declared:
                    candidates = _declared

            # Prefer nearest file by common directory prefix with caller.
            if caller_dir:
                def _score(_p):
                    _p_norm = os.path.normcase(os.path.abspath(_p))
                    _main_bonus = 1 if ("{}src{}main{}java{}".format(os.sep, os.sep, os.sep, os.sep) in _p_norm + os.sep) else 0
                    _test_penalty = -1 if ("{}src{}test{}java{}".format(os.sep, os.sep, os.sep, os.sep) in _p_norm + os.sep) else 0
                    _generated_penalty = -2 if ("{}generated-sources{}".format(os.sep, os.sep) in _p_norm + os.sep) else 0
                    try:
                        _common = os.path.commonpath([caller_dir, _p_norm])
                        return (_main_bonus, _test_penalty, _generated_penalty, len(_common))
                    except Exception:
                        return (_main_bonus, _test_penalty, _generated_penalty, -1)
                candidates = sorted(candidates, key=_score, reverse=True)
            return candidates[0]

        def _resolve_fqn_path(fqn, caller_file, member_name=None):
            if not isinstance(fqn, str):
                return None
            fqn = fqn.strip()
            if not fqn:
                return None
            resolved = fqn_to_path.get(fqn)
            if resolved:
                return resolved
            candidates = list(project_fqn_to_paths.get(fqn, []))
            best = _choose_best_candidate(candidates, caller_file, member_name=member_name)
            if best:
                return best

            # Filesystem fallback for sibling modules not present in the
            # prebuilt index (for example: ../common/src/main/java/...)
            rel_java = os.path.join(*fqn.split(".")) + ".java"
            caller_abs = os.path.abspath(caller_file) if caller_file else ""
            src_marker = os.path.join("src", "main", "java")

            probe_roots = []
            if caller_abs:
                norm = os.path.normpath(caller_abs)
                marker_pos = norm.lower().find(src_marker.lower())
                if marker_pos != -1:
                    module_root = norm[:marker_pos].rstrip("\\/")
                    if module_root and module_root not in probe_roots:
                        probe_roots.append(module_root)
                    parent_root = os.path.dirname(module_root)
                    if parent_root and os.path.isdir(parent_root):
                        for sib in os.listdir(parent_root):
                            sib_root = os.path.join(parent_root, sib)
                            if os.path.isdir(sib_root) and sib_root not in probe_roots:
                                probe_roots.append(sib_root)

            for root in probe_roots:
                candidate = os.path.join(root, "src", "main", "java", rel_java)
                if os.path.isfile(candidate):
                    if member_name and not _file_declares_member(candidate, member_name):
                        continue
                    return candidate

            return None

        _resolve_class_path_cache = {}

        def _resolve_class_path(simple_name, caller_file, member_name=None):
            if not simple_name:
                return None
            _cache_key = (
                str(simple_name).strip(),
                os.path.normcase(os.path.abspath(str(caller_file))) if caller_file else "",
                str(member_name or "").strip(),
            )
            _cached = _resolve_class_path_cache.get(_cache_key)
            if _cached is not None:
                return _cached

            simple_name = strip_generics(str(simple_name)).strip()

            def _prefer_impl_when_member_missing(resolved_path):
                if not resolved_path:
                    return None
                if not member_name:
                    return resolved_path
                if _file_declares_member(resolved_path, member_name):
                    return resolved_path

                iface_key = simple_name.split(".")[-1] if "." in simple_name else simple_name
                impl_path = _resolve_impl_path_for_member(iface_key, caller_file, member_name=member_name)
                if impl_path and _file_declares_member(impl_path, member_name):
                    return impl_path

                resolved_stem = os.path.splitext(os.path.basename(resolved_path))[0]
                impl_path = _resolve_impl_path_for_member(resolved_stem, caller_file, member_name=member_name)
                if impl_path and _file_declares_member(impl_path, member_name):
                    return impl_path

                # Additional fallback: search for method implementation in classes
                # located in the same folder/package as the resolved declaration.
                sibling_owner = _resolve_same_folder_owner_for_member(
                    resolved_path,
                    caller_file,
                    member_name=member_name,
                )
                if sibling_owner:
                    return sibling_owner

                return resolved_path

            # New case: fully-qualified type provided directly
            if "." in simple_name and simple_name[0].islower():
                direct = _resolve_fqn_path(simple_name, caller_file, member_name=member_name)
                if direct:
                    # For explicit FQN owners, trust the FQN-targeted source file.
                    # Do not remap to sibling/impl classes by member heuristics.
                    _out = direct
                    _resolve_class_path_cache[_cache_key] = _out
                    return _out

            if "." in simple_name:
                simple_name = simple_name.split(".")[-1]

            _caller_norm = os.path.normcase(os.path.abspath(caller_file)) if caller_file else ""

            # Step 1: explicit import
            imp_map = _fi_lower.get(_caller_norm, {})
            # FIX: try the simple_name as-is first (exact match), then fall back to a
            # case-insensitive scan of the import map.  This covers the rare case where
            # the class name stored in var_map / object_class_map has slightly different
            # casing than the import statement (e.g. generated sources), and also guards
            # against future regressions if the simple_name was lower-cased upstream.
            fqn = imp_map.get(simple_name)
            if not fqn:
                _sn_lower = simple_name.lower()
                for _imp_key, _imp_fqn in imp_map.items():
                    if _imp_key.lower() == _sn_lower:
                        fqn = _imp_fqn
                        break
            if fqn:
                resolved = _resolve_fqn_path(fqn, caller_file, member_name=member_name)
                if resolved:
                    _out = _prefer_impl_when_member_missing(resolved)
                    _resolve_class_path_cache[_cache_key] = _out
                    return _out

            # Step 2: same package
            caller_text = _fc_lower.get(_caller_norm, "") or file_content_cache.get(caller_file, "")
            caller_pkg_m = _pkg_re.search(caller_text)
            if caller_pkg_m:
                caller_pkg = caller_pkg_m.group(1)
                same_pkg_fqn = "{}.{}".format(caller_pkg, simple_name)
                resolved = _resolve_fqn_path(same_pkg_fqn, caller_file, member_name=member_name)
                if resolved:
                    _out = _prefer_impl_when_member_missing(resolved)
                    _resolve_class_path_cache[_cache_key] = _out
                    return _out
            # same-directory-file check (same package no import needed)
            if caller_file:
                _caller_dir = os.path.dirname(os.path.abspath(caller_file))
                for _ext in valid_extensions:
                    _candidate_path = os.path.join(_caller_dir, "{}{}".format(simple_name, _ext))
                    if os.path.isfile(_candidate_path):
                        _out = _prefer_impl_when_member_missing(_candidate_path)
                        _resolve_class_path_cache[_cache_key] = _out
                        return _out

            # Step 3: wildcard imports
            for pkg in _fw_lower.get(_caller_norm, []):
                wfqn = "{}.{}".format(pkg, simple_name)
                resolved = _resolve_fqn_path(wfqn, caller_file, member_name=member_name)
                if resolved:
                    _out = _prefer_impl_when_member_missing(resolved)
                    _resolve_class_path_cache[_cache_key] = _out
                    return _out

            # Step 4: simple-name candidates + disambiguation.
            candidates = _iter_candidate_paths(simple_name)
            best = _choose_best_candidate(candidates, caller_file, member_name=member_name)
            if best:
                _out = _prefer_impl_when_member_missing(best)
                _resolve_class_path_cache[_cache_key] = _out
                return _out

            # Step 5: suffix scan fallback
            _suffix = ".{}".format(simple_name)
            _suffix_hits = [_fpath for _fqn, _fpath in fqn_to_path.items() if _fqn.endswith(_suffix)]
            best = _choose_best_candidate(_suffix_hits, caller_file, member_name=member_name)
            if best:
                _out = _prefer_impl_when_member_missing(best)
                _resolve_class_path_cache[_cache_key] = _out
                return _out

            # Step 6: filename-stem fallback (exact, case-insensitive)
            stem_hits = list(_stem_to_paths_ci.get(simple_name.lower(), []))

            # Also try canonicalized stem variants for suffixed class names
            # e.g. ExtendedFoo -> ExtendedFoo1.java or ExtendedFoo2.java.
            if not stem_hits:
                _raw = simple_name.lower()
                _nosuffix = re.sub(r'\d+$', '', _raw)
                for _stem, _paths in _stem_to_paths_ci.items():
                    if _stem == _raw or (_nosuffix and _stem == _nosuffix):
                        for _p in _paths:
                            if _p not in stem_hits:
                                stem_hits.append(_p)
                    elif _nosuffix and _stem.startswith(_nosuffix) and _stem[len(_nosuffix):].isdigit():
                        for _p in _paths:
                            if _p not in stem_hits:
                                stem_hits.append(_p)

            best = _choose_best_candidate(stem_hits, caller_file, member_name=member_name)
            if best:
                _out = _prefer_impl_when_member_missing(best)
                _resolve_class_path_cache[_cache_key] = _out
                return _out

            _resolve_class_path_cache[_cache_key] = None
            return None

        # Capture a dotted call root (var, class, or FQN) before the trailing member chain.
        # Using only the first token (e.g. "org") breaks FQN calls like
        # "org.apache.commons.CollectionUtils.isNotEmpty(...)".
        _base_class_re = re.compile(r'^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\.(.*)', re.DOTALL)
        _caller_varmap_cache = {}
        _caller_method_varmap_cache = {}

        def _canonical_param_type_name(type_name):
            if not isinstance(type_name, str):
                return ""
            _t = strip_generics(type_name).strip()
            if not _t:
                return ""
            _t = _t.replace("...", "").replace("[]", "").strip()
            # Keep package-qualified FQNs intact for overload discrimination.
            # Example: createSearchOptions(req.SearchOptions) vs
            # createSearchOptions(domain.SearchOptions) must not collapse to the
            # same canonical key 'searchoptions'.
            if "." in _t and _t[0].islower():
                return _t.lower()
            if "." in _t:
                _t = _t.split(".")[-1]
            return _t.lower()

        def _normalize_param_types_hint(param_types_hint):
            if not isinstance(param_types_hint, str) or not param_types_hint.strip():
                return ()
            return tuple(
                _canonical_param_type_name(_p)
                for _p in param_types_hint.split(";")
                if str(_p).strip()
            )

        def _get_method_scoped_var_map(caller_file, method_name, method_param_types=None):
            """
                        Build a var->type map from declarations inside a method named method_name
                        in caller_file.

                        Includes:
                            - method parameters
                            - method-local declarations (including enhanced-for loop variables)

                        For overloaded methods, prefer exact parameter-type match when
                        method_param_types is available.
            """
            if not caller_file or not method_name:
                return {}

            _caller_norm = os.path.normcase(os.path.abspath(caller_file))
            _hint_sig = _normalize_param_types_hint(method_param_types)
            _cache_key = (_caller_norm, str(method_name), _hint_sig)
            _cached = _caller_method_varmap_cache.get(_cache_key)
            if _cached is not None:
                return _cached

            caller_text = _fc_lower.get(_caller_norm, "") or file_content_cache.get(caller_file, "")
            if not caller_text:
                _caller_method_varmap_cache[_cache_key] = {}
                return {}

            out_map = {}
            candidates = []
            sig_pat = re.compile(r'\b' + re.escape(str(method_name)) + r'\s*\(')

            for _m in sig_pat.finditer(caller_text):
                _line_start = caller_text.rfind('\n', 0, _m.start()) + 1
                _prefix = caller_text[_line_start:_m.start()]
                if '.' in _prefix:
                    continue

                _open_idx = _m.end() - 1
                _depth = 0
                _close_idx = None
                for _i in range(_open_idx, len(caller_text)):
                    _ch = caller_text[_i]
                    if _ch == '(':
                        _depth += 1
                    elif _ch == ')':
                        _depth -= 1
                        if _depth == 0:
                            _close_idx = _i
                            break

                if _close_idx is None:
                    continue

                _params = caller_text[_open_idx + 1:_close_idx]

                # Find method body for method-local variable capture.
                _body_open = caller_text.find('{', _close_idx)
                _body_close = None
                if _body_open != -1:
                    _bdepth = 0
                    for _j in range(_body_open, len(caller_text)):
                        _cj = caller_text[_j]
                        if _cj == '{':
                            _bdepth += 1
                        elif _cj == '}':
                            _bdepth -= 1
                            if _bdepth == 0:
                                _body_close = _j
                                break

                try:
                    _param_map = _build_var_map(_params + ")") if _params is not None else {}
                    _candidate_sig = tuple(
                        _canonical_param_type_name(_t)
                        for _t in _param_map.values()
                    )

                    _local_map = {}
                    if _body_open != -1 and _body_close is not None and _body_close > _body_open:
                        _body_text = caller_text[_body_open + 1:_body_close]
                        _local_map = _build_var_map(_body_text)

                    _combined_map = dict(_param_map)
                    if _local_map:
                        _combined_map.update(_local_map)

                    if _combined_map or _param_map:
                        candidates.append((_candidate_sig, _combined_map))
                        # Do not let later overload candidates overwrite already
                        # captured variable types. When signature hints are missing
                        # or ambiguous, last-write-wins can map a variable from a
                        # different overload (e.g. Authorisation -> Authorisation1).
                        for _vn, _vt in _combined_map.items():
                            out_map.setdefault(_vn, _vt)
                except Exception:
                    continue

            # Overload-aware resolution: use exact signature first, then arity.
            if candidates and _hint_sig:
                for _sig, _map in candidates:
                    if _sig == _hint_sig:
                        _caller_method_varmap_cache[_cache_key] = _map
                        return _map

                for _sig, _map in candidates:
                    if len(_sig) == len(_hint_sig):
                        _caller_method_varmap_cache[_cache_key] = _map
                        return _map

            _caller_method_varmap_cache[_cache_key] = out_map
            return out_map

        def _strip_extension(path_str):
            if not isinstance(path_str, str):
                return path_str
            root, _ = os.path.splitext(path_str)
            return root

        def _get_import_fqn_for_simple(simple_name, caller_file=None):
            """Return imported FQN for a simple class name in caller context."""
            if not isinstance(simple_name, str):
                return None
            s = strip_generics(simple_name).strip()
            if not s:
                return None
            if "." in s:
                s = s.split(".")[-1]

            caller_norm = os.path.normcase(os.path.abspath(caller_file)) if caller_file else ""
            imp_map = _fi_lower.get(caller_norm, {}) if caller_norm else {}

            fqn = imp_map.get(s)
            if fqn:
                return fqn

            s_lower = s.lower()
            for imp_simple, imp_fqn in imp_map.items():
                if isinstance(imp_simple, str) and imp_simple.lower() == s_lower:
                    return imp_fqn
            return None

        def _resolve_static_import_owner_for_member(member_name, caller_file):
            """Resolve unqualified static-imported member to owner class path.

            Supports:
              import static a.b.C.member;
              import static a.b.C.*;
            """
            if not isinstance(member_name, str) or not member_name.strip() or not caller_file:
                return None

            mname = member_name.strip()
            caller_norm = os.path.normcase(os.path.abspath(caller_file))
            imp_map = _fi_lower.get(caller_norm, {})

            # Exact static member import: key is method name, value is ownerFqn.member
            imp_fqn = imp_map.get(mname)
            if isinstance(imp_fqn, str) and imp_fqn.count(".") >= 1:
                owner_fqn = imp_fqn.rsplit('.', 1)[0]
                owner_path = _resolve_fqn_path(owner_fqn, caller_file, member_name=mname)
                if owner_path and _file_declares_member(owner_path, mname):
                    return owner_path

            # Static wildcard import: import static a.b.C.*;
            for wild in _fw_lower.get(caller_norm, []):
                if not isinstance(wild, str) or not wild.strip():
                    continue
                owner_path = _resolve_fqn_path(wild.strip(), caller_file, member_name=mname)
                if owner_path and _file_declares_member(owner_path, mname):
                    return owner_path

            return None

        def _enrich_call_with_path(
            call_str,
            caller_file,
            fallback_class_name=None,
            caller_method_name=None,
            caller_method_param_types=None,
            source_object_call=None,
        ):
            _dbg_file = os.path.basename(str(caller_file or ""))
            if not isinstance(call_str, str):
                return call_str

            def _get_super_dispatch_fallback(_caller_cls_name, _caller_method_name):
                if not _caller_cls_name or not _caller_method_name:
                    return None
                _cands = _super_dispatch_context.get((_caller_cls_name, _caller_method_name), set())
                return _choose_dispatch_target(_cands)

            _raw_call = call_str.strip()
            # Accept bare unqualified member tokens and unqualified calls with args,
            # e.g. getHash and getHash(payload, salt).
            _unq = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*(\(.*\))?$', _raw_call)
            if _unq and caller_file:
                _mname = _unq.group(1)
                _call_suffix = _unq.group(2) or ""
                _caller_cls = os.path.splitext(os.path.basename(caller_file))[0]

                # 1) If the caller class itself declares the method, keep caller owner.
                _caller_abs = os.path.abspath(caller_file)
                if _file_declares_member(_caller_abs, _mname):
                    return "{}.{}{}".format(_strip_extension(_caller_abs), _mname, _call_suffix)

                # 2) If caller does not declare it, prefer static-import owner.
                _static_owner_path = _resolve_static_import_owner_for_member(_mname, caller_file)
                if _static_owner_path:
                    return "{}.{}{}".format(_strip_extension(_static_owner_path), _mname, _call_suffix)

                _dispatch_fb = fallback_class_name or _get_super_dispatch_fallback(
                    _caller_cls,
                    caller_method_name,
                )

                _owner_unq = _resolve_class_for_method(
                    _caller_cls,
                    _mname,
                    fallback_class_name=_dispatch_fb,
                )
                # Accept caller/owner path only when the method truly exists there.
                if _method_exists_in_class(
                    _owner_unq,
                    _mname,
                    caller_file=caller_file,
                    fallback_class_name=_dispatch_fb,
                ):
                    _owner_unq_path = _resolve_class_path(_owner_unq, caller_file, member_name=_mname)
                    if _owner_unq_path:
                        return "{}.{}{}".format(_strip_extension(_owner_unq_path), _mname, _call_suffix)

                if _method_exists_in_class(_caller_cls, _mname, caller_file=caller_file):
                    return "{}.{}{}".format(_strip_extension(os.path.abspath(caller_file)), _mname, _call_suffix)

            m = _base_class_re.match(call_str.strip())
            if not m:
                return call_str

            cls_name = m.group(1)
            rest = m.group(2)
            member_name = rest.split(".")[0].strip() if rest else None
            if member_name:
                member_name = member_name.split("(")[0].strip()
            if not rest or not member_name or str(member_name).strip().lower() == "none":
                return "None"

            if cls_name and (
                cls_name[0].isupper()
                or ('.' in cls_name and cls_name[0].islower())
            ):
                # Strict FQN-owner rule:
                # when owner is explicitly package-qualified, resolve by FQN path only.
                if "." in cls_name and cls_name[0].islower():
                    _fqn_owner_path = _resolve_class_path(cls_name, caller_file, member_name=member_name)
                    if _fqn_owner_path:
                        return "{}.{}".format(_strip_extension(_fqn_owner_path), rest)
                    return "{}.{}".format(cls_name, rest)

                _caller_norm_uc = os.path.normcase(os.path.abspath(caller_file)) if caller_file else ""
                _imp_map_uc = _fi_lower.get(_caller_norm_uc, {})
                _imp_fqn = _imp_map_uc.get(cls_name)
                _is_ctor_call = (member_name == cls_name)

                # Strict precedence: when method signature carries a package-
                # qualified type with this simple class name, it must outrank
                # conflicting imports of the same simple class.
                _sig_fqn_hits = []
                if isinstance(caller_method_param_types, str) and caller_method_param_types.strip():
                    for _pt in caller_method_param_types.split(";"):
                        _ptc = strip_generics(str(_pt or "")).strip().replace("...", "").replace("[]", "")
                        if not _ptc:
                            continue
                        if "." in _ptc and _ptc[0].islower() and _ptc.split(".")[-1] == cls_name:
                            if _ptc not in _sig_fqn_hits:
                                _sig_fqn_hits.append(_ptc)
                if _sig_fqn_hits:
                    _sig_paths = []
                    for _sfqn in _sig_fqn_hits:
                        _sp = _resolve_fqn_path(_sfqn, caller_file, member_name=member_name)
                        if not _sp:
                            _sp = _resolve_fqn_path(_sfqn, caller_file, member_name=None)
                        if _sp:
                            _sig_paths.append(_sp)
                    if len(_sig_paths) == 1:
                        return "{}.{}".format(_strip_extension(_sig_paths[0]), rest)
                    if len(_sig_paths) > 1:
                        _best_sig = _choose_best_candidate(_sig_paths, caller_file, member_name=member_name)
                        if _best_sig:
                            return "{}.{}".format(_strip_extension(_best_sig), rest)
                    # Keep method-signature FQN authoritative even when the
                    # concrete file path is unavailable in this run.
                    return "{}.{}".format(_sig_fqn_hits[0], rest)

                # Constructor call (ClassName.ClassName()): use explicit import first
                # to disambiguate duplicate simple names across modules.
                if _imp_fqn and _is_ctor_call:
                    _ctor_from_import = _resolve_fqn_path(_imp_fqn, caller_file, member_name=member_name)
                    if _ctor_from_import:
                        return "{}.{}".format(_strip_extension(_ctor_from_import), rest)

                # For explicit class owners, prefer the imported type path and do
                # not drift to another same-name class when member lookup is ambiguous
                # or implicit (e.g. Lombok-generated accessors).
                if _imp_fqn and not _is_ctor_call:
                    _owner_from_import = _resolve_fqn_path(_imp_fqn, caller_file, member_name=member_name)
                    if not _owner_from_import:
                        _owner_from_import = _resolve_fqn_path(_imp_fqn, caller_file, member_name=None)
                    if _owner_from_import:
                        return "{}.{}".format(_strip_extension(_owner_from_import), rest)
                    return "{}.{}".format(_imp_fqn, rest)

                # If import is absent, prefer caller package/same-scope resolution
                # before any global duplicate-name heuristics.
                if _is_ctor_call:
                    _ctor_scoped = _resolve_class_path(cls_name, caller_file, member_name=member_name)
                    if _ctor_scoped:
                        return "{}.{}".format(_strip_extension(_ctor_scoped), rest)

                _caller_cls_uc = os.path.splitext(os.path.basename(caller_file))[0] if caller_file else ""
                _dispatch_fb_uc = fallback_class_name or _get_super_dispatch_fallback(
                    _caller_cls_uc,
                    caller_method_name,
                )

                _vmap_method_uc = _get_method_scoped_var_map(
                    caller_file,
                    caller_method_name,
                    caller_method_param_types,
                )
                _vmap_uc = _caller_varmap_cache.get(_caller_norm_uc)
                if _vmap_uc is None:
                    _caller_text_uc = _fc_lower.get(_caller_norm_uc, "") or file_content_cache.get(caller_file, "")
                    _vmap_uc = _build_var_map(_caller_text_uc or "")
                    _caller_varmap_cache[_caller_norm_uc] = _vmap_uc

                if _imp_fqn:
                    _imp_resolved = _resolve_fqn_path(_imp_fqn, caller_file, member_name=member_name)
                    if _imp_resolved:
                        return "{}.{}".format(_strip_extension(_imp_resolved), rest)

                # If the method is actually implemented on a concrete owner
                # (e.g. Page -> PageImpl), prefer resolving that owner first.
                _parent_uc = method_return_index.get(_caller_cls_uc, {}).get("__extends__") if _caller_cls_uc else None
                _keep_explicit_super_owner = (
                    bool(member_name)
                    and isinstance(_parent_uc, str)
                    and strip_generics(_parent_uc) == strip_generics(cls_name)
                )

                if not _keep_explicit_super_owner:
                    _owner_hint = (
                        _resolve_class_for_method(
                            cls_name,
                            member_name,
                            fallback_class_name=_dispatch_fb_uc,
                        )
                        if member_name
                        else cls_name
                    )
                    if _owner_hint and _owner_hint != cls_name:
                        _owner_resolved = _resolve_class_path(_owner_hint, caller_file, member_name=member_name)
                        if _owner_resolved:
                            return "{}.{}".format(_strip_extension(_owner_resolved), rest)

                # Explicit import should outrank any generic FQN hint scan from var_map.
                _import_fqn = _get_import_fqn_for_simple(cls_name, caller_file)
                if _import_fqn:
                    _import_resolved = _resolve_fqn_path(_import_fqn, caller_file, member_name=member_name)
                    if _import_resolved:
                        return "{}.{}".format(_strip_extension(_import_resolved), rest)

                resolved = _resolve_class_path(cls_name, caller_file, member_name=member_name)
                if resolved:
                    return "{}.{}".format(_strip_extension(resolved), rest)

                # If file-path resolution is unavailable (e.g. debug scan scope),
                # still promote simple Class.method to imported FQN.method.
                if _import_fqn:
                    return "{}.{}".format(_import_fqn, rest)

                return call_str

            # lowercase object variable -> resolve declared type first
            _caller_norm = os.path.normcase(os.path.abspath(caller_file)) if caller_file else ""
            caller_text = _fc_lower.get(_caller_norm, "") or file_content_cache.get(caller_file, "")
            var_map = _caller_varmap_cache.get(_caller_norm)
            if var_map is None:
                var_map = _build_var_map(caller_text or "")
                _caller_varmap_cache[_caller_norm] = var_map
            method_var_map = _get_method_scoped_var_map(
                caller_file,
                caller_method_name,
                caller_method_param_types,
            )

            # Strict rule for lowercase variable roots:
            # when a variable is declared in the current method/signature,
            # resolve owner from that declared type/import context immediately.
            _declared_owner_type = method_var_map.get(cls_name)
            if not _declared_owner_type:
                _declared_owner_type = var_map.get(cls_name)
            if isinstance(_declared_owner_type, str) and _declared_owner_type.strip():
                _declared_owner_type = strip_generics(_declared_owner_type).strip()
                if "." in _declared_owner_type and _declared_owner_type[0].islower():
                    _declared_owner_path = _resolve_fqn_path(
                        _declared_owner_type,
                        caller_file,
                        member_name=member_name,
                    )
                    if not _declared_owner_path:
                        _declared_owner_path = _resolve_fqn_path(
                            _declared_owner_type,
                            caller_file,
                            member_name=None,
                        )
                    if _declared_owner_path:
                        return "{}.{}".format(_strip_extension(_declared_owner_path), rest)
                    # STRICT FQN RULE:
                    # When declaration provides an explicit package-qualified
                    # type, never fall back to import/same-package/simple-name
                    # heuristics. Keep declared FQN owner authoritative.
                    return "{}.{}".format(_declared_owner_type, rest)
                else:
                    _declared_simple = _extract_class_from_mapped(_declared_owner_type)
                    if _declared_simple:
                        _declared_import_fqn = _get_import_fqn_for_simple(_declared_simple, caller_file)
                        if _declared_import_fqn:
                            _declared_owner_path = _resolve_fqn_path(
                                _declared_import_fqn,
                                caller_file,
                                member_name=member_name,
                            )
                            if not _declared_owner_path:
                                _declared_owner_path = _resolve_fqn_path(
                                    _declared_import_fqn,
                                    caller_file,
                                    member_name=None,
                                )
                            if _declared_owner_path:
                                return "{}.{}".format(_strip_extension(_declared_owner_path), rest)
                            # Keep import owner token authoritative even when source
                            # file/member discovery is incomplete in current scan.
                            return "{}.{}".format(_declared_import_fqn, rest)

                        _declared_owner_path = _resolve_class_path(
                            _declared_simple,
                            caller_file,
                            member_name=member_name,
                        )
                        if not _declared_owner_path:
                            _declared_owner_path = _resolve_class_path(
                                _declared_simple,
                                caller_file,
                                member_name=None,
                            )
                        if _declared_owner_path:
                            return "{}.{}".format(_strip_extension(_declared_owner_path), rest)

            # FIX: use normcase(abspath(caller_file)) for the scoped lookup so it
            # matches the key written by update_map in build_object_class_map.
            # Previously the lookup used caller_file.lower() (bare or relative path)
            # while the map was keyed on normcase(abspath(fpath)) â†’ scoped lookup
            # always missed, falling through to the ambiguous global key only.
            _ocm_scoped_key = os.path.normcase(os.path.abspath(caller_file)) if caller_file else ""
            mapped_cls = method_var_map.get(cls_name)
            if not mapped_cls:
                mapped_cls = object_class_map.get((_ocm_scoped_key, cls_name.lower()))
            # Method-scope parsing can miss some declarations (for example loop vars).
            # If the exact root is still unresolved, allow file-scope/object-map fallback
            # for the same token so calls do not leak as raw lowercase owners (e.g. org.*).
            if not mapped_cls:
                mapped_cls = var_map.get(cls_name)
            if not mapped_cls:
                mapped_cls = object_class_map.get(cls_name.lower())

            # Final lowercase-root recovery:
            # when the root token is still unresolved (or resolves to itself),
            # infer owner from in-scope declared types that actually define member_name.
            if (
                cls_name
                and cls_name[:1].islower()
                and member_name
                and (
                    not mapped_cls
                    or str(strip_generics(mapped_cls)).strip().lower() == cls_name.lower()
                )
            ):
                _cand_types = []
                _seen_cands = set()
                for _mv in list(method_var_map.values()) + list(var_map.values()):
                    if not isinstance(_mv, str) or not _mv.strip():
                        continue
                    _raw_t = strip_generics(_mv).strip()
                    if not _raw_t:
                        continue
                    if _raw_t not in _seen_cands:
                        _seen_cands.add(_raw_t)
                        _cand_types.append(_raw_t)

                _owner_hits = []
                for _ct in _cand_types:
                    _ct_simple = _extract_class_from_mapped(_ct)
                    _ct_member = _resolve_class_for_method(_ct_simple, member_name) if _ct_simple else None
                    _ct_path = _resolve_class_path(_ct, caller_file, member_name=member_name)
                    if not _ct_path and _ct_member:
                        _ct_path = _resolve_class_path(_ct_member, caller_file, member_name=member_name)

                    if _ct_path and _file_declares_member(_ct_path, member_name):
                        _hit = (_ct_path, _ct_member or _ct_simple or _ct)
                        if _hit not in _owner_hits:
                            _owner_hits.append(_hit)

                if len(_owner_hits) == 1:
                    _path_hit, _owner_hit = _owner_hits[0]
                    return "{}.{}".format(_strip_extension(_path_hit), rest)
                if len(_owner_hits) > 1:
                    _best = _choose_best_candidate([_p for _p, _ in _owner_hits], caller_file, member_name=member_name)
                    if _best:
                        return "{}.{}".format(_strip_extension(_best), rest)

            # Recovery path: if class_method_call root was degraded (e.g. "org")
            # but the original row source still has a real variable root
            # (e.g. "searchCriteria.setX(...)"), resolve using that source root.
            if not mapped_cls and isinstance(source_object_call, str):
                _src_m = _base_class_re.match(source_object_call.strip())
                _src_base = _src_m.group(1).strip() if _src_m else ""
                if _src_base and _src_base[:1].islower() and (_src_base != cls_name or not mapped_cls):
                    # If source root is already a dotted owner token (usually FQN),
                    # resolve it directly before treating it as a variable name.
                    # This fixes cases where a degraded root like "org" appears in
                    # class_method_call while source_object_call still carries
                    # "org.foo.Bar.method(...)".
                    if "." in _src_base:
                        _src_direct = _resolve_class_path(_src_base, caller_file, member_name=member_name)
                        if _src_direct:
                            return "{}.{}".format(_strip_extension(_src_direct), rest)
                        mapped_cls = _src_base
                    else:
                        mapped_cls = method_var_map.get(_src_base)
                        if not mapped_cls:
                            mapped_cls = object_class_map.get((_ocm_scoped_key, _src_base.lower()))
                        # Even when a method-scope map exists, that parser can miss
                        # some locals/params; allow file-level var_map as recovery for
                        # the exact source variable root that created this row.
                        if not mapped_cls:
                            mapped_cls = var_map.get(_src_base)
                        if not mapped_cls and not method_var_map:
                            mapped_cls = object_class_map.get(_src_base.lower())

            # if mapped_cls:
            #     mapped_cls = strip_generics(mapped_cls)
            #     # For dotted types like "OuterClass.InnerClass" the class that
            #     # owns the object is always the FIRST segment (class_1), not
            #     # the last.  e.g. final PaymentOrderSpec.PaymentOrderSpecBuilder
            #     # obj â†’ obj's class is PaymentOrderSpec, not PaymentOrderSpecBuilder.
            #     if "." in mapped_cls:
            #         mapped_cls = mapped_cls.split(".")[0]
            if mapped_cls:
                mapped_cls = strip_generics(mapped_cls)
                # For dotted types like "OuterClass.InnerClass" the class that
                # owns the object is always the FIRST segment (class_1), not
                # the last. e.g. final PaymentOrderSpec.PaymentOrderSpecBuilder
                # obj â†’ obj's class is PaymentOrderSpec, not PaymentOrderSpecBuilder.
                # But if it is a package-qualified FQN (first char is lowercase,
                # e.g. "nl.acme.schemas...FilterPayload"), keep it intact so
                # _resolve_class_path can resolve it through fqn_to_path directly.
                if "." in mapped_cls and not mapped_cls[0].islower():
                    mapped_cls = mapped_cls.split(".")[0]

                # Import-priority disambiguation:
                # If mapped_cls is a simple class name and caller explicitly imports
                # that class, resolve via the imported FQN first. This prevents
                # wrong picks when multiple modules declare the same simple name
                # (e.g. generated req.RequestDetails vs domain.RequestDetails).
                if (
                    isinstance(mapped_cls, str)
                    and mapped_cls
                    and mapped_cls[0].isupper()
                    and "." not in mapped_cls
                ):
                    _imp_map = _fi_lower.get(_caller_norm, {})
                    _imp_fqn = _imp_map.get(mapped_cls)
                    if _imp_fqn:
                        _imp_resolved = _resolve_fqn_path(_imp_fqn, caller_file, member_name=member_name)
                        if _imp_resolved:
                            return "{}.{}".format(_strip_extension(_imp_resolved), rest)
                        _imp_resolved_relaxed = _resolve_fqn_path(_imp_fqn, caller_file, member_name=None)
                        if _imp_resolved_relaxed:
                            return "{}.{}".format(_strip_extension(_imp_resolved_relaxed), rest)
                        return "{}.{}".format(_imp_fqn, rest)

            elif fallback_class_name:
                mapped_cls = fallback_class_name
            else:
                return call_str

            # resolved = _resolve_class_path(mapped_cls, caller_file, member_name=member_name)
            # if resolved:
            #     return "{}.{}".format(_strip_extension(resolved), rest)
            # return "{}.{}".format(mapped_cls, rest)
            resolved = _resolve_class_path(mapped_cls, caller_file, member_name=member_name)

            if resolved:
                _result = "{}.{}".format(_strip_extension(resolved), rest)
                return _result

            _fallback = "{}.{}".format(mapped_cls, rest)
            return _fallback

        _enrich_call_with_path_impl = _enrich_call_with_path
        _enrich_call_with_path_cache = {}
        _enrich_call_with_path_miss = object()

        def _enrich_call_with_path(
            call_str,
            caller_file,
            fallback_class_name=None,
            caller_method_name=None,
            caller_method_param_types=None,
            source_object_call=None,
        ):
            _key = (
                str(call_str or "").strip(),
                os.path.normcase(os.path.abspath(str(caller_file))) if caller_file else "",
                strip_generics(str(fallback_class_name or "")).strip(),
                str(caller_method_name or "").strip(),
                str(caller_method_param_types or "").strip(),
                str(source_object_call or "").strip(),
            )
            _cached = _enrich_call_with_path_cache.get(_key, _enrich_call_with_path_miss)
            if _cached is not _enrich_call_with_path_miss:
                return _cached

            _out = _enrich_call_with_path_impl(
                call_str,
                caller_file,
                fallback_class_name=fallback_class_name,
                caller_method_name=caller_method_name,
                caller_method_param_types=caller_method_param_types,
                source_object_call=source_object_call,
            )
            _enrich_call_with_path_cache[_key] = _out
            return _out

        # Apply row-wise (caller_file comes from the file_name column)
        _records = df_clean_exploded[
            [
                "file_name",
                "method_name",
                "Parameter_Types",
                "object_call",
                "class_method_call",
                "__source_object_call",
                "__row_order",
                "__segment_order",
            ]
        ].to_dict("records")

        _enriched_oc  = []
        _enriched_cmc = []

        def _member_token(_call):
            _s = str(_call or "").strip()
            if not _s or "." not in _s:
                return ""
            _tail = _s.rsplit(".", 1)[-1]
            return _tail.split("(", 1)[0].strip()

        def _is_invalid_none_call(_call):
            """True when call is empty/None or ends with an invalid '.None' member."""
            _s = str(_call or "").strip()
            if not _s:
                return True
            if _s.lower() == "none":
                return True
            _member = _member_token(_s)
            return bool(_member) and _member.lower() == "none"

        def _resolve_owner_from_source_var_call(_source_call, _target_member, _caller_file, _caller_method, _caller_param_types):
            """Resolve owner from source var.member(...) using declared var type context."""
            if not _target_member:
                return None

            _var = ""
            _src_member = ""
            if isinstance(_source_call, str) and _source_call.strip():
                _m = re.match(
                    r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\(',
                    _source_call.strip(),
                )
                if _m:
                    _var = (_m.group(1) or "").strip()
                    _src_member = (_m.group(2) or "").strip()
            # This resolver is only valid for explicit var.member(...) sources.
            # For unqualified calls (e.g. createPaymentTypeInformation(...)),
            # forcing a var-based owner can incorrectly remap to unrelated types.
            if not _src_member:
                return None
            if _src_member and _src_member != _target_member:
                return None

            # STRICT FQN DECLARATION RULE:
            # If the variable is explicitly declared with a package-qualified
            # type in the current method body, that FQN is authoritative and
            # must not be remapped via same-package/import/simple-name fallbacks.
            _method_text = ""
            _caller_text = ""
            if _caller_file and _caller_method:
                _caller_norm = os.path.normcase(os.path.abspath(_caller_file))
                _caller_text = _fc_lower.get(_caller_norm, "") or file_content_cache.get(_caller_file, "")
                if not _caller_text:
                    try:
                        _caller_text = read_file_cached(_caller_file)
                    except Exception:
                        _caller_text = ""
                if _caller_text:
                    _method_text = _extract_method_block(_caller_text, _caller_method)

            _fqn_decl_re = re.compile(
                r'\b([a-z_][A-Za-z0-9_]*(?:\.[a-z_][A-Za-z0-9_]*)*\.[A-Z][A-Za-z0-9_]*)\s+'
                + re.escape(_var)
                + r'\b'
            )

            def _resolve_declared_fqn_owner(_txt):
                if not _txt:
                    return None
                _dm = _fqn_decl_re.search(_txt)
                if not _dm:
                    return None
                _decl_fqn = strip_generics((_dm.group(1) or "").strip())
                if not _decl_fqn:
                    return None
                _p = _resolve_fqn_path(_decl_fqn, _caller_file, member_name=_target_member)
                if not _p:
                    _p = _resolve_fqn_path(_decl_fqn, _caller_file, member_name=None)
                return _p or _decl_fqn

            # 1) Preferred: current method body.
            _strict_owner = _resolve_declared_fqn_owner(_method_text)
            if _strict_owner:
                return _strict_owner

            # 1b) Recovery for degraded source roots (e.g. Originator.setX):
            # infer the lowercase variable that invokes this member inside the
            # current method and resolve owner from that variable declaration.
            if _method_text and (not _var or not _var[:1].islower()):
                _member_call_re = re.compile(
                    r'\b([a-z_][A-Za-z0-9_]*)\s*\.\s*' + re.escape(_target_member) + r'\s*\('
                )
                _vars_for_member = []
                for _mm in _member_call_re.finditer(_method_text):
                    _v = (_mm.group(1) or "").strip()
                    if _v and _v not in _vars_for_member:
                        _vars_for_member.append(_v)
                for _cand_var in _vars_for_member:
                    _cand_re = re.compile(
                        r'\b([a-z_][A-Za-z0-9_]*(?:\.[a-z_][A-Za-z0-9_]*)*\.[A-Z][A-Za-z0-9_]*)\s+'
                        + re.escape(_cand_var)
                        + r'\b'
                    )
                    _cm = _cand_re.search(_method_text)
                    if _cm:
                        _cand_fqn = strip_generics((_cm.group(1) or "").strip())
                        if _cand_fqn:
                            _p = _resolve_fqn_path(_cand_fqn, _caller_file, member_name=_target_member)
                            if not _p:
                                _p = _resolve_fqn_path(_cand_fqn, _caller_file, member_name=None)
                            return _p or _cand_fqn

            # 2) Fallback: around the exact source call in caller text. This
            # guards cases where method extraction misses due overload/parse drift.
            if _caller_text and _source_call:
                _idx = _caller_text.find(_source_call)
                if _idx >= 0:
                    _lo = max(0, _idx - 6000)
                    _hi = min(len(_caller_text), _idx + 2000)
                    _window = _caller_text[_lo:_hi]
                    _strict_owner = _resolve_declared_fqn_owner(_window)
                    if _strict_owner:
                        return _strict_owner

            # 3) Last fallback: full caller text.
            _strict_owner = _resolve_declared_fqn_owner(_caller_text)
            if _strict_owner:
                return _strict_owner

            _mvmap = _get_method_scoped_var_map(_caller_file, _caller_method, _caller_param_types)
            _caller_norm = os.path.normcase(os.path.abspath(_caller_file)) if _caller_file else ""
            _fvmap = _caller_varmap_cache.get(_caller_norm)
            if _fvmap is None:
                _ctext = _fc_lower.get(_caller_norm, "") or file_content_cache.get(_caller_file, "")
                _fvmap = _build_var_map(_ctext or "")
                _caller_varmap_cache[_caller_norm] = _fvmap

            if not _var or not _var[:1].islower():
                return None

            _decl_type = _mvmap.get(_var) or _fvmap.get(_var)
            if not isinstance(_decl_type, str) or not _decl_type.strip():
                return None
            _decl_type = strip_generics(_decl_type).strip()

            if "." in _decl_type and _decl_type[0].islower():
                _p = _resolve_fqn_path(_decl_type, _caller_file, member_name=_target_member)
                if not _p:
                    _p = _resolve_fqn_path(_decl_type, _caller_file, member_name=None)
                # Keep declared method-parameter FQN authoritative even when the
                # concrete file path is not currently indexed in this run.
                return _p or _decl_type

            _simple = _extract_class_from_mapped(_decl_type)
            if not _simple:
                return None

            _imp_fqn = _get_import_fqn_for_simple(_simple, _caller_file)
            if _imp_fqn:
                _p = _resolve_fqn_path(_imp_fqn, _caller_file, member_name=_target_member)
                if not _p:
                    _p = _resolve_fqn_path(_imp_fqn, _caller_file, member_name=None)
                if _p:
                    return _p

            _p = _resolve_class_path(_simple, _caller_file, member_name=_target_member)
            if not _p:
                _p = _resolve_class_path(_simple, _caller_file, member_name=None)
            return _p

        _segment_cmc_by_key = {}

        def _try_contextual_segment_path(_row_rec, _cmc_raw):
            """
            For exploded chain rows (segment_order > 0), reuse the previous
            segment's resolved owner file to resolve the current segment owner.
            This prevents ambiguous simple names (e.g. RequestDetails) from
            flipping to a different package during final enrichment.
            """
            try:
                _seg = int(_row_rec.get("__segment_order"))
                _rid = int(_row_rec.get("__row_order"))
            except Exception:
                return None

            if _seg <= 0:
                return None

            _prev_cmc = _segment_cmc_by_key.get((_rid, _seg - 1))
            if not isinstance(_prev_cmc, str) or not _prev_cmc.strip():
                return None

            _prev = _prev_cmc.strip()
            _mp = re.match(r'^(.*)\.([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*$', _prev)
            if not _mp:
                return None

            _prev_owner = _mp.group(1).strip()
            _prev_method = _mp.group(2).strip()
            if not _prev_owner or not _prev_method:
                return None
            if os.sep not in _prev_owner and "/" not in _prev_owner:
                return None

            _cur = str(_cmc_raw or "").strip()
            _mc = re.match(r'^(.*)\.([A-Za-z_]\w*\s*(?:\([^)]*\))?)\s*$', _cur)
            if not _mc:
                return None

            _cur_base = _mc.group(1).strip()
            _cur_rest = _mc.group(2).strip()
            _cur_member = _member_token(_cur)

            _prev_owner_file = _prev_owner + adapter.file_extension()
            _prev_owner_simple = os.path.basename(_prev_owner)
            _ret_type = _get_return_type(
                _prev_owner_simple,
                _prev_method,
                caller_file=_prev_owner_file,
            )
            if not _ret_type:
                return None

            _ret_simple = strip_generics(str(_ret_type).split('.')[-1])
            _target_simple = _ret_simple or _cur_base
            _cands = _resolve_type_paths_from_caller(_target_simple, _prev_owner_file)
            if not _cands:
                return None

            _best = _choose_best_candidate(_cands, _prev_owner_file, member_name=_cur_member) or _cands[0]
            return "{}.{}".format(_strip_extension(_best), _cur_rest)

        for _row in _records:
            _caller = str(_row.get("file_name") or "")
            _caller_method = str(_row.get("method_name") or "")
            _caller_param_types = str(_row.get("Parameter_Types") or "")
            _cmc    = str(_row.get("class_method_call") or "")
            _oc     = str(_row.get("object_call") or "")
            _src_oc = str(_row.get("__source_object_call") or _oc)

            # class_method_call â€” base may be UpperCamelCase (direct class ref)
            # or a lowercase variable name when _lookup_type fell back to the raw
            # token.  Pass no fallback here; object_class_map lookup handles it.
            # _cmc_enriched = _enrich_call_with_path(
            #     _cmc,
            #     _caller,
            #     caller_method_name=_caller_method,
            #     caller_method_param_types=_caller_param_types,
            #     source_object_call=_src_oc,
            # )
            # _cmc_final = _cmc_enriched if _cmc_enriched is not None else _cmc

            # For exploded chain rows, try previous-segment owner context first.
            _cmc_ctx = _try_contextual_segment_path(_row, _cmc)
            if _cmc_ctx:
                _cmc = _cmc_ctx

            # If class_method_call already contains a path separator it has already
            # been resolved by derive_chain_segments / BFS â€” re-enriching it would
            # corrupt the resolved path (the path's first segment gets mistaken for
            # a variable name).  Skip enrichment and keep it as-is.
            if os.sep in _cmc or (os.sep != "/" and "/" in _cmc):
                _cmc_final = "None" if _is_invalid_none_call(_cmc) else _cmc
            else:
                _cmc_enriched = _enrich_call_with_path(
                    _cmc,
                    _caller,
                    caller_method_name=_caller_method,
                    caller_method_param_types=_caller_param_types,
                    source_object_call=_src_oc,
                )
                _cmc_final = _cmc_enriched if _cmc_enriched is not None else _cmc
                if _is_invalid_none_call(_cmc_final):
                    _cmc_final = "None"

            # Final deterministic guard: if this row source is var.member(...),
            # bind owner to that variable's declared type context.
            _cmc_member = _member_token(_cmc_final)
            _owner_from_src_var = _resolve_owner_from_source_var_call(
                _src_oc,
                _cmc_member,
                _caller,
                _caller_method,
                _caller_param_types,
            )
            if _owner_from_src_var and isinstance(_cmc_final, str):
                _tail = _cmc_final.rsplit('.', 1)[-1].strip() if '.' in _cmc_final else ""
                if _tail:
                    _owner_render = _owner_from_src_var
                    if os.sep in str(_owner_from_src_var) or "/" in str(_owner_from_src_var):
                        _owner_render = _strip_extension(str(_owner_from_src_var))
                    _cmc_final = "{}.{}".format(_owner_render, _tail)

            # object_call â€” base may be lowercase variable name.
            # Use the resolved UpperCamelCase base from class_method_call as a
            # fallback hint so we reuse the same resolution without re-scanning.
            _oc_base = _oc.split(".")[0] if "." in _oc else ""
            if _oc_base and not _oc_base[0].isupper():
                # Resolve from the object root's own declaration/import context first.
                # This keeps different same-name types in one method separated
                # (e.g. req:domain.Requestor vs requestor:generated.InitiateRequestor).
                _oc_primary = None
                if os.sep in _oc or (os.sep != "/" and "/" in _oc):
                    _oc_primary = _oc
                else:
                    _oc_primary = _enrich_call_with_path(
                        _oc,
                        _caller,
                        caller_method_name=_caller_method,
                        caller_method_param_types=_caller_param_types,
                        source_object_call=_src_oc,
                    )

                # Try to borrow the class name that class_method_call resolved to.
                _cmc_cur = _cmc_final if isinstance(_cmc_final, str) else str(_cmc_final or "")
                _cm = re.match(r'^(.*)\.([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*$', _cmc_cur)
                _enriched_cmc_base = _cm.group(1).strip() if _cm else ""
                # _enriched_cmc_base may already be a path segment (contains os.sep)
                # so extract just the final stem if so.
                if os.sep in _enriched_cmc_base or "/" in _enriched_cmc_base:
                    _fallback = os.path.splitext(os.path.basename(_enriched_cmc_base))[0]
                else:
                    if "." in _enriched_cmc_base:
                        _tail = _enriched_cmc_base.rsplit(".", 1)[-1]
                    else:
                        _tail = _enriched_cmc_base
                    _fallback = _tail if _tail and _tail[0].isupper() else None
                # _oc_enriched = _enrich_call_with_path(
                #     _oc,
                #     _caller,
                #     fallback_class_name=_fallback,
                #     caller_method_name=_caller_method,
                #     caller_method_param_types=_caller_param_types,
                # )
                # _enriched_oc.append(_oc_enriched)

                _oc_primary_str = str(_oc_primary or "").strip()
                _primary_resolved = bool(_oc_primary_str) and (os.sep in _oc_primary_str or (os.sep != "/" and "/" in _oc_primary_str))
                if _primary_resolved and not _is_invalid_none_call(_oc_primary):
                    _oc_enriched = _oc_primary
                    _enriched_oc.append(_oc_enriched)
                elif os.sep in _oc or (os.sep != "/" and "/" in _oc):
                    _oc_enriched = "None" if _is_invalid_none_call(_oc) else _oc
                    _enriched_oc.append(_oc_enriched)
                else:
                    _oc_enriched = _enrich_call_with_path(
                        _oc,
                        _caller,
                        fallback_class_name=_fallback,
                        caller_method_name=_caller_method,
                        caller_method_param_types=_caller_param_types,
                        source_object_call=_src_oc,
                    )
                    if _is_invalid_none_call(_oc_enriched):
                        _oc_enriched = "None"
                    _enriched_oc.append(_oc_enriched)

                # If this call originates from a lowercase variable (parameter/local)
                # and both calls refer to the same member, trust object_call's
                # declared-type resolution (it preserves caller var-map FQN context).
                _oc_m = _member_token(_oc_enriched)
                _cmc_m = _member_token(_cmc_final)
                if _oc_m and _cmc_m and _oc_m == _cmc_m:
                    # Preserve strict source-var owner resolution (especially
                    # explicit FQN declarations) and do not let object_call
                    # fallback overwrite class_method_call owner.
                    if not _owner_from_src_var:
                        _cmc_final = _oc_enriched if _oc_enriched is not None else _cmc_final
            else:
                _oc_direct = _enrich_call_with_path(
                    _oc,
                    _caller,
                    caller_method_name=_caller_method,
                    caller_method_param_types=_caller_param_types,
                )
                _enriched_oc.append("None" if _is_invalid_none_call(_oc_direct) else _oc_direct)

            _enriched_cmc.append(_cmc_final)

            try:
                _rid = int(_row.get("__row_order"))
                _seg = int(_row.get("__segment_order"))
                _segment_cmc_by_key[(_rid, _seg)] = _cmc_final
            except Exception:
                pass

        # Preserve original caller file before remap so debug can inspect
        # both pre-remap and post-remap contexts.
        if "__source_file_name" not in df_clean_exploded.columns:
            df_clean_exploded["__source_file_name"] = df_clean_exploded["file_name"]

        # If a parent method is reached via super-dispatch from a unique subclass,
        # project the caller context to that subclass so lineage shows the concrete
        # caller class/method (e.g. Extended.build) instead of only Abstract.build.
        _remapped_file_names = []
        _dispatch_row_flags = []
        for _row in df_clean_exploded[["file_name", "method_name", "__source_object_call"]].to_dict("records"):
            _caller_path = str(_row.get("file_name") or "")
            _caller_method = str(_row.get("method_name") or "").strip()
            _src_oc = str(_row.get("__source_object_call") or "").strip()
            _caller_cls = os.path.splitext(os.path.basename(_caller_path))[0] if _caller_path else ""
            _dispatch_targets = _super_dispatch_context.get((_caller_cls, _caller_method), set())

            # Remap only for explicit parent-dispatch rows (e.g. super.build(...)
            # or adapter-normalized AbstractX.build(...)) where the invoked method
            # is the same as the caller method. Do not remap ordinary internal
            # calls inside the parent method body (e.g. addScalar, bindParameters).
            _is_explicit_parent_dispatch = False
            if _src_oc:
                _m_super = re.match(r'^\s*super\s*\.\s*([A-Za-z_]\w*)\s*(?:\(|$)', _src_oc)
                if _m_super and _m_super.group(1) == _caller_method:
                    _is_explicit_parent_dispatch = True
                else:
                    _m_parent = re.match(r'^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*(?:\(|$)', _src_oc)
                    if _m_parent:
                        _parent_of_caller = strip_generics(method_return_index.get(_caller_cls, {}).get("__extends__"))
                        _owner = strip_generics(_m_parent.group(1))
                        _meth = _m_parent.group(2)
                        if _owner == _parent_of_caller and _meth == _caller_method:
                            _is_explicit_parent_dispatch = True

            _target_cls = _choose_dispatch_target(_dispatch_targets)
            if _target_cls and _is_explicit_parent_dispatch:
                _target_path = _resolve_class_path(_target_cls, _caller_path)
                if _target_path:
                    _remapped_file_names.append(os.path.abspath(_target_path))
                    _dispatch_row_flags.append(True)
                    continue

            _remapped_file_names.append(_caller_path)
            _dispatch_row_flags.append(False)

        df_clean_exploded["file_name"] = _remapped_file_names
        df_clean_exploded["__is_dispatch_row"] = _dispatch_row_flags

        # class_interface_name = the caller's own class, whose file is already
        # known from file_name. No import resolution needed â€” just strip extension.
        df_clean_exploded["class_interface_name"] = (
            df_clean_exploded["file_name"].apply(_strip_extension)
        )
        df_clean_exploded["object_call"]          = _enriched_oc
        df_clean_exploded["class_method_call"]    = _enriched_cmc

        # Optional focused debug: print final (post-enrichment) class_method_call
        # values for a single method in the configured entry file.
        if _verbose_debug and _debug_probe_file and _debug_method_name:
            _entry_norm = _as_abs_norm(_debug_probe_file)
            _entry_base = os.path.basename(str(_debug_probe_file or "")).strip().lower()
            _entry_class = os.path.splitext(_entry_base)[0]

            def _path_base(p):
                return os.path.basename(str(p or "")).strip().lower()

            _method_mask = (
                df_clean_exploded["method_name"].astype(str).str.strip().str.lower()
                == str(_debug_method_name).strip().lower()
            )
            _remapped_file_mask_exact = (
                df_clean_exploded["file_name"].astype(str).apply(_as_abs_norm) == _entry_norm
            )
            _source_file_mask_exact = (
                df_clean_exploded["__source_file_name"].astype(str).apply(_as_abs_norm) == _entry_norm
            )

            # Cross-machine runs can preserve only file basenames while roots differ
            # (e.g. C:\\Users\\A\\... vs C:\\Users\\B\\...).
            _remapped_base_mask = (
                df_clean_exploded["file_name"].astype(str).apply(_path_base) == _entry_base
            )
            _source_base_mask = (
                df_clean_exploded["__source_file_name"].astype(str).apply(_path_base) == _entry_base
            )
            _class_mask = (
                df_clean_exploded["class_interface_name"].astype(str).str.strip().str.lower()
                == _entry_class
            )

            _has_exact_file_hit = bool((_remapped_file_mask_exact | _source_file_mask_exact).any())
            _remapped_file_mask = _remapped_file_mask_exact if _has_exact_file_hit else (_remapped_file_mask_exact | _remapped_base_mask)
            _source_file_mask = _source_file_mask_exact if _has_exact_file_hit else (_source_file_mask_exact | _source_base_mask)
            if _has_exact_file_hit:
                _class_mask = pd.Series([False] * len(df_clean_exploded), index=df_clean_exploded.index)

            _file_probe_mask = (
                _remapped_file_mask
                | _source_file_mask
                | _class_mask
            )

            _probe_df = df_clean_exploded[_method_mask & _file_probe_mask]

            _cmc_list = sorted(set(
                str(_v).strip()
                for _v in _probe_df["class_method_call"].dropna().tolist()
                if str(_v).strip()
            ))

            _entry_debug_print("\n[DEBUG][ENTRY_METHOD_CALLS] file:", _debug_probe_file)
            _entry_debug_print("[DEBUG][ENTRY_METHOD_CALLS] method_name:", _debug_method_name)
            _entry_debug_print("[DEBUG][ENTRY_METHOD_CALLS] method_only_count:", int(_method_mask.sum()))
            _entry_debug_print("[DEBUG][ENTRY_METHOD_CALLS] remapped_file_only_count:", int(_remapped_file_mask.sum()))
            _entry_debug_print("[DEBUG][ENTRY_METHOD_CALLS] source_file_only_count:", int(_source_file_mask.sum()))
            _entry_debug_print("[DEBUG][ENTRY_METHOD_CALLS] remapped_base_only_count:", int((_remapped_base_mask & (~_remapped_file_mask_exact)).sum()))
            _entry_debug_print("[DEBUG][ENTRY_METHOD_CALLS] source_base_only_count:", int((_source_base_mask & (~_source_file_mask_exact)).sum()))
            _entry_debug_print("[DEBUG][ENTRY_METHOD_CALLS] class_only_count:", int(_class_mask.sum()))
            _entry_debug_print("[DEBUG][ENTRY_METHOD_CALLS] row_count:", len(_probe_df))
            _entry_debug_print("[DEBUG][ENTRY_METHOD_CALLS] unique_class_method_call_count:", len(_cmc_list))
            for _idx, _cmc in enumerate(_cmc_list, start=1):
                _entry_debug_print("[DEBUG][ENTRY_METHOD_CALLS] {}. {}".format(_idx, _cmc))

        # Keep synthetic/source helper columns until export stage so we can
        # collapse caller/owner mirror rows for final Excel output only.

        # Prefer concrete implementation owners (e.g. PageImpl.method)
        # over interface-owner duplicates (e.g. Page.method) for the same
        # invocation signature in the same caller context.
        def _split_owner_member(call_str):
            s = str(call_str or "").strip()
            if not s:
                return "", ""
            # Capture the final ".member" token so owner can be a path or FQN.
            m = re.match(r'^(.*)\.([A-Za-z_]\w*)\s*(?:\(\s*\))?\s*$', s)
            if m:
                return m.group(1).strip(), m.group(2).strip()
            if "." not in s:
                return "", ""
            owner, _, tail = s.rpartition(".")
            member = tail.split("(", 1)[0].strip()
            return owner.strip(), member

        def _owner_simple_name(owner_str):
            s = str(owner_str or "").strip().replace("\\", "/")
            if not s:
                return ""
            stem = s.rsplit("/", 1)[-1]
            # If owner is package-qualified (nl.rabo...PageImpl), keep only final token.
            if "." in stem:
                stem = stem.rsplit(".", 1)[-1]
            return stem

        def _impl_root(simple_name):
            s = str(simple_name or "").strip()
            if not s:
                return ""
            return re.sub(r"(?i)(implementation|impl)$", "", s)

        _owner_member = df_clean_exploded["class_method_call"].apply(_split_owner_member)
        df_clean_exploded["__cm_owner"] = _owner_member.apply(lambda x: x[0])
        df_clean_exploded["__cm_member"] = _owner_member.apply(lambda x: x[1])
        df_clean_exploded["__cm_owner_simple"] = df_clean_exploded["__cm_owner"].apply(_owner_simple_name)
        df_clean_exploded["__cm_root"] = df_clean_exploded["__cm_owner_simple"].apply(_impl_root)
        df_clean_exploded["__cm_is_impl"] = df_clean_exploded["__cm_owner_simple"].astype(str).str.contains(
            r"(?i)(implementation|impl)$", regex=True
        )
        _project_owned_call_cache = {}

        def _is_project_owned_call(_call_str, _caller_file, _owner_simple=None, _member_name=None):
            """
            Keep only calls whose owner resolves to project source.
            This removes JDK/runtime/library calls (e.g., Enum.valueOf,
            Map.Entry.getKey/getValue) without hardcoding method names.
            """
            _cache_key = (
                str(_call_str or "").strip(),
                os.path.normcase(os.path.abspath(str(_caller_file))) if _caller_file else "",
                str(_owner_simple or "").strip(),
                str(_member_name or "").strip(),
            )
            _cached = _project_owned_call_cache.get(_cache_key)
            if _cached is not None:
                return _cached

            _call = str(_call_str or "").strip()
            if not _call or _call.lower() == "none":
                _project_owned_call_cache[_cache_key] = True
                return True

            _owner, _member = _split_owner_member(_call)
            if not _owner:
                _project_owned_call_cache[_cache_key] = False
                return False
            _owner = str(_owner).strip()
            _member_tok = str(_member_name or _member or "").strip() or None

            # Already path-enriched owner token (e.g. C:/repo/src/Foo)
            if "/" in _owner or "\\" in _owner:
                _owner_java_path = _owner if _owner.lower().endswith(adapter.file_extension()) else (_owner + adapter.file_extension())
                _out = os.path.isfile(_owner_java_path)
                _project_owned_call_cache[_cache_key] = _out
                return _out

            _simple = str(_owner_simple or _owner_simple_name(_owner)).strip()
            if not _simple:
                _project_owned_call_cache[_cache_key] = False
                return False

            # Strict project ownership rule:
            # keep only when a concrete source file path can be resolved.
            # Do not trust simple-name/index presence alone.
            _resolved = _resolve_class_path(_owner, _caller_file, member_name=_member_tok)
            if not _resolved and _simple:
                _resolved = _resolve_class_path(_simple, _caller_file, member_name=_member_tok)
            # For generated/Lombok-accessor methods, declaration probing can miss
            # method bodies; keep the call when owner path is project-resolvable
            # even if member-specific lookup fails.
            if not _resolved:
                _resolved = _resolve_class_path(_owner, _caller_file, member_name=None)
            if not _resolved and _simple:
                _resolved = _resolve_class_path(_simple, _caller_file, member_name=None)
            _out = bool(_resolved and os.path.isfile(_resolved))
            _project_owned_call_cache[_cache_key] = _out
            return _out

        _pref_key = [
            "file_name",
            "method_name",
            "__cm_member",
            "__cm_root",
        ]
        _has_impl = df_clean_exploded.groupby(_pref_key, dropna=False)["__cm_is_impl"].transform("any")
        _drop_iface_dupe = (
            _has_impl
            & (~df_clean_exploded["__cm_is_impl"])
            & df_clean_exploded["__cm_root"].astype(bool)
            & (~df_clean_exploded["__is_dispatch_row"].fillna(False))
        )
        df_clean_exploded = df_clean_exploded.loc[~_drop_iface_dupe].copy()

        # Drop self-owner duplicates when the same caller method has a
        # concrete non-self owner for the same member.
        # Example:
        #   keep  MultiSortPagingContextValidator.setMaxPageSize
        #   drop  ExtendedQueryPaymentOrderRequestValidator.setMaxPageSize
        df_clean_exploded["__caller_simple"] = (
            df_clean_exploded["class_interface_name"]
            .astype(str)
            .apply(lambda x: os.path.basename(str(x).replace('\\', '/')).strip())
        )
        df_clean_exploded["__is_self_owner"] = (
            df_clean_exploded["__cm_owner_simple"].astype(str).str.strip()
            == df_clean_exploded["__caller_simple"].astype(str).str.strip()
        )

        def _is_qualified_non_self_owner(_owner_raw):
            """True when non-self owner looks like a concrete class/path owner."""
            s = str(_owner_raw or "").strip()
            if not s:
                return False
            if "/" in s or "\\" in s:
                return True
            # FQN/class-like owners are acceptable alternatives.
            if s[:1].isupper():
                return True
            if "." in s and s.split(".")[-1][:1].isupper():
                return True
            # Dynamic chains such as sessionContext.getBusinessObject are not
            # reliable concrete owners and should not evict self-owner rows.
            return False

        df_clean_exploded["__non_self_owner_qualified"] = df_clean_exploded["__cm_owner"].apply(
            _is_qualified_non_self_owner
        )
        _ctx_key = ["file_name", "method_name", "__cm_member"]
        _has_non_self_owner = (
            (~df_clean_exploded["__is_self_owner"]) 
            & df_clean_exploded["__non_self_owner_qualified"]
            & df_clean_exploded["__cm_member"].astype(str).str.strip().ne("")
        )
        _has_non_self_owner = _has_non_self_owner.groupby(
            [df_clean_exploded[k] for k in _ctx_key],
            dropna=False,
        ).transform("any")
        _drop_self_owner_dupe = (
            df_clean_exploded["__is_self_owner"]
            & _has_non_self_owner
            & (~df_clean_exploded["__is_dispatch_row"].fillna(False))
        )
        df_clean_exploded = df_clean_exploded.loc[~_drop_self_owner_dupe].copy()

        _project_owned_mask = [
            _is_project_owned_call(_cmc, _file, _own, _mem)
            for _cmc, _file, _own, _mem in zip(
                df_clean_exploded["class_method_call"].tolist(),
                df_clean_exploded["file_name"].tolist(),
                df_clean_exploded["__cm_owner_simple"].tolist(),
                df_clean_exploded["__cm_member"].tolist(),
            )
        ]
        df_clean_exploded = df_clean_exploded.loc[_project_owned_mask].copy()

        # Enum-owner rule:
        # Drop only when owner is an enum AND the called method is not declared
        # in that enum type. Keep declared enum methods (e.g. Country.getCountry).
        _enum_owner_decl_cache = {}

        def _resolve_owner_java_path_from_call(_call_str, _caller_file):
            _owner, _member = _split_owner_member(_call_str)
            _owner = str(_owner or "").strip()
            if not _owner:
                return None
            if "/" in _owner or "\\" in _owner:
                _p = _owner if _owner.lower().endswith(adapter.file_extension()) else (_owner + adapter.file_extension())
                return _p if os.path.isfile(_p) else None
            _resolved = _resolve_class_path(_owner, _caller_file, member_name=_member)
            if not _resolved:
                _resolved = _resolve_class_path(_owner, _caller_file, member_name=None)
            return _resolved if (_resolved and os.path.isfile(_resolved)) else None

        def _is_enum_and_declares_method(_owner_java_path, _method_name):
            _k = (str(_owner_java_path or ""), str(_method_name or "").strip())
            _c = _enum_owner_decl_cache.get(_k)
            if _c is not None:
                return _c

            if not _owner_java_path or not _method_name:
                _enum_owner_decl_cache[_k] = (False, False)
                return False, False

            try:
                _txt = file_content_cache.get(_owner_java_path)
                if _txt is None:
                    _txt = read_file_cached(_owner_java_path)
            except Exception:
                _txt = ""

            if not _txt:
                _enum_owner_decl_cache[_k] = (False, False)
                return False, False

            _owner_simple = os.path.splitext(os.path.basename(_owner_java_path))[0]
            _is_enum = bool(re.search(r'\benum\s+' + re.escape(_owner_simple) + r'\b', _txt))
            if not _is_enum:
                _enum_owner_decl_cache[_k] = (False, False)
                return False, False

            # Method declaration inside enum body (instance/static/final/etc.).
            _mname = re.escape(str(_method_name).strip())
            _decl_pat = re.compile(
                r'(?m)^\s*(?:public|protected|private)?\s*'
                r'(?:(?:static|final|synchronized|native|abstract|default|strictfp)\s+)*'
                r'(?:<[^>]+>\s+)?[A-Za-z_][\w$.<>,\[\]\s?]*\s+'
                + _mname +
                r'\s*\(',
            )
            _declares = bool(_decl_pat.search(_txt))
            _enum_owner_decl_cache[_k] = (_is_enum, _declares)
            return _is_enum, _declares

        _enum_keep_mask = []
        for _cmc, _caller in zip(
            df_clean_exploded["class_method_call"].tolist(),
            df_clean_exploded["file_name"].tolist(),
        ):
            _owner_path = _resolve_owner_java_path_from_call(_cmc, _caller)
            _owner, _member = _split_owner_member(_cmc)
            _m = str(_member or "").strip()
            _is_enum, _declares = _is_enum_and_declares_method(_owner_path, _m)
            # keep if non-enum OR enum declares method; drop only enum+not-declared
            _enum_keep_mask.append((not _is_enum) or _declares)

        df_clean_exploded = df_clean_exploded.loc[_enum_keep_mask].copy()

        # Keep object-creation constructor calls in output so rows like
        # Sort.Sort() (from `new Sort(...)`) are preserved for lineage.

        if {"__row_order", "__segment_order"}.issubset(df_clean_exploded.columns):
            df_clean_exploded = df_clean_exploded.sort_values(
                ["__row_order", "__segment_order"],
                kind="mergesort",
            ).reset_index(drop=True)

        df_clean_exploded = df_clean_exploded.drop(columns=[
            "__cm_owner",
            "__cm_member",
            "__cm_owner_simple",
            "__cm_root",
            "__cm_is_impl",
            "__caller_simple",
            "__is_self_owner",
            "__non_self_owner_qualified",
            "__is_dispatch_row",
        ])

        df_application_properties = adapter.extract_application_properties_from_folder(app_folder)

        # ------------------------------------------------------------------
        # Additional: extract inline Java variable assignments as properties.
        # Handles codebases that have no separate .properties file â€” values
        # are declared directly inside Java source files.
        # The result uses the same column layout so it can be appended to
        # the existing application.properties sheet rows.
        # ------------------------------------------------------------------
        df_inline_properties = adapter.extract_inline_java_variables_as_properties(
            java_folder=app_folder,
            df_cleaned_ast=df_clean_exploded,
        )

        # Persist the raw per-file inline variable dictionary as JSON.
        inline_file_names = df_clean_exploded["file_name"].dropna().unique().tolist()
        inline_var_dict = getattr(adapter, "_last_inline_file_var_dict", None)
        if not isinstance(inline_var_dict, dict):
            inline_var_dict = adapter._build_file_variable_dict(app_folder, inline_file_names)
        inline_dict_json_path = os.path.join(OUTPUT_DIR, "inline_variable_dictionary.json")
        with open(inline_dict_json_path, "w", encoding="utf-8") as _dict_fh:
            json.dump(inline_var_dict, _dict_fh, indent=2)

        # Merge: existing annotation-based rows first, then inline rows.
        # Drop exact duplicates that might appear if the same variable was
        # already picked up by @Value scanning.
        df_application_properties = pd.concat(
            [df_application_properties, df_inline_properties],
            ignore_index=True,
        ).drop_duplicates(
            subset=["FileName", "method_name", "Property"],
            keep="first",
        )

        excel_path = os.path.join(OUTPUT_DIR,all_methods)
        if not os.path.exists(excel_path):
            with pd.ExcelWriter(excel_path, engine="openpyxl", mode="w") as writer:
                pd.DataFrame({"init": []}).to_excel(writer, sheet_name="Init", index=False)

        _pbar.set_postfix_str("Writing Excel...")
        df_clean_export = df_clean_exploded.copy()

        # Export-only collapse for dual synthetic rows:
        # keep both caller/owner views during lineage expansion, but when writing
        # final Excel remove owner-view mirror rows when the same source-file +
        # class_method_call already exists as caller-view.
        if {"__synthetic_origin", "class_method_call"}.issubset(df_clean_export.columns):
            _src_col = "__source_file_name" if "__source_file_name" in df_clean_export.columns else "file_name"
            _caller_df = df_clean_export[
                df_clean_export["__synthetic_origin"].astype(str) == "additive_caller_view"
            ]
            _caller_keys = {
                (
                    _as_abs_norm(_src),
                    str(_cmc or "").strip(),
                )
                for _src, _cmc in zip(
                    _caller_df[_src_col].tolist(),
                    _caller_df["class_method_call"].tolist(),
                )
                if str(_cmc or "").strip()
            }
            if _caller_keys:
                _owner_mask = (
                    df_clean_export["__synthetic_origin"].astype(str) == "additive_owner_view"
                )
                _owner_dupe_mask = [False] * len(df_clean_export)
                _owner_idx = df_clean_export.index[_owner_mask]
                for _i in _owner_idx:
                    _src = _as_abs_norm(df_clean_export.at[_i, _src_col])
                    _cmc = str(df_clean_export.at[_i, "class_method_call"] or "").strip()
                    if (_src, _cmc) in _caller_keys:
                        _owner_dupe_mask[df_clean_export.index.get_loc(_i)] = True
                _before_owner_collapse = len(df_clean_export)
                df_clean_export = df_clean_export.loc[[not x for x in _owner_dupe_mask]].copy()
                _removed_owner_dupes = _before_owner_collapse - len(df_clean_export)
                if _verbose_debug and _removed_owner_dupes > 0:
                    print(
                        "[DEBUG][EXPORT_OWNER_CALLER_COLLAPSE] removed_owner_view_rows={}".format(
                            _removed_owner_dupes
                        )
                    )

        if {"__row_order", "__segment_order"}.issubset(df_clean_export.columns):
            df_clean_export = df_clean_export.sort_values(
                ["__row_order", "__segment_order"],
                kind="mergesort",
            ).reset_index(drop=True)
            df_clean_export = df_clean_export.drop(
                columns=["__row_order", "__segment_order"],
                errors="ignore",
            )

        # Drop helper columns from final export after all export-level transforms.
        df_clean_export = df_clean_export.drop(
            columns=["__source_object_call", "__source_file_name", "__synthetic_origin"],
            errors="ignore",
        )

        # Final safety net: export unique rows only.
        _before_clean_rows = len(df_clean_export)
        df_clean_export = df_clean_export.drop_duplicates().reset_index(drop=True)
        _after_clean_rows = len(df_clean_export)
        if _verbose_debug and _after_clean_rows != _before_clean_rows:
            print(
                "[DEBUG][EXPORT_DEDUP] Cleaned_AST_Details: {} -> {} (removed {})".format(
                    _before_clean_rows,
                    _after_clean_rows,
                    _before_clean_rows - _after_clean_rows,
                )
            )

        _before_props_rows = len(df_application_properties)
        df_application_properties = df_application_properties.drop_duplicates().reset_index(drop=True)
        _after_props_rows = len(df_application_properties)
        if _verbose_debug and _after_props_rows != _before_props_rows:
            print(
                "[DEBUG][EXPORT_DEDUP] application.properties: {} -> {} (removed {})".format(
                    _before_props_rows,
                    _after_props_rows,
                    _before_props_rows - _after_props_rows,
                )
            )

        with pd.ExcelWriter(excel_path, engine="xlsxwriter") as writer:
            df_clean_export.to_excel(writer,sheet_name="Cleaned_AST_Details",index=False)
            df_application_properties.to_excel(writer,sheet_name="application.properties",index=False)

        # -----------------------------------------------------------
        # Populate reachable_sources from generated Excel.
        # Read Cleaned_AST_Details and collect source file paths from:
        #   1) class_method_call   (path/to/Class.method() -> path/to/Class.java)
        #   2) class_interface_name (path/to/Class -> path/to/Class.java)
        # -----------------------------------------------------------


        # â”€â”€ Checkpoint 100% â”€â”€
        _pbar_goto(100, f"Done -> {os.path.basename(excel_path)}")
        _pbar.close()
        return os.path.abspath(excel_path)

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

    # Collect pre-built index results (built in parallel with the AST loop).
    # Keep progress timer alive while waiting so [elapsed<remaining] does not
    # appear frozen at 75% when index tasks are still running.
    _pbar.set_postfix_str("Post-processing: waiting for indexes... | t={}".format(_elapsed_seconds_text()))
    while not (_ocm_future.done() and _mri_future.done()):
        _maybe_refresh_pbar(force=True)
        concurrent.futures.wait([_ocm_future, _mri_future], timeout=1.0)

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




