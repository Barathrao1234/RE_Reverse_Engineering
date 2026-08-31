"""
extract_fs_methods.py
---------------------
Reads all .md files in a folder for FS-* IDs and their source_line_ranges,
then searches a Java codebase (directory or .zip) for the corresponding
file, class, and method for each line range.

Output: Excel file with columns:
    Spec_file | id | line_range | file_name | Class | Method

Usage:
    python extract_fs_methods.py \
        --md-folder  ./specs/ \
        --java       ./codebase/   (or codebase.zip) \
        --out        output.xlsx
"""

import re
import sys
import zipfile
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment


# ---------------------------------------------------------------------------
# 1. Parse .md — collect FS-* IDs and their source_line_ranges
# ---------------------------------------------------------------------------

FS_HEADING_RE = re.compile(r'^(FS-[A-Z0-9]+-\d+)\s*:', re.MULTILINE)
LINE_RANGE_RE = re.compile(r'source_line_ranges:\s*\[([^\]]+)\]')
TOKEN_RE      = re.compile(r'(f\d+)_(\d+)(?:-(f\d+)_(\d+))?')


def parse_md(md_path: str):
    """Return list of (fs_id, description, [(file_prefix, start, end), ...])."""
    text = Path(md_path).read_text(encoding='utf-8')

    results  = []
    headings = list(FS_HEADING_RE.finditer(text))

    for i, match in enumerate(headings):
        fs_id = match.group(1)
        start = match.start()
        end   = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        block = text[start:end]

        # --- Extract description: text on the heading line after "FS-XXX-N: " ---
        heading_line = block.split('\n', 1)[0]
        desc_match   = re.match(r'^FS-[A-Z0-9]+-\d+\s*:\s*(.*)', heading_line)
        description  = desc_match.group(1).strip() if desc_match else ''

        # If no inline description, grab the first non-empty, non-heading line
        if not description:
            for line in block.split('\n')[1:]:
                candidate = line.strip().lstrip('#').strip()
                if candidate and not candidate.startswith('FS-'):
                    description = candidate
                    break

        range_matches = list(LINE_RANGE_RE.finditer(block))
        if not range_matches:
            continue

        # Use the first source_line_ranges that isn't inside [Validation Metadata]
        chosen_ranges_str = None
        for rm in range_matches:
            pre_lines = block[:rm.start()].strip().split('\n')
            if not any('[Validation Metadata' in l for l in pre_lines[-3:]):
                chosen_ranges_str = rm.group(1)
                break
        if chosen_ranges_str is None:
            chosen_ranges_str = range_matches[0].group(1)

        ranges = []
        for tok in TOKEN_RE.finditer(chosen_ranges_str):
            fp1, n1, fp2, n2 = tok.group(1), int(tok.group(2)), tok.group(3), tok.group(4)
            if fp2 and n2:
                ranges.append((fp1, int(n1), int(n2)))
            else:
                ranges.append((fp1, int(n1), int(n1)))

        if ranges:
            results.append((fs_id, description, ranges))

    return results


# ---------------------------------------------------------------------------
# 2. Build a map: file_prefix -> (display_path, lines[])
# ---------------------------------------------------------------------------

FILE_PREFIX_RE = re.compile(r'^(f\d+)_\d+\s')


def build_prefix_map(java_source: str):
    """
    Accepts either a .zip file or a directory path.
    For each .java file found, reads line 1 to discover its fXXX prefix.
    Returns dict: prefix -> (display_path, [line_text])
    """
    prefix_map = {}
    source = Path(java_source)

    if source.is_file() and source.suffix == '.zip':
        # ZIP mode
        with zipfile.ZipFile(java_source, 'r') as zf:
            java_members = [m for m in zf.namelist() if m.endswith('.java')]
            for member in java_members:
                raw = zf.read(member).decode('utf-8', errors='replace')
                lines = raw.splitlines()
                if not lines:
                    continue
                m = FILE_PREFIX_RE.match(lines[0])
                if not m:
                    continue
                prefix_map[m.group(1)] = (member, lines)

    elif source.is_dir():
        # Directory mode
        for java_file in sorted(source.rglob('*.java')):
            try:
                raw = java_file.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            lines = raw.splitlines()
            if not lines:
                continue
            m = FILE_PREFIX_RE.match(lines[0])
            if not m:
                continue
            prefix_map[m.group(1)] = (str(java_file), lines)

    else:
        raise ValueError(f"--java must be a .zip file or a directory: {java_source}")

    print(f"  Loaded {len(prefix_map)} Java file prefixes from: {java_source}")
    return prefix_map


