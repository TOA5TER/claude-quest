#!/usr/bin/env python3
import os
import re
import sys
from html import escape
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
INLINE = {"a", "abbr", "b", "bdi", "bdo", "big", "cite", "code", "data", "dfn", "em", "i", "kbd", "mark",
          "q", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "u", "var"}
HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
CLOSES_P = {"p", "ul", "ol", "table", "div", "section", "pre", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}
ALWAYS_EXCLUDED = {"head", "title", "script", "style", "header", "nav", "template"}
PROSE_EXCLUDED = {"table", "code", "pre"}
EXEMPT_TEXT = {"code", "q", "script", "style"}

# Must stay identical to the Deferral ban list in the Cold-Start Completeness Standard in SKILL.md.
DEFERRAL_PHRASES = [
    "verify that", "confirm whether", "check whether", "check if", "investigate whether",
    "needs investigation", "developer should decide", "at the developer's discretion",
    "TBD", "TODO",
    "if needed", "as appropriate", "or similar",
    "[Open Question]", "[Unverified]", "[Speculation]",
]
VERIFY_FORMS = {"verify that", "confirm whether", "check whether", "check if"}
VERIFICATION_SECTIONS = {"test-requirements", "manual-testing", "validation-checklist"}

ADJECTIVES = [
    "fast", "quickly", "slow", "efficient", "robust", "scalable", "user-friendly", "intuitive", "seamless",
    "easy", "performant", "reliable", "lightweight", "responsive", "secure", "appropriate", "reasonable",
    "sufficient", "adequate", "timely", "optimal", "minimal",
]
ADJECTIVE_BLOCKS = {"p", "li", "td", "th", "caption", "dd", "summary"}

REQUIRED_SECTIONS = ["requirements", "validation-checklist", "completeness-checks", "clarifications"]
OWNED_TABLES = {"trace-matrix", "glossary", "evidence-ledger", "assumptions", "type-checklist",
                "input-classes", "pre-mortem", "coverage-scan"}
CAPS = {"S": 1500, "M": 4000}
ITEM_CAP = 150
MAX_COLUMNS = 20
PARTITION_CAP = 3000

PERSPECTIVES = {
    "story-summary": "user", "requirements": "user", "clarifications": "user",
    "technical-context": "implementer", "implementation-steps": "implementer", "evidence-impact": "implementer",
    "test-requirements": "tester", "manual-testing": "tester", "validation-checklist": "tester",
    "completeness-checks": "operator",
}


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children = []

    def get(self, name, default=None):
        for k, v in self.attrs:
            if k == name:
                return v if v is not None else ""
        return default

    @property
    def id(self):
        return self.get("id")

    @property
    def classes(self):
        return set((self.get("class") or "").split())


class Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", [], None)
        self.stack = [self.root]

    def _pop_while(self, tags):
        while self.stack[-1].tag in tags:
            self.stack.pop()

    def handle_starttag(self, tag, attrs):
        top = self.stack[-1].tag
        if tag == "li" and top == "li":
            self.stack.pop()
        elif tag in ("td", "th"):
            self._pop_while(("td", "th"))
        elif tag == "tr":
            self._pop_while(("td", "th", "tr"))
        elif tag in ("thead", "tbody", "tfoot"):
            self._pop_while(("td", "th", "tr", "thead", "tbody", "tfoot"))
        elif top == "p" and tag in CLOSES_P:
            self.stack.pop()
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    b = Builder()
    b.feed(text)
    b.close()
    return b.root


def iter_nodes(node):
    for ch in node.children:
        if isinstance(ch, Node):
            yield ch
            yield from iter_nodes(ch)


def find_first(root, tag):
    for n in iter_nodes(root):
        if n.tag == tag:
            return n
    return None


def excluded(node):
    n = node
    while n is not None and n.tag != "#root":
        if n.tag in ALWAYS_EXCLUDED or n.id == "toc" or "toc" in n.classes:
            return True
        n = n.parent
    return False


