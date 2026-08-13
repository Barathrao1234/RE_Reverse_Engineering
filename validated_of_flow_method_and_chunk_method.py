
import json,re,os
import pandas as pd
from pathlib import Path

# def extract_entities_from_json(json_path, group_key="controller"):
#     with open(json_path, "r", encoding="utf-8") as f:
#         data = json.load(f)

#     run_id = data.get("id", "")
#     groups = data.get("groups", {}) or {}
#     entities = []

#     if group_key in groups and isinstance(groups[group_key], dict):
#         files = groups[group_key].get("files", []) or []
#         for fpath in files:
#             try:
#                 entities.append(Path(fpath).stem)
#             except Exception:
#                 entities.append(str(fpath).split(".")[0])
#     else:
#         for grp in groups.values():
#             files = (grp or {}).get("files", []) or []
#             for fpath in files:
#                 try:
#                     entities.append(Path(fpath).stem)
#                 except Exception:
#                     entities.append(str(fpath).split(".")[0])

#     seen = set()
#     unique_entities = []
#     for e in entities:
#         if e not in seen:
#             unique_entities.append(e)
#             seen.add(e)

#     return run_id, unique_entities

def extract_entities_from_json(json_path, group_key="entry_point"):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    groups = data.get("groups", {}) or {}
    entities = []

    if group_key in groups and isinstance(groups[group_key], dict):
        files = groups[group_key].get("files", []) or []
        for fpath in files:
            entities.append(str(Path(fpath).with_suffix("")))
    else:
        for grp in groups.values():
            files = (grp or {}).get("files", []) or []
            for fpath in files:
                entities.append(str(Path(fpath).with_suffix("")))

    seen = set()
    unique_entities = []
    for e in entities:
        if e not in seen:
            unique_entities.append(e)
            seen.add(e)

    return unique_entities

def get_all_occurrences_from_flow(xlsx_path, sheet_name, entity, header=None):
    """
    Scan ONLY the first column of the sheet and return ALL cell values that start with '<entity>.'.

    Example:
        entity='abc' -> matches 'abc.method no_of_lines : 10', 'abc.anotherMethod no_of_lines : 5', etc.

    Returned list is ordered by sheet scan (top-to-bottom).
    """
    df = pd.read_excel(xlsx_path, sheet_name=sheet_name, header=header, engine="openpyxl")

    matches = []
    first_col = df.iloc[:, 0]  # Select the first column only

    for val in first_col:
        if pd.notna(val):
            s = str(val).strip()
            if s.startswith(entity + "."):
                matches.append(s)
    return matches


# ============================================================
def get_subpaths_for_non_common(xlsx_path, sheet_name, non_common_values):
    """
    From impact sheet, for each ID in non_common_values, pull 'SubPath' and split by arrows.
    Returns dict: { ID -> [subpath parts] }
    """
    df = pd.read_excel(xlsx_path, sheet_name=sheet_name, engine="openpyxl")
    df["GID"] = df["GID"].astype(str).str.strip()

    # Support various arrow representations (e.g., '->', '- >', HTML-decoded)
    split_pattern = r"\s*,\s*"

    result = {}
    for val in non_common_values:
        key = str(val).strip()
        rows = df[df["GID"] == key]
        if not rows.empty:
            subpath = rows.iloc[0]["Methods"]
            if pd.notna(subpath):
                parts = [
                    p.strip()
                    for p in re.split(split_pattern, str(subpath))
                    if p.strip()
                ]
                result[key] = parts

    # print("subpaths_for_non_common : ",result)
    return result


