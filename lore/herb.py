"""Parse the HERB dataset (Salesforce Research, CC-BY-NC-4.0) into graph rows.

Everything here is deterministic: IDs, @-mentions, links and the fixed phrasing
of HERB's bug-triage messages. No LLM is involved, so every edge can be traced
back to the exact message or document it came from.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

EID = re.compile(r"eid_[0-9a-f]{8}")
CUST = re.compile(r"CUST-\d{4}")
DOC_LINK = re.compile(r"archives/docs/([A-Za-z0-9_]+)")
INT_PR = re.compile(r"https://github\.com/salesforce/([^/\s]+)/pull/(\d+)")
EXT_PR = re.compile(r"https://github\.com/(?!salesforce/)([^/\s]+/[^/\s]+)/pull/(\d+)")
# A quote mark must not touch a letter on the outside, so apostrophes ("customer's") don't open a title.
QUOTED = re.compile(r"(?<![A-Za-z])'([^']{6,120})'(?![A-Za-z])")
RENAME = re.compile(r"renamed the channel to (\S+?)\.?$")
REPORT_LEAD = re.compile(r"^(First up|Next|Lastly|Finally|Great, thanks! Next|Awesome, thanks! Next|Thanks! Next|Got it|Also)", re.I)
RESOLVED_TITLED = re.compile(r"(?:bug|issue) titled '([^']+)' has been resolved")
RESOLVED_ANY = re.compile(r"has been resolved")
REOPENED = re.compile(r"reopen", re.I)
ROOT_CAUSE = re.compile(r"root cause (?:was|is)\s+(.*)", re.I | re.S)
COMPONENT = re.compile(
    r"\b((?:[A-Z][A-Za-z0-9]*(?:\s(?:&|and)\s|\s|-|/)){0,5}"
    r"(?:Module|Layer|Algorithm|System|Engine|Service|Interface|Pipeline|Processing|Management|Gateway|"
    r"Component|Framework|Integration|Dashboard|API|Manager|Handler|Parser|Generator|Tracker|Scheduler|"
    r"Analyzer|Store|Database|Cache|Monitor|Logging|UI))\b"
)
STOP = set("the a an of to in for and on with our is are was be by it this that from at as has have bug issue".split())


def ts_ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def norm_component(raw: str) -> str:
    c = re.sub(r"^(?:(?:The|Our|Its|Their|Certain|Some|Several|Specific|This|These|Critical)\s+)+", "", raw.strip())
    return {"UI": "User Interface", "API": "API"}.get(c, c)


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 2}


def load_raw(root: Path, products: list[str] | None = None):
    meta = root / "metadata"
    employees = json.loads((meta / "employee.json").read_text(encoding="utf-8"))
    customers = json.loads((meta / "customers_data.json").read_text(encoding="utf-8"))
    org = json.loads((meta / "salesforce_team.json").read_text(encoding="utf-8"))
    files = sorted((root / "products").glob("*.json"))
    if products:
        files = [f for f in files if f.stem in products]
    prods = {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in files}
    return employees, customers, org, prods


def reporting_lines(org) -> list[dict]:
    """salesforce_team.json is a nested VP -> lead -> engineer tree; flatten it."""
    out = []

    def walk(node, manager):
        if isinstance(node, dict):
            eid = node.get("employee_id")
            if eid and manager:
                out.append({"from": eid, "to": manager})
            for k, v in node.items():
                if isinstance(v, list):
                    for child in v:
                        walk(child, eid or manager)
        elif isinstance(node, list):
            for child in node:
                walk(child, manager)

    walk(org, None)
    return out


def parse(root: Path, products: list[str] | None = None) -> dict:
    employees, customers, org, prods = load_raw(root, products)
    R = defaultdict(list)  # row buckets keyed by what the loader writes
    stats = defaultdict(int)

    for e in employees.values():
        R["person"].append({"id": e["employee_id"], "name": e["name"], "role": e["role"],
                            "location": e.get("location"), "org": e.get("org")})
    R["reports_to"] = reporting_lines(org)
    for c in customers:
        R["customer"].append({"id": c["id"], "name": c["name"], "role": c["role"], "company": c["company"]})

    for pname, p in prods.items():
        R["product"].append({"name": pname})
        R["member_of"] += [{"person": e, "product": pname} for e in p.get("team", [])]
        R["has_customer"] += [{"product": pname, "customer": c} for c in p.get("customers", [])]

        # ---- documents, chained into version history per document type
        by_type = defaultdict(list)
        for d in p.get("documents", []):
            row = {"id": d["id"], "type": d["type"], "at": d["date"], "ts": ts_ms(d["date"]),
                   "author": d.get("author"), "link": d.get("document_link"), "product": pname,
                   "content": d["content"]}
            R["document"].append(row)
            by_type[d["type"]].append(row)
        for docs in by_type.values():
            docs.sort(key=lambda r: r["ts"])
            for older, newer in zip(docs, docs[1:]):
                R["supersedes"].append({"new": newer["id"], "old": older["id"]})

        # ---- meetings
        for mt in p.get("meeting_transcripts", []):
            R["meeting"].append({"id": mt["id"], "type": mt.get("document_type"), "at": mt["date"],
                                 "ts": ts_ms(mt["date"]), "product": pname, "transcript": mt["transcript"]})
            R["attended"] += [{"person": e, "meeting": mt["id"]} for e in set(mt.get("participants", []))]

        # ---- pull requests (internal ones carry product context; external are open-source references)
        for pr in p.get("prs", []):
            m = INT_PR.search(pr["link"]) or EXT_PR.search(pr["link"])
            if not m:
                continue
            repo = m.group(1) if "salesforce" in pr["link"] else m.group(1)
            R["pr"].append({"id": pr["link"], "repo": repo, "number": int(pr["number"]), "title": pr["title"],
                            "summary": pr.get("summary"), "merged": pr.get("merged") == "True",
                            "internal": "github.com/salesforce/" in pr["link"],
                            "at": pr.get("created_at"), "ts": ts_ms(pr["created_at"]) if pr.get("created_at") else None,
                            "product": pname})

        # ---- slack
        issues: dict[str, dict] = {}       # key -> issue row (key = product::title)
        open_by_assignee = defaultdict(list)  # (channel, eid) -> [issue keys]
        last_resolved_by_channel = {}
        msgs = sorted(p.get("slack", []), key=lambda m: m["Message"]["User"]["timestamp"])
        channel_names = {}
        for m in msgs:
            ch = m["Channel"]
            channel_names.setdefault(ch["channelID"], ch["name"])
        for cid, cname in channel_names.items():
            kind = cname.split("-")[0]
            R["channel"].append({"id": cid, "name": cname, "kind": kind, "product": pname})

        for m in msgs:
            u = m["Message"]["User"]
            text, sender, at = u["text"], u["userId"], u["timestamp"]
            cid, cname = m["Channel"]["channelID"], m["Channel"]["name"]
            mid = m["id"]
            R["message"].append({"id": mid, "text": text, "at": at, "ts": ts_ms(at), "channel": cid,
                                 "sender": sender if sender.startswith("eid_") else None})
            if sender == "slack_admin_bot":
                rn = RENAME.search(text)
                if rn:
                    R["rename"].append({"channel": cid, "to": rn.group(1), "at": at, "ts": ts_ms(at), "msg": mid})
                continue
            for e in set(EID.findall(text)) - {sender}:
                R["mentions"].append({"msg": mid, "person": e})
            for c in set(CUST.findall(text)):
                R["mentions_customer"].append({"msg": mid, "customer": c})
            for d in set(DOC_LINK.findall(text)):
                R["references_doc"].append({"msg": mid, "doc": d})
            for repo, num in set(INT_PR.findall(text)):
                link = f"https://github.com/salesforce/{repo}/pull/{num}"
                R["references_pr"].append({"msg": mid, "pr": link})
                if "my PR" in text or "my pr" in text.lower():
                    R["opened_pr"].append({"person": sender, "pr": link, "msg": mid})

            if not cname.startswith("bug-"):
                continue
            ext = EXT_PR.search(text)
            quoted = QUOTED.findall(text)

            # (1) a bug report: quoted title, a customer, an @assignee, no open-source link
            assignees = [e for e in EID.findall(text) if e != sender]
            custs = CUST.findall(text)
            if quoted and custs and assignees and not ext and not REOPENED.search(text) \
                    and not RESOLVED_ANY.search(text):
                title = quoted[0]
                key = f"{pname}::{title.lower()}"
                after = text.split(f"'{title}'", 1)[-1]
                cm = COMPONENT.search(after)
                comp = norm_component(cm.group(1)) if cm else None
                desc = re.split(r"\s*(?:Impacted customer|@eid_)", after.lstrip(". "))[0].strip()
                issues[key] = {"id": key, "title": title, "product": pname, "component": comp,
                               "description": desc[:400], "reported_msg": mid, "ts": ts_ms(at), "at": at,
                               "channel": cid}
                R["issue_customer"] += [{"issue": key, "customer": c} for c in set(custs)]
                R["issue_event"].append({"issue": key, "kind": "REPORTED_IN", "msg": mid})
                R["assigned"].append({"issue": key, "person": assignees[-1], "msg": mid, "ts": ts_ms(at)})
                open_by_assignee[(cid, assignees[-1])].append(key)
                stats["reported"] += 1
                continue

            # (2) an open-source PR presented as similar to one of our issues
            if ext and len(quoted) >= 2:
                ours = f"{pname}::{quoted[-1].lower()}"
                if ours in issues:
                    R["similar_to"].append({"issue": ours, "pr": f"https://github.com/{ext.group(1)}/pull/{ext.group(2)}",
                                            "msg": mid})
                    stats["similar"] += 1
                continue

            # (3) resolution: quoted title, else paraphrase matched against the sender's open issues
            if RESOLVED_ANY.search(text):
                key, how = None, None
                t = RESOLVED_TITLED.search(text)
                if t and f"{pname}::{t.group(1).lower()}" in issues:
                    key, how = f"{pname}::{t.group(1).lower()}", "quoted-title"
                else:
                    cands = [k for k in open_by_assignee.get((cid, sender), []) if k in issues]
                    if cands:
                        w = words(text)
                        scored = sorted(cands, key=lambda k: -len(w & words(issues[k]["title"] + " " + issues[k]["description"])))
                        best = scored[0]
                        overlap = len(w & words(issues[best]["title"] + " " + issues[best]["description"]))
                        if len(cands) == 1 or overlap >= 2:
                            key, how = best, "assignee+overlap" if len(cands) > 1 else "assignee-only-open-issue"
                if key:
                    R["issue_event"].append({"issue": key, "kind": "RESOLVED_IN", "msg": mid, "match": how})
                    for repo, num in INT_PR.findall(text):
                        R["fixed_by"].append({"issue": key, "pr": f"https://github.com/salesforce/{repo}/pull/{num}"})
                    last_resolved_by_channel[cid] = (key, sender)
                    stats["resolved_" + how] += 1
                else:
                    stats["resolved_unmatched"] += 1
                continue

            # (4) root cause explanation following a resolution, from the same person
            rc = ROOT_CAUSE.search(text)
            if rc and cid in last_resolved_by_channel and last_resolved_by_channel[cid][1] == sender:
                R["root_cause"].append({"issue": last_resolved_by_channel[cid][0], "text": rc.group(1)[:400], "msg": mid})
                stats["root_cause"] += 1
                continue

            # (5) reopen
            if REOPENED.search(text) and quoted:
                key = f"{pname}::{quoted[0].lower()}"
                if key in issues:
                    R["issue_event"].append({"issue": key, "kind": "REOPENED_IN", "msg": mid})
                    stats["reopened"] += 1
                else:
                    stats["reopen_unmatched"] += 1

        R["issue"] += list(issues.values())

    R["stats"] = dict(stats)
    return R


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    rows = parse(Path(sys.argv[1]), sys.argv[2:] or None)
    for k, v in rows.items():
        print(k, v if k == "stats" else len(v))
