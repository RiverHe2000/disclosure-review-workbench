# RULES — real-report pilot

Generated: 2026-09-18T08:57:28.934903+00:00

Source labels were checked with AI assistance; an independent human audit is still needed.
These are unreviewed drafts, never automatically approved financial facts.

| Split | Source-checked labels | Correct value + unit | Correct complete tuple | Wrong nonempty drafts |
|---|---:|---:|---:|---:|
| development | 30 | 26/30 | 4/30 | 4 |
| temporal_test | 13 | 9/13 | 1/13 | 4 |
| bank_test | 15 | 15/15 | 2/12 | 0 |

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
| cba_2023 | 43 | 9.966 | 9.993 |
| cba_2024 | 43 | 13.731 | 13.756 |
| cba_2025 | 36 | 26.512 | 26.529 |
| westpac_2023 | 160 | 15.106 | 15.134 |
| westpac_2024 | 139 | 11.832 | 11.858 |
| westpac_2025 | 83 | 11.459 | 11.479 |
| anz_2023 | 68 | 7.782 | 7.797 |
| anz_2024 | 65 | 10.517 | 10.538 |
| anz_2025 | 55 | 14.282 | 14.303 |
| nab_2023 | 80 | 14.803 | 14.818 |
| nab_2024 | 82 | 17.406 | 17.425 |
| nab_2025 | 69 | 15.043 | 15.069 |
