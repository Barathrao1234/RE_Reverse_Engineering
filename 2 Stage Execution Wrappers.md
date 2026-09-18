The Final Two-Stage Execution Wrappers

For Single-Shot Execution
Stage 1 (Inventory Ledger):

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 1 (SINGLE-SHOT)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are currently operating in: MODE 3 (MASTER / SINGLE-SHOT).
No child contracts are supplied. The attached source represents the complete analytical scope.

Execute PHASE 1 ONLY (Pre-FSD Behavioral Coverage Inventory & Accounting).
Analyze the attached source and print the complete Source-Behavior Accounting Ledger as a Markdown table.

The table MUST contain these exact columns:
| Inventory ID | Observable Behavior | Trigger / Context | Source Line Ranges | Planned Disposition (Mapped / Explicitly Omitted) | Scenario Disposition (FS-SCN-<n> / NOT-SCENARIO-DISTINCT) |

Rules:
1. Apply the Unit-Test Atomization Heuristic. Do not combine independently changeable branches or data transformations.
2. Reconstruct complete execution sequences across helper and user-defined method calls without fragmenting atomic behaviors.
3. If the table exceeds token limits, break cleanly at a row boundary and emit: [CONTINUATION REQUIRED - NEXT: <Inventory ID>].

Do NOT generate Sections 1-12.
Do NOT generate Appendices A-B.
Stop immediately after printing the table.

Stage 2 (FSD Generation & Closure):

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 2 (SINGLE-SHOT)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Now, using EXACTLY the Source-Behavior Accounting Ledger you printed in Stage 1 as your strict baseline, generate the COMPLETE FINAL FUNCTIONAL SPECIFICATION (Sections 1-12 and Appendices A-B).

Requirements for Stage 2:
1. Every single item from the Stage 1 table must be accounted for with zero items dropped.
2. Carry the exact source line ranges from the Stage 1 table into the RANGES array, and explicitly insert the Stage 1 Inventory ID into the PROVENANCE array of the inline <!-- TRACE --> tags.
3. Omission Routing (Invariant 3): If any Stage 1 item is Explicitly Omitted, evaluate its nature. If it is non-behavioral technical infrastructure, log it in Section 11.3. If it is unresolved or missing observable behavior, log it in Section 11.1 or 11.2.
4. In Section 11.4, note: "N/A - Single-Shot Execution (Governed by Stage 1 Source-Behavior Ledger)."
5. Close the accounting loop in the Completeness Signature by verifying:
   Total Inventoried Source Behaviors (Stage 1) = Mapped + Explicitly Omitted + Unaccounted (Must be 0).




For Mode 1 (Leaf / Child)
Stage 1 (Leaf Inventory):

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 1 (MODE 1 LEAF)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are currently operating in: MODE 1 (LEAF / CHILD).

Orchestrator Node Context:
ORCHESTRATOR_NODE_ID: <INJECT_UNIQUE_ID_HERE>

Execute PHASE 1 ONLY.
Analyze the attached raw source and print the Leaf Source-Behavior Accounting Ledger as a Markdown table.

The table MUST contain these exact columns:
| Inventory ID (BHV-<ORCHESTRATOR_NODE_ID>-<id>) | Observable Behavior | Trigger / Context | Source Line Ranges | Planned Disposition (Mapped to BHV / Explicitly Omitted) |

Rules:
1. Ensure every independently testable behavior receives a distinct row. Do NOT merge behaviors merely because they share a method or line range.
2. If the table exceeds token limits, break cleanly at a row boundary and emit: [CONTINUATION REQUIRED - NEXT: <Inventory ID>].

Do NOT generate the Behavioral Contract yet. Stop immediately after printing the table.

Stage 2 (Contract Generation):

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 2 (MODE 1 LEAF)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Now, using EXACTLY the Leaf Source-Behavior Accounting Ledger you printed in Stage 1 as your strict baseline, generate the complete structured CHILD BEHAVIORAL CONTRACT using the 8-part Behavioral Contract Schema.

Requirements for Stage 2:
1. Ensure every mapped behavior from the Stage 1 table is explicitly detailed in Section 2 (Observable Behavior) or Section 3 (Data Contract).
2. Carry the exact line coordinates from the Stage 1 table into Section 1 (Component Identity), and explicitly thread the Stage 1 Inventory ID (BHV-*) into all relevant provenance tracking.
3. Omission Routing (Invariant 3): If any Stage 1 item was marked Explicitly Omitted, evaluate its nature. If it is non-behavioral technical infrastructure, log it in Section 8 (Omitted Technical Ranges). If it is unresolved or missing observable behavior, log it in Section 7 (Dependencies & Unresolved Constraints).
4. Conclude by re-verifying that 100% of rows in the Stage 1 table are accounted for with zero unaccounted omissions.



