# Quarantine verification criteria (draft)

Repository-specific proposals under the existing SAAF standards. These are not
new organisation-wide norms. Run `python -m unittest discover -s tests -v` on the
reviewed commit. CI evidence must identify that commit and the outcome; absent or
skipped execution remains not assessed.

| ID | Criterion | Offline evidence |
|---|---|---|
| QU-01 [code] | Outside text cannot close the wrapper by planting a wrapper tag; invisible control characters are removed and signalled. | `QuarantineTest.test_wrapped_document_cannot_close_its_own_wrapper` |
| QU-02 [code] | Recognised unwrapped Python/TypeScript prompt inputs fail; the equivalent wrapped fixture passes. | `CheckTest.test_flags_unwrapped_document_and_passes_once_wrapped`, `test_flags_typescript_prompt_template`, `test_cli_fails_the_build_only_when_something_is_flagged` |
| QU-03 [code] | Missing, empty, unsupported, unreadable, non-UTF-8 or malformed Python/notebook inputs return incomplete status, never success. | `IncompleteScan` cases in `tests/test_fail_closed.py` |
| QU-04 [code] | Multiple targets retain incomplete status if any target is not assessed. | `IncompleteScan.test_multiple_targets_keep_incomplete_status` |
| QU-05 [code] | The reusable workflow restricts its target to the caller checkout and runs a pinned checker without importing caller code. | `WorkflowBoundary` cases in `tests/test_workflow_boundary.py` |

All examples and tests are synthetic and offline. There is no model evaluation;
resistance of any live model to prompt injection remains **not assessed**. Wrapper
names, ignored lines, excluded test/build directories and regex JS matching can
miss unsafe flows; source review is still required. This is one mitigation, not
proof of complete security. The wrapper is not a lossless evidence store: retain
original source references and content separately in the calling application.

Owner acceptance, a security reporting contact and licensing remain open. This
change does not invent those decisions; no LICENSE file exists at this revision.
