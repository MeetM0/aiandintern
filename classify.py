"""Classify forum comments as acceptable, toxic or spam with Cloudflare's Clef model,
then write an HTML report that is easy to scan.

Usage:
    python3 classify.py                          # comments.json, model "clef"
    python3 classify.py --model clef-flash       # the faster, smaller model
    python3 classify.py --compare                # run both models, flag disagreements
    python3 classify.py --input comments-v2.json --output report-v2.html

Credentials are read from cloud-flare.local.sh (never committed).
API answers are cached in results/<input>.<model>.json, so re-running only rebuilds
the HTML. Use --refresh to call the API again.
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

CREDENTIALS_FILE = "cloud-flare.local.sh"
API_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/@cf/cloudflare/{model}"
LABELS = ["acceptable", "toxic", "spam"]
LOW_CONFIDENCE = 0.5

INSTRUCTIONS = (
    "`comment` was posted on the discussion forum of a college course on Generative AI "
    "for Indian engineering students. `forum` names the assignment being discussed and "
    "`thread` names the sub-thread. Decide whether a moderator should leave the comment "
    "up (acceptable), remove it for abuse (toxic), or remove it as unwanted promotion "
    "or junk (spam). Students often write casually, in Hinglish, with typos, slang or "
    "emojis; that alone does not make a comment toxic or spam. Judge what the comment "
    "does, not its tone or spelling."
)

CRITERIA = {
    "acceptable": (
        "A genuine post by a student or staff member: a question, answer, doubt, "
        "opinion, feedback, thanks, or casual chat with classmates. Includes blunt "
        "criticism or frustration about the course, assignment, tools or teaching, "
        "sarcasm and mild swearing, as long as it does not insult, demean or threaten "
        "a person or group. Sharing a relevant, non-commercial resource is acceptable."
    ),
    "toxic": (
        "Attacks, insults, mocks, demeans, harasses or threatens a person or group "
        "(another student, a TA, the instructor), including slurs, body-shaming, "
        "casteist, communal, sexist or homophobic remarks, abusive language aimed at "
        "someone (in English, Hindi or Hinglish), doxxing, or encouraging self-harm. "
        "Also toxic: stereotyping or mocking a whole group of students (a branch, hostel, "
        "batch, region, language, gender, caste or community), even when no one is named "
        "and even when said as a joke. "
        "Criticism of ideas or the course without a personal attack is not toxic."
    ),
    "spam": (
        "Unsolicited promotion or junk rather than a genuine contribution: ads, paid "
        "assignment or exam 'help', selling accounts or 'premium' AI access, referral "
        "or affiliate links, crypto, jobs or money schemes, phishing, links or contact "
        "numbers pushed to strangers, or repeated / meaningless filler posted to get "
        "attention. A student honestly sharing a free, relevant resource is not spam."
    ),
}


# ---------------------------------------------------------------- credentials / API

def load_credentials():
    if not os.path.exists(CREDENTIALS_FILE):
        sys.exit(f"Missing {CREDENTIALS_FILE}. Copy cloud-flare.sh to it and fill in your values.")
    text = open(CREDENTIALS_FILE, encoding="utf-8").read()
    creds = dict(re.findall(r'export (\w+)="([^"]*)"', text))
    account = creds.get("CLOUDFLARE_ACCOUNT_ID", "")
    token = creds.get("CLOUDFLARE_API_TOKEN", "")
    if not account or not token or "your-cloudflare" in token:
        sys.exit(f"Fill in CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN in {CREDENTIALS_FILE}.")
    return account, token


def build_payload(comment, forum):
    return {
        "state": {
            "comment": comment["comment"],
            "thread": comment.get("thread", ""),
            "forum": forum,
        },
        "questions": {
            "moderation": {
                "type": "choice",
                "instructions": INSTRUCTIONS,
                "criteria": CRITERIA,
            }
        },
    }


def ask_clef(account, token, model, payload, retries=5):
    url = API_URL.format(account=account, model=model)
    body = json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    for attempt in range(retries):
        request = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.load(response)
        except urllib.error.HTTPError as err:
            if err.code in (401, 403):
                sys.exit(f"HTTP {err.code}: check the account ID and token (needs Workers AI Read + Edit).")
            if err.code == 429 or err.code >= 500:
                wait = 2 ** attempt
                print(f"  HTTP {err.code}, retrying in {wait}s ...")
                time.sleep(wait)
                continue
            raise
        except urllib.error.URLError as err:
            wait = 2 ** attempt
            print(f"  network error ({err.reason}), retrying in {wait}s ...")
            time.sleep(wait)
            continue

        if not data.get("success"):
            raise RuntimeError(f"Clef returned an error: {data.get('errors')}")
        return data["result"]["answers"]["moderation"]

    raise RuntimeError("Gave up after repeated errors.")


def classify_all(comments, forum, model, cache_path, refresh):
    cache = {}
    if os.path.exists(cache_path) and not refresh:
        cache = json.load(open(cache_path, encoding="utf-8"))

    missing = []
    for c in comments:
        if str(c["id"]) not in cache:
            missing.append(c)

    if missing:
        account, token = load_credentials()
        print(f"Calling {model} for {len(missing)} comments ...")
        for i, c in enumerate(missing, start=1):
            answer = ask_clef(account, token, model, build_payload(c, forum))
            cache[str(c["id"])] = {
                "choice": answer["choice"],
                "probabilities": answer["probabilities"],
                "confidence": answer["confidence"],
            }
            print(f"  [{i}/{len(missing)}] #{c['id']}: {answer['choice']} ({answer['confidence']:.2f})")
            # Save after every call so an interrupted run loses nothing.
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2)
    else:
        print(f"Using cached {model} results from {cache_path}")

    return cache


# ---------------------------------------------------------------- HTML report

CSS = """
:root { --bg:#f7f7f5; --card:#fff; --ink:#1d1d1f; --muted:#6b6b70; --line:#e3e3e0;
  --acceptable:#1f7a4d; --acceptable-bg:#e3f4ea; --toxic:#b3261e; --toxic-bg:#fbe4e2;
  --spam:#9a5b00; --spam-bg:#fdf0d8; --flag:#5b3cc4; }
@media (prefers-color-scheme: dark) { :root { --bg:#151517; --card:#1f1f22; --ink:#ececee;
  --muted:#a0a0a8; --line:#33333a; --acceptable:#6fd39e; --acceptable-bg:#173a28;
  --toxic:#ff8a80; --toxic-bg:#47201e; --spam:#f3c26b; --spam-bg:#3d2e12; --flag:#b9a6ff; } }
* { box-sizing:border-box; }
body { margin:0; font:15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  background:var(--bg); color:var(--ink); }
header { position:sticky; top:0; background:var(--bg); border-bottom:1px solid var(--line);
  padding:14px 20px; z-index:1; }
h1 { font-size:18px; margin:0 0 4px; }
.sub { color:var(--muted); font-size:13px; }
.bar { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; align-items:center; }
.bar button { border:1px solid var(--line); background:var(--card); color:var(--ink);
  border-radius:999px; padding:4px 12px; cursor:pointer; font-size:13px; }
.bar button.on { border-color:var(--ink); font-weight:600; }
.bar input, .bar select { border:1px solid var(--line); background:var(--card); color:var(--ink);
  border-radius:8px; padding:5px 10px; font-size:13px; }
main { max-width:980px; margin:0 auto; padding:16px 20px 60px; }
.row { display:grid; grid-template-columns:48px 110px 1fr; gap:12px; background:var(--card);
  border:1px solid var(--line); border-left:5px solid var(--line); border-radius:8px;
  padding:10px 14px; margin-bottom:8px; }
.row.acceptable { border-left-color:var(--acceptable); }
.row.toxic { border-left-color:var(--toxic); }
.row.spam { border-left-color:var(--spam); }
.id { font-weight:700; color:var(--muted); font-variant-numeric:tabular-nums; }
.badge { display:inline-block; font-size:12px; font-weight:700; text-transform:uppercase;
  letter-spacing:.03em; padding:2px 8px; border-radius:6px; }
.badge.acceptable { color:var(--acceptable); background:var(--acceptable-bg); }
.badge.toxic { color:var(--toxic); background:var(--toxic-bg); }
.badge.spam { color:var(--spam); background:var(--spam-bg); }
.conf { font-size:12px; color:var(--muted); margin-top:4px; font-variant-numeric:tabular-nums; }
.conf.low { color:var(--flag); font-weight:700; }
.text { white-space:pre-wrap; word-break:break-word; }
.meta { font-size:12px; color:var(--muted); margin-top:4px; }
.probs { display:flex; height:6px; border-radius:3px; overflow:hidden; margin-top:6px;
  background:var(--line); max-width:320px; }
.probs span.acceptable { background:var(--acceptable); }
.probs span.toxic { background:var(--toxic); }
.probs span.spam { background:var(--spam); }
.other { font-size:12px; margin-top:4px; }
.other.disagree { color:var(--flag); font-weight:700; }
.row.marked { outline:2px dashed var(--flag); outline-offset:-2px; }
.mark-btn { margin-top:8px; border:1px solid var(--line); background:transparent; color:var(--ink);
  border-radius:6px; padding:3px 10px; font-size:12px; cursor:pointer; }
.row.marked .mark-btn { border-color:var(--flag); color:var(--flag); font-weight:700; }
.editor { display:none; margin-top:8px; padding:10px; border:1px solid var(--line); border-radius:8px; }
.editor.open { display:block; }
.editor label { font-size:13px; margin-right:12px; }
.editor select, .editor textarea { background:var(--bg); color:var(--ink); border:1px solid var(--line);
  border-radius:6px; padding:4px 8px; font:inherit; font-size:13px; }
.editor textarea { width:100%; min-height:60px; margin-top:8px; }
.editor .actions { margin-top:8px; display:flex; gap:8px; }
.editor .actions button { border:1px solid var(--line); background:var(--card); color:var(--ink);
  border-radius:6px; padding:4px 12px; cursor:pointer; font-size:13px; }
.editor .actions button.primary { background:var(--flag); color:#fff; border-color:var(--flag); }
#save-status { font-size:12px; color:var(--muted); }
@media (max-width:640px) { .row { grid-template-columns:40px 1fr; }
  .row .text-cell { grid-column:1 / -1; } }
"""

JS = """
const rows = [...document.querySelectorAll('.row')];
let label = 'all', onlyFlagged = false, onlyMarked = false;
function apply() {
  const q = document.getElementById('q').value.toLowerCase();
  let shown = 0;
  rows.forEach(r => {
    let ok = true;
    if (label !== 'all' && r.dataset.label !== label) ok = false;
    if (onlyFlagged && r.dataset.flag !== '1') ok = false;
    if (onlyMarked && !r.classList.contains('marked')) ok = false;
    if (q && !r.textContent.toLowerCase().includes(q)) ok = false;
    r.style.display = ok ? '' : 'none';
    if (ok) shown++;
  });
  document.getElementById('count').textContent = shown + ' shown';
}
document.querySelectorAll('[data-filter]').forEach(b => b.onclick = () => {
  label = b.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach(x => x.classList.toggle('on', x === b));
  apply();
});
document.getElementById('flag').onclick = e => {
  onlyFlagged = !onlyFlagged; e.target.classList.toggle('on', onlyFlagged); apply();
};
document.getElementById('q').oninput = apply;
document.getElementById('sort').onchange = e => {
  const main = document.querySelector('main');
  const key = e.target.value;
  rows.sort((a, b) => {
    if (key === 'conf') return a.dataset.conf - b.dataset.conf;
    return a.dataset.id - b.dataset.id;
  }).forEach(r => main.appendChild(r));
};

// ---------- MISTAKES.md writer: you mark a row, pick the right label, write why ----------
const LABELS = ['acceptable', 'toxic', 'spam'];
const MODEL = document.body.dataset.model;
const SOURCE = document.body.dataset.source;
const STORE_KEY = 'clef-mistakes:' + SOURCE;
// comments.json -> MISTAKES.md, comments-v2.json -> MISTAKES-v2.md
const MD_NAME = 'MISTAKES' + SOURCE.replace(/^comments/, '').replace(/\\.json$/, '') + '.md';
let mistakes = loadMistakes();
let fileHandle = null;

function loadMistakes() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY));
    if (saved) return saved;
  } catch (e) {}
  return {};
}

