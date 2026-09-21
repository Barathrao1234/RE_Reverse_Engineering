For Single-Shot Execution
Stage 1 (Inventory Ledger):

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 1 (SINGLE-SHOT)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are currently operating in: MODE 3 (MASTER / SINGLE-SHOT).
No child contracts are supplied. The attached source represents the complete analytical scope.

Orchestrator Node Context:
ORCHESTRATOR_NODE_ID: <INJECT_UNIQUE_ID_HERE>

Execute PHASE 1 ONLY (Pre-FSD Behavioral Coverage Inventory & Accounting).
Analyze the attached source and print the complete Source-Behavior Accounting Ledger as a Markdown table.

The table MUST contain these exact columns:
| Inventory ID (INV-<ORCHESTRATOR_NODE_ID>-<id>) | Observable Behavior | Trigger / Context | Source Line Ranges | Primary Behavioral Classification | Planned Primary Artifact Type | Planned Target Artifact ID | Planned Disposition (Mapped / Explicitly Omitted) | Scenario Membership (FS-SCN-<n> / MULTIPLE:<FS-SCN-<n>, ...> / NOT-SCENARIO-DISTINCT) |

Rules:
1. Use these Primary Behavioral Classification values: FLOW, BUSINESS RULE, EXCEPTION, DATA CONTRACT, SQL_MAPPING, INTEGRATION, MISSING IMPLEMENTATION, ANOMALY, UNRESOLVED EVIDENCE, NON_BEHAVIORAL TECHNICAL.
2. N-to-1 Consolidation: Multiple inventory rows MUST share the same Planned Target Artifact ID if they belong to the same semantic owner (e.g., map 10 field copies to a single DS-REQ-* Data Specification structure).
3. Pre-Planning Rule Audit: Before assigning a Planned Target Artifact ID of FS-BRL-*, validate that the behavior adds independent policy content. If it is merely data movement or flow sequence, plan it for Section 8 or Section 4 instead.
4. Do NOT include summary or meta-accounting rows inside the ledger table itself.
5. If the table exceeds token limits, break cleanly at a row boundary and emit: [CONTINUATION REQUIRED - NEXT: <Inventory ID>].

Immediately after the table is complete, print exactly one accounting line:
STAGE 1 ACCOUNTING: Total Inventoried: <n> | Planned Mapped: <n> | Planned Explicitly Omitted: <n> | Unaccounted: 0

Do NOT generate Sections 1-12.
Do NOT generate Appendices A-B.
Stop immediately after printing the footer.

Stage 2 (FSD Generation & Closure):

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 2 (SINGLE-SHOT)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Now, using EXACTLY the Source-Behavior Accounting Ledger you printed in Stage 1 as your strict baseline, generate the COMPLETE FINAL FUNCTIONAL SPECIFICATION (Sections 1-12 and Appendices A-B).

Requirements for Stage 2:
1. Stage 1 inventory IDs, source ranges, provenance, and mapped/omitted dispositions are immutable. Every single item from the Stage 1 table must be accounted for with zero items dropped. Ensure N-to-1 consolidated items are properly grouped.
2. Carry the exact deduplicated identifiers from the Stage 1 table into the PROVENANCE array, and the deduplicated source coordinates into the RANGES array.
3. Pre-Generation Rule Audit: Before emitting Section 5, validate every proposed FS-BRL. If it is merely a field copy, object assignment, SQL binding, or duplicates an FS-MPF with no additional policy content, reclassify the item to Section 8 or Section 4 and DO NOT create the FS-BRL. 
4. Routing Corrections: If any routing target was altered from Stage 1, you must emit the `ROUTING CORRECTION DISCLOSURE` ledger as Section 11.5 before the Final Validation Checklist.
5. Omission Routing (Invariant 3): Materialize items into Section 11.1, 11.2, or 11.3 exactly according to ACCOUNTING DISPOSITION SEMANTICS.
6. Single-Shot Section 11.4 Rule: Section 11.4 must contain exactly: "N/A - Single-Shot Execution (Governed by Stage 1 Source-Behavior Ledger)."
7. Close the accounting loop in the Completeness Signature by verifying:
   - Total Inventoried Source Behaviors (Stage 1) = Mapped + Explicitly Omitted + Unaccounted (Must be 0).
   - Total Ingested BHV Contracts must be explicitly logged as 0.
   - Total BHV Disposition Ledger Rows must be explicitly logged as 0 / 0 Accounted For.
   Ensure the stated Total Inventoried count matches the Stage 1 Accounting footer literal count.


