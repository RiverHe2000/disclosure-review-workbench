# Measured results and boundaries

This is a local product prototype tested on 12 official Australian bank annual reports (2023–2025). It has not been deployed in a bank or tested with external users.

## Dataset and annotation

The manifest fixes 12 publisher URLs and SHA-256 hashes. There are 60 current-period targets, 58 source-checked numeric labels and 2 explicitly unverified targets (Westpac 2025 total capital ratio and RWA). Labels were checked with AI assistance; all 58 source quotes were matched against their PDFs and six key pages were visually inspected. Independent human validation is still outstanding.

The source is not always a complete semantic label: three NAB 2023 contexts remain unverified, so the strict tuple denominator is 55. Undisclosed versus missed is not asserted for the two unverified targets.

## Unreviewed extraction results

Every prediction remains a draft. No benchmark output was automatically approved.

| Method | Correct value + unit | Strict tuple + labelled evidence | Wrong nonempty drafts (58 scored targets) | Proposed values (all 60 targets) |
|---|---:|---:|---:|---:|
| rules | 50/58 | 7/55 | 8 | 60/60 |
| qwen | 46/58 | 5/55 | 2 | 50/60 |

The model did not improve numerical coverage in this run: rules recovered 50 of the 58 labelled values and Qwen recovered 46. Qwen left 10 scored targets empty and produced 2 nonempty mismatches at the labelled precision. Complete-tuple scores and errors must also be considered before deciding whether a draft is useful. These pilot counts do not establish reliable autonomous financial extraction.

Strict tuples compare value, unit, date, entity, basis, observation and restatement, and require supporting numbers on a labelled source page. A correct alternate page can be undercounted. Matching a page and number does not independently verify its table column. Values are evaluated at each labelled source's disclosed precision. A rounded alternate citation can fail this numeric test. These are pilot counts with correlated fields, not statistical proof of generalisation.

| Split | Rules value accuracy | Qwen value accuracy |
|---|---:|---:|
| development | 26/30 | 23/30 |
| temporal_test | 9/13 | 9/13 |
| bank_test | 15/15 | 14/15 |

Development uses CBA/Westpac/ANZ 2023–2024. Temporal test uses those banks in 2025; bank test uses NAB 2023–2025. The rules and prompt were fixed before the complete test runs; labels are never supplied to the parser, ranker or model.

## Execution

Hardware: RTX 4070, 12 GB VRAM. Model: local Qwen3-4B-Instruct-2507. Greedy, bounded generation; no paid provider, inference cache or synthetic model output was used for these recorded model runs.

- rules: 12 documents, median wall time 14.0s; range 7.8–26.5s. Includes fresh parsing; the first Qwen report also includes model loading.
- qwen: 12 documents, median wall time 76.5s; range 55.7–127.2s. Includes fresh parsing; the first Qwen report also includes model loading.

Runs were measured on a shared development workstation. They are execution records, not an isolated load or throughput benchmark. The original predictions and durations are preserved when the scorer is corrected; rescoring timestamps and scorer versions are recorded separately.

## Recorded nonempty model mismatches

Values below are normalized to the labelled unit. A mismatch is defined by the labelled source precision; a rounded alternate citation is not necessarily a fabricated number.

| Report / metric | Model draft | Label | Selected PDF page |
|---|---:|---:|---:|
| westpac_2023 / total_capital_ratio | 29.87 percent | 20.45 percent | 128 |
| anz_2024 / rwa | 446600.0 AUD_million | 446582 AUD_million | 49 |

## What failed, and what the product does about it

- A genuine number can come from a regulatory minimum, another entity or the wrong year. Source membership is necessary but not sufficient; every draft requires review.
- ANZ LCR uses full-year averages in relevant reports, so a universal quarterly-average default would be wrong. Observation type is stored separately.
- PDF rows can merge columns and lose table semantics. The reviewer can replace the selected evidence candidate and correct the proposed fields.
- Unstated or model-invented reporting dates remain unresolved. The reviewer must establish an explicit date before comparison.
- Two targets were not verified in the selected Westpac 2025 report. The benchmark does not assert their absence or silently import another document's values.
- Comparisons are restricted to reviewed observations with matching context in the validated 2023–2025 corpus. The prototype does not restate historical capital frameworks.

## Workflow validation

- The real worker was terminated while parsing CBA 2024. After lease expiry a restarted worker reclaimed the job, which completed on attempt 2. See `reports/recovery.json`.
- Automated tests exercise concurrent claims, expired-token rejection, atomic completion, duplicate requests, version conflicts, immutable originals and evidence ownership.
- Browser smoke checks cover a real PDF, zoom-aligned evidence, candidate replacement, conflict handling, comparison and narrow screens.
- A separate copied database was used for six scripted browser reviews, three verified comparison deltas, and CSV/Markdown downloads. The main workspace's review count and content hash stayed unchanged.
- The recorded walkthrough is labelled a scripted demonstration. It is not a human usability study, production deployment or measured analyst time saving.

## Reproduce

```powershell
python scripts/fetch_corpus.py
python -m disclosure.cli benchmark --method rules
python -m disclosure.cli benchmark --method qwen
python scripts/write_results.py
```

The source reports, weights and database are excluded from Git. See `reports/environment.json` for the measured environment and `docs/USER_STUDY.md` for a future external evaluation protocol.