# ---------------------------------------------------------------------------
# 3. For a given line number find containing method and class
# ---------------------------------------------------------------------------

CLASS_RE  = re.compile(r'\b(?:class|interface|enum)\s+(\w+)')
METHOD_RE = re.compile(
    r'(?:public|protected|private|static|final|synchronized|abstract|default|native'
    r'|\s)+[\w\[\]<>,\s]+\s+(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w\s,]+)?\s*\{'
)
# Constructors have no return type: public/protected/private ClassName(...)
CONSTRUCTOR_RE = re.compile(
    r'(?:public|protected|private)\s+(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w\s,]+)?\s*\{'
)


def _match_method_or_constructor(stripped: str):
    """Return method/constructor name if the line is a signature, else None."""
    mm = METHOD_RE.search(stripped)
    if mm:
        return mm.group(1)
    cm = CONSTRUCTOR_RE.search(stripped)
    if cm:
        return cm.group(1)
    return None


def find_enclosing_class(lines: list, start_idx: int):
    """Walk backwards from start_idx to find the enclosing class name."""
    for i in range(start_idx, -1, -1):
        stripped = re.sub(r'^f\d+_\d+\s*', '', lines[i]).strip()
        cm = CLASS_RE.search(stripped)
        if cm:
            return cm.group(1)
    return '?'


def find_methods_in_range(lines: list, start_line: int, end_line: int):
    """
    Scan every line in [start_line, end_line] (1-based) for method signatures.
    For each line in the range that has no direct method hit, also walk
    backwards to find the enclosing method.

    Returns:
        found_class  – enclosing class (str)
        methods_str  – unique method names joined by ', ' (str)
    """
    start_idx = max(0, start_line - 1)
    end_idx   = min(end_line - 1, len(lines) - 1)

    # --- Collect all method/constructor signatures that START inside the range
    methods_in_range = []
    seen = set()
    for i in range(start_idx, end_idx + 1):
        stripped = re.sub(r'^f\d+_\d+\s*', '', lines[i]).strip()
        name = _match_method_or_constructor(stripped)
        if name and name not in seen:
            seen.add(name)
            methods_in_range.append(name)

    # --- Always include the method that *encloses* the start of the range ---
    # (handles the case where the range begins inside an existing method body)
    enclosing = _find_enclosing_method(lines, start_idx)
    if enclosing and enclosing not in seen:
        methods_in_range.insert(0, enclosing)   # prepend — it starts first
        seen.add(enclosing)

    # --- Resolve class from the start of the range --------------------------
    found_class = find_enclosing_class(lines, start_idx)

    methods_str = ', '.join(methods_in_range) if methods_in_range else '?'
    return found_class, methods_str


def _find_enclosing_method(lines: list, idx: int):
    """
    Walk backwards from idx to find the method whose opening brace
    encloses that position.  Returns method name or '' if not found.
    """
    brace_depth = 0
    for i in range(idx, -1, -1):
        stripped = re.sub(r'^f\d+_\d+\s*', '', lines[i]).strip()
        brace_depth += stripped.count('}') - stripped.count('{')
        name = _match_method_or_constructor(stripped)
        if name and brace_depth <= 0:
            return name
    return ''


# ---------------------------------------------------------------------------
# 4. Build rows — loop over all .md files in a folder
# ---------------------------------------------------------------------------