For Mode 1 (Leaf / Child - Generates BHV Contract)
Stage 1 (Leaf Inventory):

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 1 (MODE 1 LEAF)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are currently operating in: MODE 1 (LEAF / CHILD).

Orchestrator Node Context:
ORCHESTRATOR_NODE_ID: <INJECT_UNIQUE_ID_HERE>

Execute PHASE 1 ONLY.
Analyze the attached raw source and print the Leaf Source-Behavior Accounting Ledger as a Markdown table. Do NOT assign final FS-* IDs in this mode.

The table MUST contain these exact columns:
| Inventory ID (INV-<ORCHESTRATOR_NODE_ID>-<id>) | Observable Behavior | Trigger / Context | Source Line Ranges | Primary Behavioral Classification | Planned Primary Artifact Type | Planned Target Destination | Planned Disposition (Mapped to BHV / Explicitly Omitted) | Scenario Membership (LOCAL-SCN-<ORCHESTRATOR_NODE_ID>-<n> / MULTIPLE:<LOCAL-SCN-<ORCHESTRATOR_NODE_ID>-<n>, ...> / NOT-SCENARIO-DISTINCT) |

Rules:
1. Use these Primary Behavioral Classification values: FLOW, BUSINESS RULE, EXCEPTION, DATA CONTRACT, SQL_MAPPING, INTEGRATION, MISSING IMPLEMENTATION, ANOMALY, UNRESOLVED EVIDENCE, NON_BEHAVIORAL TECHNICAL.
2. N-to-1 Consolidation: Multiple inventory rows MUST share the same Planned Target Destination if they belong to the same semantic owner (e.g., map 10 field copies to a single Data Contract structure (Behavioral Contract Schema item 3)).
3. Pre-Planning Rule Audit: Before assigning a Planned Target Destination of FS-BRL, validate that the behavior adds independent policy content. If it is merely data movement or flow sequence, plan it for Section 8 or Section 4 instead.
4. Do NOT include summary or meta-accounting rows inside the ledger table itself.
5. If the table exceeds token limits, break cleanly at a row boundary and emit: [CONTINUATION REQUIRED - NEXT: <Inventory ID>].

Immediately after the table is complete, print exactly one accounting line:
STAGE 1 ACCOUNTING: Total Inventoried: <n> | Planned Mapped: <n> | Planned Explicitly Omitted: <n> | Unaccounted: 0

Do NOT generate the Behavioral Contract yet. Stop immediately after printing the footer.

Stage 2 (Contract Generation):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 2 (MODE 1 LEAF)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Now, using EXACTLY the Leaf Source-Behavior Accounting Ledger you printed in Stage 1 as your strict baseline, generate the complete structured CHILD BEHAVIORAL CONTRACT using the 9-part Behavioral Contract Schema.

Requirements for Stage 2:
1. Stage 1 inventory IDs, source ranges, provenance, and mapped/omitted dispositions are immutable. Every mapped behavior from the Stage 1 table must be explicitly represented in the appropriate Behavioral Contract schema field according to its classification. You may correct the routing destination if the Quality Gate dictates, but do not force a behavior into Schema Item 2 or 3 when its classification belongs to another schema field.
2. Routing Corrections: If any routing destination was altered from Stage 1, emit the `ROUTING CORRECTION DISCLOSURE` ledger after Schema Item 9.
3. Carry the exact deduplicated identifiers from the Stage 1 table into PROVENANCE, and deduplicated source coordinates into RANGES for Section 1 (Component Identity). 
4. Omission Routing (Invariant 3): Materialize items into Behavioral Contract Schema items 7 or 8 exactly according to ACCOUNTING DISPOSITION SEMANTICS.
5. Conclude by re-verifying that 100% of rows in the Stage 1 table are accounted for with zero unaccounted omissions. Ensure the stated Total Inventoried count matches the Stage 1 Accounting footer literal count.
6. Scenario Preservation: Explicitly carry the Scenario Membership value(s) from the Stage 1 table into Behavioral Contract Schema item 9, preserving MULTIPLE:<...> groupings where present.


For Mode 2 (Parent / Intermediate - Generates BHV Contract)
Stage 1 (Reconciliation Ledger):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 1 (MODE 2 PARENT)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are currently operating in: MODE 2 (PARENT / INTERMEDIATE).

Orchestrator Node Context:
ORCHESTRATOR_NODE_ID: <INJECT_UNIQUE_ID_HERE>

Execute PHASE 1 ONLY.
Analyze the attached Parent source code AND reconcile it against all supplied direct-child Behavioral Contracts.
Apply Invariant 4: Stitch cross-chunk boundary logic across the parent-child interfaces before defining behaviors. Do NOT assign final FS-* IDs in this mode.

