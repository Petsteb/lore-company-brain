# Submission checklist: Graph Hacks 2026

Deadline: **Oct 18, 11:59 PM IST**, which is 21:29 Athens time. Submit on the WeMakeDevs hackathon page, from the same panel you registered in.

| Field | Value |
|---|---|
| Project | Lore: a company brain that shows its work |
| Track | **Track 03: Company Brain**. Optionally also Track 01, Agents That Act on Connected Data; the rules allow naming more than one. |
| Repository (public) | https://github.com/Petsteb/lore-company-brain |
| README with setup and graph model | `README.md`: Graph data model, Key queries, Run it |
| Cypher queries and algorithms | `README.md` → *Key queries*; all of them in `lore/tools.py`, `lore/load.py` and `lore/decisions.py` |
| Demo video | `demo/lore-demo.mp4`, captioned, about 2.5 minutes. Upload it to YouTube (unlisted) or Drive and paste the link. A narrated take following `DEMO.md` is better if there's time. |
| Live deployment | Hugging Face Space, if deployed (`python scripts/deploy_hf.py`). Otherwise: Docker one-liner in the README (`docker build -t lore . && docker run -p 7860:7860 lore`). |
| AI assistance disclosure | README → *AI assistance disclosure* (rule 11) |
| Data | HERB, synthetic, CC-BY-NC-4.0, credited in the README (rule 6) |

## Before you submit
- [ ] Watch `demo/lore-demo.mp4` once and upload it.
- [ ] Put the live URL (or "local Docker setup, see README") into the README's top lines and the form.
- [ ] Read `DEMO.md` → *How to explain it*. Rules 12 and 13 say you must be able to explain the graph model, the queries and the decisions.
- [ ] Optional side prize, best blog post: publish `BLOG.md` (dev.to, Hashnode, Medium or the WeMakeDevs blog) and submit the link.
