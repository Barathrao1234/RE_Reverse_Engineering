for fpath in source_files:
                if fpath not in file_content_cache:
                    try:
                        _ = read_file_cached(fpath)
                    except Exception:
                        file_content_cache[fpath] = ""

                # Always parse from raw file text — never use raw_ast_cache here.
                # raw_ast_cache may contain a stripped AST written by _file_worker
                # subprocesses (which parse comment-stripped source), causing
                # tree.filter(ClassDeclaration) to return 0 results.
                tree = None
                raw_text = file_content_cache.get(fpath, "")
                if raw_text and _jl:
                    try:
                        tree = _jl.parse.parse(raw_text)
                    except Exception as e:
                        print(f"[DEBUG] javalang FAILED for {os.path.basename(fpath)}: {e}")
                        tree = False

                print(f"[DEBUG] fpath={os.path.basename(fpath)}, tree={tree is not False and bool(tree)}, cache_len={len(raw_text)}")

                if tree and tree is not False and _jl and declaration_types_early:
                    found = list(tree.filter(declaration_types_early))
                    print(f"[DEBUG] AST filter found {len(found)} declarations in {os.path.basename(fpath)}")
                    for _, decl in found:
                        name = getattr(decl, "name", None)
                        print(f"  decl type={type(decl).__name__}, name={name}")
                        if name:
                            _add(name, fpath)
                            if name.endswith("Impl"):
                                _add(name[:-4], fpath)
                else:
                    text = file_content_cache.get(fpath, "")
                    matches = list(_decl_re_early.findall(text))
                    print(f"[DEBUG] regex fallback for {os.path.basename(fpath)}, text_len={len(text)}, matches={matches}")
                    for _m in _decl_re_early.finditer(text):
                        name = _m.group(1)
                        _add(name, fpath)
                        if name.endswith("Impl"):
                            _add(name[:-4], fpath)
