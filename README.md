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
                    except Exception:
                        tree = False

                if tree and tree is not False and _jl and declaration_types_early:
                    for _, decl in tree.filter(declaration_types_early):
                        name = getattr(decl, "name", None)
                        if name:
                            _add(name, fpath)
                            if name.endswith("Impl"):
                                _add(name[:-4], fpath)
                else:
                    text = file_content_cache.get(fpath, "")
                    for _m in _decl_re_early.finditer(text):
                        name = _m.group(1)
                        _add(name, fpath)
                        if name.endswith("Impl"):
                            _add(name[:-4], fpath)
