# Documentation Review Plan

Purpose: guide the complete documentation review and cleanup for this repo, with a bias toward legibility for AI agents and future maintainers.
Status: active-plan
Last verified: 2026-04-10
Canonical for: the documentation-review process and rollout order
Related docs: `vision_documents/architecture.txt`, `vision_documents/the_yoga.txt`, `vision_documents/spring_cleaning.txt`, `README.md`, `CONTRIBUTING.md`, `ARCHITECTURE.md`
Related code: repo root, `edgecaster/`, `tests/`, `assets/`
Supersedes: none

## 1. Goals

This review should leave the repo with documentation that is:

- easy for AI agents to navigate without reading the whole repo blindly
- easy for humans to trust, skim, and maintain
- explicit about what is canonical, active, archived, or scratch
- aligned with the current code rather than stale plans
- structured so future docs do not sprawl into another pile of semi-trusted notes

## 2. Working Rules

- Code wins by default when docs disagree.
- If the disagreement is materially unclear, escalate to the user instead of guessing.
- `vision_documents/architecture.txt` is a current-state orientation document. It should usually be updated to match the code.
- `vision_documents/the_yoga.txt` is canonical north-star guidance.
- `vision_documents/spring_cleaning.txt` is canonical active planning guidance.
- Outdated vision docs should move into `vision_documents/archived/`.
- When an outdated doc contains useful material, merge the useful parts into a canonical doc first, then archive the original.
- New long-lived index and agent-facing docs should generally be Markdown.
- Existing canonical `.txt` docs can stay in place during the first cleanup pass to avoid unnecessary rename churn.
- `AGENTS.md` files should combine orientation, prescriptive guardrails, coding style preferences, and gameplay-aesthetic guidance.
- Every `AGENTS.md` should instruct agents to update the relevant local index docs when code or documentation changes alter structure, ownership, status, commands, or testing guidance.
- Add `CLAUDE.md` files alongside shared `AGENTS.md` files, using Claude's `@path` import syntax so Claude-compatible tooling inherits the same guidance.
- Coding-style guidance should include a preference for descriptive variable names and relatively verbose comments.
- Add narrow `INDEX.md` files only where they reduce repeated orientation work in dense or high-churn areas. Avoid creating index docs for every folder.

## 3. Vision Doc Taxonomy

Primary classification for vision/planning docs:

- `north-star`: long-lived vision, philosophy, or aesthetic direction that should be referenced consistently
- `active-plan`: current short-term or medium-term execution plan
- `historical`: no longer active, but worth preserving for context
- `scratch`: partial notes, temporary working material, or abandoned fragments

Additional note for docs like `architecture.txt`:

- Some docs are better treated as `current-state reference` than as vision/planning docs.
- These should still carry metadata and be surfaced prominently, but they should not be forced into the four-way vision taxonomy if that makes them misleading.

## 4. Core Deliverables

By the end of the review, the repo should have:

- a trustworthy root `AGENTS.md`
- a trustworthy root `CLAUDE.md` that imports `AGENTS.md`
- a trustworthy `vision_documents/AGENTS.md`
- a trustworthy `vision_documents/CLAUDE.md` that imports `AGENTS.md`
- a `vision_documents/INDEX.md` that clearly maps canon, active plans, archived material, and items pending review
- a reviewed and classified `vision_documents/` tree
- an `archived/` area for stale plans and scratch material
- clear root docs with no stale path references
- directory-level `AGENTS.md` files in the main code areas
- matching `CLAUDE.md` shims in the main code areas where shared agent guidance exists
- narrow `INDEX.md` files in the densest areas of the repo
- a stable reading order for agents
- metadata on important vision/reference docs
- cross-links between docs, code, and tests where useful

## 5. Layered Navigation Docs

The repo should use a small layered documentation system instead of pushing every kind of guidance into one huge file.

- `AGENTS.md`: shared agent behavior, local invariants, coding style, testing expectations, and maintenance duties
- `CLAUDE.md`: lightweight compatibility shim that imports the sibling `AGENTS.md`
- `INDEX.md`: narrow map of what exists in that area, what is canonical, and what still needs review
- canonical docs such as `architecture.txt`: durable content for a domain or the current system state

Current index plan:

- `vision_documents/INDEX.md` - first priority
- `edgecaster/systems/INDEX.md` - high priority after systems review begins
- `tests/INDEX.md` - high priority after test review begins

Possible later additions if clearly useful:

- `edgecaster/content/INDEX.md`
- `edgecaster/scenes/INDEX.md`

Rule of thumb:

- add an `INDEX.md` when agents repeatedly need a map of a dense area
- do not create an index file that merely restates another document without reducing search cost

## 6. Rollout Phases

### Phase 0: Foundations

Purpose: create the structure that the rest of the review will hang on.

Deliverables:

- create `vision_documents/archived/`
- create root `AGENTS.md`
- create root `CLAUDE.md` that imports `AGENTS.md`
- create `vision_documents/AGENTS.md`
- create `vision_documents/CLAUDE.md` that imports `AGENTS.md`
- create `vision_documents/INDEX.md`
- define the metadata template for important docs
- correct obviously stale front-door references in `README.md` and `CONTRIBUTING.md`
- decide the role of `ARCHITECTURE.md` as a short summary and redirect to `vision_documents/architecture.txt`

Exit criteria:

- a new agent can identify canonical docs and the first reading order without asking
- the repo no longer points newcomers at `v1/Edgecaster`

### Phase 1: Vision Document Triage

Purpose: classify the existing vision corpus into canonical, active, archived, or scratch material.

Per-doc workflow:

1. Read the document.
2. Compare it against the current code and canonical docs.
3. Classify it.
4. Merge forward any useful material.
5. Archive or keep in place as appropriate.
6. Add metadata or redirect notes.

Desired outputs:

- reviewed status for every file currently in `vision_documents/`
- canonical docs clearly identified
- stale docs moved into `vision_documents/archived/`
- overlapping plans collapsed where possible

### Phase 2: Root Documentation Cleanup

Purpose: make the repo front door reliable.

Targets:

- `README.md`
- `CONTRIBUTING.md`
- `ARCHITECTURE.md`
- `CHANGELOG.md` if needed
- root `AGENTS.md`
- root `CLAUDE.md`

Tasks:

- remove stale path references
- ensure current run/test commands are accurate
- keep `ARCHITECTURE.md` concise and point to `vision_documents/architecture.txt`
- add links to the canonical vision/reference docs
- warn agents away from logs, generated artifacts, and standalone prototype files when appropriate

### Phase 3: Package-Level Orientation

Purpose: give agents local guidance where they actually work.

High-priority `AGENTS.md` targets:

- `edgecaster/AGENTS.md`
- `edgecaster/systems/AGENTS.md`
- `edgecaster/scenes/AGENTS.md`
- `edgecaster/content/AGENTS.md`
- `edgecaster/render/AGENTS.md`
- `tests/AGENTS.md`
- `assets/AGENTS.md`

Matching `CLAUDE.md` targets:

- `edgecaster/CLAUDE.md`
- `edgecaster/systems/CLAUDE.md`
- `edgecaster/scenes/CLAUDE.md`
- `edgecaster/content/CLAUDE.md`
- `edgecaster/render/CLAUDE.md`
- `tests/CLAUDE.md`
- `assets/CLAUDE.md`

High-priority `INDEX.md` targets:

- `edgecaster/systems/INDEX.md`
- `tests/INDEX.md`

Useful second wave:

- `edgecaster/patterns/AGENTS.md`
- `edgecaster/state/AGENTS.md`
- `edgecaster/ui/AGENTS.md`
- `tools/AGENTS.md` if tooling grows

Each local `AGENTS.md` should answer:

- what belongs here
- what does not belong here
- key invariants and architecture boundaries
- common file entrypoints
- how to test changes in this area
- local style or design notes
- which local `INDEX.md` file must be updated when structure or behavior changes

### Phase 4: Full Repo Read-Through

Purpose: review the actual codebase in a deliberate order so docs are grounded in reality.

Recommended reading order:

1. repo entrypoints and runtime path
   - `run_edgecaster.bat`
   - `edgecaster/main.py`
   - `edgecaster/engine.py`
   - `edgecaster/scenes/manager.py`
2. main game loop and orchestration
   - `edgecaster/scenes/dungeon.py`
   - `edgecaster/game.py`
3. core gameplay systems
   - `edgecaster/systems/`
4. scenes, rendering, and UI
   - `edgecaster/scenes/`
   - `edgecaster/render/`
   - `edgecaster/ui/`
5. content, state, and runtime data flow
   - `edgecaster/content/`
   - `edgecaster/state/`
   - `edgecaster/prototypes.py`
   - `edgecaster/spawn_factory.py`
   - `edgecaster/enemies/factory.py`
