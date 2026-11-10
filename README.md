# saaf-quarantine

Auditee documents are data, not instructions. `saaf-quarantine` is a CI check plus a drop-in helper that stops a SAAF agent from putting outside text (policies, evidence, vendor reports, access lists, reviewed repositories, other agents' findings) into an LLM prompt unmarked. Unmarked, one planted sentence in a document can steer the audit conclusion.

| Part | What it does |
|---|---|
| `quarantine_check.py` | CI check: stdlib only, runs offline. Finds the prompts a repository builds (Python, notebooks, JS, TS) and fails when one embeds document-like text that did not pass through `quarantine()`. Prints `file:line` or GitHub annotations. |
| `saaf_quarantine.py` | Drop-in helper, one file, stdlib only. `quarantine(text, source)` wraps a document in `<untrusted_document>` tags. Before wrapping, it strips spoofed wrapper tags and invisible Unicode, and it records any injection signals it finds as an attribute. `SYSTEM_RULE` tells the model that wrapped text is evidence only. |
| `.github/workflows/quarantine.yml` | Reusable workflow: any SAAF repo adopts the check with five lines of YAML. |
| `skills/saaf-quarantine/SKILL.md` | Agent skill that tells a coding agent how to fix a flagged prompt. |

## What it stops

Run against the SAAF-Project repositories during the Security Challenge (10 November 2026), it failed 19 of the 23 repositories reported for prompt injection that day, each at the line where a document enters the prompt. It passed the four repositories that already separate data from instructions. The detailed findings went to the SAAF organisers, not into this repository.

## Run it

Python 3.8 or newer, no install:

```bash
python3 quarantine_check.py path/to/agent-repo      # exit 1 and file:line for every unquarantined prompt input
python3 -m unittest discover -s tests              # self-test
```

## Add it to a SAAF repository

1. Copy `saaf_quarantine.py` next to your agent code, then wrap every outside document and add the rule to the system prompt:

   ```python
   from saaf_quarantine import SYSTEM_RULE, quarantine

   user_prompt = f"POLICY TEXT:\n{quarantine(policy_text, source='policy.pdf')}"
   client.messages.create(system=SYSTEM_RULE + "\n\n" + SYSTEM_PROMPT, messages=[{"role": "user", "content": user_prompt}], ...)
   ```

2. Add the check to CI in `.github/workflows/quarantine.yml`:

   ```yaml
   name: quarantine
   on: [push, pull_request]
   permissions:
     contents: read
   jobs:
     quarantine:
       uses: SAAF-Project/saaf-quarantine/.github/workflows/quarantine.yml@main
   ```

3. Or run it as a pre-commit hook:

   ```yaml
   - repo: https://github.com/SAAF-Project/saaf-quarantine
     rev: main
     hooks:
       - id: saaf-quarantine
   ```

A line you have reviewed and accept can carry the comment `quarantine: ignore <reason>`. The check also accepts existing wrappers whose name contains `quarantine`, `untrusted` or `spotlight`.

## Limitations

- **Mitigation, not immunity.** Wrapping and the system rule make indirect prompt injection less likely to work, not impossible. Keep a human sign-off on audit conclusions and give agents no tools they do not need.
- **Heuristic on names.** The check judges "document-like" from identifiers (`doc`, `text`, `policy`, `evidence`, `findings`, `json.dumps(...)`, and so on). A document held in a variable called `x` slips through; a harmless `{report_title}` can be flagged. It follows no data flow across function calls, so a document that first passes through a helper can slip through, and a repository that wraps documents in its own helper in another module is still flagged at the call sites (call `quarantine()` in that helper, or mark the lines `quarantine: ignore`).
- **Code only.** It does not read prompts that live only in Markdown skills or in Copilot Studio configuration. It also cannot wrap native PDF or image blocks sent as files.
- **Regex for JS and TS.** Python and notebooks are parsed properly; JS and TS template literals are matched by pattern.
- **One weakness class.** It does not cover authentication, model-output rendering, secrets or dependencies.
