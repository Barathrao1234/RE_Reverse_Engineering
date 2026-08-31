

import os
import re
from pathlib import Path
import pandas as pd
from typing import Set, Dict, Optional, List, Tuple

# ================= DEBUG CONTROL ================= #

DEBUG = True

def debug(msg: str):
    if DEBUG:
        print(msg)

# ================= NORMALIZATION ================= #

def norm(value: str) -> str:
    return str(value).strip().upper()

# ================= REGEX ================= #

ID_PATTERN = re.compile(r"\b(f\d+_\d+)\b", re.IGNORECASE)

RANGE_PATTERN = re.compile(
    r"\b(f\d+_\d+)\s*-\s*(f\d+_\d+)\b",
    re.IGNORECASE
)

SOURCE_RANGE_BLOCK = re.compile(
    r"source_line_ranges\s*:\s*\[(.*?)\]",
    re.DOTALL | re.IGNORECASE
)

GROUP_ID_PATTERN = re.compile(r"\b(G\d+)\b", re.IGNORECASE)

CHUNK_ID_PATTERN = re.compile(
    r"\b(C\d+_[A-Za-z0-9_-]+)\b",
    re.IGNORECASE
)

# ================= JAVA CLASS/METHOD INDEX ================= #

# Patterns for Java structural elements
_CLASS_PATTERN = re.compile(
    r"^\s*(?:(?:public|private|protected|abstract|final|static|strictfp)\s+)*"
    r"(?:class|interface|enum|@interface)\s+(\w+)",
)

_METHOD_PATTERN = re.compile(
    r"^\s*(?:(?:public|private|protected|abstract|final|static|synchronized|native|strictfp|default)\s+)*"
    r"(?:<[\w,\s<>\[\]?]+>\s+)?"          # optional generic return type
    r"(?:[\w.<>\[\]]+\s+)+"               # return type (1+ tokens)
    r"(\w+)\s*\("                         # method name + opening paren
)

_SKIP_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "try", "else",
    "do", "return", "new", "throw", "assert", "case", "finally",
    "synchronized", "instanceof", "this", "super",
}

# Patterns that look like method calls, not declarations
_METHOD_CALL_EXCLUDE = re.compile(r"\)\s*[;,)\]]")  # ends with ); or ), etc. → call
_ANNOTATION_LINE = re.compile(r"^\s*@")


def _is_method_declaration(line: str) -> Optional[str]:
    """
    Return method name if the line is a method/constructor declaration,
    else return None.
    Heuristic: has a '(' but is NOT a call (no trailing ';' on the
    open-paren expression), and is not an annotation.
    """
    if _ANNOTATION_LINE.match(line):
        return None
    # Must contain '(' to be a method
    if "(" not in line:
        return None
    # Skip obvious non-declarations
    stripped = line.strip()
    if stripped.startswith("//") or stripped.startswith("*"):
        return None

    m = _METHOD_PATTERN.match(line)
    if not m:
        return None

    name = m.group(1)
    if name.lower() in _SKIP_KEYWORDS:
        return None

    # The segment before the '(' should not end with ')' (that would be a call)
    before_paren = line[: line.index("(")]
    if before_paren.rstrip().endswith(")"):
        return None

    return name