def get_dependents_by_a(xlsx_path, sheet_name, a_value, header=None):
    """
    Given an Excel sheet, locate the first cell that EXACTLY matches 'a_value',
    then traverse downward collecting right-side non-empty values until the hierarchy ends.

    NOTE: This function correctly iterates Series values using 'row.values'.
    """
    df = pd.read_excel(
        xlsx_path,
        sheet_name=sheet_name,
        header=header,
        engine="openpyxl"
    )

    # Normalize column names to simple indices for consistent handling
    df.columns = [f"col{i}" for i in range(df.shape[1])]
    a_value = str(a_value).strip()

    # Find the row & column where a_value exists (exact match)
    start_row = start_col = None

    for r_idx, row in df.iterrows():
        values = list(row.values)  # ensure we iterate over values, not labels
        for c_idx, val in enumerate(values):
            if pd.notna(val) and str(val).strip() == a_value:
                start_row = r_idx
                start_col = c_idx
                break
        if start_row is not None:
            break

    # If not found, return empty
    if start_row is None:
        return []

    dependents = []

    # Traverse downward from the found row
    for r_idx in range(start_row, len(df)):
        row = df.iloc[r_idx]
        values = list(row.values)

        # Find first non-empty column in the row
        non_empty_cols = [
            idx for idx, v in enumerate(values)
            if pd.notna(v) and str(v).strip()
        ]

        if not non_empty_cols:
            continue

        first_col = non_empty_cols[0]

        # Stop when hierarchy ends (first non-empty column moved to the left of start_col)
        if r_idx != start_row and first_col <= start_col:
            break

        # Collect right-side non-empty values (to the right of the 'a_value' column)
        for c_idx in non_empty_cols:
            if c_idx > start_col:
                dependents.append(str(values[c_idx]).strip())

    # Include the anchor at the beginning
    return [str(a_value).strip()] + dependents

def traverse_chain(start_chunk_id, child_graph_df, parent_chunks_df):
    local_results = []
    current_chunk_id = start_chunk_id

    # ---- ALWAYS try to fetch filenames for this chunk_id ----
    chunk_match = parent_chunks_df[
        parent_chunks_df["chunk_id"] == current_chunk_id
    ]
    if not chunk_match.empty:
        for s in chunk_match["clear_methods"].dropna():
            # split by comma (multiple entries per cell) → take text before 'no_of_lines'
            local_results.extend(
                [part.split(' no_of_lines')[0].strip()
                for part in str(s).split(',')
                if part.strip()]
            )

    # ---- Find children ----
    next_rows = child_graph_df[
        child_graph_df["parent_chunk_id"] == current_chunk_id
    ]

    # ---- If children exist, traverse them ----
    for _, r in next_rows.iterrows():
        local_results.append(r["child_entity"])
        local_results.extend(
            traverse_chain(r["child_chunk_id"], child_graph_df, parent_chunks_df)
        )

    return local_results


def process_excel(method_path, sheet1, excel_path, unique_entities):
    # Load sheets
    child_graph_df = pd.read_excel(excel_path, sheet_name="Child_Graph_Reduced")
    parent_chunks_df = pd.read_excel(excel_path, sheet_name="Parent_to_Chunks_Updated")

    result_list = []

    # Step 1: Search unique_entities in parent_entity column
    for entity in unique_entities:
        # if entity != "MacsTxController":
        #     continue
        occurrences = get_all_occurrences_from_flow(method_path, sheet1, entity, header=None)

        if not occurrences:
            continue

        normalized = [x.split()[0].strip() for x in occurrences]

        # Process ALL occurrences of MacsTxController.processTx
        for occ in normalized:
                # print("class.method : ",occ)
                # print("\n\n")
            # if occ == "MacsTxController.generateEscSummPDF":
                # print(f"Processing flow for {occ}")

                matches = child_graph_df[child_graph_df["parent_entity"] == occ]

                for _, row in matches.iterrows():
                    # ===============================
                    # ADD PARENT CHUNK FIRST
                    # ===============================
                    parent_chunk_id = row["parent_chunk_id"]

                    parent_match = parent_chunks_df[
                        parent_chunks_df["chunk_id"] == parent_chunk_id
                    ]

                    if not parent_match.empty:
                        for s in parent_match["clear_methods"].dropna():
                            # split by comma (multiple entries per cell) → take text before 'no_of_lines'
                            result_list.extend(
                                [part.split(' no_of_lines')[0].strip()
                                for part in str(s).split(',')
                                if part.strip()]
                            )

                    result_list.append(row["parent_entity"])

                    child_chunk_id = row["child_chunk_id"]
                    result_list.append(row["child_entity"])

                    result_list.extend(traverse_chain(child_chunk_id,child_graph_df,parent_chunks_df))
                # print("all methods : ",result_list)


    return result_list