def ser(x):
    if isinstance(x, str):
        return escape(x, quote=False)
    attrs = "".join(
        " " + k if v is None else ' %s="%s"' % (k, escape(v, quote=True)) for k, v in x.attrs
    )
    if x.tag in VOID:
        return "<%s%s>" % (x.tag, attrs)
    if x.tag in ("script", "style"):
        inner = "".join(c for c in x.children if isinstance(c, str))
    else:
        inner = "".join(ser(c) for c in x.children)
    return "<%s%s>%s</%s>" % (x.tag, attrs, inner, x.tag)


def text_of(node, skip=lambda n: False):
    parts = []
    for ch in node.children:
        if isinstance(ch, str):
            parts.append(ch)
        elif ch.tag in ALWAYS_EXCLUDED or ch.id == "toc" or "toc" in ch.classes or skip(ch):
            continue
        elif ch.tag in INLINE:
            parts.append(text_of(ch, skip))
        else:
            parts.append(" " + text_of(ch, skip) + " ")
    return "".join(parts)


def nodes_text(nodes, skip=lambda n: False):
    wrapper = Node("#wrap", [], None)
    wrapper.children = list(nodes)
    return text_of(wrapper, skip)


def count(text):
    return len(text.split())


def visible_words(nodes):
    return count(nodes_text(nodes))


def prose_skip(n):
    return n.tag in PROSE_EXCLUDED


def norm(text):
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip()


def own_text(node, skip=EXEMPT_TEXT):
    out = []
    for ch in node.children:
        if isinstance(ch, str):
            out.append(ch)
        elif ch.tag in skip:
            out.append(" ")
        elif ch.tag in INLINE:
            out.append(own_text(ch, skip))
        else:
            out.append(" ")
    return "".join(out)


def heading_flow(h):
    level = HEADINGS[h.tag]
    sibs = h.parent.children
    i = next(k for k, c in enumerate(sibs) if c is h)
    out = [h]
    for c in sibs[i + 1:]:
        if isinstance(c, Node) and c.tag in HEADINGS and HEADINGS[c.tag] <= level:
            break
        out.append(c)
    return out


def heading_sid(h):
    if h.id:
        return h.id
    p = h.parent
    if p is not None and p.id and p.tag in ("section", "div", "article"):
        return p.id
    return re.sub(r"[^a-z0-9]+", "-", text_of(h).lower()).strip("-")


def ids_of(root):
    ids = {}
    for n in iter_nodes(root):
        if n.id and n.id not in ids:
            ids[n.id] = n
    return ids


def location(node):
    if node.id:
        return "#" + node.id
    anc = node.parent
    while anc is not None and anc.tag != "#root" and not anc.id:
        anc = anc.parent
    if anc is None or anc.tag == "#root":
        base = "document"
        scope = [n for n in iter_nodes(node_root(node))]
    else:
        base = "#" + anc.id
        scope = list(iter_nodes(anc))
    n = [x for x in scope if x.tag == node.tag]
    idx = next(i for i, x in enumerate(n) if x is node) + 1
    return "%s/%s[%d]" % (base, node.tag, idx)


def node_root(node):
    while node.parent is not None:
        node = node.parent
    return node


def rows_of(table):
    out = []

    def walk(n):
        for c in n.children:
            if isinstance(c, Node):
                if c.tag == "tr":
                    out.append(c)
                elif c.tag != "table":
                    walk(c)

    walk(table)
    return out


def cells_of(tr):
    return [c for c in tr.children if isinstance(c, Node) and c.tag in ("td", "th")]


def data_rows(table):
    return [r for r in rows_of(table) if any(c.tag == "td" for c in cells_of(r))]


def header_names(table):
    for r in rows_of(table):
        cs = cells_of(r)
        if cs and all(c.tag == "th" for c in cs):
            return [norm(text_of(c)).lower() for c in cs]
    return []


def col_index(table, name, default):
    names = header_names(table)
    return names.index(name) if name in names else default


def cell_text(c):
    return norm(text_of(c))


def cell_links(c):
    return {a.get("href")[1:] for a in iter_nodes(c) if a.tag == "a" and (a.get("href") or "").startswith("#")}


def table_columns(table):
    best = 0
    for r in rows_of(table):
        total = 0
        for c in cells_of(r):
            try:
                total += max(1, int(c.get("colspan") or 1))
            except ValueError:
                total += 1
        best = max(best, total)
    return best