def build_all_rows(md_folder: str, java_source: str):
    """
    Loop over every .md file in md_folder (recursively),
    parse each one, and collect rows from the shared codebase.
    """
    md_files = sorted(Path(md_folder).rglob('*.md'))
    if not md_files:
        print(f"No .md files found in: {md_folder}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(md_files)} .md file(s) in: {md_folder}")

    # Build the prefix map once — shared across all .md files
    prefix_map = build_prefix_map(java_source)

    all_rows = []
    for md_path in md_files:
        spec_file = md_path.name
        print(f"  Processing: {spec_file}")

        fs_items = parse_md(str(md_path))
        if not fs_items:
            print(f"    (no FS IDs found — skipped)")
            continue

        for fs_id, description, ranges in fs_items:
            for (fp, start, end) in ranges:
                range_str = (
                    f"{fp}_{start}" if start == end
                    else f"{fp}_{start}-{fp}_{end}"
                )

                if fp not in prefix_map:
                    all_rows.append({
                        'Spec_file'   : spec_file,
                        'id'          : fs_id,
                        'description' : description,
                        'line_range'  : range_str,
                        'file_name'   : f'(prefix {fp} not found)',
                        'Class'       : '',
                        'Method'      : '',
                    })
                    continue

                member, lines = prefix_map[fp]
                file_name = Path(member).name
                cls, method = find_methods_in_range(lines, start, end)

                all_rows.append({
                    'Spec_file'   : spec_file,
                    'id'          : fs_id,
                    'description' : description,
                    'line_range'  : range_str,
                    'file_name'   : file_name,
                    'Class'       : cls,
                    'Method'      : method,
                })

        print(f"    → {sum(1 for r in all_rows if r['Spec_file'] == spec_file)} rows added")

    return all_rows


# ---------------------------------------------------------------------------
# 5. Write Excel
# ---------------------------------------------------------------------------

HEADER_FILL = PatternFill('solid', fgColor='1F3864')
HEADER_FONT = Font(name='Arial', bold=True, color='FFFFFF', size=11)
EVEN_FILL   = PatternFill('solid', fgColor='DCE6F1')
ODD_FILL    = PatternFill('solid', fgColor='FFFFFF')
CELL_FONT   = Font(name='Arial', size=10)

COLUMNS    = ['Spec_file', 'id', 'description', 'line_range', 'file_name', 'Class', 'Method']
COL_WIDTHS = [40, 18, 60, 30, 45, 30, 35]


def write_excel(rows, out_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'traceability_matrix'

    # Header row
    for col_idx, (col, width) in enumerate(zip(COLUMNS, COL_WIDTHS), start=1):
        cell = ws.cell(row=1, column=col_idx, value=col)
        cell.font      = HEADER_FONT
        cell.fill      = HEADER_FILL
        cell.alignment = Alignment(horizontal='center', vertical='center')
        ws.column_dimensions[cell.column_letter].width = width

    ws.row_dimensions[1].height = 20
    ws.freeze_panes = 'A2'

    # Data rows
    for row_idx, row in enumerate(rows, start=2):
        fill = EVEN_FILL if row_idx % 2 == 0 else ODD_FILL
        for col_idx, col in enumerate(COLUMNS, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=row.get(col, ''))
            cell.font      = CELL_FONT
            cell.fill      = fill
            cell.alignment = Alignment(vertical='center', wrap_text=False)

    ws.auto_filter.ref = ws.dimensions

    wb.save(out_path)
    print(f"\nSaved: {out_path}  ({len(rows)} total rows)")
    return out_path


# ---------------------------------------------------------------------------
# 6. Main — configure your paths here
# ---------------------------------------------------------------------------

# # >>> SET YOUR PATHS HERE <<<
# MD_FOLDER   = r"C:\Users\Barath\Downloads\functional_spec\Markdown_spec"            # folder containing your .md spec files
# JAVA_SOURCE = r"C:\Project\Reverse_Engineering_framework\end_to_end_framework_result_all_application\day_trade\day_trade\testing_24_7\with_line_id"         # Java codebase: folder OR .zip file
# OUTPUT_FILE = r"C:\Users\Barath\Desktop\Reverse_Engineering_rabobank\output\fs_methods.xlsx"  # output Excel file path


def traceability_matrix(MD_FOLDER, JAVA_SOURCE, OUTPUT_FILE):
    rows = build_all_rows(MD_FOLDER, JAVA_SOURCE)

    if not rows:
        print("No rows generated — nothing to write.", file=sys.stderr)
        sys.exit(1)

    traceability_matrix_report = write_excel(rows, OUTPUT_FILE)
    return traceability_matrix_report


# if __name__ == '__main__':
#     traceability_matrix(MD_FOLDER, JAVA_SOURCE, OUTPUT_FILE)