function persist() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(mistakes)); } catch (e) {}
}

function quote(text) {
  return '> ' + text.trim().split('\\n').join('\\n> ');
}

function toMarkdown() {
  const ids = Object.keys(mistakes).map(Number).sort((a, b) => a - b);
  let md = '# Clef mistakes\\n\\n';
  md += 'Model: `' + MODEL + '` · Data: `' + SOURCE + '` · ';
  md += ids.length + ' mistakes out of ' + rows.length + ' comments.\\n\\n';
  ids.forEach(id => {
    const m = mistakes[id];
    md += '## #' + id + '\\n\\n' + quote(m.text) + '\\n\\n';
    md += '- **Clef:** ' + m.clef + ' (confidence ' + m.conf + ')\\n';
    md += '- **Mine:** ' + m.mine + '\\n';
    if (m.borderline) md += '- **Borderline:** yes\\n';
    md += '- **Why:** ' + m.why.trim().replace(/\\n+/g, ' ') + '\\n\\n';
  });
  return md;
}

function setStatus(text) {
  document.getElementById('save-status').textContent = text;
}

async function writeToFile() {
  const md = toMarkdown();
  if (fileHandle) {
    const writable = await fileHandle.createWritable();
    await writable.write(md);
    await writable.close();
    setStatus('Saved to ' + fileHandle.name + ' at ' + new Date().toLocaleTimeString());
    return;
  }
  setStatus('Not linked to a file yet. Click "Save ' + MD_NAME + '".');
}

