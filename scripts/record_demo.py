"""Record a captioned walkthrough of the Lore UI to demo/lore-demo.mp4.

Needs: the API running on :8000 with a freshly loaded graph, `pip install playwright imageio-ffmpeg`,
and Google Chrome installed. Usage:  python scripts/record_demo.py [base_url]
"""
import shutil
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/"
OUT = Path(__file__).resolve().parents[1] / "demo"
W, H = 1440, 900

CAPTION_JS = """(t) => {
  let d = document.getElementById('__cap');
  if (!d) { d = document.createElement('div'); d.id = '__cap';
    d.style.cssText = 'position:fixed;left:50%;bottom:26px;transform:translateX(-50%);max-width:1080px;'
      + 'background:rgba(23,32,43,.94);color:#fff;font:500 21px/1.4 "Instrument Sans",system-ui,sans-serif;'
      + 'padding:13px 22px;border-radius:10px;z-index:99999;text-align:center;pointer-events:none';
    document.body.appendChild(d); }
  d.style.display = t ? 'block' : 'none'; d.textContent = t;
}"""


def main():
    OUT.mkdir(exist_ok=True)
    raw = OUT / "raw"
    shutil.rmtree(raw, ignore_errors=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(raw),
                                  record_video_size={"width": W, "height": H}, color_scheme="light")
        p = ctx.new_page()

        def cap(text, ms):
            p.evaluate(CAPTION_JS, text)
            p.wait_for_timeout(ms)

        def go(h, wait=2200):
            p.evaluate("(h) => { window.scrollTo(0, 0); window.lore.go(h); }", h)
            p.wait_for_timeout(wait)

        def click_text(t, exact=True, wait=1800):
            try:
                p.get_by_text(t, exact=exact).first.click(timeout=4000)
            except Exception as e:  # keep recording; a missed click shows up in review
                print("click failed:", t, e)
            p.wait_for_timeout(wait)

        def scroll(px, wait=2200):
            p.evaluate("(y) => window.scrollBy({top: y, behavior: 'smooth'})", px)
            p.wait_for_timeout(wait)

        p.goto(BASE)
        p.wait_for_timeout(1500)
        cap("Lore is a company brain on FalkorDB: people, products, issues, customers, docs, meetings and decisions in one graph.", 5000)
        cap("Every answer shows the records it came from, and the exact query that found them.", 3500)

        box = p.locator("input").first
        box.click()
        cap("Start from a name. Search works as you type.", 1200)
        box.type("Alice Brown", delay=90)
        p.wait_for_timeout(1200)
        cap("Four people are called Alice Brown. Lore shows who is who, and never guesses.", 5000)

        go("#/e/eid_92c62291/leave?reach=1")
        cap("Pick the engineer. \"What if she leaves?\" Looking only at her direct work: 7 open issues and 4 meeting commitments.", 6000)
        click_text("Team")
        cap("Look one step further: the customers waiting on those issues.", 4500)
        click_text("Company-wide")
        cap("Company-wide: 7 people who could step in, found through the same kind of component in other products.", 6000)
        scroll(520)
        cap("Each answer is a set of paths through the graph. The ochre threads are the evidence.", 5000)
        scroll(700)
        cap("Every finding links to its record, so you can keep following the trail.", 4000)

        go("#/e/eid_92c62291/handoff")
        cap("Get work done: draft a handoff. Nothing changes until someone approves it.", 3500)
        click_text("Draft handoff", wait=2500)
        cap("Each issue gets the colleague with the strongest evidence in the graph, and the reason why.", 5500)
        click_text("Approve handoff", wait=2200)
        cap("Approved. New owners are recorded with who approved it. The old assignments are closed, not deleted.", 5000)
        click_text("See what’s still at risk", wait=2500)
        cap("Her open issues drop from 7 to 0. The 4 meeting commitments remain for people to reassign.", 5500)

        go(f"#/e/{'TrendForce%3A%3Ainadequate%20handling%20of%20missing%20data'}/history", 2500)
        cap("Knowledge changes. This bug was resolved, then the customer reopened it, so it reads as reopened.", 5500)
        scroll(450)
        cap("Each step quotes the Slack message it came from.", 4500)

        go("#/e/TrendForce%3A%3AAPI%20Authentication%20Layer/experts?reach=1", 2500)
        cap("Who knows the API authentication layer? Direct evidence first...", 4000)
        click_text("Plus similar parts", wait=2500)
        cap("...then authors of the current design docs, and people who fixed the same component in other products.", 5500)

        go("#/e/TrendForce/decisions", 2500)
        cap("Decisions are traced to the meeting and a verbatim quote. Ones a later meeting changed are marked revised.", 6000)

        go("#/", 1500)
        box = p.locator("input").first
        box.click()
        box.type("Quantum flux capacitor", delay=60)
        p.wait_for_timeout(1500)
        cap("Ask about something that isn't there, and it says so.", 4500)

        go("#/e/eid_92c62291/leave?reach=1", 2500)
        try:
            p.get_by_text("How this was answered").first.click(timeout=4000)
            p.wait_for_timeout(800)
            p.get_by_text("How this was answered").first.scroll_into_view_if_needed()
        except Exception as e:
            print("trace open failed:", e)
        p.wait_for_timeout(1200)
        cap("Every query is hand-written Cypher. The model only picks which question to ask, through MCP or the API.", 6500)
        go("#/", 1200)
        cap("Lore. Built on FalkorDB. Data: HERB, a synthetic company (Salesforce Research, CC-BY-NC-4.0).", 4500)

        video = p.video
        ctx.close()
        webm = Path(video.path())
        browser.close()

    mp4 = OUT / "lore-demo.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(webm),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", "-movflags", "+faststart", str(mp4)],
                   check=True)
    print(mp4, mp4.stat().st_size)


if __name__ == "__main__":
    main()