def tables_with(root, cls):
    return [t for t in iter_nodes(root) if t.tag == "table" and cls in t.classes]


def phrase_regex(phrase):
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])", re.IGNORECASE)


DEFERRAL_RE = [(p, phrase_regex(p.replace("’", "'"))) for p in DEFERRAL_PHRASES]
ADJECTIVE_RE = [(a, phrase_regex(a)) for a in ADJECTIVES]
QUOTED_RE = re.compile(r'"[^"]*"')


def lint(root, size, cap_accepted):
    findings = []

    def add(cls, loc, detail):
        findings.append((cls, loc, detail))

    ids = ids_of(root)
    body = find_first(root, "body")
    scope = body if body is not None else root

    prose = count(text_of(scope, prose_skip))
    cap = CAPS.get(size)
    if cap is not None and prose > cap and not cap_accepted:
        add("cap", "document", "%d prose words exceed the %s cap of %d" % (prose, size, cap))

    for n in iter_nodes(scope):
        if n.tag in ("p", "li", "td") and not excluded(n):
            own = count(norm(own_text_items(n)))
            if own > ITEM_CAP:
                add("long-item", location(n), "%d words, limit %d" % (own, ITEM_CAP))

    cur_section = None
    for n in iter_nodes(scope):
        if n.tag == "h2":
            cur_section = heading_sid(n)
        if excluded(n) or n.tag in INLINE or n.tag in EXEMPT_TEXT:
            continue
        in_verification = cur_section in VERIFICATION_SECTIONS
        a = n.parent
        while a is not None and not in_verification:
            if a.id in VERIFICATION_SECTIONS:
                in_verification = True
            a = a.parent
        text = norm(own_text(n))
        unquoted = QUOTED_RE.sub(" ", text)
        for phrase, rx in DEFERRAL_RE:
            if in_verification and phrase in VERIFY_FORMS:
                continue
            if rx.search(unquoted):
                add("deferral", location(n), 'phrase "%s"' % phrase)
        if n.tag in ADJECTIVE_BLOCKS:
            if not re.search(r"[0-9]", norm(own_text(n, {"script", "style"}))):
                for adj, rx in ADJECTIVE_RE:
                    if rx.search(unquoted):
                        add("adjective", location(n), 'vague word "%s" with no number in the same block' % adj)

    ac_ids = [i for i in ids if re.fullmatch(r"AC-\d+", i)]
    r_ids = [i for i in ids if re.fullmatch(r"R-\d+", i)]
    t_ids = [i for i in ids if re.fullmatch(r"T-\d+", i)]

    matrices = tables_with(scope, "trace-matrix")
    if matrices:
        mapped_ac, mapped_r, mapped_t = set(), set(), set()
        expected = 2 if size == "S" else 3
        for m in matrices:
            for tr in data_rows(m):
                cells = cells_of(tr)
                for c in cells:
                    if c.tag == "td" and not cell_text(c):
                        add("orphan", location(tr), "empty matrix cell")
                if len(cells) != expected:
                    add("orphan", location(tr), "row has %d cells, size %s needs %d" % (len(cells), size, expected))
                    continue
                links = [cell_links(c) for c in cells]
                t_cell = links[-1]
                row_t = {l for l in t_cell if re.fullmatch(r"T-\d+", l)}
                if row_t:
                    mapped_ac |= links[0]
                    if expected == 3:
                        mapped_r |= links[1]
                if links[0] or links[1]:
                    mapped_t |= row_t
        for i in ac_ids:
            if i not in mapped_ac:
                add("orphan", "#" + i, "AC has no matrix row with a test link")
        if size != "S":
            for i in r_ids:
                if i not in mapped_r:
                    add("orphan", "#" + i, "rule is not linked in the rules cell of a row with a test link")
        for i in t_ids:
            if i not in mapped_t:
                add("orphan", "#" + i, "test is not linked in the tests cell of a mapped row")

    for a in iter_nodes(scope):
        href = a.get("href") if a.tag == "a" else None
        if href and href.startswith("#") and len(href) > 1 and href[1:] not in ids:
            add("undefined-ref", location(a), 'href="%s" has no matching id' % href)
    terms = set()
    for g in tables_with(scope, "glossary"):
        term_col = col_index(g, "term", 0)
        for tr in data_rows(g):
            cells = cells_of(tr)
            for c in cells:
                if c.tag == "td" and not cell_text(c):
                    add("undefined-ref", location(tr), "empty glossary cell")
            if term_col < len(cells):
                terms.add(cell_text(cells[term_col]).lower())
            for ref in re.findall(r"[A-Za-z]+-\d+", cell_text(cells[-1])) if cells else []:
                if ref not in ids:
                    add("undefined-ref", location(tr), 'read-by id "%s" does not exist' % ref)
    for n in iter_nodes(scope):
        if n.tag in ("dfn", "abbr") and not excluded(n):
            t = cell_text(n).lower()
            if t not in terms:
                add("undefined-ref", location(n), 'term "%s" has no glossary row' % cell_text(n))

    for t in iter_nodes(scope):
        if t.tag != "table":
            continue
        cls = t.classes
        if not (cls & OWNED_TABLES):
            for tr in data_rows(t):
                for c in cells_of(tr):
                    if c.tag == "td" and not cell_text(c):
                        add("table-gap", location(tr), "empty cell")

    for t in tables_with(scope, "decision-table"):
        if t.get("data-hit-policy") not in ("unique", "first"):
            add("decision-table", location(t), "data-hit-policy is not unique or first")
        rows = data_rows(t)
        if not rows or "else-row" not in rows[-1].classes:
            add("decision-table", location(t), "last body row lacks class else-row")

    for t in tables_with(scope, "evidence-ledger"):
        for tr in data_rows(t):
            if any(c.tag == "td" and not cell_text(c) for c in cells_of(tr)):
                add("uncited", location(tr), "empty cell (source missing)")

    for t in tables_with(scope, "assumptions"):
        for tr in data_rows(t):
            cells = cells_of(tr)
            problems = []
            if any(c.tag == "td" and not cell_text(c) for c in cells):
                problems.append("empty cell")
            has_q = any(l.startswith("Q-") for c in cells for l in cell_links(c))
            if not has_q and "[Inference]" not in cell_text(tr):
                problems.append("no #Q- link and no [Inference]")
            if problems:
                add("assumption", location(tr), "; ".join(problems))

    for cls in ("type-checklist", "input-classes", "pre-mortem", "coverage-scan"):
        for t in tables_with(scope, cls):
            names = header_names(t)
            for tr in data_rows(t):
                cells = cells_of(tr)
                problems = []
                if any(c.tag == "td" and not cell_text(c) for c in cells):
                    problems.append("empty cell")
                if cls == "coverage-scan":
                    problems += coverage_problems(t, names, cells)
                if cls == "pre-mortem":
                    problems += premortem_problems(t, names, cells)
                if problems:
                    add("unanswered", location(tr), "; ".join(problems))

    for sec in REQUIRED_SECTIONS:
        if sec not in ids:
            add("structure", "document", "missing section id %s" % sec)
    if not ac_ids:
        add("structure", "document", "no AC id")
    if not matrices:
        add("structure", "document", "no table.trace-matrix")
    for t in iter_nodes(scope):
        if t.tag == "table" and table_columns(t) > MAX_COLUMNS:
            add("structure", location(t), "table has %d columns, limit %d" % (table_columns(t), MAX_COLUMNS))

    return findings


