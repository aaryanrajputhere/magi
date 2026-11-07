# Planning, verification and repair token optimization

Implemented against campaign `6459de35-b2ab-4e6c-9ed9-fa29514c4d84` (frozen baseline under `evaluation/token-baselines/`).

## Behavior

- Planning retains source passages once, removes the redundant source catalog and review source-hash snapshot, and shares identical campaign context/constraints between asset packages. Evidence outside asset packages is retained. Current decisions exclude storage-only hashes. Original stored artifacts remain intact.
- Verification receives one dictionary of original evidence, with channel membership referenced by chunk ID. Conflicting content under the same chunk ID is rejected. All unique original rule passages and channel limits remain available.
- Defects identify an asset (or campaign-level field), editable field, explanation, defect kind, and relevant rule references. Code checks targets/references and requires exact asset coverage.
- Repair returns patches for exactly the defective fields. Missing, duplicate, extra, immutable-field and incorrectly typed patches are rejected. The full resulting outline and deliverable contract are validated; unrelated fields/assets are preserved by code.
- Sentence completion uses the existing candidate facts without resending the corpus. Other content repairs receive the affected channels' full original rules, including exceptions and any other channels cited by defects. Global changes receive all channels. Unchanged assets are available as dependency context.
- A separate verification request rechecks changes and dependencies against the affected original rules. Remaining defects stop the operation; no verified approval receipt is issued. No automatic repair loop or additional approval interaction is introduced.

This change does not prune original rule text semantically or alter the existing writing-rule extraction/cache workflow. Reliable rule extraction and narrower factual retrieval remain separate experiments; independent verification continues to use originals.

## Offline measurement

Run in the backend Python environment:

```sh
python -m scripts.measure_plan_prompt_baseline ../evaluation/token-baselines/6459de35-b2ab-4e6c-9ed9-fa29514c4d84/runs.json
```

| Saved request | Before payload tokens | After payload tokens | Reduction |
| --- | ---: | ---: | ---: |
| Planning | 163,300 | 121,458 | 25.6% |
| Verification | 131,631 | 81,808 | 37.9% |

These are `cl100k_base` counts of JSON payloads only, excluding instructions, output schemas and transport overhead. They differ from recorded provider usage. The script transforms saved requests without model calls. Repair and recheck savings depend on the new verifier's findings and need a live equivalent-run measurement; no combined campaign saving is claimed.

## Validation

19 focused tests pass across `test_plan_token_optimization.py`, `test_plan_requirement_schema.py` and `test_plan_recovery.py`. They cover evidence preservation, mutation isolation, allowed patch targets/types, structural/content/global repairs, original-source rechecking, and refusal to approve a failed recheck. Strict output schema generation is checked for the new contracts. Lint passes for the implementation helpers, new measurement script, and focused tests.

Broader campaign/agent tests contain existing failures; comparison with this task's changes removed separates these from regressions. Tests run in disposable containers with source mounted read-only, without campaign data or provider credentials. The running app is not rebuilt/restarted and the saved campaign is not rerun.
