path_part, methodname = class_method.rsplit(".", 1)
classname = path_part  # ← keep full path, not just basename
try:
    method_line_map[(classname.lower(), methodname.lower())] = int(line_count) if line_count not in (None, "", "nan") and str(line_count).strip() not in ("", "nan", "None") else None
except (ValueError, TypeError):
    method_line_map[(classname.lower(), methodname.lower())] = None


==============================

# Only blank cells where line count is a real 0 or unresolved None — not legitimate counts
zero_pattern = re.compile(r'no_of_lines\s*:\s*(0|None)$', re.IGNORECASE)
=======================

if orig_row is None:
    ext_line_count = method_line_map.get(
        (classname.lower(), methodname.lower()), None
    )
    display_cls = os.path.splitext(os.path.basename(classname))[0]
    # Use '?' sentinel so filter_zero_line_cells does NOT blank unresolved methods
    line_display = ext_line_count if ext_line_count is not None else "?"
    external_node = f"{display_cls}.{methodname} no_of_lines : {line_display}"
    return [[external_node]]

    