def build_java_line_index(project_path: str) -> Dict[str, Tuple[str, str, str]]:
    """
    Walk *project_path* recursively, find all *.java files that contain
    line IDs of the form  f<N>_<M>  and build:

        line_id (upper) -> (java_file_name, class_name, method_name)

    class_name / method_name will be "<class-level>" / "<no method>"
    when the line sits outside any method body.

    The function relies on brace-counting to track open/close scopes and
    on line-prefix IDs already embedded in the source (the same format
    used everywhere else in this tool-chain).
    """
    index: Dict[str, Tuple[str, str, str]] = {}

    if not project_path or not os.path.isdir(project_path):
        debug(f"⚠️  Java project path not found or not a directory: {project_path}")
        return index

    java_files_scanned = 0

    for root, _dirs, files in os.walk(project_path):
        for fname in files:
            if not fname.lower().endswith(".java"):
                continue

            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                    raw_lines = fh.readlines()
            except Exception as exc:
                debug(f"  ⚠️  Cannot read {fpath}: {exc}")
                continue

            # Quick pre-check: does this file have any line IDs?
            joined = "".join(raw_lines[:5])  # IDs appear early (first line of each row)
            if not ID_PATTERN.search("".join(raw_lines)):
                continue

            java_files_scanned += 1

            # ---- Per-file scope tracking ----
            # Stack entries: {"type": "class"|"method"|"block",
            #                 "name": str, "brace_depth": int}
            scope_stack: List[Dict] = []
            brace_depth = 0          # total open-brace count so far
            current_class = "<unknown>"
            current_method = "<no method>"

            # pending_annotation_ids: line IDs from @annotation lines waiting
            # to be attributed to the method declared on the very next
            # non-annotation, non-blank line.
            pending_annotation_ids: List[str] = []

            for raw_line in raw_lines:
                # --- Extract the embedded line ID (if any) ---
                id_match = ID_PATTERN.search(raw_line)
                line_id = id_match.group(1).upper() if id_match else None

                # Strip the ID prefix so the structural patterns work cleanly
                code_line = ID_PATTERN.sub("", raw_line, count=1)

                is_blank      = not code_line.strip()
                is_annotation = bool(_ANNOTATION_LINE.match(code_line))

                # --- Count braces BEFORE structural decisions so that a
                #     closing '}' line is attributed to the scope it closes. ---
                opens  = code_line.count("{")
                closes = code_line.count("}")

                # ── STEP 1: update brace depth
                brace_depth += opens - closes

                # ── STEP 2: pop scopes that have been fully closed.
                #    Save snapshot BEFORE popping so closing '}' is still
                #    attributed to the scope it closes.
                snapshot_class  = current_class
                snapshot_method = current_method

                while scope_stack and brace_depth <= scope_stack[-1]["brace_depth"]:
                    popped = scope_stack.pop()
                    if popped["type"] == "method":
                        current_method = next(
                            (s["name"] for s in reversed(scope_stack) if s["type"] == "method"),
                            "<no method>",
                        )
                    elif popped["type"] == "class":
                        current_class = next(
                            (s["name"] for s in reversed(scope_stack) if s["type"] == "class"),
                            "<unknown>",
                        )
                        current_method = "<no method>"

                # ── STEP 3: detect new structural scopes opening on this line.
                #    Done AFTER popping. Snapshot is then updated so the
                #    declaration line itself is attributed to its own scope.
                cls_m = _CLASS_PATTERN.match(code_line)
                if cls_m:
                    scope_stack.append({
                        "type": "class",
                        "name": cls_m.group(1),
                        "brace_depth": brace_depth - opens,
                    })
                    current_class  = cls_m.group(1)
                    current_method = "<no method>"
                    snapshot_class  = current_class
                    snapshot_method = current_method

                elif _is_method_declaration(code_line):
                    method_name = _is_method_declaration(code_line)
                    scope_stack.append({
                        "type": "method",
                        "name": method_name,
                        "brace_depth": brace_depth - opens,
                    })
                    current_method = method_name
                    # Update snapshot so the declaration line records the new method
                    snapshot_class  = current_class
                    snapshot_method = current_method

                    # Flush pending annotations: they belong to this method
                    for ann_id in pending_annotation_ids:
                        index[ann_id] = (fname, current_class, current_method)
                    pending_annotation_ids.clear()

                # ── STEP 4: record the line
                if line_id:
                    if is_annotation:
                        # Defer: we don't know the method yet
                        pending_annotation_ids.append(line_id)
                    else:
                        # snapshot reflects: closing '}' owner OR new scope just pushed
                        index[line_id] = (fname, snapshot_class, snapshot_method)

                        # Class-level annotations (never followed by a method)
                        # get the same context as this non-annotation line.
                        if not is_blank and pending_annotation_ids:
                            for ann_id in pending_annotation_ids:
                                index[ann_id] = (fname, snapshot_class, snapshot_method)
                            pending_annotation_ids.clear()

            # End of file: flush any trailing annotations with last known context
            for ann_id in pending_annotation_ids:
                index[ann_id] = (fname, current_class, current_method)
            pending_annotation_ids.clear()

    debug(f"✅ Java line index built: {len(index)} entries from {java_files_scanned} file(s)")
    return index


# ================= GLOBAL LINE CACHE (CRITICAL FIX) ================= #