6. mapgen and worldgen
   - `edgecaster/mapgen.py`
   - `edgecaster/mapgen_sites.py`
   - `edgecaster/climate.py`
   - `edgecaster/corruption.py`
   - `edgecaster/overmap_accel.py`
7. tests and support material
   - `tests/`
   - `assets/`
   - `tools/`
8. standalone and historical reference code
   - `fractal_lab.py`
   - `distorted_Julia.py`
   - `edgecaster_mvp.py`

Important rule:

- documentation should be updated slice by slice as each area is reviewed
- do not wait until the end of the full read-through to write everything down

### Phase 5: Consolidation

Purpose: reduce long-term doc sprawl.

Tasks:

- merge overlapping handoff notes into fewer maintained docs
- decide whether redundant documents should become redirects, summaries, or archives
- move stale plans out of the active path
- keep one clear answer to "where should I read first for this topic?"

### Phase 6: Maintenance Loop

Purpose: prevent the docs from drifting back into an untrusted pile.

Ongoing rules:

- update `architecture.txt` when responsibilities move
- update the relevant local `AGENTS.md` when boundaries or invariants change
- update the relevant local `INDEX.md` when structure, ownership, status, commands, or tests change
- when adding a new shared `AGENTS.md`, add a sibling `CLAUDE.md` shim that imports it
- archive plans when they stop being active
- prefer updating an existing canonical doc over creating a near-duplicate
- add or revise metadata when a doc changes status

## 7. Vision Documents Review Status

Known canonical now:

- `architecture.txt` - canonical current-state reference
- `the_yoga.txt` - canonical north-star
- `spring_cleaning.txt` - canonical active-plan

First-pass classification was completed on 2026-04-10.

The current source of truth for reviewed status is:

- `vision_documents/INDEX.md`

Rule:

- once a first-pass classification has been completed, do not maintain a second parallel status list here
- use the working log here for milestone notes and `INDEX.md` for the folder map

## 8. Metadata Template

Important docs should gradually move toward a simple metadata block like this:

```text
Title: ...
Purpose: ...
Status: north-star | active-plan | historical | scratch | current-state reference
Last verified: YYYY-MM-DD
Canonical for: ...
Related docs: ...
Related code: ...
Related tests: ...
Supersedes: ...
Superseded by: ...
```

This does not need to become a rigid bureaucracy. The point is to make each doc easy to trust at a glance.

## 9. Definition of Done

The review is in good shape when:

- every important doc has a clear role
- every stale vision doc has either been archived or merged forward
- root docs are accurate
- major code areas have local `AGENTS.md`
- major shared agent docs have matching `CLAUDE.md` import shims
- dense areas that need maps have maintained `INDEX.md` files
- the canonical reading path is obvious
- architecture docs match the current code well enough to be trusted
- agents can avoid large irrelevant files and historical traps

## 10. Working Log

This section is intentionally lightweight and should be updated as the review progresses.

### 2026-04-10

- Confirmed canonical docs:
  - `vision_documents/architecture.txt`
  - `vision_documents/the_yoga.txt`
  - `vision_documents/spring_cleaning.txt`
- Agreed taxonomy:
  - `north-star`
  - `active-plan`
  - `historical`
  - `scratch`
- Agreed archive location:
  - `vision_documents/archived/`
- Agreed handling for stale docs:
  - merge useful material forward, then archive the stale source
- Agreed root architecture split:
  - keep `ARCHITECTURE.md` as a short summary and redirect
- Agreed doc-format strategy:
  - use Markdown for new index and agent-facing docs
  - avoid unnecessary first-pass renames of canonical `.txt` docs
- Agreed layered navigation strategy:
  - use `AGENTS.md` for shared rules and orientation
  - use `CLAUDE.md` shims to import shared `AGENTS.md` guidance
  - add narrow `INDEX.md` files only in dense or high-churn areas
- Added initial index targets:
  - `vision_documents/INDEX.md`
  - `edgecaster/systems/INDEX.md`
  - `tests/INDEX.md`
- Completed first-pass `vision_documents/` classification:
  - kept active/current docs in place
  - identified north-star docs beyond `the_yoga.txt`
  - identified historical and scratch docs to archive after merge-forward cleanup
- Added local navigation docs:
  - `edgecaster/AGENTS.md`
  - `edgecaster/systems/AGENTS.md`
  - `edgecaster/systems/INDEX.md`
  - `tests/AGENTS.md`
  - `tests/INDEX.md`