Print the Parent-Child Reconciled Behavioral Ledger as a Markdown table containing these exact columns:
| Inventory ID (INV-<ORCHESTRATOR_NODE_ID>-<id>) | Originating Entity (Parent Source / Child Contract ID) | Observable Behavior & Integration Effect | Source Line Ranges / Child Provenance | Primary Behavioral Classification | Planned Primary Artifact Type | Planned Target Destination | Planned Disposition (Mapped to Parent BHV / Absorbed / Omitted) | Scenario Membership (preserve existing LOCAL-SCN-* from child contracts and parent-local LOCAL-SCN-<ORCHESTRATOR_NODE_ID>-<n> / MULTIPLE:<...> / NOT-SCENARIO-DISTINCT) |

Rules:
1. Use the standard Primary Behavioral Classification values.
2. N-to-1 Consolidation applies. 
3. Pre-Planning Rule Audit: Before assigning a Planned Target Destination of FS-BRL, validate that the behavior adds independent policy content. If it is merely data movement or flow sequence, plan it for Section 8 or Section 4 instead.
4. Scenario Union: You MUST carry forward and union all LOCAL-SCN-* scenario memberships from child behaviors contributing to the reconciled Parent behavior (including child behaviors classified as Mapped to Parent BHV or Absorbed) with any parent-local scenario memberships in the Parent's Scenario Membership column.
5. Parent Inventory Lineage: A Mode 2 Parent INV-* is a newly assigned reconciled inventory identity for parent-level behavior. It MUST retain explicit linkage to every contributing upstream INV-* and/or source behavior from which it was reconstructed, recorded in the Source Line Ranges / Child Provenance column. Upstream INV-* identities remain immutable and are not replaced.
6. Do NOT include summary or meta-accounting rows inside the ledger table itself.
7. If the table exceeds token limits, break cleanly at a row boundary and emit: [CONTINUATION REQUIRED - NEXT: <Inventory ID>].

Immediately after the table is complete, print exactly one accounting line:
STAGE 1 ACCOUNTING: Total Reconciled Behaviors: <n> | Planned Mapped: <n> | Planned Absorbed: <n> | Planned Explicitly Omitted: <n> | Unaccounted: 0

Do NOT generate the Parent Behavioral Contract yet. Stop immediately after printing the footer.

Stage 2 (Parent Contract Assembly):

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 2 (MODE 2 PARENT)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Now, using EXACTLY the Reconciled Behavioral Ledger from Stage 1 as your strict baseline, generate the complete structured PARENT BEHAVIORAL CONTRACT using the 9-part Behavioral Contract Schema.

Requirements for Stage 2:
1. Execute Invariant 1 (Universal Provenance Retention & Union): For all absorbed or merged child behaviors, compute the exact set-theoretic union of child coordinates for RANGES and preserve their provenance IDs in PROVENANCE. Thread the new Parent Inventory IDs alongside them.
2. Stage 1 inventory IDs, source ranges, provenance, and mapped/absorbed/omitted dispositions are immutable. You may correct the final artifact routing destination if the Quality Gate dictates. Ensure all child-driven control flows and local parent behaviors from the Stage 1 table are represented.
3. Routing Corrections: If any routing destination was altered from Stage 1, emit the `ROUTING CORRECTION DISCLOSURE` ledger after Schema Item 9.
4. Omission Routing (Invariant 3): Materialize items into Behavioral Contract Schema items 7 or 8 exactly according to ACCOUNTING DISPOSITION SEMANTICS.
5. Conclude with a verification statement confirming all Stage 1 items are fully accounted for. Ensure the stated Total Reconciled Behaviors and Absorbed counts match the Stage 1 Accounting footer.
6. Scenario Preservation: Explicitly carry the Scenario Membership value(s) from the Stage 1 table into Behavioral Contract Schema item 9, preserving MULTIPLE:<...> groupings where present.


For Mode 3 (Master Finalization with Chunks - Generates Final FSD)
Stage 1 (Dual-Ledger Reconciliation):

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 1 (MODE 3 MASTER)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are currently operating in: MODE 3 (MASTER / CHUNKS).

Orchestrator Node Context:
ORCHESTRATOR_NODE_ID: <INJECT_UNIQUE_ID_HERE>

Execute PHASE 1 ONLY. You must analyze the attached Master entry-point source code AND ingest the full manifest of supplied child/parent BHV contracts.

Print TWO separate planning tables:

