import re, os
from pathlib import Path

def clean_functional_spec(INPUT_FOLDER, OUTPUT_PATH):
    # ================= CONFIG =================

    TRACE_FOLDER = os.path.join(OUTPUT_PATH, "Trace_Removed")
    FINAL_FOLDER = os.path.join(OUTPUT_PATH, "FINAL_SPEC")

    REMOVE_FROM_HEADERS = [
        "11. Omissions, Coverage Analysis & Technical Infrastructure",
    ]

    REMOVE_CONTENT = [
        "Flow-Relevant INV Items Locked",
        "Flow-Exempt INV Items",
    ]

    REMOVE_LINE = [
        "[CONTINUATION REQUIRED - NEXT: Section 4. Functional Flow]",
        "Updated todo list",
    ]

    REMOVE_HEADERS_LOWER = {h.lower() for h in REMOVE_FROM_HEADERS}
    REMOVE_CONTENT_LOWER = {c.lower() for c in REMOVE_CONTENT}
    REMOVE_LINE_LOWER    = {l.lower() for l in REMOVE_LINE}

    TRACE_RE     = re.compile(r"<!--\s*TRACE:\s*\[.*?\]\s*-->")
    PROJECTED_RE = re.compile(r"\[Projected:[^\]]*\]", re.IGNORECASE)
    HEADING_RE   = re.compile(r"^(#{1,6})\s*(.+)")
    # matches **anything** or **anything:** even across partial lines
    BOLD_HDR_RE = re.compile(r"^\*\*(.+?)[\*:]*\*\*")


    def _bold_title(line: str):
        """Extract lowercased title from a **Header** or **Header:** line, or None."""
        m = BOLD_HDR_RE.match(line.strip())
        if m:
            return m.group(1).strip().lower().rstrip(":").strip()
        return None


    # ================= TRACE REMOVAL ==================

    def remove_Trace(lines):
        changed = False
        result = []
        for line in lines:
            cleaned = line
            if TRACE_RE.search(cleaned):
                cleaned = TRACE_RE.sub("", cleaned)
                changed = True
            if PROJECTED_RE.search(cleaned):
                cleaned = PROJECTED_RE.sub("", cleaned)
                changed = True
            if cleaned != line:
                cleaned = cleaned.rstrip()
                result.append(cleaned + "\n" if line.endswith("\n") else cleaned)
            else:
                result.append(line)
        return result, changed


    # ================= CONTINUATION & EXACT LINE REMOVAL ==================

    def remove_continuation(lines):
        """
        1. REMOVE_CONTENT bold headers: collapse multiline bold opener into one
           logical header, then delete it + content until blank line or next bold header.
        2. REMOVE_LINE: drop any line whose stripped text matches exactly.
        """
        changed  = False
        result   = []
        deleting = False
        # buffer to handle **Header: [\n](…)** split across lines
        bold_buf = []

        def flush_buf_as_keep():
            result.extend(bold_buf)
            bold_buf.clear()

        i = 0
        while i < len(lines):
            line     = lines[i]
            stripped = line.strip()

            # --- REMOVE_LINE exact match ---
            if stripped.lower() in REMOVE_LINE_LOWER:
                changed = True
                i += 1
                continue

            # --- Inside a REMOVE_CONTENT block ---
            if deleting:
                if stripped == "":
                    deleting = False
                    result.append(line)
                    i += 1
                    continue
                title = _bold_title(line)
                if title is not None:
                    deleting = False
                    if title in REMOVE_CONTENT_LOWER:
                        deleting = True
                        changed  = True
                    else:
                        result.append(line)
                    i += 1
                    continue
                # still inside block — drop line
                changed = True
                i += 1
                continue

            # --- Not deleting: detect start of bold header ---
            # Handle multiline bold: **Header: [\n](…)**
            if stripped.startswith("**"):
                # collect lines until closing ** is found
                buf   = [line]
                buf_s = stripped
                j     = i + 1
                while "**" not in buf_s[2:] and j < len(lines):
                    buf.append(lines[j])
                    buf_s += " " + lines[j].strip()
                    j += 1

                # Check if any REMOVE_CONTENT keyword appears in combined text
                combined_lower = buf_s.lower()
                matched_keyword = next(
                    (kw for kw in REMOVE_CONTENT_LOWER if kw in combined_lower), None
                )
                if matched_keyword:
                    deleting = True
                    changed  = True
                    i = j    # skip all buffered lines
                    continue
                # Not a target — keep as-is
                result.extend(buf)
                i = j
                continue

            result.append(line)
            i += 1

        return result, changed


    # ================= NAMED SECTION REMOVAL ==================

    def remove_content_after_headers(lines):
        result       = []
        deleting     = False
        target_level = None

        for line in lines:
            m = HEADING_RE.match(line)
            if m:
                level = len(m.group(1))
                title = m.group(2).strip().lower()
                if not deleting:
                    if title in REMOVE_HEADERS_LOWER:
                        deleting     = True
                        target_level = level
                    else:
                        result.append(line)
                else:
                    if level <= target_level:
                        deleting = False
                        if title in REMOVE_HEADERS_LOWER:
                            deleting     = True
                            target_level = level
                        else:
                            result.append(line)
            else:
                if not deleting:
                    result.append(line)

        return result


    # ================= FILE PROCESSING ==================

    def process_folder(input_folder, output_folder, process_fn, label):
        input_folder  = Path(input_folder)
        output_folder = Path(output_folder)
        modified = total = 0

        print(f"\n--- {label} ---")
        for md_file in sorted(input_folder.rglob("*.md")):
            try:
                md_file.relative_to(output_folder)
                continue
            except ValueError:
                pass

            total += 1
            relative_path = md_file.relative_to(input_folder)
            output_path   = output_folder / relative_path
            output_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                lines             = md_file.read_text(encoding="utf-8").splitlines(keepends=True)
                new_lines, changed = process_fn(lines)
                output_path.write_text("".join(new_lines), encoding="utf-8")
                print(f"  {'Updated' if changed else 'No Change'}: {relative_path}")
                if changed:
                    modified += 1
            except Exception as e:
                print(f"  Error:   {relative_path} → {e}")

        print(f"  Total: {total}  |  Modified: {modified}")


    def process_folder_inplace(folder, process_fn, label):
        """Read and overwrite files in the same folder."""
        folder   = Path(folder)
        modified = total = 0

        print(f"\n--- {label} ---")
        for md_file in sorted(folder.rglob("*.md")):
            total += 1
            try:
                lines              = md_file.read_text(encoding="utf-8").splitlines(keepends=True)
                new_lines, changed = process_fn(lines)
                md_file.write_text("".join(new_lines), encoding="utf-8")
                print(f"  {'Updated' if changed else 'No Change'}: {md_file.name}")
                if changed:
                    modified += 1
            except Exception as e:
                print(f"  Error:   {md_file.name} → {e}")

        print(f"  Total: {total}  |  Modified: {modified}")


    # ================= RUN ==================

    # Step 1 — remove TRACE tags → Trace_Removed/
    process_folder(
        INPUT_FOLDER, TRACE_FOLDER,
        lambda lines: remove_Trace(lines),
        "Step 1: Removing TRACE tags"
    )

    # Step 2 — remove bold blocks + exact lines → in-place on Trace_Removed/
    process_folder_inplace(
        TRACE_FOLDER,
        lambda lines: remove_continuation(lines),
        "Step 2: Removing continuation blocks & exact lines"
    )

    # Step 3 — remove named # sections → Trace_Removed/ → FINAL_SPEC/
    process_folder(
        TRACE_FOLDER, FINAL_FOLDER,
        lambda lines: (remove_content_after_headers(lines), True),
        "Step 3: Removing named # sections"
    )

    print("\n All done. Final files in:", FINAL_FOLDER)


INPUT_FOLDER = r"C:\Users\TCS\Downloads\V33\V33\SPEC"
OUTPUT_PATH  = r"C:\Users\TCS\Downloads\V33\V33\trace_removal_and_header_removal"

clean_functional_spec(INPUT_FOLDER, OUTPUT_PATH)