def store_results_to_excel(
        OUTPUT_FILE,
        a_value,
        normalized_list1,
        normalized_updated_list2,
        common_values,
        non_common_values
    ):
        row = {
            "Endpoint": a_value,
            "list_of_file_flow_method": ", ".join(normalized_list1),
            "no_of_file_flow_method": len(normalized_list1),
            "list_of_chunk_method": ", ".join(normalized_updated_list2),
            "no_of_chunk_method": len(normalized_updated_list2),
            "common_values": ", ".join(common_values),
            "len_common_values": len(common_values),
            "non_common_values": ", ".join(non_common_values),
            "len_non_common_values": len(non_common_values),
        }

        output_file = Path(OUTPUT_FILE)

        if output_file.exists():
            existing_df = pd.read_excel(output_file, engine="openpyxl")
            new_df = pd.concat([existing_df, pd.DataFrame([row])], ignore_index=True)
        else:
            new_df = pd.DataFrame([row])

        new_df.to_excel(output_file, index=False, engine="openpyxl")


def Validation_flow_method_and_chunk_method(method_path,chunk_file_path,groups,OUTPUT_PATH,output_file,line_count_path):
    
    sheet1 = "Original Flow"
    impact_sheet = "group_mappings"

    run_id, unique_entities = extract_entities_from_json(groups)
    # print(unique_entities)


    processed_review_task = False
    for entity in unique_entities:
        print("=" * 100)
        print(f"Processing entity: {entity}")
        # if entity != "MacsTxController":
        #     continue
        occurrences = get_all_occurrences_from_flow(
                    method_path, sheet1, entity, header=None
                )
        if not occurrences:
            print(f"  [SKIP] No occurrences found in '{sheet1}' for entity '{entity}'.")
            print("=" * 100)
            print()
            continue

        print(f"  Found {len(occurrences)} occurrence(s) in '{sheet1}':")
        for idx, occ in enumerate(occurrences, start=1):
            # print("OCC : ",occ)
            normalized = occ.split()[0]
            # print("normalized : ",normalized)
            # if not processed_review_task and "MacsTxController.generateEscSummPDF" in normalized:
            #     processed_review_task = True
            list1 = get_dependents_by_a(method_path, sheet1, occ, header=None)
            list_of_file_flow_method = [str(x) for x in list1]
            normalized_flow_method_list = [x.split()[0] for x in list_of_file_flow_method]
            lines = []
            for l in list_of_file_flow_method:
                lines.append(l.split()[3])
            # print("no_of_methods: ",len(list_of_file_flow_method)) 
            total_lines = sum(int(x) for x in lines if x.isdigit())
            # print("total number of lines for method : ",total_lines) 

            lines1 = []

            unique_flow_method = list(set(list_of_file_flow_method))
            for l1 in unique_flow_method:
                lines1.append(l1.split()[3])
            # print("no_of_unique_methods: ",len(unique_flow_method)) 
            unique_total_lines = sum(int(x) for x in lines1 if x.isdigit())
            # print("total number of lines for unique_method: ",unique_total_lines) 

            def store_line_count_in_excel(lines_output_path,occ,list_of_file_flow_method,no_of_method,total_lines,unique_flow_method,no_of_unique_methods,unique_total_lines):
                row = {
                    "method": occ,
                    "flow_methods": list_of_file_flow_method,
                    "no_of_file_flow_method": no_of_method,
                    "total_lines_of_method": total_lines,
                    "unique_flow_method": unique_flow_method,
                    "no_of_chunk_method": no_of_unique_methods,
                    "total_lines_of_unique_method": unique_total_lines
                }
                
                output_file = Path(lines_output_path)
                if output_file.exists():
                    existing_df = pd.read_excel(output_file, engine="openpyxl")
                    new_df = pd.concat([existing_df, pd.DataFrame([row])], ignore_index=True)
                else:
                    new_df = pd.DataFrame([row])

                new_df.to_excel(output_file, index=False, engine="openpyxl")

            # lines_output_path = r"C:\Tradaa_Reverse_Engineering\Lines_of_method_and_unique_method_all.xlsx"
            line_count_path = os.path.join(OUTPUT_PATH,line_count_path)
            store_line_count_in_excel(line_count_path,occ,list_of_file_flow_method,len(list_of_file_flow_method),total_lines,unique_flow_method,len(unique_flow_method),unique_total_lines)

            final_results = process_excel(method_path,sheet1,chunk_file_path, unique_entities)
            # print(final_chunk_methods)

            subpaths_map = get_subpaths_for_non_common(chunk_file_path, impact_sheet, final_results)
            # print(subpaths_map)
            new_chunk_method_list = []
            for item in final_results:
                key = str(item).split()[0]
                if key in subpaths_map:
                    new_chunk_method_list.extend(subpaths_map[key])
                else:
                    new_chunk_method_list.append(item)
            

            seen = set()
            deduped_updated_chunk_method_list = []
            for x in new_chunk_method_list:
                key = str(x).strip()
                if key not in seen:
                    deduped_updated_chunk_method_list.append(x)
                    seen.add(key)

            # print("count deduped : ",deduped_updated_chunk_method_list)

            # print("updated_chunk_method_list : ",updated_chunk_method_list)

            normalized_chunk_list = [ str(x).split()[0] for x in deduped_updated_chunk_method_list]
            # print("normalized_chunk_list : ",normalized_chunk_list)
            # print("count : ",len(normalized_chunk_list))
            common_values = [value for value in normalized_flow_method_list if value in normalized_chunk_list]
            non_common_values = [value for value in normalized_flow_method_list if value not in normalized_chunk_list]

            OUTPUT_FILE = os.path.join(OUTPUT_PATH,output_file)
            store_results_to_excel(
                OUTPUT_FILE,
                occ,
                normalized_flow_method_list,
                normalized_chunk_list,
                list(common_values),
                list(non_common_values)
            )

            print(f"Stored results for entity '{entity}'.")
            
            print("   Lines_of_method_and_unique_method saved")
            print()


