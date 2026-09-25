# AFTER
declared_types = list(adapter.get_declared_types(ast))

# ast succeeded but returned no types — javalang silently dropped the class
# (e.g. unsupported annotation syntax). Retry with comment-stripped source
# before falling back, because stripping often resolves the silent failure.
if not declared_types and code_for_ast is not code_no_comments:
    _retry_ast = adapter.parse_ast(code_no_comments)
    if _retry_ast:
        _retry_types = list(adapter.get_declared_types(_retry_ast))
        if _retry_types:
            ast = _retry_ast
            code_for_ast = code_no_comments
            declared_types = _retry_types

if not declared_types:
    # Still empty after retry — use per_method_calls fallback so real method
    # names are preserved instead of collapsing everything to "UnknownMethod".
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
