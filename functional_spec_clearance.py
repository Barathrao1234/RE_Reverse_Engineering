import re, os
from pathlib import Path

def clean_functional_spec(INPUT_FOLDER, OUTPUT_PATH, FOLDER):
    # ================= CONFIG =================

    TRACE_FOLDER = os.path.join(OUTPUT_PATH, "Trace_Removed")
    FINAL_FOLDER = os.path.join(OUTPUT_PATH, "FINAL_SPEC")

    REMOVE_FROM_HEADERS = [
        "## Omissions and Coverage Analysis",
        "## Post-Merger Audit Log",
    ]

    REMOVE_HEADERS_LOWER = {h.lower() for h in REMOVE_FROM_HEADERS}

    TRACE_RE    = re.compile(r"<!--\s*TRACE:\s*\[.*?\]\s*-->")
    HEADING_RE  = re.compile(r"^(#{1,6})\s+(.+)")


    # ================= TRACE REMOVAL ==================

    def remove_Trace(lines: list[str]) -> tuple[list[str], bool]:
        """
        Strip <!-- TRACE: [...] --> from each line in-place.
        Preserves the rest of the line. Returns (new_lines, changed).
        """
        changed = False
        result = []
        for line in lines:
            if TRACE_RE.search(line):
                cleaned = TRACE_RE.sub("", line).rstrip()
                result.append(cleaned + "\n" if line.endswith("\n") else cleaned)
                changed = True
            else:
                result.append(line)
        return result, changed


    # ================= NAMED SECTION REMOVAL ==================

    def remove_content_after_headers(lines: list[str]) -> list[str]:
        """
        Remove a named heading (# / ## / ... level) and all lines below it
        until the next heading of the same or higher level (fewer or equal #s).
        """
        result = []
        deleting = False
        target_level = None

        for line in lines:
            m = HEADING_RE.match(line)
            if m:
                level      = len(m.group(1))
                full_heading = line.strip().lower()   # e.g. "## omissions and coverage analysis"

                if not deleting:
                    if full_heading in REMOVE_HEADERS_LOWER:
                        deleting = True
                        target_level = level
                    else:
                        result.append(line)
                else:
                    if level <= target_level:
                        # Same or higher heading — stop deleting
                        deleting = False
                        if full_heading in REMOVE_HEADERS_LOWER:
                            deleting = True
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
        for md_file in input_folder.rglob("*.md"):
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
                lines = md_file.read_text(encoding="utf-8").splitlines(keepends=True)
                new_lines, changed = process_fn(lines)
                output_path.write_text("".join(new_lines), encoding="utf-8")
                status = "Updated" if changed else "No Change"
                print(f"  {status}: {relative_path}")
                if changed:
                    modified += 1
            except Exception as e:
                print(f"  Error:   {relative_path} → {e}")

        print(f"  Total: {total}  |  Modified: {modified}")


    # ================= RUN ==================

    # Step 1 — remove TRACE tags → save to Trace_Removed/
    process_folder(
        INPUT_FOLDER, TRACE_FOLDER,
        lambda lines: remove_Trace(lines),
        "Step 1: Removing TRACE tags"
    )

    # Step 2 — remove named sections → read Trace_Removed/, save to FINAL_SPEC/
    process_folder(
        TRACE_FOLDER, FINAL_FOLDER,
        lambda lines: (remove_content_after_headers(lines), True),
        "Step 2: Removing named sections"
    )

    print("\n All done. Final files in:", FINAL_FOLDER)
