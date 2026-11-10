# Future Work — Mediterranean Meal Planner

Remaining and deferred work, last updated **2026-10-08** (§0); the rest dates from **2026-06-08**, after completing
`IMPROVEMENTS.md`, `IMPROVEMENTS_V2.md` (all 4 phases), and addressing
`gemini_review.md`. Completed plans are archived in `archive/` with
`archive/IMPLEMENTATION_NOTES.md`. The master design doc is `PLAN.md`.

Use this file as the entry point when planning the next session.

---

## 0a. Lunch weeks (2026-11-10) — merged and deployed

`/plan-lunches` plans two Sunday batch lunches and saves them into their week. Steps 1
and 2 of the plan are done: `lunch_week.py`, checker rules, `cli.py lunch-*`, save by
part, the lunch reviewer rubric, lunch methods in the PDF and Kitchen Mode. How it works:
`CLAUDE.md` → "Lunch weeks". Why: `DECISIONS.md`.

**Next action: run the first real `/plan-lunches`.** The history cap is gone (every
week is kept). After the first lunch-only save, look at that week in the app's Generate
tab: only the PDF, the CLI and Kitchen Mode were exercised with a lunch-only week.

| Item | Trigger |
|---|---|
| **Lunch-only button in the app's Generate tab** (API path). | Only if planning lunches without a session is wanted. |
| **Weight tracker logs a batch lunch once.** `Weight/core/diet.py` hides a lunch after one log, keyed by week and id, so a 3-portion batch can be logged from the plan on one day only. | When lunch logging in Crumb matters. Fix belongs in `Weight/`. |
| **Lunch targets are prompt-only** (450–550 kcal, ≥30 g protein, ≥10 g fiber). | If saved lunches drift under them. |
| **`special_ingredient_unused` reads as new on a merged week** whenever the lunch plan changes which ingredients it lists. Noise, not wrong. | If it buries real findings in `lunch-check`. |
| **Dinner prep tasks that served a replaced lunch** stay on the prep list. The skill tells the session to point them out; nothing removes them. | If it causes wasted Sunday work. |

## 0. Resume point (2026-10-08)

**Deployed `ab3e89f`:** weeks are now planned in Claude Code with `/plan-week`, and the
app is the backup. Generation, repair and review run on Opus 5.5. `made_in` keeps
home-made components off the shopping list. How it works: `CLAUDE.md` → "Planning a
Week — two paths". Why: `DECISIONS.md`.

**Next action: run the first real `/plan-week`** from the `Diet/` directory, on the home
network if Tailscale is still down. It opens with the look-back at the 2026-08-29 week,
then plans the new one. At the end it creates `sessions/review-log.md`, the first
measurement of what a session's review costs.

**Waiting on the user:** nothing. They will run that week.

### Deferred from the session-planning work (pick up on the trigger)

| Item | Trigger |
|---|---|
| **Is double dinner review worth it?** Read `sessions/review-log.md`: if reviewer B rarely finds what A missed, drop to one (skill step 6). | After 3–4 logged sessions. |
| **First app generation on Opus 5.5.** Only a small live call has exercised `_ask()`; a full week plus review/repair has not. Watch for time, truncation (`max_tokens` 64k), and cost against the old ~$0.70. | The next time the app's "Generate Week" is used. |
| **API key is plain text in `/etc/systemd/system/diet.service`** (readable by any Pi user). Move it to an `EnvironmentFile=` with mode 600, as cloudflared's token is. | Any other Pi service change, or before any second user gets a Pi account. |
| **Leftover lunches are left out of the shopping list entirely** (`shopping.py`), including their fresh add-ons such as yogurt or greens. Pre-existing behavior, noticed in passing. | The first time an add-on is missing at the store, or a shopping review flags it. |

---

## 1. Wire the Weekly Email Report into the UI  ⬅ highest-value remaining item

**Status:** Backend fully built, **UI not wired**. This is the only major
feature from `PLAN.md` that isn't reachable by the user.

