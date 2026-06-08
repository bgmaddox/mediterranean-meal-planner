# Future Work — Mediterranean Meal Planner

Remaining and deferred work as of **2026-06-08**, after completing
`IMPROVEMENTS.md`, `IMPROVEMENTS_V2.md` (all 4 phases), and addressing
`gemini_review.md`. Completed plans are archived in `archive/` with
`archive/IMPLEMENTATION_NOTES.md`. The master design doc is `PLAN.md`.

Use this file as the entry point when planning the next session.

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
- **Python version:** the project `.venv` runs **Python 3.14**, while the global
  convention (`~/.claude/CLAUDE.md`) standardizes on **3.13**. Confirm this is
  intentional or realign.

---

## Branch / deploy reminders
- Work on a `feature/<short-description>` branch off `main` (per `CLAUDE.md`).
- Current feature branch with all the above-completed work: `feature/substitute-and-fixes`.
- Deploy after merge: `ssh rachett 'bash ~/deploy.sh diet'`.
