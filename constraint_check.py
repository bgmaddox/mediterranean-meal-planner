"""
constraint_check.py
-------------------
Deterministic post-generation checking of a WeekPlan.

The system prompt *asks* Claude to respect the food constraints and the sodium
ceiling. Nothing verified that it did — a plan containing mushrooms passed
validation. This module does the verifying, in Python, where the answer is not
a matter of the model's judgment.

Two severities:

  hard  — an unambiguous violation of a rule the household treats as absolute
          (banned ingredient present, fish in a lunch, sodium over the ceiling).
          `meal_planner` rejects the plan and regenerates, feeding the reason back.
  soft  — worth surfacing but not worth burning another generation on
          (over the sodium *target* but under the ceiling, a near-repeat of a
          recent week, shellfish at lunch).

Everything here is text matching over ingredient names, meal names, and
instructions. That is a blunt instrument, so the term lists below are
deliberately conservative: a term earns `hard` only when a false positive is
implausible. Anything fuzzier is `soft`.
"""

from __future__ import annotations

import difflib
import re
from typing import Iterable, Literal, Optional, TypedDict

from schemas import WeekPlan, MealHistoryEntry
from system_prompt import SODIUM_HARD_CEILING_MG, SODIUM_TARGET_DEFAULT_MG


Severity = Literal["hard", "soft"]


class Violation(TypedDict):
    code: str            # machine-readable kind, e.g. 'banned_ingredient'
    severity: Severity
    meal_id: str         # '' for week-level findings
    meal_name: str
    message: str         # human-readable, and readable enough to send back to Claude


# ─────────────────────────────────────────────────────────────────────────────
# TERM LISTS
# Each entry maps a banned concept to the surface forms that identify it.
# Matching is word-boundary and plural-tolerant ('shiitake' matches 'shiitakes').
# ─────────────────────────────────────────────────────────────────────────────

MUSHROOM_TERMS = [
    "mushroom", "shiitake", "portobello", "portabella", "cremini", "crimini",
    "porcini", "enoki", "chanterelle", "maitake", "champignon", "duxelles",
]

# 'orange' is also a color, so the bare word is matched only when it is not
# acting as an adjective for a differently-colored-vegetable (see _ORANGE_COLOR_RE).
ORANGE_TERMS = [
    "orange juice", "mandarin", "clementine", "tangerine", "satsuma",
    "orange marmalade",
]
_ORANGE_COLOR_RE = re.compile(
    r"\borange[-\s]+(bell\s+)?(pepper|sweet\s+potato|carrot|cauliflower|tomato|"
    r"squash|lentil|flesh|fleshed|colou?red)",
    re.IGNORECASE,
)
_ORANGE_WORD_RE = re.compile(r"\borange(s|\s+zest|\s+segments|\s+juice)?\b", re.IGNORECASE)

# Fin fish — the office-reheat constraint. Unambiguous species only.
FISH_TERMS = [
    "salmon", "tuna", "cod", "halibut", "tilapia", "trout", "mackerel", "sardine",
    "snapper", "haddock", "swordfish", "mahi mahi", "mahi-mahi", "herring",
    "sea bass", "branzino", "sole fillet", "pollock", "fish fillet",
]
# Present in trace amounts as a seasoning; smells far less. Flagged, not blocking.
FISH_TRACE_TERMS = ["anchovy", "anchovies", "fish sauce", "worcestershire"]
SHELLFISH_TERMS = [
    "shrimp", "prawn", "scallop", "crab", "lobster", "mussel", "clam",
    "squid", "calamari", "octopus",
]


def _term_re(term: str) -> re.Pattern:
    """Word-boundary, plural-tolerant matcher for a multi-word term."""
    parts = [re.escape(w) for w in term.split()]
    return re.compile(r"\b" + r"\s+".join(parts) + r"e?s?\b", re.IGNORECASE)


_COMPILED: dict[str, list[tuple[str, re.Pattern]]] = {
    name: [(t, _term_re(t)) for t in terms]
    for name, terms in {
        "mushroom": MUSHROOM_TERMS,
        "orange": ORANGE_TERMS,
        "fish": FISH_TERMS,
        "fish_trace": FISH_TRACE_TERMS,
        "shellfish": SHELLFISH_TERMS,
    }.items()
}


def _find(text: str, group: str) -> Optional[str]:
    """Return the first term from `group` present in `text`, or None."""
    for term, pattern in _COMPILED[group]:
        if pattern.search(text):
            return term
    return None


