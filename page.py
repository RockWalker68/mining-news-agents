"""
page.py: turns the morning brief into a phone-friendly web page.

Layout, top to bottom:
  1. The Rat's poem as the headline
  2. Today's headlines (open; tap to collapse)
  3. Dr I Simon's big takeaways (bullets)
  4. Very short summaries from Meldrum and Yukon Cornelius
  5. Tap-to-expand extras: creative angles and full discussions

Names come from prompts.py.
"""

import html
import re
import shutil
from datetime import datetime
from pathlib import Path

from prompts import STRATEGIST_NAME as S, GEOLOGIST_NAME as G, CHIEF_NAME as C, POET_NAME as R


def esc(text):
    return html.escape(text or "")


def inline(text):
    """Escape text, then allow **bold**."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc(text))


def to_html(text):
    """Convert plain agent text into paragraphs and bullet lists."""
    out, items = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith(("- ", "* ", "• ")):
            items.append(f"<li>{inline(line[2:])}</li>")
            continue
        if items:
            out.append("<ul>" + "".join(items) + "</ul>")
            items = []
        if line:
            out.append(f"<p>{inline(line)}</p>")
    if items:
        out.append("<ul>" + "".join(items) + "</ul>")
    return "\n".join(out)


def details(title, body):
    return f'<details><summary>{esc(title)}</summary><div class="more">{body}</div></details>'


def build_page(brief, out_path):
    when = datetime.fromisoformat(brief["time"])
    hour = when.hour % 12 or 12
    updated = f"{hour}:{when:%M} {'AM' if when.hour < 12 else 'PM'}"

    # Today's headlines, shown open right under the poem so you can scan them first.
    headlines = ""
    if brief.get("articles"):
        links = "".join(
            f'<li><a href="{esc(a.get("link", "#"))}">{esc(a["title"])}</a>'
            f' <span class="src">{esc(a.get("source", ""))}</span></li>'
            for a in brief["articles"])
        headlines = (f'<details class="headlines" open><summary>Today\'s headlines '
                     f'({len(brief["articles"])})</summary><ul class="links">{links}</ul></details>')

    if brief.get("quiet"):
        main = '<p class="quiet">No new mining news in the last 24 hours. The desk is enjoying its coffee.</p>'
    else:
        takeaways = "".join(f"<li>{inline(t)}</li>" for t in brief.get("takeaways", []))
        if not takeaways:
            takeaways_html = to_html(brief.get("chief_conclusion", ""))
        else:
            takeaways_html = f"<ul class='takeaways'>{takeaways}</ul>"

        main = f"""
<section class="poem">{esc(brief.get('poem', ''))}<span class="byline">— {esc(R)}</span></section>

{headlines}

<section>
  <h2>{esc(C)}'s big takeaways</h2>
  {takeaways_html}
</section>

<section class="pair">
  <div class="card">
    <h3>{esc(S)} <span class="role">Strategist</span></h3>
    <p>{inline(brief.get('strategist_short', ''))}</p>
  </div>
  <div class="card">
    <h3>{esc(G)} <span class="role">Geologist</span></h3>
    <p>{inline(brief.get('geologist_short', ''))}</p>
  </div>