method_path = r"C:\Tradaa_Reverse_Engineering_Results\JAVA\MACS\SERVICE\007.1_Method_Detailed_Flow_Occurrence_Distribution.xlsx"
# chunk_file_path = r"C:\Users\2782646\Downloads\chunks_flow_with_reusability_15_12.xlsx"
chunk_file_path = r"C:\Tradaa_Reverse_Engineering_Framework\Input\macs_llm_input_calls_27_1.xlsx"
# impact_file_path = r"C:\Users\2782646\Downloads\15_12_macs_uniquepaths.xlsx"
# json_path = r"C:\Tradaa_Reverse_Engineering_Results\JAVA\MACS\SERVICE\002_Standard_File_Name_Keyword_Analysis_Report.json"
json_path = r"C:\TRADA_RESULTS\JAVA\ADC\F_SERVICE\002_Standard_File_Name_Keyword_Analysis_Report.json"
OUTPUT_PATH = r"C:\Tradaa_Reverse_Engineering\Methods"
VALIDATION_EXCEL = "adc_valid_3.xlsx"
ENDPOINT_LEVEL_LINE_COUNT = "adc_line_count.xlsx"
Validation_flow_method_and_chunk_method(method_path,chunk_file_path,json_path,OUTPUT_PATH,VALIDATION_EXCEL,ENDPOINT_LEVEL_LINE_COUNT)
