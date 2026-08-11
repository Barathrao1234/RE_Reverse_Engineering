# ── DEBUG ──
print(f"[DEBUG] _all_project_files_early count: {len(_all_project_files_early)}")
print(f"[DEBUG] type_to_path_full_early count: {len(type_to_path_full_early)}")
print(f"[DEBUG] RequestDetails in type_to_path_full_early: {type_to_path_full_early.get('RequestDetails')}")
print(f"[DEBUG] SearchPeriod in type_to_path_full_early: {type_to_path_full_early.get('SearchPeriod')}")

# Check if the file even exists in the walk
for _fp in _all_project_files_early:
    if 'RequestDetails' in os.path.basename(_fp):
        print(f"[DEBUG] Found file matching RequestDetails: {_fp}")
