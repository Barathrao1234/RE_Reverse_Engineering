# Also populate main-process file_content_cache for LOC computation
                if _file_path not in file_content_cache:
                    try:
                        with open(_file_path, "r", encoding="utf-8") as _fh:
                            file_content_cache[_file_path] = _fh.read()
                    except UnicodeDecodeError:
                        with open(_file_path, "r", encoding="latin-1") as _fh:
                            file_content_cache[_file_path] = _fh.read()
                    except Exception:
                        pass
