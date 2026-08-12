import os
import re
import openpyxl
from openpyxl.styles import Alignment
from datetime import datetime

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
SRC_EXT      = ".java"
SRC_ENCODING = "utf-8"


def log_time(message):
    with open("Reordering.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} - {message}\n")


# ─────────────────────────────────────────────
#  STEP 1 – locate source files by full_path (class)
# ─────────────────────────────────────────────
# full_path looks like "com.foo.TradeSLSBBean" (dotted package + class name).
# We resolve it to an actual file on disk under SRC_ROOT, trying the
# package-path layout first (com/foo/TradeSLSBBean.java) and falling back
# to a bare-filename search (TradeSLSBBean.java anywhere under SRC_ROOT) in
# case the on-disk layout doesn't mirror the package exactly.

def _read_text(path, encoding):
    try:
        with open(path, encoding=encoding) as f:
            return f.read()
    except UnicodeDecodeError:
        with open(path, encoding="latin-1") as f:
            return f.read()


class SourceIndex:
    """Resolves full_path -> file path, with caching, and builds a
    bare-filename -> [paths] index once for fallback lookups."""

    def __init__(self, src_root, ext, encoding):
        self.src_root = src_root
        self.ext = ext
        self.encoding = encoding
        self._path_cache = {}          # full_path_lower -> resolved file path (or None)
        self._filename_index = {}      # bare_classname_lower -> [file paths]
        self._text_cache = {}          # file path -> source text (comments/strings stripped)
        self._build_filename_index()

    def _build_filename_index(self):
        for dirpath, dirnames, filenames in os.walk(self.src_root):
            dirnames.sort()
            for fname in filenames:
                if not fname.endswith(self.ext):
                    continue
                classname = os.path.splitext(fname)[0].lower()
                self._filename_index.setdefault(classname, []).append(
                    os.path.join(dirpath, fname)
                )

    def resolve(self, full_path):
        """full_path e.g. 'com.foo.TradeSLSBBean' -> absolute file path or None."""
        key = full_path.lower()
        if key in self._path_cache:
            return self._path_cache[key]

        segments = full_path.split(".")
        classname = segments[-1]
        result = None

        # 1) Try exact package-path layout: src_root/com/foo/TradeSLSBBean.java
        candidate = os.path.join(self.src_root, *segments) + self.ext
        if os.path.isfile(candidate):
            result = candidate
        else:
            # 2) Fall back to bare-filename index. If multiple files share the
            #    same class name, prefer one whose directory path ends with
            #    the given package segments (best partial match), else first.
            candidates = self._filename_index.get(classname.lower(), [])
            if len(candidates) == 1:
                result = candidates[0]
            elif len(candidates) > 1:
                pkg_suffix = os.path.join(*segments[:-1]) if len(segments) > 1 else ""
                best = None
                for c in candidates:
                    norm = c.replace("\\", "/")
                    if pkg_suffix and norm.replace("\\", "/").find(pkg_suffix.replace("\\", "/")) != -1:
                        best = c
                        break
                result = best or candidates[0]

        self._path_cache[key] = result
        return result

    def get_text(self, file_path):
        if file_path not in self._text_cache:
            raw = _read_text(file_path, self.encoding)
            self._text_cache[file_path] = _STRIP_RE.sub(" ", raw)
        return self._text_cache[file_path]


# Strips block comments, line comments, strings, char literals
_STRIP_RE = re.compile(
    r'/\*.*?\*/'
    r'|//[^\n]*'
    r'|"(?:[^"\\]|\\.)*"'
    r"|'(?:[^'\\]|\\.)*'",
    re.DOTALL,
)

_TOKEN_RE = re.compile(
    r"""
    (?:
      ^[ \t]*
      (?:(?:public|private|protected|static|final
             |abstract|synchronized|native|strictfp)\s+)*
      (?:[\w<>\[\].,?\s]+?\s+)?
      (?P<decl>[A-Za-z_]\w*)\s*
      \([^)]*\)
      \s*(?:throws\s+[\w\s,]+)?
      \s*(?=\{)
    )
    | (?:(?P<receiver>[A-Za-z_]\w*)\.)?
      (?P<call>[A-Za-z_]\w*)\s*\(
    | (?P<lbrace>\{)
    | (?P<rbrace>\})
    """,
    re.VERBOSE | re.MULTILINE,
)

KEYWORD_SKIP = frozenset({
    "if", "else", "while", "for", "switch", "return",
    "try", "catch", "finally", "new", "throw", "assert",
    "class", "interface", "enum", "import", "package",
    "void", "int", "long", "double", "float", "boolean",
    "byte", "char", "short", "string", "static", "final",
    "abstract", "public", "private", "protected", "synchronized",
    "super", "this",
})