function downloadFile() {
  const blob = new Blob([toMarkdown()], { type: 'text/markdown' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = MD_NAME;
  a.click();
  setStatus('Downloaded ' + MD_NAME + '. Move it into the repo folder.');
}

document.getElementById('save-md').onclick = async () => {
  if (!('showSaveFilePicker' in window)) {
    downloadFile();
    return;
  }
  try {
    if (!fileHandle) {
      fileHandle = await window.showSaveFilePicker({
        suggestedName: MD_NAME,
        types: [{ description: 'Markdown', accept: { 'text/markdown': ['.md'] } }],
      });
    }
    await writeToFile();
  } catch (e) {
    if (e.name !== 'AbortError') {
      setStatus('Could not write the file (' + e.message + '). Downloading instead.');
      downloadFile();
    }
  }
};

function refreshCounts() {
  const n = Object.keys(mistakes).length;
  document.getElementById('marked').textContent = '✗ Marked by me (' + n + ')';
}

function refreshRow(row) {
  const m = mistakes[row.dataset.id];
  const btn = row.querySelector('.mark-btn');
  if (m) {
    row.classList.add('marked');
    btn.textContent = '✎ Mistake: Clef ' + m.clef + ' → me ' + m.mine;
  } else {
    row.classList.remove('marked');
    btn.textContent = '✗ Clef is wrong';
  }
}

async function changed(row) {
  persist();
  refreshRow(row);
  refreshCounts();
  if (fileHandle) {
    try { await writeToFile(); } catch (e) { setStatus('Auto-save failed: ' + e.message); }
  }
}

rows.forEach(row => {
  const id = row.dataset.id;
  const clef = row.dataset.label;
  const editor = row.querySelector('.editor');
  const select = editor.querySelector('select');
  const borderline = editor.querySelector('input[type=checkbox]');
  const why = editor.querySelector('textarea');

  LABELS.forEach(l => {
    if (l === clef) return;
    const opt = document.createElement('option');
    opt.value = l;
    opt.textContent = l;
    select.appendChild(opt);
  });

  row.querySelector('.mark-btn').onclick = () => {
    const m = mistakes[id];
    if (m) {
      select.value = m.mine;
      borderline.checked = m.borderline;
      why.value = m.why;
    }
    editor.classList.toggle('open');
    if (editor.classList.contains('open')) why.focus();
  };

  editor.querySelector('.save').onclick = () => {
    if (!why.value.trim()) {
      why.focus();
      why.placeholder = 'Write one or two sentences on why before saving.';
      return;
    }
    mistakes[id] = {
      text: row.querySelector('.text').textContent,
      clef: clef,
      conf: Number(row.dataset.conf).toFixed(2),
      mine: select.value,
      borderline: borderline.checked,
      why: why.value,
    };
    editor.classList.remove('open');
    changed(row);
  };

  editor.querySelector('.remove').onclick = () => {
    delete mistakes[id];
    why.value = '';
    borderline.checked = false;
    editor.classList.remove('open');
    changed(row);
  };

  refreshRow(row);
});

document.getElementById('marked').onclick = e => {
  onlyMarked = !onlyMarked; e.target.classList.toggle('on', onlyMarked); apply();
};

refreshCounts();
apply();
"""

EDITOR_HTML = """
    <button class="mark-btn" type="button">✗ Clef is wrong</button>
    <div class="editor">
      <label>Right label: <select></select></label>
      <label><input type="checkbox"> Borderline</label>
      <textarea placeholder="Why is Clef wrong? One or two sentences. If borderline, explain your choice."></textarea>
      <div class="actions">
        <button class="save primary" type="button">Save</button>
        <button class="remove" type="button">Remove</button>
      </div>
    </div>"""


def esc(value):
    return html.escape(str(value))


def prob_bar(probabilities):
    parts = []
    for label in LABELS:
        pct = round(100 * probabilities.get(label, 0), 1)
        parts.append(f'<span class="{label}" style="width:{pct}%" title="{label} {pct}%"></span>')
    return '<div class="probs">' + "".join(parts) + "</div>"


def prob_text(probabilities):
    parts = []
    for label in LABELS:
        parts.append(f"{label[0].upper()} {probabilities.get(label, 0):.2f}")
    return " · ".join(parts)


def render_html(data, source, comments, model, results, second_model, second_results):
    counts = {label: 0 for label in LABELS}
    flagged = 0
    rows = []

    for c in comments:
        r = results[str(c["id"])]
        label = r["choice"]
        counts[label] = counts.get(label, 0) + 1
        is_low = r["confidence"] < LOW_CONFIDENCE

        other_html = ""
        disagree = False
        if second_results is not None:
            other = second_results[str(c["id"])]
            disagree = other["choice"] != label
            css = "other"
            if disagree:
                css = "other disagree"
            other_html = (f'<div class="{css}">{esc(second_model)}: {esc(other["choice"])} '
                          f'({other["confidence"]:.2f})</div>')

        flag = is_low or disagree
        if flag:
            flagged += 1

        conf_css = "conf"
        if is_low:
            conf_css = "conf low"

        rows.append(f"""
<div class="row {label}" data-id="{c['id']}" data-label="{label}" data-conf="{r['confidence']}" data-flag="{int(flag)}">
  <div class="id">#{c['id']}</div>
  <div>
    <span class="badge {label}">{esc(label)}</span>
    <div class="{conf_css}">conf {r['confidence']:.2f}</div>
  </div>
  <div class="text-cell">
    <div class="text">{esc(c['comment'])}</div>
    <div class="meta">{esc(c.get('author', ''))} · {esc(c.get('thread', ''))} · {esc(c.get('posted_at', ''))} · {prob_text(r['probabilities'])}</div>
    {prob_bar(r['probabilities'])}
    {other_html}
    {EDITOR_HTML}
  </div>
</div>""")

    flag_hint = f"confidence &lt; {LOW_CONFIDENCE}"
    if second_results is not None:
        flag_hint += f" or {esc(model)} and {esc(second_model)} disagree"

    md_name = "MISTAKES" + source.replace("comments", "", 1).replace(".json", "") + ".md"
    summary = " · ".join(f"{counts[label]} {label}" for label in LABELS)
    title = f"Clef moderation — {esc(data.get('forum', ''))}"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clef moderation report</title><style>{CSS}</style></head>
<body data-model="{esc(model)}" data-source="{esc(source)}">
<header>
  <h1>{title}</h1>
  <div class="sub">{esc(data.get('course', ''))} · model <b>{esc(model)}</b> · {len(comments)} comments · {summary} · {flagged} flagged for review ({flag_hint})</div>
  <div class="bar">
    <button data-filter="all" class="on">All</button>
    <button data-filter="acceptable">Acceptable ({counts['acceptable']})</button>
    <button data-filter="toxic">Toxic ({counts['toxic']})</button>
    <button data-filter="spam">Spam ({counts['spam']})</button>
    <button id="flag">⚑ Needs a look ({flagged})</button>
    <select id="sort"><option value="id">Sort by id</option><option value="conf">Lowest confidence first</option></select>
    <input id="q" placeholder="Search text…">
    <span class="sub" id="count"></span>
  </div>
  <div class="bar">
    <button id="marked">✗ Marked by me (0)</button>
    <button id="save-md">💾 Save {md_name}</button>
    <span id="save-status">Pick {md_name} in your repo folder once; after that every change auto-saves.</span>
  </div>
</header>
<main>{''.join(rows)}</main>
<script>{JS}</script>
</body></html>"""


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Moderate forum comments with Cloudflare Clef.")
    parser.add_argument("--input", default="comments.json")
    parser.add_argument("--output", default=None, help="HTML file to write (default: report-<input>.html)")
    parser.add_argument("--model", default="clef", choices=["clef", "clef-flash"])
    parser.add_argument("--compare", action="store_true", help="also run the other model and flag disagreements")
    parser.add_argument("--refresh", action="store_true", help="ignore cached results and call the API again")
    args = parser.parse_args()

    data = json.load(open(args.input, encoding="utf-8"))
    comments = data["comments"]
    forum = data.get("forum", "")
    stem = os.path.splitext(os.path.basename(args.input))[0]

    output = args.output
    if output is None:
        output = f"report-{stem}.html"

    results = classify_all(comments, forum, args.model,
                           f"results/{stem}.{args.model}.json", args.refresh)

    second_model = None
    second_results = None
    if args.compare:
        second_model = "clef-flash"
        if args.model == "clef-flash":
            second_model = "clef"
        second_results = classify_all(comments, forum, second_model,
                                      f"results/{stem}.{second_model}.json", args.refresh)

    page = render_html(data, os.path.basename(args.input), comments, args.model, results, second_model, second_results)
    with open(output, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
