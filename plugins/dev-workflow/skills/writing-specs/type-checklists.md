# Type Checklists

Phase 5 tags the change with every type below that matches, then copies that type's rows into the spec's Type Checklists table (`table.type-checklist`, `id="type-checklists"`). A change can match several types.

## Answer rules

- Every copied row is answered, or marked `N/A: reason`. An empty cell is never acceptable.
- When no type matches, the table is replaced by one line: `Type checklists: none apply: reason`.
- An S spec copies tagged rows only.
- Row IDs stay exactly as written here so the matrix, the lint and the reviewers can cite them.

## Terraform and ConfigMap scope

The terraform/configmap-only classification (see Change-Scope Classification in `SKILL.md`) keeps its current behavior, with one link to this file:

- A terraform/configmap-only scope is tagged Infra, and INF-1 is answered by the terraform plan check.
- The Test Requirements statement that names terraform plan carries one T-n id.
- Every AC and every R maps to that T in the Traceability Matrix.
- The badge-waived Validation Checklist item carries no AC id.

## UI

- UI-1: What does the user see while the data is loading?
- UI-2: What does the user see when there is nothing to show (the empty state)?
- UI-3: What does the user see when the load or action fails, and how do they recover?
- UI-4: What does the user see when only part of the data loads or only part of an action succeeds?
- UI-5: What happens to controls and content the user lacks permission to see or use (hidden, disabled or an explanation)?
- UI-6: Is every new control reachable by keyboard, labelled for assistive technology and readable at the contrast the codebase requires?
- UI-7: Is every new user-facing string translatable, and do dates, numbers and text length behave in other locales?

## API

- API-1: Who may call each endpoint or operation, and what enforces it on every path?
- API-2: Which error codes and bodies does each failure return, and do they match the existing convention?
- API-3: What happens when the same request is sent twice (idempotency key, safe retry or documented duplicate)?
- API-4: How do list responses paginate, sort and cap their size?
- API-5: Does the change keep every existing request and response shape working for current callers, and if not, how are they versioned or migrated?

## Bug

- BUG-1: What are the exact steps, inputs and environment that reproduce the bug?
- BUG-2: What is the root cause, shown by evidence rather than assumed from the symptom?
- BUG-3: Where else does the same pattern occur, and is every such site fixed or explicitly out of scope?
- BUG-4: Which test fails before the fix and passes after it?

## Infra

- INF-1: What does the plan or diff show will change, and does it match the intended change exactly?
- INF-2: How does the change stay isolated from other environments (variables, workspaces, namespaces, state)?
- INF-3: How is the change rolled back, and what state does the rollback leave behind?
- INF-4: What is the blast radius if the change misbehaves (services, users, data, other stacks)?

## Migration

- MIG-1: How many rows or objects does the migration touch, and does it finish within the deploy and timeout limits at that volume?
- MIG-2: What happens if the migration runs twice or stops partway and is re-run?
- MIG-3: How is the migration undone, and what happens to data written after it ran?
- MIG-4: Which locks does the migration take, for how long, and what blocks while it holds them?

## Integration

- INT-1: Which real payloads from the other system were inspected, and does the code handle their actual shape, including optional and unexpected fields?
- INT-2: What timeout applies to each call, and what happens to the caller when it expires?
- INT-3: Which failures are retried, how often, with what backoff, and is a retry safe?
- INT-4: What rate limits apply, and what happens when they are hit?

## Refactor

- REF-1: What behavior must stay identical, and which test or check proves it before and after?
- REF-2: Which callers, importers and consumers depend on what moves or changes, and is each one updated or confirmed unaffected?

## Rules

- RUL-1: What is the real vocabulary of the inputs the rules read (exact values, spellings, formats and absences found in real data)?
- RUL-2: Is every rule set written as a decision table with a hit policy, an else row and one test per row?