def get_call_order_for_method(src_index, full_path, method_name):
    """
    Opens the file for `full_path`, finds `method_name`'s declaration body,
    and returns the list of callee names (lowercased) in the textual order
    they're called within that method body (top-level calls only — not
    calls made inside nested lambda/anonymous classes' *other* methods,
    but calls inside the body including nested blocks/lambdas are included).
    Returns [] if the file or method can't be found.
    """
    file_path = src_index.resolve(full_path)
    if not file_path:
        return []
    text = src_index.get_text(file_path)
    method_lower = method_name.lower()

    brace_depth = 0
    # Each stack entry: [method_name_lower, depth_at_which_body_opened]
    method_stack = []
    target_depth = None   # brace_depth of our target method's body once found
    calls = []
    found_start = False
    finished = False

    for m in _TOKEN_RE.finditer(text):
        if finished:
            break

        if m.group("lbrace"):
            brace_depth += 1
            continue

        if m.group("rbrace"):
            brace_depth -= 1
            while method_stack and brace_depth < method_stack[-1][1]:
                popped = method_stack.pop()
                if target_depth is not None and popped[1] == target_depth and popped[0] == method_lower:
                    finished = True
            continue

        if m.group("decl"):
            mname = m.group("decl")
            if mname in KEYWORD_SKIP:
                continue
            mname_lower = mname.lower()
            body_depth = brace_depth + 1
            method_stack.append([mname_lower, body_depth])
            if not found_start and mname_lower == method_lower:
                found_start = True
                target_depth = body_depth
            continue

        if m.group("call"):
            callee = m.group("call")
            if callee in KEYWORD_SKIP:
                continue
            # Only record calls while we're inside the target method's body
            if target_depth is not None and method_stack and method_stack[-1][1] == target_depth \
                    and method_stack[-1][0] == method_lower and brace_depth >= target_depth:
                calls.append(callee.lower())
            elif target_depth is not None and any(
                s[0] == method_lower and s[1] == target_depth for s in method_stack
            ) and brace_depth >= target_depth:
                # inside a nested block (if/for/lambda) within the target method
                calls.append(callee.lower())
            continue

    return calls


# ─────────────────────────────────────────────
#  STEP 2 – helpers
# ─────────────────────────────────────────────
def extract_fullpath_method(cell_value):
    """'com.foo.TradeSLSBBean.createTrade' -> ('com.foo.TradeSLSBBean', 'createTrade')"""
    if not cell_value:
        return None
    base = str(cell_value).split(" ")[0]
    if "." not in base:
        return None
    full_path, method = base.rsplit(".", 1)
    return (full_path, method)


def get_call_rank(src_index, parent_fullpath_method, child_cell_value, rank_cache):
    """Rank of the child's method name within the parent's call order."""
    child_fm = extract_fullpath_method(child_cell_value)
    if child_fm is None or parent_fullpath_method is None:
        return 10**9
    parent_full_path, parent_method = parent_fullpath_method
    child_method_lower = child_fm[1].lower()

    cache_key = (parent_full_path.lower(), parent_method.lower())
    if cache_key not in rank_cache:
        calls = get_call_order_for_method(src_index, parent_full_path, parent_method)
        rank_cache[cache_key] = calls
    else:
        calls = rank_cache[cache_key]

    try:
        return calls.index(child_method_lower)
    except ValueError:
        return 10**9


# ─────────────────────────────────────────────
#  STEP 3 – read / group / sort / write
# ─────────────────────────────────────────────
def read_original_flow(ws):
    merged_lookup = {}
    for merged_range in ws.merged_cells.ranges:
        anchor_val = ws.cell(merged_range.min_row, merged_range.min_col).value
        for row in range(merged_range.min_row, merged_range.max_row + 1):
            for col in range(merged_range.min_col, merged_range.max_col + 1):
                merged_lookup[(row, col)] = anchor_val
    rows = []
    for r in range(2, ws.max_row + 1):
        row_data = [merged_lookup.get((r, c), ws.cell(r, c).value)
                    for c in range(1, ws.max_column + 1)]
        rows.append(row_data)
    return rows


def group_by_level1(rows):
    groups = []
    sentinel = object()
    current_root, current_rows = sentinel, []
    for row in rows:
        l1 = row[0]
        if l1 != current_root:
            if current_rows:
                groups.append((current_root, current_rows))
            current_root, current_rows = l1, [row]
        else:
            current_rows.append(row)
    if current_rows:
        groups.append((current_root, current_rows))
    return groups


