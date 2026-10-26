#!/usr/bin/env bash
# Tests skills/writing-specs/spec-lint.py against synthetic fixtures in tests/fixtures/spec-lint/.
set -uo pipefail

require_python3() {
  command -v python3 >/dev/null 2>&1 || {
    echo "test_spec_lint.sh: python3 is required but was not found on PATH" >&2
    exit 1
  }
}

# Why: the guard must run before any external command, so the T-9 sub-case can run it with an empty PATH.
if [ "${1:-}" = "--guard-only" ]; then
  require_python3
  exit 0
fi

require_python3

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
LINT="$PLUGIN_ROOT/skills/writing-specs/spec-lint.py"
TEMPLATE="$PLUGIN_ROOT/skills/writing-specs/spec-template.html"
FX="$PLUGIN_ROOT/tests/fixtures/spec-lint"

mkdir -p "$REPO_ROOT/.scratch/tmp"
TMP="$(mktemp -d "$REPO_ROOT/.scratch/tmp/spec-lint-test.XXXXXX")"
cleanup() {
  for f in "$TMP"/*; do [ -e "$f" ] && rm -f "$f"; done
  rmdir "$TMP" 2>/dev/null
}
trap cleanup EXIT

FAILURES=0
fail() {
  echo "FAIL: $*" >&2
  FAILURES=$((FAILURES + 1))
}

has() {
  case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac
}

require() {
  local label="$1" text="$2" needle="$3"
  has "$text" "$needle" || fail "$label: missing '$needle'"
}

forbid() {
  local label="$1" text="$2" needle="$3"
  if has "$text" "$needle"; then fail "$label: unexpectedly contains '$needle'"; fi
}

OUT=""
RC=0
lint() {
  OUT="$(python3 "$LINT" "$@" 2>&1)"
  RC=$?
}

expect_rc() {
  local label="$1" want="$2"
  [ "$RC" -eq "$want" ] || fail "$label: exit $RC, wanted $want (output: $OUT)"
}

finding_classes() {
  printf '%s\n' "$OUT" | grep '^FINDING ' | awk '{print $2}' | sort -u | tr '\n' ' '
}

finding_count() {
  printf '%s\n' "$OUT" | grep -c "^FINDING $1 "
}

# Why: each lint class must be reported by its own fixture and by no other class, or the classes are not disjoint.
expect_only_class() {
  local label="$1" file="$2" cls="$3"
  lint "$FX/$file"
  expect_rc "$label" 1
  [ "$(finding_classes)" = "$cls " ] || fail "$label: classes were '$(finding_classes)', wanted only '$cls'"
  require "$label" "$OUT" "FINDING $cls "
  printf '%s\n' "$OUT" | tail -n1 | grep -Eq '^spec-lint: [0-9]+ finding\(s\)$' || fail "$label: last line is not the summary"
}

# Why: the clean S fixture is the baseline every other fixture is a one-defect mutation of.
lint "$FX/clean-s.html"
expect_rc "clean S fixture" 0
require "clean S fixture" "$OUT" "spec-lint: 0 finding(s)"
forbid "clean S fixture" "$OUT" "FINDING"

# Why: a clean M spec with links in every matrix cell must also pass, so the 3-cell matrix encoding is exercised without findings.
lint "$FX/matrix-linked-m.html"
expect_rc "clean M fixture" 0

# Why: every lint class has one fixture.
expect_only_class "cap" cap.html cap
expect_only_class "long-item" long-item.html long-item
expect_only_class "deferral" deferral.html deferral
expect_only_class "adjective" adjective.html adjective
expect_only_class "orphan" orphan.html orphan
expect_only_class "undefined-ref" undefined-ref.html undefined-ref
expect_only_class "table-gap" table-gap.html table-gap
expect_only_class "decision-table" decision-table.html decision-table
expect_only_class "uncited" uncited.html uncited
expect_only_class "assumption" assumption.html assumption
expect_only_class "unanswered" unanswered.html unanswered
expect_only_class "structure" structure.html structure

# Why: the deferral fixture also holds a quoted phrase, a code phrase and a verify sentence in #test-requirements; only the plain phrase may be reported.
lint "$FX/deferral.html"
[ "$(finding_count deferral)" -eq 1 ] || fail "deferral fixture: expected exactly one deferral finding, got $(finding_count deferral)"
require "deferral fixture" "$OUT" 'phrase "verify that"'
forbid "deferral fixture" "$OUT" "test-requirements"
forbid "deferral fixture" "$OUT" '"TBD"'
forbid "deferral fixture" "$OUT" '"TODO"'

# Why: the adjective fixture holds a numbered use and a longer word ("Efficiency") that must not match.
lint "$FX/adjective.html"
[ "$(finding_count adjective)" -eq 1 ] || fail "adjective fixture: expected exactly one adjective finding, got $(finding_count adjective)"

# Why: an accepted cap suppresses cap only.
lint "$FX/cap-accepted.html"
expect_rc "cap accepted" 0

# Why: an unreadable file or a missing size class is exit 2, not a finding.
lint "$FX/does-not-exist.html"
expect_rc "missing file" 2
lint "$FX/no-size-class.html"
expect_rc "no data-size-class" 2
lint
expect_rc "no arguments" 2

# Why: with python3 absent, the test guard must stop non-zero and name python3.
GUARD_OUT="$(PATH=/nonexistent "$BASH" "${BASH_SOURCE[0]}" --guard-only 2>&1)"
GUARD_RC=$?
[ "$GUARD_RC" -ne 0 ] || fail "python3 guard: exit 0 with python3 missing"
require "python3 guard" "$GUARD_OUT" "python3"

# Why: --words counts prose without tables, code, pre, header, TOC, scripts, styles and comments, and total without only the page furniture.
lint --words "$FX/words.html"
expect_rc "--words" 0
require "--words" "$OUT" "prose 10"
require "--words" "$OUT" "total 17"
lint --words "$FX/does-not-exist.html"
expect_rc "--words missing file" 2

# Why: --sections prints one line per h2 and h3 with its id and visible words, tables included.
lint --sections "$FX/headings.html"
expect_rc "--sections" 0
require "--sections" "$OUT" "h2 alpha 10"
require "--sections" "$OUT" "h3 alpha-sub 5"
require "--sections" "$OUT" "h2 beta 2"
[ "$(printf '%s\n' "$OUT" | wc -l | tr -d ' ')" -eq 3 ] || fail "--sections: expected one line per heading"

# Why: --extract of a heading id runs up to the next heading of the same or higher level, and an unknown id is exit 2.
lint --extract "$FX/headings.html" alpha
expect_rc "--extract h2" 0
require "--extract h2" "$OUT" "Alpha heading"
require "--extract h2" "$OUT" "four five"
require "--extract h2" "$OUT" "six seven"
forbid "--extract h2" "$OUT" "Beta"
lint --extract "$FX/headings.html" alpha-sub
expect_rc "--extract h3" 0
require "--extract h3" "$OUT" "four five"
forbid "--extract h3" "$OUT" "one two three"
forbid "--extract h3" "$OUT" "eight"
lint --extract "$FX/clean-s.html" requirements glossary
expect_rc "--extract two ids" 0
require "--extract two ids" "$OUT" 'id="requirements"'
require "--extract two ids" "$OUT" 'id="glossary"'
lint --extract "$FX/headings.html" alpha no-such-id
expect_rc "--extract unknown id" 2

# Why: a matrix that names a range instead of linking each rule leaves the rules unmapped, which is an orphan.
lint "$FX/matrix-range.html"
expect_rc "matrix range" 1
require "matrix range" "$OUT" "FINDING orphan #R-1"
[ "$(finding_classes)" = "orphan " ] || fail "matrix range: classes were '$(finding_classes)'"

# Why: --partition writes the whole spec for S, and DT-2 packs h2 sections at 3,000 words for larger specs with the earliest-section tie rule for perspective.
lint --partition "$FX/clean-s.html" "$TMP/s"
expect_rc "--partition S" 0
require "--partition S" "$OUT" "partition-1.html"
require "--partition S" "$OUT" "perspective=all"
[ -s "$TMP/s/partition-1.html" ] && mv "$TMP/s/partition-1.html" "$TMP/s-partition-1.html" && rmdir "$TMP/s" || fail "--partition S: partition-1.html not written"

para() { printf '<p>'; printf 'alpha %.0s' $(seq 1 100); printf '</p>\n'; }
section() {
  printf '<section id="%s"><h2>%s</h2>\n' "$1" "$1"
  for _ in $(seq 1 14); do para; done
  printf '</section>\n'
}
{
  printf '<!doctype html><html><head><title>big</title></head><body data-size-class="M">\n'
  section requirements; section technical-context; section test-requirements; section completeness-checks
  printf '</body></html>\n'
} > "$TMP/big.html"
lint --partition "$TMP/big.html" "$TMP/big"
expect_rc "--partition M" 0
[ "$(printf '%s\n' "$OUT" | wc -l | tr -d ' ')" -eq 2 ] || fail "--partition M: expected two partitions, got: $OUT"
require "--partition M" "$OUT" "partition-1.html sections=requirements,technical-context words=2802 perspective=user"
require "--partition M" "$OUT" "partition-2.html sections=test-requirements,completeness-checks words=2802 perspective=tester"
for k in 1 2; do
  [ -s "$TMP/big/partition-$k.html" ] || fail "--partition M: partition-$k.html not written"
  [ -f "$TMP/big/partition-$k.html" ] && mv "$TMP/big/partition-$k.html" "$TMP/big-partition-$k.html"
done
rmdir "$TMP/big" 2>/dev/null

# Why: mutated copies of a clean fixture show each rule fires on its own defect and stays quiet on the allowed forms.
mutate() {
  local base="$1" old="$2" new="$3"
  PYTHONDONTWRITEBYTECODE=1 python3 - "$FX/$base" "$TMP/mutated.html" "$old" "$new" <<'PY'
import sys
src, out, old, new = sys.argv[1:5]
text = open(src, encoding="utf-8").read()
if text.count(old) != 1:
    sys.exit("mutate: expected one match for %r, found %d" % (old, text.count(old)))
open(out, "w", encoding="utf-8").write(text.replace(old, new))
PY
}

lint_mutation() {
  local label="$1" base="$2" old="$3" new="$4" want="$5"
  mutate "$base" "$old" "$new" || { fail "$label: mutation did not apply"; return; }
  lint "$TMP/mutated.html"
  if [ -z "$want" ]; then
    expect_rc "$label" 0
  else
    expect_rc "$label" 1
    [ "$(finding_classes)" = "$want " ] || fail "$label: classes were '$(finding_classes)', wanted only '$want'"
  fi
}

CC_END='</section>
<section id="clarifications">'
COV='<table class="coverage-scan"><thead><tr><th>ID</th><th>Item</th><th>Status</th><th>Resolution</th></tr></thead><tbody><tr><td>CS-1</td><td>Edge cases</td><td>%s</td><td>%s</td></tr></tbody></table>'
cov() { printf "$COV" "$1" "$2"; }

lint_mutation "coverage clear" clean-s.html "$CC_END" "$(cov Clear 'R-1')$CC_END" ""
lint_mutation "coverage bare out of scope" clean-s.html "$CC_END" "$(cov Partial 'out of scope: later')$CC_END" unanswered
lint_mutation "coverage unknown status" clean-s.html "$CC_END" "$(cov Maybe 'R-1')$CC_END" unanswered
lint_mutation "coverage partial with no link" clean-s.html "$CC_END" "$(cov Partial 'R-1')$CC_END" unanswered
lint_mutation "coverage partial with question" clean-s.html "$CC_END" "$(cov Partial '<a href="#Q-1">Q-1</a>')$CC_END" ""
lint_mutation "coverage no-behavior-change prefix" clean-s.html "$CC_END" "$(cov Partial 'out of scope, no behavior change: cosmetic')$CC_END" ""

PM='<table class="pre-mortem"><thead><tr><th>ID</th><th>Cause</th><th>Effect</th><th>Severity</th><th>Detection</th></tr></thead><tbody><tr><td>F-1</td><td>Bad input</td><td>Crash</td><td>%s</td><td>%s</td></tr></tbody></table>'
pm() { printf "$PM" "$1" "$2"; }
lint_mutation "pre-mortem High without detection" clean-s.html "$CC_END" "$(pm High 'logs')$CC_END" unanswered
lint_mutation "pre-mortem High with test link" clean-s.html "$CC_END" "$(pm High '<a href="#T-1">T-1</a>')$CC_END" ""
lint_mutation "pre-mortem High with alert" clean-s.html "$CC_END" "$(pm High 'alert: crash-rate')$CC_END" ""
lint_mutation "pre-mortem Medium without detection" clean-s.html "$CC_END" "$(pm Medium 'logs')$CC_END" ""

lint_mutation "decision table without else-row" clean-s.html '<tr class="else-row">' '<tr>' decision-table
lint_mutation "undefined dfn term" clean-s.html '<dfn>greeting</dfn>' '<dfn>salutation</dfn>' undefined-ref
lint_mutation "undefined abbr term" clean-s.html 'uses the given name' 'uses the <abbr>CLI</abbr> name' undefined-ref
lint_mutation "glossary read-by id missing" clean-s.html '<td>The line the tool prints.</td><td>R-1</td>' '<td>The line the tool prints.</td><td>R-9</td>' undefined-ref
lint_mutation "unmapped test" clean-s.html '<section id="manual-testing">' '<p id="T-2">A second test nobody maps.</p><section id="manual-testing">' orphan
lint_mutation "empty matrix cell" matrix-linked-m.html '<td><a href="#R-1">R-1</a> <a href="#R-2">R-2</a> <a href="#R-3">R-3</a></td>' '<td></td>' orphan
lint_mutation "assumption with Inference" clean-s.html '<td>Clients send UTF-8 input.</td><td><a href="#Q-1">Q-1</a></td>' '<td>Clients send UTF-8 input.</td><td>[Inference] UTF-8 is the common default.</td>' ""
lint_mutation "quoted deferral phrase" clean-s.html 'prints a greeting for the name it is given.' 'prints a greeting; the word <q>TBD</q> is quoted here.' ""
lint_mutation "unquoted deferral phrase" clean-s.html 'prints a greeting for the name it is given.' 'prints a greeting; the owner is TBD.' deferral

WORDS150="$(python3 -c 'print(" ".join(["word"] * 150))')"
WORDS151="$(python3 -c 'print(" ".join(["word"] * 151))')"
lint_mutation "item of exactly 150 words" clean-s.html 'The tool prints a greeting for the name it is given.' "$WORDS150" ""
lint_mutation "item of 151 words" clean-s.html 'The tool prints a greeting for the name it is given.' "$WORDS151" long-item

# Why: --partition needs the size class like the lint does.
lint --partition "$FX/no-size-class.html" "$TMP/none"
expect_rc "--partition no size class" 2

# Why: the filled template proves a spec that follows the template lints clean; sections the fixture lacks are dropped and their dangling links unwrapped.
fill_template() {
  local filled="$TMP/template-filled.html"
  PYTHONDONTWRITEBYTECODE=1 python3 - "$LINT" "$TEMPLATE" "$FX/clean-s.html" "$filled" <<'PY'
import importlib.util, sys
lint_path, template, fixture, out = sys.argv[1:5]
spec = importlib.util.spec_from_file_location("speclint", lint_path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
tpl, fx = m.parse(template), m.parse(fixture)
tids, fids = m.ids_of(tpl), m.ids_of(fx)
missing = [i for i in ("requirements", "evidence-impact", "completeness-checks", "clarifications") if i not in tids]
if missing:
    sys.exit("template lacks section ids: " + ", ".join(missing))

def unit(ids, sid):
    n = ids[sid]
    return m.heading_flow(n) if n.tag in m.HEADINGS else [n]

body = m.find_first(tpl, "body")
for h, _ in m.sections_of(body, ("h2",)):
    sid = m.heading_sid(h)
    old = unit(tids, sid)
    parent = old[0].parent
    kids = parent.children
    at = next(i for i, c in enumerate(kids) if c is old[0])
    new = unit(fids, sid) if sid in fids else []
    for c in old:
        kids.remove(c)
    kids[at:at] = new
fbody = m.find_first(fx, "body")
body.attrs = [(k, v) for k, v in body.attrs if k not in ("data-size-class", "data-cap-accepted")] + \
             [(k, v) for k, v in fbody.attrs if k in ("data-size-class", "data-cap-accepted")]
ids = m.ids_of(tpl)
for a in [n for n in m.iter_nodes(tpl) if n.tag == "a"]:
    href = a.get("href") or ""
    if href.startswith("#") and len(href) > 1 and href[1:] not in ids:
        kids = a.parent.children
        at = next(i for i, c in enumerate(kids) if c is a)
        kids[at:at + 1] = a.children
with open(out, "w", encoding="utf-8") as fh:
    fh.write("<!doctype html>\n" + "".join(m.ser(c) for c in tpl.children))
PY
  local rc=$?
  [ "$rc" -eq 0 ] || { fail "template fill: substitution script failed with exit $rc"; return; }
  lint "$filled"
  expect_rc "template filled with the clean fixture" 0
  require "template filled with the clean fixture" "$OUT" "spec-lint: 0 finding(s)"
}
fill_template

if [ "$FAILURES" -eq 0 ]; then
  echo "PASS"
else
  echo "$FAILURES failure(s)" >&2
  exit 1
fi
