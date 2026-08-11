if tree and tree is not False and _jl and declaration_types_early:
    found = list(tree.filter(declaration_types_early))
    print(f"[DEBUG] AST filter found {len(found)} declarations in {os.path.basename(fpath)}")
    for _, decl in found:
        print(f"  decl type={type(decl).__name__}, name={getattr(decl, 'name', None)}")
    for _, decl in found:
        name = getattr(decl, "name", None)
        if name:
            _add(name, fpath)
            if name.endswith("Impl"):
                _add(name[:-4], fpath)
