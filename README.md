# Interview app (iteration 2)

Participants open a link, read the consent text, and go through the interview in a chat window. A small
Python state machine (`engine.py`) decides every question. Claude (Opus 5.5 via the API) is called once per
answer and only **classifies** it (`classifier.py`): decline / trait / vague / answer, the situation, action and
outcome quotes, and a short restatement. The model never writes a question.

```
participant ──> Streamlit page ──> classifier (Claude API, structured JSON output)
                     │                     │ label + quotes
                     │<── engine.py: fixed next question
                     └──> database: session state + one row per turn (both in one transaction)
researcher ──> ?admin=1  (password)  turn log, audit marks, adherence rate, CSV/JSON export
```

## Files

| File | What it does |
|---|---|
| `engine.py` | All fixed strings and the decision table (Appendix C + iteration-2 changes) |
| `classifier.py` | The only LLM call. Returns a validated classification; retries on malformed output |
| `storage.py` | SQLite locally, Postgres (e.g. Supabase) in production; optimistic locking per turn |
| `app.py` | Participant chat (`?code=P01`) and routing to the researcher view (`?admin=1`) |
| `admin.py` | Sessions, positions, accepted incidents, turn log with audit dropdown, exports |
| `consent.md` | **Placeholder. Replace with your consent text before sending links.** |
| `replay.py` | Runs a text file of answers through the real classifier and engine, no database |
| `tests/` | 15 tests: the decision table and an end-to-end app run with a fake classifier |

## 1. Run it locally (15 min)

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edit secrets.toml: ANTHROPIC_API_KEY, ADMIN_PASSWORD, and delete the DATABASE_URL line to use local SQLite
streamlit run app.py
```

Open `http://localhost:8501/?code=TEST1` for a participant session and `http://localhost:8501/?admin=1` for the
researcher view. Run the tests with `pip install pytest && pytest -q`.

## 2. Test the classifier before anyone else sees it

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python replay.py my-test-answers.txt
```

Optional: `--effort low|medium|high|xhigh|max` and `--model <id>`. `my-test-answers.txt` holds one answer per paragraph, in order. The first paragraph is the role form as a `ROLES` block (one entry per line: `category; title; company or club; from; to`, see `replay.py`). Paste the answers
from interview 1 and 2 and add hard cases: "skip", "we already covered that", "I'm very reliable", "it went
well", "what do you mean?". Read every printed question and check it against the protocol. This is the
first evaluation step for the thesis (classifier agreement with your own judgement).

## 3. Deploy

**API key.** console.anthropic.com > Settings > Billing > buy credits; Settings > API keys > create a key. Set a
monthly spend limit in the Console.

**Database (persistent storage).** Free hosting does not keep local files across restarts, so use an external
database. Supabase: create a project in an EU region (Frankfurt), then Connect > Session pooler and copy the URI. Prefix
it with `postgresql+psycopg2://` instead of `postgresql://` and put it in `DATABASE_URL`. The tables are created on
first start.

**Hosting (Streamlit Community Cloud).** Push this folder to a GitHub repository (the `.gitignore` keeps
`secrets.toml` out), go to share.streamlit.io, create an app from the repo with `app.py` as the entry point, and
paste the contents of your `secrets.toml` into Advanced settings > Secrets. Make sure the app is not set to
require a viewer login, otherwise participants can't open it. Check whether a private repository is supported on
your plan; if you use a public one, nothing secret is in the code.

**Links.** Send each participant `https://<your-app>.streamlit.app/?code=P01` (P02, ...). Only codes listed in
`ALLOWED_CODES` can start a session, so strangers can't use up your API credits. A participant can close the tab
and reopen the same link to continue where they stopped.

## 4. Settings (secrets)

| Key | Default | Note |
|---|---|---|
| `MODEL` | `claude-opus-5-5` | Logged in every session and every turn |
| `EFFORT` | `medium` | `low`, `medium`, `high`, `xhigh` or `max`. Opus 5.5 always thinks before answering; this sets how much. `medium` is the model's documented default. Logged with every turn. |
| `MAX_POSITIONS` | `0` (no cap) | Decide before data collection and report it (Appendix C §9) |
| `ALLOWED_CODES` | empty | Comma-separated. Empty lets any code start a session |

## 5. Cost (estimate, not measured)

Prices from the Claude pricing page (October 2026): Opus 5.5 costs $4 per million input tokens, $20 per million output
tokens, and $0.20 per million for cached input. Per turn: input tokens x $4/M + output tokens x $20/M.

Opus 5.5 always thinks before it answers, and thinking tokens are billed as output tokens, so a turn can cost
considerably more than the short JSON answer suggests. **This has not been measured.** `replay.py` prints the tokens
per answer and an estimated total for the 19-answer test file; multiply by the number of turns you expect per session
(roughly 60-100) to get a session cost before you send any link. If it is too slow or expensive, try `EFFORT = "low"`
and compare the labels in a second replay. The admin view shows the real token counts per session.

## 6. What changed compared with Appendix C (report as iteration 2)

1. **Architecture:** the decision table runs as code; the LLM only classifies. Replaces the operator-relayed WoZ setup
   of Sections 3.2.2 / 4.4 for remote sessions.