def build_global_line_cache(search_dirs: Set[str]) -> Dict[str, str]:
    """
    Build once: line_id -> line_content
    Prevents O(N²) file scanning.
    """
    cache = {}
    for directory in search_dirs:
        if not directory or not os.path.isdir(directory):
            continue

        for fname in os.listdir(directory):
            if fname.lower().endswith(".txt"):
                fpath = os.path.join(directory, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        for line in f:
                            m = ID_PATTERN.search(line)
                            if m:
                                cache[m.group(1).upper()] = line.rstrip("\n")
                except Exception:
                    continue

    debug(f"✅ Global line cache built: {len(cache)} entries")
    return cache

def resolve_line_content_from_cache(line_id: str, cache: Dict[str, str]) -> str:
    return cache.get(line_id, "<CONTENT NOT FOUND>")

# ================= EXPECTED IDS ================= #

def extract_ids_and_lines_from_txt(path: str) -> Set[str]:
    ids = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            m = ID_PATTERN.search(line)
            if m:
                ids.add(m.group(1).upper())
    return ids

def extract_expected_ids(path: str) -> Set[str]:
    debug(f"    🔍 Extracting expected IDs from: {path}")
    ids = extract_ids_and_lines_from_txt(path)
    debug(f"    ✅ Expected IDs extracted: {len(ids)}")
    return ids

# ================= SPEC RANGE HANDLING ================= #

def extract_covered_ranges_from_spec(spec_path: str) -> List[Tuple[int, int]]:
    ranges = []
    with open(spec_path, "r", encoding="utf-8") as f:
        content = f.read()

    for block in SOURCE_RANGE_BLOCK.findall(content):
        for start, end in RANGE_PATTERN.findall(block):
            try:
                s = int(start.split("_")[1])
                e = int(end.split("_")[1])
                ranges.append((s, e))
            except Exception:
                continue

    debug(f"    ✅ Covered ranges extracted: {len(ranges)}")
    return ranges

def is_line_covered(line_id: str, ranges: List[Tuple[int, int]]) -> bool:
    try:
        n = int(line_id.split("_")[1])
    except Exception:
        return False

    return any(start <= n <= end for start, end in ranges)

# ================= RECURSIVE CHUNK EXPECTED IDS ================= #

def collect_recursive_expected_ids(
    chunk_id: str,
    chunk_lookup: Dict[str, pd.Series],
    source_dir: str,
    group_input_dir: Optional[str],
    visited: Set[str]
) -> Set[str]:

    chunk_id = norm(chunk_id)
    debug(f"\n🔹 Resolving expected IDs for chunk: {chunk_id}")

    if chunk_id in visited:
        return set()

    visited.add(chunk_id)

    row = chunk_lookup.get(chunk_id)
    if row is None:
        debug(f"  ❌ Chunk NOT FOUND: {chunk_id}")
        return set()

    expected = set()

    # ---- Own chunk ----
    chunk_txt = os.path.join(source_dir, f"{chunk_id}.txt")
    if os.path.exists(chunk_txt):
        expected |= extract_expected_ids(chunk_txt)

    # ---- Groups (optional) ----
    groups_val = row.get("groups")
    if group_input_dir and pd.notna(groups_val):
        for gid in GROUP_ID_PATTERN.findall(str(groups_val)):
            grp_txt = os.path.join(group_input_dir, f"{norm(gid)}.txt")
            if os.path.exists(grp_txt):
                expected |= extract_expected_ids(grp_txt)

    # ---- Child chunks ----
    children_val = row.get("direct_children")
    if pd.notna(children_val):
        children = [norm(c) for c in CHUNK_ID_PATTERN.findall(str(children_val))]
        for cid in children:
            expected |= collect_recursive_expected_ids(
                cid, chunk_lookup, source_dir, group_input_dir, visited
            )

    debug(f"  ✅ TOTAL expected IDs for {chunk_id}: {len(expected)}")
    return expected

# ================= MAIN DRIVER ================= #

def recursive_chunk_vs_spec_line_number_validation(
    chunk_excel: str,
    source_dir: str,
    spec_dir: str,
    output_path: Path,
    output_excel: str,
    chunk_sheet,
    group_sheet: Optional[str] = None,
    group_input_dir: Optional[str] = None,
    java_project_path: Optional[str] = None,   # ← NEW: root of Java source tree
):
    """
    Validate chunk spec coverage and (optionally) resolve missing line IDs
    to their Java class + method context.

    Parameters
    ----------
    chunk_excel        : Path to the chunk Excel workbook.
    source_dir         : Directory containing per-chunk .txt files.
    spec_dir           : Directory containing per-chunk .md spec files.
    output_path        : Base directory for the Validation_Reports folder.
    output_excel       : Output Excel filename (placed inside Validation_Reports/).
    chunk_sheet        : Sheet name/index for chunk data in chunk_excel.
    group_sheet        : Sheet name for group data (optional).
    group_input_dir    : Directory containing per-group .txt files (optional).
    java_project_path  : Root directory of the Java project to scan for
                         class/method context of missing lines (optional).
                         When supplied, the Missing_Lines sheet gains three
                         extra columns: Java_File, Class_Name, Method_Name.
    """

    debug("\n📊 Reading chunk Excel...")
    df_chunk = pd.read_excel(chunk_excel, sheet_name=chunk_sheet)

    chunk_lookup: Dict[str, pd.Series] = {
        norm(r["chunk_id"]): r
        for _, r in df_chunk.iterrows()
        if pd.notna(r.get("chunk_id"))
    }

    debug(f"✅ Total chunks loaded: {len(chunk_lookup)}")

    # ✅ Build cache ONCE (performance fix)
    search_dirs = {source_dir, group_input_dir} if group_input_dir else {source_dir}
    line_cache = build_global_line_cache(search_dirs)

    # ✅ Build Java line index ONCE (when project path is provided)
    java_index: Dict[str, Tuple[str, str, str]] = {}
    if java_project_path:
        debug(f"\n☕ Building Java line index from: {java_project_path}")
        java_index = build_java_line_index(java_project_path)

    summary_rows = []
    missing_rows = []

    for chunk_id in chunk_lookup:
        debug("\n==============================")
        debug(f"📄 PROCESSING CHUNK: {chunk_id}")

        spec_path = os.path.join(spec_dir, f"{chunk_id}.md")
        if not os.path.exists(spec_path):
            debug(f"❌ Spec NOT FOUND: {spec_path}")
            continue

        covered_ranges = extract_covered_ranges_from_spec(spec_path)

        expected_ids = collect_recursive_expected_ids(
            chunk_id,
            chunk_lookup,
            source_dir,
            group_input_dir,
            visited=set()
        )

        covered_ids = {
            eid for eid in expected_ids
            if is_line_covered(eid, covered_ranges)
        }

        missing = expected_ids - covered_ids

        debug(f"📊 RESULT for {chunk_id}")
        debug(f"   Expected: {len(expected_ids)}")
        debug(f"   Covered : {len(covered_ids)}")
        debug(f"   Missing : {len(missing)}")

        summary_rows.append({
            "Entity": chunk_id,
            "Expected_IDs": len(expected_ids),
            "Covered_IDs": len(covered_ids),
            "Coverage_%": round(
                (len(covered_ids) / len(expected_ids)) * 100, 2
            ) if expected_ids else 100.0
        })

        for mid in sorted(missing):
            java_file, class_name, method_name = java_index.get(
                mid, ("<not found>", "<not found>", "<not found>")
            ) if java_index else ("<N/A>", "<N/A>", "<N/A>")

            missing_rows.append({
                "Entity":       chunk_id,
                "Line_ID":      mid,
                "Line_Content": resolve_line_content_from_cache(mid, line_cache),
                "Class":        class_name,
                "Method":       method_name,
            })

    debug("\n📤 Writing Excel output...")
    output_path = os.path.join(output_path, "Validation_Reports")

    os.makedirs(output_path, exist_ok=True)
    output_excel_path = os.path.join(output_path, output_excel)

    with pd.ExcelWriter(output_excel_path, engine="openpyxl") as writer:
        pd.DataFrame(summary_rows).to_excel(
            writer, sheet_name="Chunk_Coverage_Summary", index=False
        )
        pd.DataFrame(missing_rows).to_excel(
            writer, sheet_name="Missing_Lines", index=False
        )

    print(f"\n✅ Coverage report generated: {output_excel_path}")
    return output_excel_path