TABLE 1: Master Local Source-Behavior Ledger
(Cataloging observable behavior located directly in the Master source code)
| Local Inventory ID (INV-<ORCHESTRATOR_NODE_ID>-<id>) | Observable Behavior | Trigger / Context | Source Line Ranges | Primary Behavioral Classification | Planned Primary Artifact Type | Planned Target Artifact ID | Planned Disposition (Mapped / Explicitly Omitted) | Scenario Membership (FS-SCN-<n> / MULTIPLE:<FS-SCN-<n>, ...> / NOT-SCENARIO-DISTINCT) |

TABLE 2: Ingested BHV Input Disposition Ledger (Section 11.4 Planning)
(Reconciling every supplied BHV contract from downstream chunks)
| BHV Source Node ID | Contributing Component / Class | Planned Disposition (Mapped / Absorbed / Omitted) | Planned Target Destination(s) | Reconciled Line Ranges (Union Applied) | Justification / Override Reason |

Rules:
1. For Table 1, use the standard Primary Behavioral Classification values and N-to-1 Consolidation logic. 
2. Pre-Planning Rule Audit: Before assigning a Planned Target Artifact ID of FS-BRL-* in Table 1, validate that the behavior adds independent policy content.
3. For Table 2, Master may evaluate the full BHV contract content to determine if a child's provisional classification must be overridden and log the justification in the final column.
4. Do NOT include summary or meta-accounting rows inside the ledger tables themselves.
5. Apply Invariant 4 across chunk boundaries.
6. If either table exceeds token limits, break cleanly at a row boundary and emit: [CONTINUATION REQUIRED - NEXT: <Table / ID>].

Immediately after the tables are complete, print exactly one accounting line:
STAGE 1 ACCOUNTING: Local Inventoried: <n> | Local Mapped: <n> | Local Explicitly Omitted: <n> | Local Unaccounted: 0 | Ingested BHVs: <n> | BHV Mapped: <n> | BHV Absorbed: <n> | BHV Omitted: <n> | BHV Unaccounted: 0

Do NOT generate Sections 1-12 or Appendices yet. Stop immediately after printing the footer.

Stage 2 (Final FSD Assembly & Closure):

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 2 (MODE 3 MASTER)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Now, using BOTH Table 1 (Local Source Ledger) and Table 2 (BHV Disposition Ledger) from Stage 1 as your strict completeness baselines, generate the COMPLETE FINAL FUNCTIONAL SPECIFICATION (Sections 1-12 and Appendices A-B).

Requirements for Stage 2:
1. Every local behavior from Table 1 and every BHV contract from Table 2 must be fully accounted for and integrated into the document according to their planned dispositions. Ensure N-to-1 consolidated items are properly grouped.
2. Stage 1 inventory IDs, source ranges, provenance, and accounting dispositions (Mapped/Absorbed/Omitted) are immutable. You may correct the final artifact routing destination if the Quality Gate dictates. 
3. Routing Corrections: If any routing destination was altered from Table 1, emit the `ROUTING CORRECTION DISCLOSURE` ledger as Section 11.5 before the Final Validation Checklist.
4. In Section 11.4, emit the finalized BHV Input Disposition Ledger matching Table 2.
5. Ensure all inline <!-- TRACE --> tags respect Invariant 1: deduplicated identifiers in PROVENANCE, and deduplicated source coordinates in RANGES. Every Mode 3 local `INV-*` from Table 1 that is mapped, consolidated, or otherwise contributes to a final artifact MUST be explicitly carried into that artifact’s PROVENANCE. No local `INV-*` may be dropped merely because its behavior is consolidated under another inventory item or artifact.
6. Pre-Generation Rule Audit: Validate every proposed FS-BRL. If it does not add independent policy content beyond data mapping or flow sequence, correct its routing to Section 8 or Section 4.
7. Omission Routing (Invariant 3): Route any Table 1 Explicitly Omitted items correctly according to ACCOUNTING DISPOSITION SEMANTICS.
8. Execute the Final Validation Checklist and print the full Completeness Signature, explicitly proving:
   - Total Inventoried Source Behaviors (Table 1) = Mapped + Omitted + Unaccounted (Must be 0).
   - Ensure the stated Total Inventoried and Ingested counts match the Stage 1 Accounting footer.
   - Total Ingested BHV Contracts (Table 2) = Mapped + Absorbed + Omitted (100% Match).
9. Scenario Resolution: Reconcile both Master-local FS-SCN-* candidates from Table 1 and ingested LOCAL-SCN-* memberships from the supplied BHV contracts into a unified final FS-SCN-* scenario catalogue. Merge them if they represent the same observable end-to-end path/outcome, preserving local identifiers in the provenance (if applicable; Master-local or Single-Shot scenarios without child identifiers are exempt).