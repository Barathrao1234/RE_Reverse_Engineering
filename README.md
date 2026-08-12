def transform_levels(df: pd.DataFrame, level_prefix: str = "Level_") -> pd.DataFrame:
    # Identify level columns in order
    level_cols = [c for c in df.columns if c.startswith(level_prefix)]
    try:
        level_cols = sorted(level_cols, key=lambda c: int(c.split("_")[1]))
    except Exception:
        pass

    if not level_cols:
        raise ValueError("No level columns found.")

    # Leave Level_1 unchanged; process from Level_2 to Level_{n-1}
    for i in range(1, len(level_cols) - 1):
        cur_col  = level_cols[i]
        next_col = level_cols[i + 1]

        cur_ffill = df[cur_col].ffill()

        # Only fill rows that are BOTH:
        #   1. currently NaN in cur_col  (merged/blank cell — no real value)
        #   2. have a child in next_col  (so they need the parent label propagated)
        mask = df[cur_col].isna() & df[next_col].notna()
        df.loc[mask, cur_col] = cur_ffill.loc[mask]

    return df
