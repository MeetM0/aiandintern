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

3. Open `report-comments.html` in Chrome. Filter by label, click "Needs a look" to see
   low-confidence answers (< 0.5) and model disagreements, or sort by lowest confidence.
4. To record a mistake, click "Clef is wrong" on a comment, pick the right label, mark it
   borderline if needed and write why. "Save MISTAKES.md" writes all marked comments to the file.

API answers are cached in `results/`, so re-running only rebuilds the HTML. `--refresh` calls the API again.

## Tools and models

- Cloudflare Workers AI, `@cf/cloudflare/clef` (main labels) and `clef-flash` (comparison).
- Each comment is sent with its `thread` and the forum title as context, plus one choice
  question describing acceptable / toxic / spam (see `CRITERIA` in `classify.py`).
- Claude (in the Chrome side panel) wrote `classify.py`, the HTML report and its
  "Clef is wrong" buttons, and set up the GitHub repo.
- `MISTAKES.md` is my own judgement: I read the comments and Clef's labels and wrote every
  label and reason myself. The report's buttons only save what I type.

## What I thought

- Getting Clef running was quick; most of my time went into a 401 error, which turned out
  to be a wrong token value in `cloud-flare.local.sh`, not the code.
- The two mistakes I found are both comments that stereotype a whole group (a hostel side,
  a branch) without insulting one named person. Clef called both acceptable with very low
  confidence (0.22), so the confidence score was a useful pointer to what needed a human look.
- Next I would add "stereotyping or mocking a group (branch, hostel, region)" explicitly
  to the toxic criteria and re-run to see whether Clef catches these.

## Second run (comments-v2.json, `clef` model)

- Command: `python classify.py --input comments-v2.json --output results-v2.html`
- Before this run I added group stereotyping to the toxic criteria, because both v1 mistakes were of that kind; Claude made the code change.
- Clef labelled 71 acceptable, 14 toxic and 15 spam. I found 6 mistakes (see `MISTAKES-v2.md`):
  - **Harsh words that hurt no one were called toxic:** a student mocking himself (#7, borderline) and a student reporting an incident (#20).
  - **Targeted sarcasm was missed:** mockery of a named classmate passed as acceptable (#42, #91).
  - **Spam judged by surface cues, not intent:** a phishing "verify your Moodle account" post passed as acceptable (#40), while a genuine optional workshop announcement with a forms link was called spam because it said "URGENT" (#62).
- All 6 had confidence 0.55 or lower, so the low-confidence filter again pointed at the right places, though 3 of them (#20, #42, #91) were just above my 0.5 flag line.
- The checking of Clef's labels in `MISTAKES-v2.md` is my own work.
