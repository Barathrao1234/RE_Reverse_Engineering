
import os
import re
import sys
import openpyxl
from openpyxl.styles import Alignment

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
INPUT_EXCEL  = r"path/to/007_2_Method_Detailed_Flow_Occurrence_Distribution.xlsx"
SRC_ROOT     = r"path/to/java/source/root"
OUTPUT_EXCEL = r"path/to/007_2_Method_Detailed_Flow_Occurrence_Distribution_REORDERED.xlsx"
SRC_EXT      = ".java"
SRC_ENCODING = "utf-8"


# ─────────────────────────────────────────────
#  STEP 1 – fast single-pass scanner
# ─────────────────────────────────────────────

# Strips block comments, line comments, strings, char literals in one pass
_STRIP_RE = re.compile(
    r'/\*.*?\*/'            # block comment
    r'|//[^\n]*'           # line comment
    r'|"(?:[^"\\]|\\.)*"'  # string literal
    r"|'(?:[^'\\]|\\.)*'", # char literal
    re.DOTALL,
)

# Tokeniser: matches the things we care about in one scan
#   group "decl"   → method declaration (name + opening brace on same/next logical line)
#   group "call"   → method call site   (optional receiver dot + name + open paren)
#   group "lbrace" → {
#   group "rbrace" → }
_TOKEN_RE = re.compile(
    r"""
    # ── method declaration ──────────────────────────────────────────────────
    (?:
      ^[ \t]*
      (?:(?:public|private|protected|static|final
             |abstract|synchronized|native|strictfp)\s+)*
      (?:[\w<>\[\].,?\s]+?\s+)?          # return type (optional for ctors)
      (?P<decl>[A-Za-z_]\w*)\s*          # method / ctor name
      \([^)]*\)                           # parameter list
      \s*(?:throws\s+[\w\s,]+)?          # optional throws clause
      \s*(?=\{)                           # lookahead: body opens with {
    )
    # ── call site ────────────────────────────────────────────────────────────
    | (?:(?P<receiver>[A-Za-z_]\w*)\.)?  # optional receiver
      (?P<call>[A-Za-z_]\w*)\s*\(        # callee name + (
    # ── braces ───────────────────────────────────────────────────────────────
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


def _read_text(path, encoding):
    try:
        with open(path, encoding=encoding) as f:
            return f.read()
    except UnicodeDecodeError:
        with open(path, encoding="latin-1") as f:
            return f.read()


def build_codebase_order(src_root, ext, encoding):
    """
    Single-pass scanner. Returns:
        decl_rank_map  : {(class_lower, method_lower): int}
        call_order_map : {(caller_class_lower, caller_method_lower):
                              [(receiver_lower|None, callee_lower, call_rank)]}
    """
    decl_rank_map  = {}
    call_order_map = {}

    global_decl_rank = 0
    global_call_rank = 0
    file_count       = 0

    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames.sort()
        for fname in sorted(filenames):
            if not fname.endswith(ext):
                continue

            classname = os.path.splitext(fname)[0].lower()
            fpath     = os.path.join(dirpath, fname)
            text      = _STRIP_RE.sub(" ", _read_text(fpath, encoding))

            # ── single pass over all tokens ──────────────────────────────
            # Stack entries: (caller_class, caller_method, brace_depth_at_entry, seen_set)
            method_stack = []
            brace_depth  = 0

            for m in _TOKEN_RE.finditer(text):
                # ── { ────────────────────────────────────────────────────
                if m.group("lbrace"):
                    brace_depth += 1
                    continue

                # ── } ────────────────────────────────────────────────────
                if m.group("rbrace"):
                    brace_depth -= 1
                    # Pop any method whose body has closed
                    while method_stack and brace_depth < method_stack[-1][2]:
                        method_stack.pop()
                    continue

                # ── declaration ──────────────────────────────────────────
                if m.group("decl"):
                    mname = m.group("decl")
                    if mname in KEYWORD_SKIP:
                        continue
                    mname_lower = mname.lower()
                    key = (classname, mname_lower)
                    if key not in decl_rank_map:
                        decl_rank_map[key] = global_decl_rank
                        global_decl_rank += 1
                    # Push onto stack; body opens at brace_depth+1
                    method_stack.append((classname, mname_lower, brace_depth + 1, set()))
                    continue

                # ── call site ────────────────────────────────────────────
                if m.group("call"):
                    callee = m.group("call")
                    if callee in KEYWORD_SKIP:
                        continue
                    callee_lower   = callee.lower()
                    receiver       = m.group("receiver")
                    receiver_lower = receiver.lower() if receiver else None

                    # Register call in every enclosing method body
                    # (innermost first — stops at the closest enclosing scope)
                    if method_stack:
                        caller_class, caller_method, _, seen = method_stack[-1]
                        dedup = (receiver_lower, callee_lower)
                        if dedup not in seen:
                            seen.add(dedup)
                            caller_key = (caller_class, caller_method)
                            call_order_map.setdefault(caller_key, []).append(
                                (receiver_lower, callee_lower, global_call_rank)
                            )
                            global_call_rank += 1

            file_count += 1
            if file_count % 50 == 0 or file_count == 1:
                print(f"  [scan] {file_count} files | "
                      f"{global_decl_rank} decls | "
                      f"{global_call_rank} calls",
                      flush=True)

    print(f"\n[scan] Done — {file_count} files, "
          f"{global_decl_rank} declarations, {global_call_rank} call sites.")
    return decl_rank_map, call_order_map


# ─────────────────────────────────────────────
#  STEP 2 – helpers  (unchanged from fixed version)
# ─────────────────────────────────────────────
def extract_classmethod(cell_value):
    if not cell_value:
        return None
    base = str(cell_value).split(" ")[0]
    if "." not in base:
        return None
    parts = base.rsplit(".", 1)
    return (parts[0].lower(), parts[1].lower())


def get_decl_rank(cell_value, decl_rank_map):
    cm = extract_classmethod(cell_value)
    if cm is None:
        return 10**9
    rank = decl_rank_map.get(cm)
    if rank is not None:
        return rank
    method_lower = cm[1]
    matches = [v for (c, m), v in decl_rank_map.items() if m == method_lower]
    return min(matches) if matches else 10**9


def get_call_rank(parent_cm, child_cell_value, call_order_map):
    child_cm = extract_classmethod(child_cell_value)
    if child_cm is None:
        return 10**9
    child_class, child_method = child_cm
    callees = call_order_map.get(parent_cm, [])

    # Exact: receiver class matches
    for (recv, callee, rank) in callees:
        if callee == child_method and (recv is None or recv == child_class):
            return rank
    # Method-name-only
    for (recv, callee, rank) in callees:
        if callee == child_method:
            return rank
    # Global fallback
    matches = [r for entries in call_order_map.values()
                 for (_, c, r) in entries if c == child_method]
    return min(matches) if matches else 10**9


# ─────────────────────────────────────────────
#  STEP 3 – read / group / sort / write
#  (identical to previous fixed version)
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


def hierarchical_sort(rows, level_idx, call_order_map):
    if level_idx >= len(rows[0]):
        return rows
    seen = {}
    for row in rows:
        val = row[level_idx]
        seen.setdefault(val, []).append(row)
    if len(seen) == 1:
        return hierarchical_sort(rows, level_idx + 1, call_order_map)
    parent_cm = extract_classmethod(rows[0][level_idx - 1]) if level_idx > 0 else None

    def child_key(v):
        return 10**9 if v is None else get_call_rank(parent_cm, v, call_order_map)

    result = []
    for val in sorted(seen, key=child_key):
        result.extend(hierarchical_sort(seen[val], level_idx + 1, call_order_map))
    return result


def sort_all(groups, decl_rank_map, call_order_map):
    sorted_groups = sorted(groups, key=lambda g: get_decl_rank(g[0], decl_rank_map))
    result = []
    for root_val, group_rows in sorted_groups:
        sorted_rows = hierarchical_sort(group_rows, 1, call_order_map)
        result.append((root_val, sorted_rows))
    print("\n[reorder] New Level_1 order:")
    for i, (root, _) in enumerate(result, 1):
        print(f"  {i:>3}. rank={get_decl_rank(root, decl_rank_map):<8} {root}")
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
            end = r + 1
            while end < len(data) and data[end][ci] == val:
                end += 1
            if end - r > 1:
                ws.merge_cells(start_row=r+2, start_column=ci+1,
                                end_row=end+1, end_column=ci+1)
                ws.cell(r+2, ci+1).alignment = merge_align
            r = end

    for col_cells in ws.columns:
        w = max((len(str(c.value)) for c in col_cells if c.value), default=10)
        ws.column_dimensions[col_cells[0].column_letter].width = min(w + 4, 60)

    print(f"[write] 'Original Flow' written ({len(all_rows)} rows, {n_cols} cols)")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"Input  : {INPUT_EXCEL}")
    print(f"Source : {SRC_ROOT}")
    print(f"Output : {OUTPUT_EXCEL}")
    print(f"{'='*60}\n")

    decl_rank_map, call_order_map = build_codebase_order(SRC_ROOT, SRC_EXT, SRC_ENCODING)

    print("\n[excel] Loading workbook …")
    wb      = openpyxl.load_workbook(INPUT_EXCEL)
    ws_orig = wb["Original Flow"]
    n_cols  = ws_orig.max_column

    rows   = read_original_flow(ws_orig)
    groups = group_by_level1(rows)
    print(f"[group] {len(groups)} Level_1 root groups found.")

    sorted_groups = sort_all(groups, decl_rank_map, call_order_map)
    write_original_flow_sheet(wb, sorted_groups, n_cols)

    print(f"\n[save] Saving → {OUTPUT_EXCEL}")
    wb.save(OUTPUT_EXCEL)
    print("[done] ✅  Reordered workbook saved.\n")


if __name__ == "__main__":
    main()