def _find_orange(text: str) -> Optional[str]:
    """Orange, minus the color-adjective false positives ('orange bell pepper')."""
    hit = _find(text, "orange")
    if hit:
        return hit
    scrubbed = _ORANGE_COLOR_RE.sub(" ", text)
    m = _ORANGE_WORD_RE.search(scrubbed)
    return m.group(0).strip() if m else None


# ─────────────────────────────────────────────────────────────────────────────
# TEXT EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def _ingredient_text(meal: dict) -> str:
    return " ; ".join(str(i.get("name", "")) for i in meal.get("ingredients") or [])


def _prose_text(meal: dict) -> str:
    """Instructions and packing notes — a secondary sweep for ingredients that
    appear in the method but were left off the ingredient list."""
    parts = list(meal.get("instructions") or [])
    for key in ("pack_instructions", "kid_adaptation", "lunch_scaling_instructions",
                "sunday_prep"):
        if meal.get(key):
            parts.append(str(meal[key]))
    return " ; ".join(parts)


def _searchable(meal: dict) -> str:
    return f"{meal.get('name', '')} ; {_ingredient_text(meal)} ; {_prose_text(meal)}"


def _v(code: str, severity: Severity, meal: dict, message: str) -> Violation:
    return {
        "code": code,
        "severity": severity,
        "meal_id": str(meal.get("id", "")),
        "meal_name": str(meal.get("name", "")),
        "message": message,
    }


# ─────────────────────────────────────────────────────────────────────────────
# USER CONSTRAINTS
# Free text like "no shellfish" or "less rice this week". Only the negations we
# can parse with confidence become checks; the rest stay prompt-only and are
# reported as such so they are not silently assumed to be enforced.
# ─────────────────────────────────────────────────────────────────────────────

_NEGATION_RE = re.compile(
    r"^\s*(?:no|avoid|skip|exclude|omit|without)\s+(?P<term>[a-z][a-z\s'-]{1,30}?)"
    r"\s*(?:[.,;—-]|$)",
    re.IGNORECASE,
)
# Words that make a negation about something other than an ingredient.
_NON_INGREDIENT_HINTS = ("meal", "dish", "recipe", "night", "repeat", "more than", "prep")


def parse_user_constraints(texts: Iterable[str]) -> tuple[list[str], list[str]]:
    """
    Split free-text constraints into (enforceable_terms, unenforceable_texts).

    Only leading-negation phrasings yield a term. 'No shellfish' → 'shellfish'.
    'Keep Tuesday fast' → unenforceable, left to the prompt.
    """
    terms: list[str] = []
    unenforceable: list[str] = []
    for text in texts:
        m = _NEGATION_RE.match(text or "")
        term = m.group("term").strip().lower() if m else ""
        if term and not any(h in term for h in _NON_INGREDIENT_HINTS):
            terms.append(term)
        elif (text or "").strip():
            unenforceable.append(text.strip())
    return terms, unenforceable


# ─────────────────────────────────────────────────────────────────────────────
# CHECKS
# ─────────────────────────────────────────────────────────────────────────────

def _check_banned(meal: dict) -> list[Violation]:
    """The three hardcoded household constraints, applied to any meal."""
    out: list[Violation] = []
    text = _searchable(meal)

    hit = _find(text, "mushroom")
    if hit:
        out.append(_v("banned_ingredient", "hard", meal,
                      f"contains a mushroom ingredient ('{hit}') — mushrooms are banned in all forms"))

    hit = _find_orange(text)
    if hit:
        out.append(_v("banned_ingredient", "hard", meal,
                      f"contains orange ('{hit}') — oranges and orange juice are banned "
                      f"(lemon and lime are fine)"))
    return out


def _check_user_terms(meal: dict, terms: list[str]) -> list[Violation]:
    text = _searchable(meal)
    out: list[Violation] = []
    for term in terms:
        if _term_re(term).search(text):
            out.append(_v("user_constraint", "hard", meal,
                          f"contains '{term}', which the user's constraints exclude"))
    return out


def _check_lunch_fish(lunch: dict) -> list[Violation]:
    out: list[Violation] = []
    text = _searchable(lunch)

    hit = _find(text, "fish")
    if hit:
        out.append(_v("fish_in_lunch", "hard", lunch,
                      f"is a fish lunch ('{hit}') — fish is never allowed in lunches "
                      f"(cannot be reheated at the office)"))
    hit = _find(text, "fish_trace")
    if hit:
        out.append(_v("fish_trace_in_lunch", "soft", lunch,
                      f"contains '{hit}' — trace fish seasoning, acceptable but worth noting"))
    hit = _find(text, "shellfish")
    if hit:
        out.append(_v("shellfish_in_lunch", "soft", lunch,
                      f"contains shellfish ('{hit}') — not banned, but reheats poorly at a desk"))
    return out