def own_text_items(n):
    out = []
    for ch in n.children:
        if isinstance(ch, str):
            out.append(ch)
        elif ch.tag in ("script", "style") or ch.tag in ("p", "li", "td", "ul", "ol", "table"):
            out.append(" ")
        else:
            out.append(own_text_items(ch) if ch.tag in INLINE else " " + own_text_items(ch) + " ")
    return "".join(out)


def coverage_problems(table, names, cells):
    status_col = names.index("status") if "status" in names else 2
    res_col = names.index("resolution") if "resolution" in names else len(cells) - 1
    out = []
    if status_col >= len(cells):
        return out
    status = cell_text(cells[status_col])
    if status not in ("Clear", "Partial", "Missing"):
        out.append("status is not Clear, Partial or Missing")
    elif status in ("Partial", "Missing"):
        has_q = any(l.startswith("Q-") for c in cells for l in cell_links(c))
        res = cell_text(cells[res_col]) if res_col < len(cells) else ""
        if not has_q and not res.startswith("out of scope, no behavior change:"):
            out.append("%s row has no #Q- link and no 'out of scope, no behavior change:' prefix" % status)
    return out


def premortem_problems(table, names, cells):
    sev_col = names.index("severity") if "severity" in names else len(cells) - 2
    det_col = names.index("detection") if "detection" in names else len(cells) - 1
    if sev_col < 0 or det_col >= len(cells) or sev_col >= len(cells):
        return []
    if cell_text(cells[sev_col]) != "High":
        return []
    det = cells[det_col]
    links = cell_links(det)
    if any(l.startswith("AC-") or l.startswith("T-") for l in links) or "alert:" in cell_text(det):
        return []
    return ["High row has no #AC- or #T- link and no alert: entry"]