def hierarchical_sort(rows, level_idx, src_index, rank_cache):
    if level_idx >= len(rows[0]):
        return rows
    seen = {}
    for row in rows:
        val = row[level_idx]
        seen.setdefault(val, []).append(row)
    if len(seen) == 1:
        return hierarchical_sort(rows, level_idx + 1, src_index, rank_cache)

    parent_fm = extract_fullpath_method(rows[0][level_idx - 1]) if level_idx > 0 else None

    def child_key(v):
        return 10**9 if v is None else get_call_rank(src_index, parent_fm, v, rank_cache)

    result = []
    for val in sorted(seen, key=child_key):
        result.extend(hierarchical_sort(seen[val], level_idx + 1, src_index, rank_cache))
    return result


def sort_all(groups, src_index):
    rank_cache = {}
    # Level_1 roots keep their original relative order (no parent to rank
    # them against) unless you want them sorted too — ask if so.
    result = []
    for root_val, group_rows in groups:
        sorted_rows = hierarchical_sort(group_rows, 1, src_index, rank_cache)
        result.append((root_val, sorted_rows))
    print("\n[reorder] Level_1 groups processed:")
    for i, (root, _) in enumerate(result, 1):
        print(f"  {i:>3}. {root}")
    return result


def write_original_flow_sheet(wb, sorted_groups, n_cols):
    if "Original Flow" in wb.sheetnames:
        idx = wb.sheetnames.index("Original Flow")
        del wb["Original Flow"]
    else:
        idx = 0
    ws = wb.create_sheet("Original Flow", idx)
    ws.append([f"Level_{i+1}" for i in range(n_cols)])

    all_rows = [row for _, grp in sorted_groups for row in grp]
    prev = [None] * n_cols
    for row in all_rows:
        padded = (list(row) + [None] * n_cols)[:n_cols]
        out = []
        for ci, val in enumerate(padded):
            if val is not None and val == prev[ci]:
                out.append(None)
            else:
                out.append(val)
                prev[ci] = val
                for d in range(ci + 1, n_cols):
                    prev[d] = None
        ws.append(out)

    merge_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    data = list(ws.iter_rows(min_row=2, values_only=True))

    for ci in range(n_cols):
        r = 0
        while r < len(data):
            val = data[r][ci]
            if val is None:
                r += 1
                continue
            start = r
            next_filled = r + 1
            while next_filled < len(data):
                if data[next_filled][ci] is not None:
                    break
                next_filled += 1
            end = next_filled - 1
            if next_filled >= len(data):
                end = len(data) - 1
            if end > start:
                ws.merge_cells(start_row=start + 2, start_column=ci + 1,
                                end_row=end + 2, end_column=ci + 1)
                ws.cell(start + 2, ci + 1).alignment = merge_align
            r = next_filled

    for col_cells in ws.columns:
        w = max((len(str(c.value)) for c in col_cells if c.value), default=10)
        ws.column_dimensions[col_cells[0].column_letter].width = min(w + 4, 60)

    print(f"[write] 'Original Flow' written ({len(all_rows)} rows, {n_cols} cols)")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def reorder_flow(INPUT_EXCEL, SRC_ROOT, OUTPUT_EXCEL):
    print(f"\n{'='*60}")
    print(f"Input  : {INPUT_EXCEL}")
    print(f"Source : {SRC_ROOT}")
    print(f"Output : {OUTPUT_EXCEL}")
    print(f"{'='*60}\n")
    start_time = datetime.now()
    log_time("Chunk Formation START")

    print("[index] Indexing source tree …")
    src_index = SourceIndex(SRC_ROOT, SRC_EXT, SRC_ENCODING)

    print("[excel] Loading workbook …")
    wb = openpyxl.load_workbook(INPUT_EXCEL)
    ws_orig = wb["Original Flow"]
    n_cols = ws_orig.max_column

    rows = read_original_flow(ws_orig)
    groups = group_by_level1(rows)
    print(f"[group] {len(groups)} Level_1 root groups found.")

    sorted_groups = sort_all(groups, src_index)
    write_original_flow_sheet(wb, sorted_groups, n_cols)

    print(f"\n[save] Saving → {OUTPUT_EXCEL}")
    wb.save(OUTPUT_EXCEL)
    print("[done] ✅  Reordered workbook saved.\n")

    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()
    log_time(f"Chunk Formation END | Duration={elapsed:.3f} sec")
