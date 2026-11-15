"""
Tests for the snack profile: the user's snacking notes reach the prompt, snacks
carry a portion and a weekly count, those fields render, and the preference can
be set the way the Pi will set it.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cli  # noqa: E402
import data_store  # noqa: E402
import pdf_render  # noqa: E402
import pi_ops  # noqa: E402
import shopping  # noqa: E402
from system_prompt import build_system_prompt  # noqa: E402
from test_cli import sandbox  # noqa: E402,F401  (fixture)

NOTES = "Salty before lunch at work; sweet around 10pm at home. I like pistachios."

SNACK = {
    "name": "Lightly salted pistachios",
    "description": "Pre-bag the week on Sunday.",
    "when": "Before lunch, at work",
    "portion": "1 oz in-shell (about 49 nuts), ~160 kcal",
    "portions_per_week": 5,
    "ingredients": [{"name": "lightly salted in-shell pistachios", "quantity": "16",
                     "unit": "oz bag", "pantry_staple": False}],
}


def test_notes_reach_the_prompt_and_switch_to_one_snack_per_time():
    prompt = build_system_prompt(snack_notes=NOTES)
    assert NOTES in prompt
    assert "one for each snack time" in prompt
    assert "portions_per_week" in prompt


def test_without_notes_the_prompt_keeps_general_suggestions():
    prompt = build_system_prompt()
    assert "2–3 suggestions for the week" in prompt
    assert "Portions are the point" in prompt      # portion rules apply either way


def test_snacks_off_removes_notes_and_schema():
    prompt = build_system_prompt(snack_notes=NOTES, include_snacks=False)
    assert NOTES not in prompt
    assert '"snacks"' not in prompt


def test_snack_ingredients_reach_the_shopping_list():
    sections = shopping.build_shopping_list({"dinners": [], "lunches": [], "snacks": [SNACK]})
    names = [i.name for items in sections.values() for i in items]
    assert "lightly salted in-shell pistachios" in names


def test_pdf_shows_portion_and_time_and_tolerates_old_snacks():
    html = pdf_render._snacks_section([SNACK, {"name": "Old snack", "description": "x"}])
    assert "Before lunch, at work" in html
    assert "~160 kcal" in html and "5 this week" in html
    assert "Old snack" in html


def test_set_snack_notes_writes_and_clears(sandbox):  # noqa: F811
    assert pi_ops.set_snack_notes(data_store, {"snack_notes": f"  {NOTES} "}) == {"snack_notes": NOTES}
    assert data_store.load_preferences()["snack_notes"] == NOTES
    pi_ops.set_snack_notes(data_store, {"snack_notes": ""})
    assert data_store.load_preferences()["snack_notes"] is None


def test_cli_set_snacks_offline(sandbox, capsys):  # noqa: F811
    assert cli.main(["set-snacks", NOTES, "--offline"]) == 0
    assert data_store.load_preferences()["snack_notes"] == NOTES
    assert "pistachios" in capsys.readouterr().out