</section>
"""

    # The extras, collapsed by default.
    extras = []
    if brief.get("creative"):
        extras.append(details(f"{C}'s creative angles",
                              "<ul>" + "".join(f"<li>{inline(c)}</li>" for c in brief["creative"]) + "</ul>"))

    for session in brief.get("chief_sessions", []):
        t = datetime.fromisoformat(session["time"])
        body = (f"<h4>{esc(C)}'s review</h4>{to_html(session['opening'])}"
                f"<h4>{esc(S)}'s answers</h4>{to_html(session['strategist_answer'])}"
                f"<h4>{esc(G)}'s answers</h4>{to_html(session['geologist_answer'])}"
                f"<h4>{esc(C)}'s conclusions</h4>{to_html(session['conclusion'])}")
        extras.append(details(f"{C} session · {t:%a} {t.hour % 12 or 12}{'am' if t.hour < 12 else 'pm'}", body))

    for d in brief.get("discussions", []):
        t = datetime.fromisoformat(d["time"])
        body = (f"<h4>Summary</h4>{to_html(d['summary'])}"
                f"<h4>{esc(S)}</h4>{to_html(d['strategist'])}"
                f"<h4>{esc(G)}</h4>{to_html(d['geologist'])}"
                f"<h4>{esc(S)}'s reply</h4>{to_html(d['strategist_reply'])}"
                f"<h4>{esc(G)}'s reply</h4>{to_html(d['geologist_reply'])}")
        extras.append(details(f"{S} & {G} · {t:%a} {t.hour % 12 or 12}{'am' if t.hour < 12 else 'pm'} "
                              f"({d['article_count']} articles)", body))


    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Mining Desk">
<meta name="theme-color" content="#f6f1e7" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#16130f" media="(prefers-color-scheme: dark)">
<title>Mining Desk</title>
<link rel="apple-touch-icon" href="icon.png">
<link rel="icon" href="icon.png">
<style>
  :root {{
    --bg: #f6f1e7; --ink: #241f19; --muted: #7a6f60; --card: #fffaf0;
    --line: #e2d8c6; --accent: #b0602a;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #16130f; --ink: #ece4d6; --muted: #a39683; --card: #211c16;
             --line: #3a3128; --accent: #e08a4c; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--ink);
    font: 17px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    padding: env(safe-area-inset-top) 16px calc(32px + env(safe-area-inset-bottom));
  }}
  main {{ max-width: 640px; margin: 0 auto; }}
  header {{ padding: 20px 0 6px; display: flex; justify-content: space-between;
            align-items: baseline; border-bottom: 1px solid var(--line); }}
  .brand {{ font-weight: 700; letter-spacing: .08em; text-transform: uppercase; font-size: 13px; }}
  .date {{ color: var(--muted); font-size: 14px; }}
  .poem {{
    font-family: "Iowan Old Style", Georgia, serif; font-style: italic;
    font-size: 24px; line-height: 1.45; white-space: pre-line;
    padding: 28px 0 24px; border-bottom: 1px solid var(--line);
  }}
  h2 {{ font-size: 13px; letter-spacing: .08em; text-transform: uppercase;
        color: var(--accent); margin: 24px 0 10px; }}
  h3 {{ font-size: 15px; margin: 0 0 6px; }}
  h4 {{ font-size: 14px; color: var(--accent); margin: 18px 0 4px; }}
  ul {{ padding-left: 20px; margin: 8px 0; }}
  li {{ margin: 0 0 8px; }}
  .takeaways li {{ margin-bottom: 12px; }}
  .pair {{ display: grid; gap: 12px; margin: 24px 0; }}
  @media (min-width: 560px) {{ .pair {{ grid-template-columns: 1fr 1fr; }} }}
  .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px;
           padding: 14px 16px; font-size: 16px; }}
  .card p {{ margin: 0; }}
  .role {{ color: var(--muted); font-weight: 400; font-size: 13px; margin-left: 4px; }}
  .byline {{ display: block; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
             font-style: normal; font-size: 14px; color: var(--muted); margin-top: 12px; }}
  details {{ border-top: 1px solid var(--line); }}
  details:last-of-type {{ border-bottom: 1px solid var(--line); }}
  summary {{ cursor: pointer; padding: 14px 0; font-weight: 600; font-size: 15px; }}
  .more {{ font-size: 15px; padding-bottom: 16px; }}
  .links a {{ color: var(--ink); }}
  .headlines {{ border-top: none; border-bottom: 1px solid var(--line); }}
  .headlines .links {{ font-size: 15px; padding-bottom: 8px; }}
  .headlines .links li {{ margin-bottom: 10px; }}
  .src {{ color: var(--muted); font-size: 13px; }}
  .quiet {{ padding: 40px 0; color: var(--muted); font-style: italic; }}
  footer {{ color: var(--muted); font-size: 13px; margin-top: 28px; }}
</style>
</head>
<body>
<main>
<header>
  <span class="brand">Mining Desk</span>
  <span class="date">{esc(brief.get('date_label', ''))}</span>
</header>
{main}
{''.join(extras)}
<footer>Updated {updated} Pacific · {brief.get('article_count', 0)} articles reviewed · AI analysis for personal interest, not investment advice.</footer>
</main>
<script>
  // Home-screen apps on iPhone keep an old copy open in the background.
  // If you come back after 30+ minutes, reload so you see the latest brief.
  var loadedAt = Date.now();
  document.addEventListener("visibilitychange", function () {{
    if (document.visibilityState === "visible" && Date.now() - loadedAt > 30 * 60 * 1000) {{
      location.reload();
    }}
  }});
</script>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")

    # Copy the home-screen icon next to the page, if there is one.
    if Path("icon.png").exists():
        shutil.copy("icon.png", out_path.parent / "icon.png")

    # Tells GitHub Pages to serve the folder as-is (skips its "Jekyll" processing).
    (out_path.parent / ".nojekyll").write_text("")