def sections_of(root, levels):
    out = []
    for h in iter_nodes(root):
        if h.tag in levels and not excluded(h) and h.parent is not None:
            out.append((h, heading_flow(h)))
    return out


def run_words(path):
    root = parse(path)
    body = find_first(root, "body") or root
    print("prose %d" % count(text_of(body, prose_skip)))
    print("total %d" % count(text_of(body)))


def run_sections(path):
    root = parse(path)
    for h, flow in sections_of(root, ("h2", "h3")):
        print("%s %s %d" % (h.tag, heading_sid(h), visible_words(flow)))


def run_extract(path, wanted):
    root = parse(path)
    ids = ids_of(root)
    chunks = []
    for i in wanted:
        n = ids.get(i)
        if n is None:
            print("spec-lint: unknown id %s" % i, file=sys.stderr)
            return 2
        chunks.append("".join(ser(x) for x in heading_flow(n)) if n.tag in HEADINGS else ser(n))
    print("\n".join(chunks))
    return 0


def chunk_pieces(pieces, limit):
    chunks, cur, cur_w = [], [], 0
    for p in pieces:
        w = visible_words([p])
        if cur and cur_w + w > limit:
            chunks.append(cur)
            cur, cur_w = [], 0
        cur.append(p)
        cur_w += w
    if cur:
        chunks.append(cur)
    return chunks


def shallow(node, children):
    n = Node(node.tag, list(node.attrs), None)
    n.children = list(children)
    return n


def split_table(table, limit):
    rows = rows_of(table)
    head = [r for r in rows if not any(c.tag == "td" for c in cells_of(r))]
    body = [r for r in rows if any(c.tag == "td" for c in cells_of(r))]
    head_w = visible_words(head)
    parts = []
    for chunk in chunk_pieces(body, max(1, limit - head_w)):
        t = shallow(table, [])
        if head:
            th = Node("thead", [], t)
            th.children = list(head)
            t.children.append(th)
        tb = Node("tbody", [], t)
        tb.children = list(chunk)
        t.children.append(tb)
        parts.append(t)
    return parts


def split_node(node, limit):
    if visible_words([node]) <= limit:
        return [node]
    if node.tag == "table":
        return split_table(node, limit)
    pieces = []
    for ch in node.children:
        if isinstance(ch, Node) and visible_words([ch]) > limit:
            pieces.extend(split_node(ch, limit))
        else:
            pieces.append(ch)
    return [shallow(node, c) for c in chunk_pieces(pieces, limit)]


def group_atoms(nodes, limit):
    if visible_words(nodes) <= limit:
        return [nodes]
    pieces = []
    for n in nodes:
        if isinstance(n, Node) and visible_words([n]) > limit:
            pieces.extend(split_node(n, limit))
        else:
            pieces.append(n)
    return chunk_pieces(pieces, limit)


def unit_atoms(flow, limit):
    if visible_words(flow) <= limit:
        return [flow]
    groups, cur = [], [flow[0]]
    for n in flow[1:]:
        if isinstance(n, Node) and n.tag == "h3":
            groups.append(cur)
            cur = []
        cur.append(n)
    groups.append(cur)
    atoms = []
    for g in groups:
        atoms.extend(group_atoms(g, limit))
    return atoms


