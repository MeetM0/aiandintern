# Notes

## How to run

1. Copy `cloud-flare.sh` to `cloud-flare.local.sh` and fill in your Cloudflare account ID and a
   Workers AI API token (permissions: Workers AI Read + Edit). This file is git-ignored.
2. Python 3.8+ only, no packages to install:

   ```bash
   python3 classify.py                      # classifies comments.json with clef -> report-comments.html
   python3 classify.py --compare            # also runs clef-flash and flags disagreements
   python3 classify.py --input comments-v2.json
   ```

3. Open `report-comments.html` in a browser. Filter by label, click "Needs a look" to see
   low-confidence answers (< 0.5) and model disagreements, or sort by lowest confidence.

API answers are cached in `results/`, so re-running only rebuilds the HTML. `--refresh` calls the API again.

## Tools and models

- Cloudflare Workers AI, `@cf/cloudflare/clef` (main labels) and `clef-flash` (comparison).
- Each comment is sent with its `thread` and the forum title as context, plus one choice
  question describing acceptable / toxic / spam (see `CRITERIA` in `classify.py`).
- <!-- TODO: which AI assistant(s) you used to write the code, e.g. Claude -->
- `MISTAKES.md` was written by me after reading all 100 comments; no AI was used for it.

## What I thought

<!-- TODO: 3–5 lines in your own words: what surprised you about Clef's mistakes,
     which kinds of comments were hardest to call, what you'd change in the criteria. -->
