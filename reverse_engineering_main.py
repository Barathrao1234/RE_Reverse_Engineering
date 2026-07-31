import os,json,sys
import pandas as pd
from pathlib import Path
import yaml


from dotenv import load_dotenv

CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parent.parent           # C:\Tradaa_Reverse_Engineering
sys.path.append(str(PROJECT_ROOT))                  # Ensure Methods is importable

# --- .env ---".env"
load_dotenv()
CONFIGURATION_FILE = r"C:\Users\Barath\Desktop\Reverse_Engineering_rabobank\config.json"

from numbering_empty_line_removal import process_files


from java_adapter import JavaAdapter

from method_lineage_generation import method_lineage
from Method_Detailed_Flow_Generation import generate_method_level_hierarchy
from Application_Based_Methods import extract_methods_from_project

# post llm
from merged_sheet_read import demerge
from llm_input_sheet import llm_input_sheet
from code_extraction_bef_summary12_2_section_wise_updated_rpg_sql import code_extraction
from chunks_formation_changes_for_java_trigger_node import chunks_formation
# from endpoint_chunks_flow import getting_endpoint_chunk_flow
from llm_call_summary_gen_for_end_to_end_integration import summary_generation

# from Methods.For_end_to_end_work.validation_scripts.LLM_output_coverage import validate_sections
from functional_spec_clearance import clean_functional_spec

# validation
from validation.recursive_chunk_vs_spec_line_number_validation import recursive_chunk_vs_spec_line_number_validation
from validation.Traceability_matrix import traceability_matrix
from validation.Coverage_Classifier_for_missing_lines import missing_lines_coverage
# document_generation
from Markdown2Word_v3.script_v2 import md_to_docs
from remove_source_line_range_v1 import clean_folder

# OUTPUT_DIR = os.getenv("TEST_RESULTS")
OUTPUT_DIR = os.getenv("INTEGRATION_TEST_RESULTS")

os.makedirs(OUTPUT_DIR, exist_ok=True)

