"""
shopping.py
-----------
Builds the weekly shopping list from a WeekPlan.

Entry point: build_shopping_list(week_plan)
  - Collects all non-pantry-staple ingredients from dinners and lunches
  - Deduplicates by name (case-insensitive)
  - Groups by store section
  - Returns a dict of section → list of ShoppingItem

format_shopping_list_text(sections)
  - Formats the grouped dict into a clean, copyable/printable string
"""

from collections import defaultdict
from schemas import WeekPlan
from system_prompt import PANTRY_STAPLES

# Normalised set for fast lookup
_STAPLES_SET = {s.lower().strip() for s in PANTRY_STAPLES}


# Store sections and the keywords that route ingredients there.
# Order matters — first match wins.
_SECTION_RULES: list[tuple[str, list[str]]] = [
    ("Produce", [
        "tomato", "pepper", "zucchini", "cucumber", "spinach", "kale", "arugula",
        "lettuce", "cabbage", "broccoli", "cauliflower", "asparagus", "artichoke",
        "celery", "carrot", "sweet potato", "potato", "eggplant", "avocado",
        "lemon", "lime", "cherry", "berry", "strawberr", "blueberr", "raspberry",
        "apple", "pear", "grape", "mango", "banana", "orange", "citrus",
        "herb", "dill", "parsley", "basil", "cilantro", "mint", "chive",
        "scallion", "green onion", "leek", "fennel", "beet", "radish",
        "corn", "pea", "edamame", "snap pea", "bok choy",
    ]),
    ("Proteins", [
        "salmon", "cod", "tilapia", "halibut", "flounder", "tuna", "shrimp",
        "chicken", "turkey", "beef", "lamb", "pork", "sausage", "egg",
        "sardine", "anchov", "mackerel", "herring", "scallop", "mussel",
        "tofu", "tempeh", "lentil", "chickpea", "bean",
    ]),
    ("Dairy & Eggs", [
        "yogurt", "milk", "cheese", "feta", "mozzarella", "parmesan",
        "ricotta", "cream", "butter", "egg",
    ]),
    ("Pantry", [
        "pasta", "rice", "bread", "pita", "wrap", "tortilla", "flour", "oat",
        "quinoa", "farro", "barley", "bulgur", "grain", "cereal",
        "oil", "vinegar", "sauce", "paste", "broth", "stock", "can", "jar",
        "nut", "seed", "tahini", "hummus", "olive", "caper",
        "honey", "syrup", "sugar", "salt", "spice", "herb",
        "za'atar", "harissa", "sumac", "ras el hanout", "preserved lemon",
        "pomegranate", "coconut",
    ]),
    ("Frozen", [
        "frozen",
    ]),
]

_DEFAULT_SECTION = "Other"


def _classify(ingredient_name: str) -> str:
    name_lower = ingredient_name.lower()
    for section, keywords in _SECTION_RULES:
        if any(kw in name_lower for kw in keywords):
            return section
    return _DEFAULT_SECTION


class ShoppingItem:
    """A deduplicated ingredient line on the shopping list."""

    def __init__(self, name: str, quantity: str, unit: str):
        self.name = name
        self.quantity = quantity
        self.unit = unit
        # Raw quantity strings from Claude vary; store all and display combined
        self._entries: list[tuple[str, str]] = [(quantity, unit)]

    def merge(self, quantity: str, unit: str) -> None:
        """Record an additional quantity of the same ingredient."""
        self._entries.append((quantity, unit))

    def display_quantity(self) -> str:
        """
        Return a human-readable quantity string.
        If the ingredient appears in multiple meals, list each quantity separately.
        """
        if len(self._entries) == 1:
            q, u = self._entries[0]
            return f"{q} {u}".strip()
        parts = [f"{q} {u}".strip() for q, u in self._entries]
        return " + ".join(parts)

    def __repr__(self) -> str:
        return f"ShoppingItem({self.name!r}, {self.display_quantity()!r})"


def build_shopping_list(
    week_plan: WeekPlan,
) -> dict[str, list[ShoppingItem]]:
    """
    Build a grouped shopping list from a WeekPlan.

    Parameters
    ----------
    week_plan : WeekPlan
        The generated week's meal plan.

    Returns
    -------
    dict[str, list[ShoppingItem]]
        Keys are section names (Produce, Proteins, Dairy & Eggs, Pantry, Frozen, Other,
        and Pantry Staples (check stock)). Values are lists of ShoppingItem, sorted
        alphabetically within each section. Duplicate ingredients are merged.
    """
    # Collect all ingredients (dinners + lunches + snacks)
    all_ingredients = []
    for dinner in week_plan.get("dinners", []):
        all_ingredients.extend(dinner.get("ingredients", []))
    for lunch in week_plan.get("lunches", []):
        if lunch.get("source") != "leftover":
            all_ingredients.extend(lunch.get("ingredients", []))
    for snack in week_plan.get("snacks", []) or []:
        all_ingredients.extend(snack.get("ingredients", []))

    # Deduplicate non-pantry and pantry ingredients separately
    seen: dict[str, ShoppingItem] = {}
    pantry_seen: dict[str, ShoppingItem] = {}
    for ing in all_ingredients:
        name_key = ing["name"].lower().strip()
        if ing.get("pantry_staple", False) or name_key in _STAPLES_SET:
            if name_key in pantry_seen:
                pantry_seen[name_key].merge(ing.get("quantity", ""), ing.get("unit", ""))
            else:
                pantry_seen[name_key] = ShoppingItem(
                    name=ing["name"],
                    quantity=ing.get("quantity", ""),
                    unit=ing.get("unit", ""),
                )
        else:
            if name_key in seen:
                seen[name_key].merge(ing.get("quantity", ""), ing.get("unit", ""))
            else:
                seen[name_key] = ShoppingItem(
                    name=ing["name"],
                    quantity=ing.get("quantity", ""),
                    unit=ing.get("unit", ""),
                )

    # Group non-pantry items by section
    sections: dict[str, list[ShoppingItem]] = defaultdict(list)
    for item in seen.values():
        section = _classify(item.name)
        sections[section].append(item)

    # Sort items within each section alphabetically
    for section in sections:
        sections[section].sort(key=lambda i: i.name.lower())

    # Return sections in display order; pantry staples last as a check-stock section
    ordered_sections = [
        "Produce", "Proteins", "Dairy & Eggs", "Pantry", "Frozen", "Other"
    ]
    result = {s: sections[s] for s in ordered_sections if s in sections}
    if pantry_seen:
        result["Pantry Staples (check stock)"] = sorted(
            pantry_seen.values(), key=lambda i: i.name.lower()
        )
    return result


def format_shopping_list_text(sections: dict[str, list[ShoppingItem]]) -> str:
    """
    Format the shopping list as a clean, copyable plain-text string.

    Parameters
    ----------
    sections : dict[str, list[ShoppingItem]]
        Output of build_shopping_list().

    Returns
    -------
    str
        Multi-line string suitable for copying to a notes app or printing.
    """
    lines = ["SHOPPING LIST", "=" * 30, ""]
    for section, items in sections.items():
        if not items:
            continue
        lines.append(f"[ {section.upper()} ]")
        for item in items:
            lines.append(f"  • {item.name} — {item.display_quantity()}")
        lines.append("")
    return "\n".join(lines).rstrip()
