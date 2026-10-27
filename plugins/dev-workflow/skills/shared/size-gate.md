# Size Gate

The single definition of the size gate: how big a story or spec may be before it must be split. Read it for the counting rule, the thresholds, the size classes, the split patterns, the split-plan format and the marker that records a kept oversized story. The readers below cite sections by name.

## Who Reads This

- `skills/writing-specs/SKILL.md`, C1 Size Gate and C10 Length Caps: applies every threshold, assigns the size class and proposes the split plan at spec time.
- `skills/creating-stories/SKILL.md`, Phase 3.5 Size Check: applies the rule-count and examples-per-rule thresholds before any story exists, and writes the marker.
- `skills/writing-specs/spec-lint.py` keeps its own copy of the S and M prose caps so the lint never fails to run; `tests/test_size_gate.sh` fails when that copy differs from the Size Classes table below.

## Counting

Count rules as Example Mapping does: one rule per distinct business rule the request or its acceptance criteria imply, never one per acceptance-criterion line. Count each rule's concrete examples under that rule.

## Thresholds

The gate trips when any row below is true.

| Check | Trips when | Applied by |
|-------|------------|------------|
| Rule count | above 5 rules | writing-specs and creating-stories |
| Examples for any one rule | above 5 examples | writing-specs and creating-stories |
| Projected spec prose | above the M prose cap | writing-specs only, because creating-stories has no spec to project |

## Size Classes

| Class | Applies when | Prose cap (words) |
|-------|--------------|-------------------|
| **S** | The story has at most 3 acceptance criteria for the repo, one repo is in scope and the change adds no new rule or state logic. | 1,500 |
| **L** | The gate trips. The spec always carries `data-cap-accepted="true"`. | none |
| **M** | Otherwise. | 4,000 |

Prose words are counted as C10 Length Caps defines them.

## Split Patterns

Split by the SPIDR patterns (Spike, Paths, Interfaces, Data, Rules) or the Humanizing Work story-splitting patterns (for example by workflow step, business-rule variation or data variation). Every part must itself stay within the Thresholds. When one rule alone has too many examples, split by business-rule or data variation: divide that rule into narrower rules, or divide its examples across parts, so each part lists the narrower rules or the examples it takes. Never cut one part per repo or one part per environment: those stay in one story under the multi-repo and multi-environment rules.

## Split Plan

List, per part:

- title and scope
- repos to modify and repos to reference, each a subset of the whole request's repos
- the counted rules it takes, by rule text, each with its example count; every counted rule goes to exactly one part (a rule divided under Split Patterns counts as its narrower rules, each going to exactly one part)
- its acceptance criteria, by criterion text
- its order

A writing-specs plan uses the story's existing acceptance criteria, each assigned to exactly one part, and also states whether the original story is archived or kept as the parent. It ends with the instruction to verify the plan, create each part with creating-stories, and run writing-specs on each part.

A creating-stories plan drafts each part's acceptance criteria for that part's rules under Phase 3's acceptance-criteria rule (up to 5 per part). Because the parts are created in the same run, it ends with the two Phase 3.5 options instead of the instruction above.

## Size-Accepted Marker

When a user keeps one oversized story, creating-stories ends the story description with this single line, with `{N}` replaced by the number of counted rules:

`Size accepted by user: the size gate tripped at story creation ({N} rules counted) and the user kept one story.`

creating-stories is the writer. writing-specs C1 is the reader: it matches the prefix `Size accepted by user:` ignoring case, and a repeated line acts as one.
