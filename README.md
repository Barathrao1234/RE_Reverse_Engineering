print(f"[DEBUG] java_files found by BFS : {len(java_files)}")
print(f"[DEBUG] ast_results rows        : {len(ast_results)}")
print(f"[DEBUG] method_map classes      : {len(method_map)}")
print(f"[DEBUG] errors from parsing     : {len(errors)}")
if errors:
    for e in errors[:5]:  # show first 5 errors
        print(f"  ERROR: {e}")
