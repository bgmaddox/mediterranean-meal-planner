# Decisions & Do-Not-Repeat Ledger

Settled decisions and dead ends for this project. **Read before proposing an approach.**
If something here is wrong, correct the entry rather than silently working around it.

---

## Settled

**Meal composition is enforced by rules, not left to the model.**
"Mediterranean plate geometry" — smaller protein, half-plate vegetables, a fiber floor,
and an enforced composition mix — is a hard constraint (`98bd69c`), later joined by a
legume target and shared rule constants (`39ef733`). Do not relax these into prompt
suggestions; they were deliberately moved out of the prompt into enforced rules.

**Anchor recipes are a curated library, not model-generated.**
~33 anchors sourced from Olive Tomato (`67d32bd`), Mediterranean Dish (`83945d6`), and
Mediterranean Living (`27e9088`), each with a `role` field (`a9013c5`). Plans are built
by matching anchors — including matching them to use-up ingredients (`cfe86c3`) — not
by inventing dishes.

**Vegetables can be the dish, not only a side** (`053a0a7`), via named lathera
archetypes (`b5fac6a`).

**There is an auto-retry on constraint violation**, and the PDF carries a
fiber/composition scorecard (`39ef733`). Constraint failures are meant to be caught and
retried, not silently accepted.

**Health targets are the point:** weight loss, cholesterol, uric acid. Changes that
improve variety at the cost of these targets are regressions.

**Constraints are verified in code, not merely requested in the prompt** (`71c83bf`).
`constraint_check.py` checks every generated plan. Asking Claude for a rule and never
checking it is how a reheated salmon lunch shipped in the one stored week — the prompt
had forbidden fish at lunch the whole time. Hard violations (banned ingredient, fish at
lunch, sodium over the ceiling, a dinner duplicated within the week, a leftover pointing
at a missing dinner) reject the plan and feed the reason back; soft ones surface in the
UI. **When adding a rule to the prompt, ask whether it can also be checked.**

**A defect in one recipe is repaired, never regenerated** (`b61b392`).
`plan_critique.review_meal` proofreads each meal separately for internal
contradictions; `meal_planner.repair_meal` fixes that one meal in place. Regenerating
the week over one bad dinner discards four good ones and rerolls everything. Three
guards make repair safe: one attempt per meal, revert if the repair *introduces* a hard
violation (measured against the plan's baseline, **not** against zero — a plan can
arrive already carrying one, and the first version rejected every repair for this
reason), and re-proof so an unfixed defect stays visible.

**Model judgment is split by how trustworthy it is** (`b61b392`).
Contradictions inside a recipe are objective enough to act on. Variety and plate
geometry are matters of taste and stay advisory forever. Do not promote a judgment
category to a defect category without evidence that it is checkable.

**Superseded 2026-10-07: everything runs on Opus 5.5**, by the user's choice. The entry below is the measurement it replaces, kept because it is still the evidence if cost matters again.

**Review runs on Sonnet 5, repairs on Opus 4.8** (`4491f03`). Measured, not assumed:
three Sonnet review runs over the same week found 10/14/13 defects against Opus 5's 10,
recovered 9 of its 10, found two it missed, and cost 46% as much. Note that 10/14/13
spread — run-to-run variance is wider than the gap between models, so this says "not
worse for half the price", not "better". Writing a corrected recipe was never measured
on Sonnet; that is why repairs stayed on the generation model.

**Weeks are planned in a Claude Code session first; the app is the backup** (2026-10-07).
A session has no per-call cost, so review can run as often as it is useful. The session
replaces only the model call. Prompt building, checks, review rubric and save path are
the app's own code, run through `cli.py`, so the two paths cannot drift on rules.

**The Pi's data is the only real copy.** A session reads a mirror of it and writes a
week through the Pi's own `data_store` over SSH. Do not sync by copying files in either
direction: the app writes favorites and ratings on the Pi, and the Mac's `data/` is a
test corpus the tests depend on.

**A low rating is feedback, not a deletion** (2026-10-07). The weekly look-back saves
every rated meal as a favorite, 1–2★ included, because the prompt reads 1–2★ as "include
rarely". A disliked *dish* gets a low rating. A disliked *ingredient* gets a constraint,
which the checker enforces.

**Session reviewers: two per dinner, pending measurement** (2026-10-07). This is a bet
based on the 10/14/13 run-to-run spread, not a measured result. `sessions/review-log.md`
records each session's reviewer count, tokens, and how often reviewer B found what A
missed. If B rarely adds anything, go back to one.

---

## Do not repeat

**Do not reintroduce the duplicated pantry-staple rule.**
It was consolidated in Phase 3 (`63b3497`) along with a prompt tightening.

**Watch the no-orange constraint.**
It was violated by a salmon anchor and fixed in `500f1fa`. New anchors need checking
against it — this failure mode has already happened once.

*Update (`71c83bf`): it happened a second time.* Four `orange` mentions across three
anchors' `technique_notes` — the field the earlier fix did not cover, since the notes
are injected into every generation verbatim. This is now checked automatically:
`tests/test_constraint_check.py::test_anchor_recipe_library_has_no_banned_ingredients`
runs the banned-ingredient matcher over the whole anchor library. The lesson generalises
past oranges — **a constraint that lives only in review will be violated again; put it
in a test.**

**Do not measure a repair against "the plan is clean."**
The first repair gate rejected all 10 repairs on the real stored week, because that week
already carries a hard violation (the salmon lunch) and no repair could ever produce a
clean plan. The bar is whether a repair *introduced* something new.

**Prompt caching does not help this workload.** Measured 2026-09-06: ~76% of every
review call is the meal being reviewed, which differs each call; the shared prefixes are
566–759 tokens; the 10,132-token generation prompt runs once a week and is always cold.
Total saving ≈ $0.02/run. Caching the week plan as a shared prefix is *worse* — at
16,871 tokens it is ~9.5× a single meal. Do not revisit without a workload change.
