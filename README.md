cls, meth = subchild.rsplit(".", 1)

# Resolve short classname to full path using df_orig
if not os.path.isabs(cls) and "\\" not in cls and "/" not in cls:
    matched = df_orig[df_orig['classname'].apply(
        lambda x: os.path.splitext(os.path.basename(x))[0].lower()
    ) == cls.lower()]
    if not matched.empty:
        cls = matched.iloc[0]['classname']