def skeleton(body, inner, title):
    attrs = "".join(" " + k if v is None else ' %s="%s"' % (k, escape(v, quote=True)) for k, v in body.attrs)
    return '<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>%s</title></head>\n<body%s>\n%s\n</body></html>\n' % (
        escape(title), attrs, inner)


def perspective_of(weights, order):
    best, best_w = None, 0
    for p in order:
        if weights.get(p, 0) > best_w:
            best, best_w = p, weights[p]
    return best or "user"


def run_partition(path, outdir):
    root = parse(path)
    body = find_first(root, "body")
    size = body.get("data-size-class") if body is not None else None
    if size not in ("S", "M", "L"):
        print("spec-lint: no valid data-size-class on <body>", file=sys.stderr)
        return 2
    os.makedirs(outdir, exist_ok=True)
    units = sections_of(body, ("h2",))
    sids = [heading_sid(h) for h, _ in units]
    total = count(text_of(body))
    parts = []
    if size == "S" or total <= PARTITION_CAP:
        inner = "".join(ser(c) for c in body.children)
        parts.append({"html": inner, "words": total, "sections": sids, "persp": "all"})
    else:
        packed, cur = [], None
        for h, flow in units:
            sid = heading_sid(h)
            if sid == "glossary":
                continue
            persp = PERSPECTIVES.get(sid, "user")
            for atom in unit_atoms(flow, PARTITION_CAP):
                w = visible_words(atom)
                html = "".join(ser(x) for x in atom)
                if not h.id and sid:
                    html = '<section id="%s">%s</section>' % (escape(sid), html)
                if cur is None or cur["words"] + w > PARTITION_CAP:
                    cur = {"html": "", "words": 0, "sections": [], "weights": {}, "order": []}
                    packed.append(cur)
                cur["html"] += html
                cur["words"] += w
                if sid not in cur["sections"]:
                    cur["sections"].append(sid)
                if persp not in cur["order"]:
                    cur["order"].append(persp)
                cur["weights"][persp] = cur["weights"].get(persp, 0) + w
        for p in packed:
            p["persp"] = perspective_of(p["weights"], p["order"])
            parts.append(p)
    for k, p in enumerate(parts, 1):
        name = "partition-%d.html" % k
        with open(os.path.join(outdir, name), "w", encoding="utf-8") as fh:
            fh.write(skeleton(body, p["html"], name))
        print("%s sections=%s words=%d perspective=%s" % (name, ",".join(p["sections"]), p["words"], p["persp"]))
    return 0


def run_lint(path):
    root = parse(path)
    body = find_first(root, "body")
    size = body.get("data-size-class") if body is not None else None
    if size not in ("S", "M", "L"):
        print("spec-lint: no valid data-size-class on <body>", file=sys.stderr)
        return 2
    findings = lint(root, size, body.get("data-cap-accepted") == "true")
    for cls, loc, detail in findings:
        print("FINDING %s %s: %s" % (cls, loc, detail))
    print("spec-lint: %d finding(s)" % len(findings))
    return 1 if findings else 0


def usage():
    print("usage: spec-lint.py <spec> | --words <file> | --partition <file> <dir> | --sections <file> | --extract <file> <id>...",
          file=sys.stderr)
    return 2


def main(argv):
    if len(argv) < 2:
        return usage()
    mode = argv[1]
    try:
        if mode == "--words" and len(argv) == 3:
            run_words(argv[2])
            return 0
        if mode == "--sections" and len(argv) == 3:
            run_sections(argv[2])
            return 0
        if mode == "--partition" and len(argv) == 4:
            return run_partition(argv[2], argv[3])
        if mode == "--extract" and len(argv) >= 4:
            return run_extract(argv[2], argv[3:])
        if not mode.startswith("--") and len(argv) == 2:
            return run_lint(mode)
    except (OSError, UnicodeDecodeError) as err:
        print("spec-lint: cannot read file: %s" % err, file=sys.stderr)
        return 2
    return usage()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
