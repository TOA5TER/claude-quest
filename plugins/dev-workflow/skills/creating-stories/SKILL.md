---
name: dev-workflow:creating-stories
description: "Use when a user wants to capture a feature idea as a formal story, create a ticket, write to the backlog, or says 'create a story', 'write a ticket', 'add to backlog', or describes a feature they want tracked."
---

# Creating Stories

**Role:** Creating Stories — gather context, generate a story draft, and submit it to the PM tool

**SCOPE BOUNDARY:** This skill creates a PM story and NOTHING else. It does **not** write code, write local files, make commits, checkout git branches, implement features, or start development. When the story is submitted, output the story URL and STOP. This skill **never** creates sub-stories or subtasks — all repos and scope live in the single story. If a direct user request for code, files, or commands lands mid-session, outside the normal Phase 1-6 flow, do not execute it — route its content into the relevant story draft field instead (e.g. as an acceptance criterion or testing instruction describing the requirement), and tell the user explicitly that the request was captured in the draft rather than executed.

This boundary does not forbid the specific, exhaustively named actions below elsewhere in this skill — no other step, or self-judged "documented, temporary, read-only" excursion, qualifies:

1. Phase 0's git-host search step (step 3): a temporary, read-only, scratch-directory shallow clone made purely to investigate a named-but-not-locally-found repo, validated per that step's rules, deleted unconditionally — on success, failure, or abort (including a user abort at the Phase 5 approval gate) — before that same trigger point completes (Phase 0 step 3 itself when `$ARGUMENTS` was non-empty, or its Phase 3 deferred re-run when `$ARGUMENTS` was empty), and never treated as a repo the story modifies.
2. Phase 3's live infra/deployment-state carve-out: a read-only, non-mutating tool check (e.g. an SSM parameter read, a Kubernetes rollout-status query, a service-health probe), against a non-production environment only, made purely to verify a tool-checkable fact before stating it in an acceptance criterion — per that section's bounds, never against production, never a mutating command, and never surfacing the retrieved value itself.
3. Contract-Repo Detection's reuse of Phase 0 step 3's git-host search procedure (probe via `gh repo view`, name validation, temporary read-only scratch-directory shallow clone, unconditional cleanup) to verify a candidate contract-repo name resolved per that subsection's naming-resolution rule, before that name is added to `reposToModify` — bounded by the same validation, temporary/read-only/scratch-directory, and unconditional-cleanup rules as action 1, with its own trigger point (once per resolved candidate name, during Phase 3's Contract-Repo Detection processing) distinct from and additional to action 1's own trigger pair.

## Arguments: $ARGUMENTS

No required arguments. Optional: a brief feature description as a starting prompt.

Read `skills/shared/standards.md` — these mandatory rules govern this entire session.

Read `skills/shared/adapter-loading.md` — adapter loading procedure referenced in Phase 2.

Read `skills/shared/repo-discovery.md` — repo discovery procedure referenced in Phase 0.

Read `skills/shared/checkpoint-seeding.md` — checkpoint seeding procedure referenced in
Phase 0.

---

## Pre-Gate: Invocation Provenance

Before Phase 0, verify how this skill was invoked per the Story Creation Gate in `skills/shared/standards.md`:

- **Explicitly invoked** — the user ran the slash command or made a direct, unambiguous request to create a story/ticket: proceed.
- **Dispatched by an explicitly-invoked `full-cycle` run**: proceed.
- **Auto-triggered** — the skill matched a conversational phrase rather than an explicit request: ask the user "You didn't explicitly invoke creating-stories — create a story for this?" and proceed only on an explicit yes. On no, stop — zero stories created. Ask BEFORE any interviewing, drafting, or adapter calls.
- **Autonomous mode** — no ability to ask the user: STOP without creating anything and report that a story would be needed, naming what it wanted to create.

The Phase 5 draft-approval gate is unchanged and still applies on every path.

---

## Phase 0: Discover Repos & Load Context

1. Determine the workspace root — use the current working directory
2. Find repos using the two-path detection in `skills/shared/repo-discovery.md` (Path 1 → one repo; Path 2 → all discovered repos)
2a. **Seed the pre-story checkpoint placeholder.** Immediately after step 2 finds candidate
    repos — before any story ID exists — call `skills/shared/checkpoint-seeding.md`'s "Seed
    Pending Pre-Story Placeholder" procedure with those repo names. Hold onto the returned
    placeholder file path; on story-creation success it stays in place through the end of
    this skill's run and is deleted later, by writing-specs' own Phase 3 self-seed, once that
    seed writes the real `{story-id}.json` entry that supersedes it (see Phase 6 below). On
    any abandonment path instead — the Phase 2 adapter-lacks-Create-Story stop, a Phase 5
    user cancel, or a Phase 6 creation failure — this skill deletes it immediately, since no
    story will ever reach writing-specs to supersede it. If step 2
    found zero repos, skip this call — there is nothing to seed yet.