For Mode 2 (Parent / Intermediate)
Stage 1 (Reconciliation Ledger):

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 1 (MODE 2 PARENT)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are currently operating in: MODE 2 (PARENT / INTERMEDIATE).

Orchestrator Node Context:
ORCHESTRATOR_NODE_ID: <INJECT_UNIQUE_ID_HERE>

Execute PHASE 1 ONLY.
Analyze the attached Parent source code AND reconcile it against all supplied direct-child Behavioral Contracts.
Apply Invariant 4: Stitch cross-chunk boundary logic across the parent-child interfaces before defining behaviors.

Print the Parent-Child Reconciled Behavioral Ledger as a Markdown table containing these exact columns:
| Inventory ID (BHV-<ORCHESTRATOR_NODE_ID>-<id>) | Originating Entity (Parent Source / Child Contract ID) | Observable Behavior & Integration Effect | Source Line Ranges / Child Provenance | Planned Disposition (Mapped to Parent BHV / Absorbed / Omitted) |

If the table exceeds token limits, break cleanly at a row boundary and emit: [CONTINUATION REQUIRED - NEXT: <Inventory ID>].

Do NOT generate the Parent Behavioral Contract yet. Stop immediately after printing the table.


Stage 2 (Parent Contract Assembly):

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 2 (MODE 2 PARENT)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Now, using EXACTLY the Reconciled Behavioral Ledger from Stage 1 as your strict baseline, generate the complete structured PARENT BEHAVIORAL CONTRACT using the 8-part Behavioral Contract Schema.

Requirements for Stage 2:
1. Execute Invariant 1 (Universal Provenance Retention & Union): For all absorbed or merged child behaviors, compute the exact set-theoretic union of child coordinates and preserve their provenance IDs. Thread the new Parent Inventory IDs alongside them.
2. Ensure all child-driven control flows and local parent behaviors from the Stage 1 table are represented.
3. Omission Routing (Invariant 3): If any Stage 1 item is marked Omitted, log non-behavioral infrastructure in Section 8, and log unresolved/missing behavior in Section 7.
4. Conclude with a verification statement confirming all Stage 1 items are fully accounted for.



For Mode 3 (Master Finalization with Chunks)
Stage 1 (Dual-Ledger Reconciliation):

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 1 (MODE 3 MASTER)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are currently operating in: MODE 3 (MASTER / CHUNKS).

Execute PHASE 1 ONLY. You must analyze the attached Master entry-point source code AND ingest the full manifest of supplied child/parent BHV contracts.

Print TWO separate planning tables:

TABLE 1: Master Local Source-Behavior Ledger
(Cataloging observable behavior located directly in the Master source code)
| Local Inventory ID | Observable Behavior | Trigger / Context | Source Line Ranges | Planned Disposition (Mapped / Explicitly Omitted) | Target FS Artifact Type (FS-MPF / FS-BRL / etc.) | Scenario Disposition (FS-SCN-<n> / NOT-SCENARIO-DISTINCT) |

TABLE 2: Ingested BHV Input Disposition Ledger (Section 11.4 Planning)
(Reconciling every supplied BHV contract from downstream chunks)
| BHV Source Node ID | Contributing Component / Class | Planned Disposition (Mapped / Absorbed / Omitted) | Planned Target FS Artifact ID(s) | Reconciled Line Ranges (Union Applied) |

Apply Invariant 4 across chunk boundaries.
If either table exceeds token limits, break cleanly at a row boundary and emit: [CONTINUATION REQUIRED - NEXT: <Table / ID>].

Do NOT generate Sections 1-12 or Appendices yet. Stop immediately after printing both tables.

Stage 2 (Final FSD Assembly & Closure):


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE EXECUTION INSTRUCTION - STAGE 2 (MODE 3 MASTER)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Now, using BOTH Table 1 (Local Source Ledger) and Table 2 (BHV Disposition Ledger) from Stage 1 as your strict completeness baselines, generate the COMPLETE FINAL FUNCTIONAL SPECIFICATION (Sections 1-12 and Appendices A-B).

Requirements for Stage 2:
1. Every local behavior from Table 1 and every BHV contract from Table 2 must be fully materialized into the document.
2. In Section 11.4, emit the finalized BHV Input Disposition Ledger matching Table 2.
3. Ensure all inline <!-- TRACE --> tags carry the full union of line ranges and provenance identifiers (explicitly threading in the Stage 1 Local Inventory IDs and Table 2 BHV Source Node IDs into the PROVENANCE array).
4. Omission Routing (Invariant 3): Route any Table 1 Explicitly Omitted items correctly (non-behavioral to Section 11.3; unresolved behavior to Section 11.1 or 11.2). 
5. Execute the Final Validation Checklist and print the full Completeness Signature, explicitly proving:
   - Total Inventoried Source Behaviors (Table 1) = Mapped + Omitted + Unaccounted (Must be 0).
   - Total Ingested BHV Contracts (Table 2) = Mapped + Absorbed + Omitted (100% Match).