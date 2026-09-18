# Interview notes

## Thirty-second explanation

Disclosure Desk is a local review product for bank risk metrics. It processes real Australian annual reports, proposes five metrics using either rules or a local language model, and links each proposal back to a physical PDF page. The reviewer confirms the reporting date, entity and basis before comparing periods. I focused on source variability, recoverable background work and honest evaluation because these were missing from my earlier component-focused projects.

## Engineering decisions worth discussing

- **Keep numbers and meaning separate.** A number appearing in the source does not establish its year or entity. The model's draft and the reviewer-approved result are separate records.
- **Let the parser own geometry.** The model selects candidate IDs; it never invents PDF coordinates. The same source-only ranking supplies the rule and model paths.
- **Use one local system with durable boundaries.** SQLite WAL, short write transactions, lease tokens and optimistic review versions are enough for this single-reviewer application. I would change the storage and queue before claiming multi-user scale.
- **Test failures that matter.** A stale worker cannot commit after losing its lease; an interrupted job can be reclaimed; a stale browser form cannot overwrite a newer review; unmatched contexts cannot silently produce a financial delta.
- **Keep evaluation independent of prompts.** Extraction never receives gold labels. Sources are hash-pinned, current-period targets are distinguished from repeated comparatives, and bank/time test splits are reported separately.

## Demonstration sequence

1. Open a report and show its proposed metric beside the actual PDF.
2. Show a missing date or ambiguous scope and explain why the figure is still pending.
3. Select the correct source candidate and record a reason for the review.
4. Compare two periods: reviewed and matching rows have a delta; the others remain blocked.
5. Export the result, then open the benchmark and explain a real failure.

## Claims to avoid

- Do not describe the prototype as bank production experience, a cloud deployment, a compliance certification or multi-tenant software.
- Do not claim measured analyst time savings: no independent users have participated yet.
- Do not call 58 source-assisted labels independently human-validated or treat correlated fields as independent experiments.
- Do not equate evidence existence with semantic correctness or a higher numeric score with a deployable autonomous model.

## Resume wording

Built a local bank-disclosure review application using FastAPI, React and Qwen: processed 12 official annual reports with hash-pinned sources, linked proposed metrics to PDF evidence, implemented versioned human review and context-checked cross-period comparison, and verified durable job recovery and browser-to-export workflows. Evaluated rules and local-model drafts against 58 source-checked targets, reporting numerical and semantic errors separately.

Only add a model improvement figure if the committed results support it, together with its denominator and the pilot's limitations.