**What already exists (`email_report.py`):**
- `generate_email_narrative(week_plan)` — Claude call for prose sections.
- `build_html_email(...)` — full HTML assembly.
- `send_email(html, subject, recipient)` — Gmail SMTP via `SMTP_HOST/PORT/USER/PASSWORD` env vars.
- `send_weekly_report(week_plan, shopping_sections, prefs, week_start)` — the full pipeline entry point.
- `schemas.py` already has `recipient_email: Optional[str]` and `auto_send_email: bool` on `UserPreferences`.

**What's missing (all in `app.py`):**
1. `import email_report` (and `EmailError`).
2. **Generate tab:** a "📧 Send Weekly Email" button (near the PDF/Drive export
   buttons, ~line 320). On click: ensure `shopping_sections` is built, then call
   `email_report.send_weekly_report(plan, st.session_state.shopping_sections, prefs, st.session_state.week_start)`
   inside a spinner; surface `EmailError` via `st.error`. Disable the button if
   `prefs.get("recipient_email")` is unset (with a help tooltip pointing to Settings).
3. **Settings tab:** inputs for `recipient_email` (text) and `auto_send_email`
   (checkbox), persisted via `data_store.update_preferences(...)`. Add these
   inside the existing `_settings_fragment()` (Phase 3) so they get scoped reruns.
4. **(Optional) Auto-send:** if `auto_send_email` is True, call
   `send_weekly_report` automatically after a successful generation in
   `_prompt_preview_dialog`.

**Setup note for the user (one-time, outside the app):** create a Gmail App
Password and export `SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD` (see `PLAN.md`
→ "SMTP Setup (Gmail)").

**Acceptance:** with env vars + a recipient set, clicking "Send Weekly Email"
delivers a formatted HTML report; missing config shows a clear error, not a crash.

---

## 2. Deferred from the Gemini review (conscious "not now" calls)

These were reviewed and intentionally postponed — revisit only if the trigger
condition below is met.

| Item | Why deferred | Revisit when |
|---|---|---|
| **`pint` unit summing in shopping list** | Chokes on `"handful"`, `"1 large"`, `"to taste"` — needs careful fallbacks. Current string-concat merge (`"1 lb + 1 lb"`) is safe but messy. | The messy quantities become a real annoyance in practice. Implement with a parse-and-fallback wrapper (sum when both sides parse to a common unit; else concat). |
| **SQLite migration** | Loses the human-editable JSON design goal; no concurrency problem at single-user scale. | Data grows enough that rewriting whole JSON arrays on every toggle is measurably slow, or multi-user/concurrent access becomes a requirement. |
| **FastAPI + Next.js rewrite** | Large effort; `@st.fragment` (Phase 3) was the cheaper fix for the "clunky" feel. | After living with the fragment-optimized Streamlit app — only if responsiveness/mobile UX is still unsatisfactory. |
| **Bug C — deepcopy before state mutation** | Not a real bug in single-threaded Streamlit; current in-place-then-reassign pattern works. | Won't fix unless the execution model changes (e.g., a migration off Streamlit). |

---

## 3. Smaller follow-ups & polish (nice-to-have)

- **Substitution → live verification:** Phase 4's `substitute_ingredient` was
  verified up to the API boundary via `AppTest`, but the actual Claude call and
  dialog round-trip haven't been exercised live. Do a manual `streamlit run`
  pass: substitute an ingredient on a dinner (with a paired leftover lunch) and
  on a lunch; confirm the recipe, paired lunch, and shopping list all update
  coherently and the "avoid in future" checkbox adds a constraint.
- **Budget mode depth:** `budget` is passed into the prompt, but `PLAN.md`'s
  nice-to-have #9 also envisioned Claude *flagging* expensive-ingredient weeks.
  Consider surfacing a budget-vs-estimate indicator on the week summary strip.
- ~~Python version~~ settled: the Mac `.venv` is 3.14, the Pi's `~/apps/venv` is 3.13.5.
  The `ab3e89f` deploy was verified on 3.13 (imports, prompt build, headless app run);
  the test suite runs only on the Mac. Don't align them as a side effect of other work.

---

## Branch / deploy reminders
- Work on a `feature/<short-description>` branch off `main` (per `CLAUDE.md`).
- Deploy after merge: `ssh rachett 'bash ~/deploy.sh diet'` (if Tailscale times out:
  `ssh bgmaddox@rachett.local ...`), then verify per the `pi-server` skill.
