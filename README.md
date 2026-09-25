# --- LINEAGE_DEBUG_ENTRY_POINT + LINEAGE_DEBUG_METHOD_NAME early probe ---  ← ADD FROM HERE
    _early_dbg_file  = os.environ.get("LINEAGE_DEBUG_ENTRY_POINT") or details.get("debug_entry_point") or ""
    _early_dbg_method = os.environ.get("LINEAGE_DEBUG_METHOD_NAME") or details.get("debug_method_name") or ""
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

    # ---- Optional chain resolution ----                                        ← LINE 2478 CONTINUES HERE
