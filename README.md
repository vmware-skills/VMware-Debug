<!-- mcp-name: io.github.vmware-skills/vmware-debug -->

# VMware Debug

> **Disclaimer**: Community-maintained open-source project, **not affiliated with,
> endorsed by, or sponsored by VMware, Inc. or Broadcom Inc.** "VMware" and
> "vSphere" are trademarks of Broadcom. Source is publicly auditable under the MIT
> license.

The diagnostic brain of the VMware skill family. You bring the symptom (an error,
a log dump, a slow VM); this skill runs a systematic investigation, correlates
events from the other skills into one timeline, ranks root-cause hypotheses, and
tells you what to check next. It **never touches vSphere** — it connects to
nothing and never executes fixes; its only writes are to a local case ledger
under `$OPS_HOME`. Remediation is routed to `vmware-aiops` (single op) or
`vmware-pilot` (multi-step, gated), mirroring the `vmware-harden → vmware-pilot`
advisor/executor split.

See [`skills/vmware-debug/SKILL.md`](skills/vmware-debug/SKILL.md) for the full
methodology, the event-envelope contract, and symptom routing.

## Upgrading to 2.0

2.0 renames 13 MCP tools so every name starts with a verb (e.g. `case_close` → `close_case`, `case_get` → `get_case`, `case_grade` → `grade_case`). The full old → new table is in
[RELEASE_NOTES.md](RELEASE_NOTES.md#v200--mcp-tools-renamed-verb-first). Nothing else changes: same tools, parameters,
safety gates and CLI commands. If an agent prompt, script or `~/.vmware/rules.yaml` names a tool, update it — though deny
rules written with the old names keep working with vmware-policy ≥ 1.18 (`vmware-audit policy` lists any rule the
renames widened), and vmware-pilot ≥ 1.13 translates old names in saved workflows.

## MCP tools (14 — 7 read, 7 write)

The seven writes go to the local case ledger only; none reaches a VMware system.

**Correlation** — stateless, for a single look:

| Tool | What |
|---|---|
| `build_incident_timeline` | [READ] Correlate pre-fetched events → timeline + spikes + ranked hypotheses + next-check ideas |
| `list_symptom_categories` | [READ] List recognised symptom categories + what to check for each |

**Investigation ledger** — for an incident you will reason about over time:

| Tool | What |
|---|---|
| `open_case` | [WRITE] Define the event; returns a case id and the grade this environment can reach |
| `get_case_readiness` | [READ] What grade this environment can reach, per symptom category, **before** you start |
| `get_case_knowledge` | [READ] Which knowledge formats are accepted, what is mounted, and which entries apply to a case |
| `plan_case` | [READ] What to fetch next — skill, tool and purpose per step; recomputed from the case's current state |
| `list_cases` | [READ] Cases, newest first |
| `get_case` | [READ] One case: scope, ledger sizes, grade history |
| `record_case_hypothesis` | [WRITE] Register a candidate explanation, or read the ledger of what supports and refutes each |
| `submit_case_evidence` | [WRITE] Record one retrieved fact, with its source, query and time basis |
| `record_case_gap` | [WRITE] Record what could **not** be retrieved, and how to close it |
| `record_case_timeline` | [WRITE] Correlate everything the case has collected into one timeline |
| `grade_case` | [WRITE] Recompute the conclusion grade from the ledger and record it |
| `close_case` | [WRITE] Record the final grade, archive, and name what was left open |

## Offline / Air-Gapped Install (from source)

This project uses the modern PEP 517 build system (hatchling), so there is **no
`setup.py`** by design — that is expected, not a missing file. If you cloned the
source and hit `ERROR: File "setup.py" or "setup.cfg" not found ... editable mode
currently requires a setuptools-based build`, your `pip` is older than 21.3 and
cannot do an *editable* (`-e`) install with a non-setuptools backend. Editable
mode is a developer convenience, not needed to run the tool — do one of:

```bash
# From the source tree — a normal (non-editable) install builds a wheel:
pip install .              # NOT  pip install -e .

# ...or upgrade pip first, and editable works too:
pip install --upgrade pip && pip install -e .
```

For a **truly air-gapped host**, build the wheels on a connected machine and copy
them over — the target then needs no network:

```bash
# On a connected machine, collect this package + its dependencies as wheels:
pip wheel . -w dist        # → dist/*.whl   (or: uv build, for just this package)

# Copy dist/ to the air-gapped host, then install offline:
pip install --no-index --find-links dist vmware-debug
```

## License

MIT.