def _check_sodium(meal: dict, target_mg: int) -> list[Violation]:
    nutrition = meal.get("nutrition_estimate") or {}
    value = nutrition.get("sodium_mg")
    if not isinstance(value, (int, float)):
        return []
    value = int(value)
    if value > SODIUM_HARD_CEILING_MG:
        return [_v("sodium_over_ceiling", "hard", meal,
                   f"is {value} mg sodium per adult serving, over the hard ceiling of "
                   f"{SODIUM_HARD_CEILING_MG} mg — re-engineer the recipe (rinse canned "
                   f"legumes, unsalted broth, less added salt), do not just report a lower number")]
    if value > target_mg:
        return [_v("sodium_over_target", "soft", meal,
                   f"is {value} mg sodium per adult serving, over the {target_mg} mg target "
                   f"(under the {SODIUM_HARD_CEILING_MG} mg ceiling)")]
    return []


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).strip()


def _check_repeats(plan: WeekPlan, history: list[MealHistoryEntry],
                   threshold: float = 0.82) -> list[Violation]:
    """Near-duplicate dinner names against recent weeks, and within the week itself."""
    out: list[Violation] = []
    past = [(_normalize_name(n), n, entry.get("week_start", ""))
            for entry in history for n in entry.get("meal_names") or []]

    seen_this_week: list[tuple[str, str]] = []
    for dinner in plan.get("dinners") or []:
        norm = _normalize_name(dinner.get("name", ""))
        if not norm:
            continue
        for past_norm, past_name, week in past:
            if difflib.SequenceMatcher(None, norm, past_norm).ratio() >= threshold:
                out.append(_v("repeat_from_history", "soft", dinner,
                              f"closely repeats '{past_name}' from the week of {week}"))
                break
        for prev_norm, prev_name in seen_this_week:
            if difflib.SequenceMatcher(None, norm, prev_norm).ratio() >= threshold:
                out.append(_v("repeat_within_week", "hard", dinner,
                              f"duplicates '{prev_name}' already in this same week"))
                break
        seen_this_week.append((norm, dinner.get("name", "")))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def check_week_plan(
    plan: WeekPlan,
    *,
    user_constraints: Optional[list[str]] = None,
    target_sodium_mg: int = SODIUM_TARGET_DEFAULT_MG,
    history: Optional[list[MealHistoryEntry]] = None,
) -> list[Violation]:
    """
    Check a generated plan against the constraints that can be verified in code.

    Parameters
    ----------
    plan : WeekPlan
    user_constraints : list of str, optional
        Active free-text constraints from data/constraints.json. Only parseable
        negations are enforced (see `parse_user_constraints`).
    target_sodium_mg : int
        The user's per-adult-serving sodium target. The hard ceiling is
        SODIUM_HARD_CEILING_MG regardless.
    history : list of MealHistoryEntry, optional
        Recent weeks, for repeat detection.

    Returns
    -------
    list[Violation] — hard violations first, then soft.
    """
    user_terms, _ = parse_user_constraints(user_constraints or [])
    out: list[Violation] = []

    dinners = plan.get("dinners") or []
    lunches = plan.get("lunches") or []
    snacks = plan.get("snacks") or []

    for meal in [*dinners, *lunches, *snacks]:
        out += _check_banned(meal)
        out += _check_user_terms(meal, user_terms)

    for lunch in lunches:
        out += _check_lunch_fish(lunch)

    for meal in [*dinners, *lunches]:
        out += _check_sodium(meal, target_sodium_mg)

    out += _check_repeats(plan, history or [])

    out.sort(key=lambda v: 0 if v["severity"] == "hard" else 1)
    return out


def hard_violations(violations: list[Violation]) -> list[Violation]:
    return [v for v in violations if v["severity"] == "hard"]


def format_violations(violations: list[Violation]) -> str:
    """Render violations as feedback text for the regeneration prompt."""
    lines = []
    for v in violations:
        label = f"{v['meal_id']} '{v['meal_name']}'".strip() if v["meal_name"] else "The plan"
        lines.append(f"- {label} {v['message']}.")
    return "\n".join(lines)