2. **Briefing** before the first question.
3. **Outcome question** extended: "Please describe what actually happened or changed."
4. **Fixed transition sentences** at stage changes, with no content.
5. **Role-form labels** for all positions, so "your role as {label}" also fits hobbies.
6. **Role form** (`roles.py`, `roles_ui.py`, engine `app-v3`): jobs, volunteering and hobbies are entered in a short form instead of a free-text answer; no model is involved and the paid status follows from the category.
7. **One fixed clarifying clause per question** for the "question misunderstood" rule.
8. **Stop handling:** an explicit stop request or two complaints in a row lead to "Would you like to stop the interview here?".
9. **Restatements** are generated by the classifier but only shown if they are at most 25 words and at least 60% of
   their content words appear in the participant's answer; otherwise the plain fixed question is shown.
10. **Audit trail:** every turn stores the raw answer, the classification with a one-line rationale, the rule
    applied, the exact question shown, model and token usage.

## 7. Known limits

- The first real run (on Windows) failed because Opus 5.5 does not accept `temperature` and rejects forced tool use.
  Both were fixed according to the Opus 5.5 migration guide, and the request is now checked against the installed
  SDK's function signature in the tests. It has still **not** been run successfully against the live API by me. Run
  `replay.py` before the first participant.
- **Reproducibility.** Temperature, top_p and top_k cannot be set on Opus 5.5, and the docs describe no seed. Two
  runs on the same answer can therefore label differently. What the app does log: model id, effort, prompt version,
  stop reason and token counts for every turn. Report this as a limitation, and measure it: run `replay.py` on the
  same file three times and compare the labels (test-retest agreement). That number belongs in your evaluation.
- Classification errors are still possible. They now show up as a wrong rule in the turn log, not as an invented
  question, and the audit column measures how often they happen.
- Raw answers are stored verbatim, including any names a participant types. Redact before profile generation
  (Appendix C §10) and say so in the consent text.
- Participants answering asynchronously have no one to ask. The fixed clarifying clauses are the only help.


## CV upload and anonymisation (added 18.10.2026)

After the interview is complete, the participant uploads a CV (PDF, up to 6 pages, 8 MB). `cv.py` blacks out, locally and
without any AI service: the participant's name (full and first/last separately), extra terms they list (employers, city,
university), email addresses, phone numbers, web links, all images (photo, logos) and clickable links; document metadata is
cleared. Redaction is true PDF redaction (the text underneath is removed). The result keeps the original layout.
The original upload is processed in memory and never stored; only the blacked-out PDF and a counts-only report are saved
(table `cvs`). The participant reviews every page and confirms before submitting. In `?admin=1` the researcher downloads the PDF
and ticks "checked by hand".

Limits: scanned PDFs (no text layer), password-protected files and Word files are refused (export Word as PDF first).
Addresses, dates of birth and unlisted employers or school names are NOT detected automatically; only the leftover check
(re-reads the result for every listed term) and the manual check catch them. A name that is split over two lines may not be
matched. Treat the automatic step as an aid, never as the guarantee. PyMuPDF is AGPL-licensed.

## Behavioural resume for recruiters (`profile_gen.py`, `profile_ui.py`, version `resume-v2`)

Interview additions: the role form (engine `app-v3`, 20.10.2026) replaces the free-text listing, the list
confirmation and the paid-status question of `app-v2`. Fields per category:

| Category | Counts as | Asks for | Period |
|---|---|---|---|
| Job (employment, student job, internship, freelance) | job | job title, company (or "self-employed") | from and to, required (MM/YYYY or YYYY, "now") |
| Volunteering | non-work | role, organisation | optional (both or none) |
| Hobby | non-work | what you do, as a role ("marathon runner"); club or team optional | none |

After each aim question a fixed tools question ("which tools, methods or areas of knowledge ...", wording for jobs
and for activities outside work). The resume shows the category next to each role.

In `?admin=1`, for every completed session:
1. **Remove personal names** with replacement rules (`find => replacement`). Company names, titles, places and
   achievements stay (decided 18.10.2026).
2. **Same event told twice** (resume-v2). Pairs of incidents from the same role are listed with their content
   overlap (pairs at 40 % or more are marked "likely"). Tick the pairs that describe the same event and save (also
   when nothing is ticked); generation stays disabled until the decisions are saved. Stored in table `merges`.
3. **Generate.** One model call per incident or merged event: headline (<= 12 words), situation and result (<= 35 words each), up to
   two competency labels (Bartram 2005 factor names, coding agenda v1), circumstance tags, tools named in the
   incident, personal names still present. Code checks every entry (word limits, numbers, verbatim evidence quote
   for every label and tag, tools must appear in the answers, no redacted or reported personal names) and
   regenerates on failure (max. 3 attempts, logged).
   Plus one call per role that restates the aim answer as a purpose line (<= 15 words, >= 60 % of its content
   words from the answer, no numbers or names that are not in it; 3 attempts, then the line is left out).
4. **Check** each entry against the answers (side by side, with the evidence quotes). No hand-editing.
5. **Download** the PDF (resume-v2): a role x theme matrix (company, category, period, purpose, tools; situations per
   theme; roles with at least one situation), the labels by reach ("in more than one role" / "in one role only"), then
   the three themes; a merged event is shown in full under its first theme and cross-listed under the other. Entries
   sharing a label that recurs across roles sit next to each other. Older resume-v1 profiles stay stored with their
   PDF. Then the three themes ("Doing the work", "Standing by standards", "Helping others") with headline, role, labels (blue),
   circumstance tags (amber), tools (grey), situation and result. Aims, reflections, prompt levels and JSON are not
   shown. Packet (resume + blacked-out CV) once approved.

Replay file for the new flow: `201026 - test-answers v3.txt` (role form as a ROLES block, then the tools answers). Delete the old `181026 - test-answers v2.txt`.
