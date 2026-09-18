# QWEN — real-report pilot

Generated: 2026-09-18T09:10:47.887256+00:00

Source labels were checked with AI assistance; an independent human audit is still needed.
These are unreviewed drafts, never automatically approved financial facts.

| Split | Source-checked labels | Correct value + unit | Correct complete tuple | Wrong nonempty drafts |
|---|---:|---:|---:|---:|
| development | 30 | 23/30 | 4/30 | 2 |
| temporal_test | 13 | 9/13 | 1/13 | 0 |
| bank_test | 15 | 14/15 | 0/12 | 0 |

## Limits

- All extracted values require manual review; this benchmark scores draft extraction.
- Fields within reports are dependent; counts are pilot observations, not population guarantees.
- Evidence existence does not establish correct column, period or regulatory scope.
- Complete-tuple scoring additionally requires a labelled source page; correct alternative citations may be undercounted.
- Timing includes fresh PDF parsing. Model calls are uncached. Initial model load is included in first document.
- Scores were recomputed from recorded predictions; original model outputs and execution timings were preserved. The scorer checks restatement and source precision.

## Per-document execution

| Report | Candidate rows | Parsing seconds | Total seconds |
|---|---:|---:|---:|
| cba_2023 | 43 | 8.836 | 66.24 |
| cba_2024 | 43 | 12.35 | 62.99 |
| cba_2025 | 36 | 22.777 | 74.687 |
| westpac_2023 | 160 | 14.502 | 100.131 |
| westpac_2024 | 139 | 12.996 | 127.24 |
| westpac_2025 | 83 | 11.217 | 55.727 |
| anz_2023 | 68 | 6.773 | 93.166 |
| anz_2024 | 65 | 9.428 | 91.54 |
| anz_2025 | 55 | 13.743 | 93.141 |
| nab_2023 | 80 | 12.747 | 72.904 |
| nab_2024 | 82 | 16.328 | 61.686 |
| nab_2025 | 69 | 13.626 | 78.369 |