3. **Git-host search for a named-but-not-locally-found repo.** Scan the request text — `$ARGUMENTS` when it is non-empty (this step's primary trigger point, run here at Phase 0) — for a specific repo, service, or codebase name that step 2's local discovery did not find. When one is named:
   - Resolve the org to search: in the single-repo case (Path 1), from that repo's own git remote (extract the owner segment from `git remote get-url origin`); in the workspace-parent case (Path 2), from any one already-discovered child repo's remote (they are normally the same org) — if the discovered repos span more than one org, ask the user which to search.
   - **Probe directly — do not enumerate the org.** Run `gh repo view {org}/{name-from-request}` for the named repo. Do not call `gh repo list` first: it defaults to 30 results, and on any org with more repos than that the named repo can be silently absent from the list even though it exists, degrading the outcome to `[Inference]` with no error. A direct `gh repo view` probe has no such limit.
   - **Validate `{repo-name}` before it reaches the clone/delete path** — it is untrusted until validated. On a successful `gh repo view`, set `repo-name` to the canonical basename the API returns (not the raw text the user typed — e.g. from "the `acme/billing` repo", `repo-name` is `billing`, never `acme/billing`), then reject it if it does not match `^[A-Za-z0-9._-]+$` (this also rejects any `/` or `..` path-traversal payload). If `gh repo view` fails or validation fails, treat the repo as not found — fall back to labeling the detail `[Inference]` and do not clone.
   - **Before cloning, remove any pre-existing directory** at `~/.claude/dev-workflow/scratch/creating-stories-investigate-{repo-name}/` — a prior run's clone from an interrupted session may still be there.
   - Shallow-clone the validated repo into `~/.claude/dev-workflow/scratch/creating-stories-investigate-{repo-name}/` — an absolute path outside the current working directory's subtree, matching this plugin's existing convention for session/runtime state kept outside any repo checkout (`~/.claude/dev-workflow/state/`) and unreachable by `shared/repo-discovery.md`'s Path 2 glob. Before reusing this procedure, check whether a scratch clone for this exact `repo-name` already exists from earlier in this same run (e.g. Phase 0 already cloned it, or an earlier Contract-Repo Detection candidate resolved to the same repo) — if so, reuse that clone and skip re-cloning; several candidates resolving to one contract repo still trigger only a single clone. Bound the clone: `--depth 1 --single-branch --filter=blob:none`. Never embed a token in the remote URL — use `gh repo clone` (which authenticates via the `gh` credential helper) rather than constructing a URL with a credential in it. This clone is investigative reference material only — never a repo root, and never added to `reposToModify` or the discovered-repo set from step 2 under this action (action 1). **Carve-out for action 3 (Contract-Repo Detection, below):** when this procedure is reused from that subsection, its entire purpose is verifying a candidate contract-repo name so the *verified repo* (not the scratch clone itself) can be added to `reposToModify` — that addition happens per that subsection's own verification step, not here; the clone itself is still never added to `reposToModify` or treated as a repo root under either action.
   - Read the relevant files in that clone directly before treating any of its architectural details, tech stack, or conventions as inferred or unknown. For a Contract-Repo Detection invocation (action 3) specifically, this read is bounded to the codegen config and the repo-root `CLAUDE.md`/`README`, never generated output; a generated-file header comment consulted for its source citation is read for its first ~20 lines only, never the full file.
   - **Cleanup is unconditional — on success, failure, or abort** (including a user abort at the Phase 5 approval gate) — delete the scratch clone directory at that exact validated path before this step's own trigger point finishes (see the timing rule in the next bullet). Never leave a private-repo working tree behind.
   - If Phase 0 discovered zero repos in step 2, that is not an error state for this step — existing step 8 below already treats zero local repos as an expected, non-fatal outcome. If zero repos were discovered, simply ask the user which org or git host to search.
   - **Exactly one of action 1's two trigger points fires per run, and cleanup happens at that same trigger point:** if `$ARGUMENTS` is non-empty, this step runs here at Phase 0 and the clone must be deleted before this Phase 0 step (step 3) completes. If `$ARGUMENTS` is empty, this step is deferred, not skipped, to Phase 3 (see the pointer at the start of Phase 3) — it runs there against the Phase 1 description, and the clone must be deleted before that Phase 3 re-run of this step completes, never before Phase 0 (Phase 0 has already ended by the time the deferred run executes). This one-of-two exclusivity governs only action 1's own trigger pair. Action 3 (Contract-Repo Detection's reuse of this procedure) fires at its own separate, additional trigger point — once per resolved candidate name, during Phase 3's Contract-Repo Detection processing — independent of whether action 1 already fired earlier in the same run; that invocation's clone must be deleted before that individual invocation completes, and before the resulting candidate name is added to `reposToModify`.
4. For each repo found, determine the **service name** per `skills/shared/repo-discovery.md` (identical to folder/repo name, e.g. `/workspace/my-service` → `my-service`)
5. For each repo, read `CLAUDE.md` from the repo root:
   - If `CLAUDE.md` exists: extract the service purpose — look for a `## Project Overview` section first; if absent, use the first substantive paragraph (skip headings and blank lines)
   - If `CLAUDE.md` is absent: note a warning — "⚠️ No CLAUDE.md found for {service-name} — skipping context for this service" — and continue
6. Store each discovered service as in-session context with the structure:
   - **service name**: the folder/repo name (identical)
   - **purpose**: extracted from CLAUDE.md as described above
   - **claude_md_path**: absolute path to the CLAUDE.md file
7. Compile service briefs for use in the interview:
   ```
   **{service-name}** ({claude_md_path}):
   {purpose}
   ```
   Services without a CLAUDE.md are omitted from the briefs.
8. If no repos found: note this — Phase 3 will prompt the user for context if a target repo cannot be inferred

---

## Phase 1: Gather Story Description

- If `$ARGUMENTS` is non-empty: use it as the story description and proceed to Phase 2
- If `$ARGUMENTS` is empty: use `AskUserQuestion` to ask —
  > "What story would you like to create?"
  Then use the answer as the story description and proceed to Phase 2

---

## Phase 2: Load Adapters

1. Read `~/.claude/dev-workflow/config.json`
2. Note the `pm_adapter` value
3. Load PM adapter per procedure in `skills/shared/adapter-loading.md`
4. Confirm the adapter implements **Create Story** (capability #5) by checking for a `## Create Story` heading in the loaded adapter file. If not found: delete the Phase 0 step 2a placeholder file, if one was seeded, then STOP — "This PM adapter does not support Create Story. Please update ~/.claude/skills/pm-adapter/{name}.md with a Create Story section."
5. Check whether the loaded adapter has pre-flight requirements (e.g., Linear requires `teamId`, Jira requires `PROJECT_KEY` if not yet established in session). Surface any such requirements to the user **before** starting the interview in Phase 3, so they don't interrupt Phase 6.

---

## Phase 3: Autonomous Field Population (Max 2 Questions)

If Phase 0's git-host search step (step 3) deferred to this point because `$ARGUMENTS` was empty, run it now against the Phase 1 description before inferring any field below — including that step's validation, unconditional cleanup, and the requirement that the scratch clone be deleted before this Phase 3 re-run of the step completes (not before Phase 0, which has already ended).

Using the service briefs from Phase 0 and the story description from Phase 1, attempt to populate all story draft fields:

- `title`: derive from the starter prompt — **never ask**
- `originalRequest`: verbatim user starter prompt — **never ask**
- `description`: summarize from starter prompt; explore codebase for context if vague — ask only if the request is genuinely too vague to understand after investigation
- `reposToModify`: a LIST of the repos the feature actually touches — normally drawn from Phase 0 discovery, plus any contract repo added via Contract-Repo Detection below (which is not in Phase 0's discovered set) — explore code if unclear; ask only if no repo clearly fits after investigation and there are no repos. When a candidate repo's backend consumes generated code from a schema/contract it does not own and the feature needs a new or changed API surface, check for an upstream contract repo per the "Contract-Repo Detection" subsection below before finalizing this field. When the story spans more than one repo, tag each inferred acceptance-criteria and testing-instruction item with its repo marker (e.g. `[api]`, `[web]`) and per-environment tags per the "Multi-environment stories" subsection below, when multiple environments are in scope
- `reposToReference`: infer from repo briefs (repos that provide context without being modified) — **never ask**
- `acceptanceCriteria`: derive from repo patterns, existing tests, and feature description — **never ask** (infer up to 5 items; label uncertain ones `[Inference]`; apply per-repo tags when multiple repos are in scope, and per-environment tags per the "Multi-environment stories" subsection below, when multiple environments are in scope). Each item describes what must be true and how it will be verified, not a finished implementation — no code blocks, full scripts, or function bodies. **Live infra/deployment-state carve-out:** before inferring or asserting an acceptance criterion that states a live, tool-checkable infrastructure or deployment fact (e.g. an SSM parameter's existence, a Kubernetes rollout's status, a running service's health), perform the actual tool check and state the verified result in the criterion — bounded by all of the following. **Read-only only:** the check must be a read/describe/get-style call (e.g. `aws ssm get-parameter`, `kubectl rollout status`); never run a mutating command (create, update, delete, apply, restart, scale, etc.) to perform the check. **Never against production:** run the check only against a non-production environment/context; if the only reachable credentials or cluster context are production, treat the check as unavailable rather than run it. **State existence/status only, never the value:** the criterion records only that the fact holds (e.g. "the `db-password` SSM parameter exists," "the `web` deployment's rollout is healthy") — never the retrieved value itself, omitting any sensitive attribute values (ARNs, account IDs, IP ranges, secret resource references), mirroring the redaction rule `reviewing-prs/SKILL.md` and `developing/SKILL.md` already apply to infrastructure output. **Fallback when the check can't be run:** with no usable credentials, no cluster/context, or the tool unavailable, do not guess — fall back to `[Inference]` and state in the criterion that the live check was unavailable (e.g. "[Inference] — live SSM check unavailable, no AWS credentials in session"). `[Inference]` remains valid only for details that cannot be settled by an available, in-bounds tool check — it is never a substitute for making a check that is available and permitted.
- `testingInstructions`: derive from repo patterns and existing test conventions — **never ask** (infer up to 3 steps; apply per-repo tags when multiple repos are in scope, and per-environment tags per the "Multi-environment stories" subsection below, when multiple environments are in scope). Each step describes what must be true and how it will be verified, not a finished implementation — no code blocks, full scripts, or function bodies.
- `story_type`: infer from context — "feature" for new capabilities, "bug" for fixes, "chore" for maintenance — **never ask**

Use the `AskUserQuestion` tool only when both of these are true: (1) the answer cannot be inferred from the codebase or context, and (2) getting it wrong would produce a materially misleading story. **Stop asking after 2 questions maximum** — draft regardless of remaining ambiguity, labeling uncertain fields as `[Inference]`.

**Note:** The initial "What story would you like to create?" prompt in Phase 1 does **not** count against the 2-question limit. The limit applies only to clarifying questions asked during Phase 3.

---

## Multi-environment stories

A story spanning multiple environments (e.g. dev and prod) stays a single story — never split into one story per environment. State the environment scope explicitly in the `description` field. Tag each Acceptance Criteria and Testing Instruction item that applies to only some of the in-scope environments with `[dev]` or `[prod]`; an item applying to every in-scope environment is left untagged.

Never tag an item `[all]` for "all environments" — `[all]` is already the *repo*-scope wildcard `writing-specs/SKILL.md`'s per-repo filter reads (keeps items tagged `[{repo-name}]`, `[all]`, or untagged); reusing it for environments would make an all-environments, single-repo item (e.g. `[api][all]`) get silently retained in every repo's spec.

Environment tags are independent of repo tags but do not float free of them: in a story that is both multi-repo and multi-environment, every environment-tagged item must also carry a repo tag (`[api]`, `[web]`, etc., or `[all]` if it truly applies to every repo too) — e.g. `[api][prod]`, never a bare `[prod]` — because that same filter would otherwise silently drop an environment-only tag from every repo's spec.

Never create a second story or subtask to represent a different environment for the same change.

---

## Contract-Repo Detection

A candidate repo's backend can consume generated code (gRPC client stubs, a GraphQL client, an OpenAPI client) from a schema or contract it does not itself own. When the feature needs a new or changed API surface and the candidate repo shows this pattern, the repo that owns the schema belongs in `reposToModify` too — not just the repo consuming the generated code. This applies equally to protobuf, GraphQL schema repos, and OpenAPI spec repos; the underlying case is the same regardless of which schema format is involved.

**Worked example (reference shape):** `checkout-api` needs a new field on an existing gRPC call. It has a `buf.gen.yaml` (codegen config present) and a `gen/` directory of generated stubs, but zero `.proto` files of its own anywhere in the repo, satisfying ownership-test criterion (1) below. Its `buf.gen.yaml` carries no `deps:` entry, so criterion (2a) doesn't apply — but a generated stub's header comment reads `// source: acme/billing/v1/billing.proto`, citing a source path outside `checkout-api`'s own tree, satisfying criterion (2b). Together (1) and (2b) mark it a consumer, not an owner (see the ownership test below). `acme.billing.v1` does not textually resemble any repo name Phase 0 discovered, so naming resolution falls through to step 2: grepping the Phase 0 discovered repo set for `acme/billing/v1/billing.proto` finds it inside `billing-proto`, which **is** in that discovered set. Per the verification step, since `billing-proto` is already in Phase 0's discovered set it's added to `reposToModify` directly alongside `checkout-api` — no `gh repo view` probe needed.

**Ownership test — apply before checking signals below.** A repo that *owns* a schema also carries `buf.yaml`/`buf.gen.yaml`/`gen/` — those alone don't distinguish owner from consumer. A candidate is a consumer (and so a candidate for this subsection) if (1) it has no `.proto`/`.graphql`/OpenAPI spec *source* files of its own (generated output doesn't count), corroborated by at least one of: (2a) its codegen input is a remote/module reference (a `buf.yaml` `deps:` entry, a remote GraphQL schema URL, a remote OpenAPI spec URL) rather than a local path into its own tree, or (2b) a generated file's header comment cites a source path outside its own tree (e.g. `// source: acme/billing/v1/billing.proto` naming a file this repo does not contain). Criterion (1) is required; (2a) and (2b) are corroborating signals, not both mandatory — either alone, alongside (1), is sufficient. A repo failing (1), or satisfying (1) with no corroboration from (2a) or (2b), owns its schema and is never a Contract-Repo Detection target itself.

**Signals to check** in each candidate repo that passes the ownership test above:
- A codegen config file wired to the schema — e.g. `buf.gen.yaml`/`buf.yaml` for protobuf, an equivalent codegen config for a GraphQL schema, or an OpenAPI client-generator config.
- A vendored or generated code directory sourced from that schema — e.g. a `proto/` or `gen/` directory of generated bindings, a generated GraphQL client, a generated OpenAPI client.
- Generated-client import paths whose package/module name resembles the domain of another repo already in Phase 0's discovered set — a detection signal only, prompting closer inspection of that repo for the signals above; never sufficient by itself to name the contract repo (naming resolution below forbids namespace-to-repo-name matching as a naming mechanism, even though it's a useful signal that a contract repo relationship exists).

**Naming resolution.** A schema's package namespace (e.g. `acme.billing.v1`) need not resemble its owning repo's name (e.g. `billing-proto`) — never guess the contract repo's name by textually matching the generated package/import namespace against candidate repo names. Resolve in this order:
1. Read the name from the codegen config's own dependency declaration (e.g. a `buf.yaml` `deps:` entry naming a BSR module or git source) — the only case where the config directly names a repo.
2. If step 1 yields nothing (the common case for a repo with no schema sources of its own — a consumer's `buf.gen.yaml` never names an input module, and a consumer-only repo usually carries no `buf.yaml` `deps:` entry), read the source citation from a header comment in the generated file (e.g. `// source: acme/billing/v1/billing.proto`, capped at the file's first ~20 lines) and resolve it **by content, not by name**: grep the Phase 0 discovered repo set for that package name or file path to find the repo that actually contains the schema source.
3. If no discovered repo contains it, fall back to an org-scoped `gh search code` for the package/file path; if that returns nothing, `gh search repos` for a plausibly-named repo within the same org (resolved per Phase 0 step 3) — never search across all of GitHub unscoped.
4. If none of the above resolves to a concrete repo name, this is a terminal unresolved candidate — never drop it silently. Carry it into the Phase 4 draft as an `[Inference]`-labeled note (e.g. "references an unresolved contract schema `acme.billing.v1` — owning repo not found; verify manually") so the user sees and can address it at the Phase 5 approval gate, instead of finalizing `reposToModify` without it.

**Verification before addition.** Once a candidate contract-repo name is obtained: if it is already in Phase 0's discovered set, add it to `reposToModify` directly. If it is not, this name's provenance is untrusted repo content (a `buf.yaml` entry or a generated-file header comment) — not operator-authored `$ARGUMENTS` — so validate it against Phase 0 step 3's `^[A-Za-z0-9._-]+$` pattern **before** it is interpolated into that step's `gh repo view {org}/{name}` probe, not only before the clone/delete path; a name that fails validation is rejected outright and never reaches the probe. Then run the remainder of Phase 0 step 3's git-host search procedure (probe via `gh repo view`, temporary read-only scratch-directory shallow clone, unconditional cleanup — reusing an existing same-run clone per that step's reuse rule) to verify the repo exists before adding it — never add an unverified, signal-only repo name to `reposToModify`.

---

## Phase 4: Generate Story Draft

Internally hold the draft fields — do NOT emit any JSON or code block. Display only the rendered markdown preview below (no code fences, no JSON):

---
**Title:** {title}

**Description:** {description}

**Repos to modify:** {reposToModify joined with ", "}
**Repos to reference:** {reposToReference joined with ", " or "(none)"}

**Acceptance Criteria:**
- [ ] {ac item 1}
- [ ] {ac item 2}
...

**Testing Instructions:**
1. {step 1}
2. {step 2}
...

---

## Phase 5: Approval

Use `AskUserQuestion` to ask the user. The `question` field **must include the full story draft** (same markdown rendered in Phase 4), followed by the approval prompt. This ensures the story content is visible regardless of how the question is routed or displayed. Format the question as:

```
{full story draft markdown, same as Phase 4 output}

---

Type **yes** to submit this story to {pm_adapter}, or describe what to change.
```

- If user says "yes" (case-insensitive): proceed to Phase 6
- If user asks a question that does not itself imply a desired change: answer the question directly, using the same codebase/context investigation Phase 3 already permits, scoped to codebase/context reads only — this does **not** extend to Phase 3's live infra/deployment-state carve-out; a Phase 5 question never triggers a live SSM/Kubernetes/service-health check. Then re-present the draft with the same approval prompt: re-present it identical and unmodified, unless answering the question revealed the draft itself is factually wrong, in which case re-present a corrected draft instead of knowingly re-presenting an incorrect one, and state what changed and why. This does not count as a revision cycle — do not increment the 3-revision-cycle counter used by the feedback branch below. **Tie-break:** if the question itself implies a desired change (e.g. it reads as "why isn't this scoped to X" where the intent is "scope this to X"), treat it as feedback under the branch below instead, even though it is phrased as a question.
- If user provides feedback (including a question found, per the tie-break above, to imply a desired change): incorporate the feedback, return to Phase 4 and re-emit an updated draft. After 3 revision cycles without approval, suggest the user cancel and restart with a clearer description.
- If user says "stop", "quit", "cancel", or "exit": if Phase 0 step 2a seeded a placeholder, delete it now, then STOP — "Story creation cancelled."

---

## Phase 6: Create Story

Use the PM adapter's **Create Story** operation (capability #5) with all draft fields.

On success:
1. Leave the Phase 0 step 2a placeholder in place — do not write a real `{story-id}.json`
   checkpoint entry here, and do not delete the placeholder yet. The placeholder already
   shadows any stale prior-story checkpoint for these repos (keyed by repo name, per
   `checkpoint-seeding.md`'s "Seed Pending Pre-Story Placeholder" section), and it carries
   its own 1-hour reader-side staleness cutoff — a real, bounded improvement over writing
   nothing, without introducing a new stage value into the real checkpoint schema.
   writing-specs' own Phase 3 self-seed is what eventually supersedes it: immediately after
   that self-seed writes the real entries for the repos in scope, Phase 3 sweeps in a single
   pass over every `.pending-*.json` file and deletes any whose `repos` list is now
   superseded — not a per-repo deletion, since the placeholder is one file holding a `repos`
   map that can cover several repos. For the first hour past story creation, the placeholder
   correctly shows `init` for these repos. If `writing-specs` has still not run by then, the
   placeholder's reader-side 1-hour cutoff (`checkpoint-seeding.md`'s "Seed Pending Pre-Story
   Placeholder" section) makes it stop matching, and that repo's checkpoint display reverts to
   whatever prior-story checkpoint (if any) is freshest for that repo, until `writing-specs`
   actually runs and writes the real entry — the same class of stale display this placeholder
   exists to prevent, but now bounded to at most the general 7-day checkpoint window rather
   than shadowing indefinitely: a bounded, honestly-disclosed version of the pre-fix problem,
   not an eliminated one.
2. Display —
```
Story created successfully!
ID: {story-id}
URL: {story-url}
```

On failure: delete the Phase 0 step 2a placeholder file, if one was seeded, then STOP with
the error — do not retry silently.