def reverse_engineering(technology,application,layer,OUTPUT_PATH,LLM_type):
    
    
    with open(CONFIGURATION_FILE, "r") as file:
        data = json.load(file)
    
    technology = technology.lower()
    application = application.lower()

    details = data["Language"][technology]["Application"][application]["Language_details"]
    regex = data["Language"][technology]["Application"][application]["Regex_Pattern"]
    results = data["OUTPUT_PATHS"]
    llm_prompts = data["Language"][technology]["Prompt_path"]
    java_adapter = JavaAdapter()
    

    if ".java" in details["extension"]:
        if application== "day_trade":
            base = os.getenv("JAVA_CONTROLLER_PATH")

        
        entry_point = r"C:\Users\Barath\Downloads\groups.json"
        
        base_norm = Path(base).as_posix().rstrip('/')
        with open(entry_point, "r", encoding="utf-8") as f:
            group_data = json.load(f)
        java_paths = group_data["groups"]["rule"]["files"]

        FILE_CONTROLLER_FILES = []
        for p in java_paths:
            # Normalize full path
            p_norm = Path(p).as_posix().rstrip('/')
            # Remove java_base from path
            if p_norm.startswith(base_norm):
                relative_path = p_norm[len(base_norm):].lstrip('/')
                FILE_CONTROLLER_FILES.append(relative_path)
        # ALL_METHODS = r"C:\Users\Barath\Downloads\methods.xlsx"
        # # print("getting method_lineage")
        # # ALL_METHODS = r"C:\TRADA_RESULTS\java_sundaram_finance\pl_sql\test_4\007_Method_Metadata.xlsx"
        
        ALL_METHODS = extract_methods_from_project(details["extension"],details["SERVICE_PROJECT_PATH"],OUTPUT_PATH, results["METHOD_LINEAGE_EXCEL_FILE"])
        ALL_METHODS = method_lineage(adapter=java_adapter,details=details,data=data,technology=technology,application=application,
                app_folder=details["SERVICE_PROJECT_PATH"],OUTPUT_DIR=OUTPUT_PATH,groups=entry_point,
                all_methods = ALL_METHODS,controller_files = FILE_CONTROLLER_FILES,include_unqualified=True,
                accept_local_new_types=True,accept_parameter_types=True,accept_same_package=True)
        print("METHOD_LINEAGE_EXCEL : ",ALL_METHODS)
        
        print("method lineage generated")

        print("generating method flow")
        METHOD_CONTROLLER_FILES = []
        for controller in FILE_CONTROLLER_FILES:
            METHOD_CONTROLLER_FILES.append(os.path.splitext(os.path.basename(controller))[0])

        print("METHOD_CONTROLLER_FILES : ",METHOD_CONTROLLER_FILES)
        METHOD_FLOW = generate_method_level_hierarchy(details,OUTPUT_PATH,ALL_METHODS, details["AST_SHEET"], METHOD_CONTROLLER_FILES, details["SERVICE_PROJECT_PATH"], results["METHOD_FLOW_OCCURRENCE_DISTRIBUTION"],details["DVT_FILES"])
        print("method_flow generated")
        
        DEMERGED_FLOW = demerge(METHOD_FLOW,OUTPUT_PATH,results["DEMERGED_FLOW"])
        # # UNIQUE_PATH_EXCEL = unique_paths_to_groups(DEMERGED_FLOW)
        # # UNIQUE_GROUP_EXCEL = uniquegroupsforming(UNIQUE_PATH_EXCEL)
        
        CHUNK_EXCEL,program_or_process,CHUNK_LIMIT= chunks_formation(DEMERGED_FLOW)
        LLM_CHUNK_XLSX = llm_input_sheet(CHUNK_EXCEL,METHOD_FLOW,OUTPUT_PATH,results["LLM_CHUNK_EXCEL"],DEMERGED_FLOW,PARENT_SHEET = "Parent_to_Chunks",GRAPH_SHEET  = "Child_Graph_Reduced",GROUPS_SHEET = "Sheet1_GroupMap")

        project_code_with_id = process_files(details["SERVICE_PROJECT_PATH"],OUTPUT_PATH,id_mode="physical")
        chunk_text,COMBINED_DIR = code_extraction(project_code_with_id,details["extension"],METHOD_FLOW,OUTPUT_PATH,LLM_CHUNK_XLSX,CHUNK_LIMIT,level="process",chunks_sheet = "Parent_to_Chunks_Updated",group_sheet = "group_mappings",spec_sheet = "application.properties",call_sheet=None,ui_extension=None)
       

        Sections_in_narrative = [
            "business_functional_name",
            "core_business_functionality",
            "business_function",
            "business_functions_list",
            "business_rules"
        ]
        final_spec_each_chunk,final_spec_parent_chunk_for_tech_spec,final_spec_parent_chunk_for_func_spec = summary_generation(chunk_text,LLM_CHUNK_XLSX,Sections_in_narrative,llm_prompts["java_functional_prompt"],details["extension"],program_or_process,LLM_type = LLM_type)
        
        missing_lines_report = recursive_chunk_vs_spec_line_number_validation(
            str(LLM_CHUNK_XLSX),
            str(COMBINED_DIR),
            str(final_spec_parent_chunk_for_tech_spec),
            str(OUTPUT_PATH),
            str(results["recursive_chunk_vs_spec_line_number_validation"]),
            chunk_sheet=0,
            group_sheet=None,
            group_input_dir=None
        )

        traceability_matrix_report = traceability_matrix(final_spec_parent_chunk_for_tech_spec,project_code_with_id,results["traceability_matrix"])
        missing_content_coverage_report = missing_lines_coverage(OUTPUT_PATH,final_spec_parent_chunk_for_tech_spec, fs_df = traceability_matrix_report, missing_df = missing_lines_report, OUTPUT_FILE = results["missing_lines_coverage_report"])


        #     print("getting technical flow")
        #     Get_UI_Flow(TECHNICAL_SPEC_DIR,LLM_type,gemma_url,GEMINI_MODEL,OUTPUT_PATH,flow_prompt=llm_prompts["java_technical_flow_prompt"],DIRECTORY="TECHNICAL_FLOW",timeout_sec=800)

        # final_spec_document = md_to_docs(
        #     final_spec_parent_chunk_for_tech_spec,
        #     OUTPUT_PATH,
        #     Spec_type="PROGRAM_SPECIFICATION"
        # )

        # spec_without_source_line = clean_folder(final_spec_document)
        # clean_functional_spec(spec_without_source_line,OUTPUT_PATH,FOLDER="Final_Functional_Spec")

        


if __name__ == "__main__":
    
    technology = input("Enter Technology : ")
    application = input("Enter application : ")
    layer = input("Enter layer : ")
    technology_folder = os.path.join(OUTPUT_DIR,technology)
    application_folder = os.path.join(technology_folder, application)
    OUTPUT_PATH = os.path.join(application_folder, layer)

    os.makedirs(OUTPUT_PATH, exist_ok=True)
    print("Reports will save in : ",OUTPUT_PATH)

    # ── Tell VS Code extension the BASE_DIR ──
    config_path = os.path.join(r"C:\Users\Barath\Desktop\Reverse_Engineering_rabobank\copilot-bridge", ".copilot_bridge_config.json")
    
    with open(config_path, "w") as f:
        json.dump({"baseDir": OUTPUT_PATH}, f)
    print(f"[Config] Written OUTPUT_PATH → {config_path}")

    LLM_type = input("Which LLM to Use? : ")
    reverse_engineering(technology,application,layer,OUTPUT_PATH,LLM_type)

    