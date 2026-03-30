# Meal Planner Project — Planning Document

## Overview

A weekly meal planning and shopping list Streamlit app. Uses Claude API to dynamically generate meal plans rather than a static recipe database. Covers 5 dinners/week (family of 4) and 5 lunches/week (1 adult, office). Incorporates a Mediterranean diet framework with health-specific constraints.

---

## User Profile

- **Household:** 2 adults + 5-year-old + 2-year-old
- **Health Goals:**
  - Lose weight (caloric awareness, high satiety foods)
  - Reduce LDL cholesterol, raise HDL cholesterol
  - Manage uric acid levels (gout prevention)
- **Diet Framework:** Mediterranean diet
- **Adults' flavor tolerance:** Adventurous — open to bold Mediterranean flavors (harissa, za'atar, preserved lemon, etc.)
- **Kids' flavor tolerance:** Mild — same dishes but scaled back on spice/bold flavors
- **Weekly budget:** No fixed budget currently; make it a configurable app setting for future use

---

## Food Constraints

| Constraint | Who | Severity |
|---|---|---|
| No mushrooms | Adults (user preference) | Hard no |
| No oranges | Wife | Hard no (lemons/limes fine) |
| No fish reheated at work | User (lunch) | Hard rule — no fish-based lunches |
| Fish at dinner | All | Fine and encouraged (2–3x/week) |

**Note:** More constraints may be added over time. The app must have an easy way to add/remove food constraints that Claude sees when generating plans.

---

## Meal Requirements

### Lunch
- **Who:** 1 adult (user only); make this a configurable setting (1 or 2 adults)
- **Where:** Office, weekdays (Mon–Fri), 5 lunches/week
- **Reheating:** Microwave only — but cold lunches are also acceptable if they taste good and are easy
- **Hard rule:** No fish that needs to be reheated (fish smell at office); cold fish dishes like a salad with canned salmon are borderline — avoid to be safe
- **Goal:** Tasty and easy. Minimal effort at lunchtime.
- **Source:** Mix of standalone lunch recipes + dinner leftovers (app decides which approach is easier per meal)

### Dinner
- **Who:** Family of 4 (2 adults + 5yo + 2yo)
- **Nights/week:** 5 (Mon–Fri assumed; weekends free)
- **Cook time:** 30–45 minutes max for most meals (some flexibility for slow cooker / Instant Pot set-and-forget)
- **Style:** Mediterranean-leaning, kid-adaptable (same dish, milder for kids)
- **Ingredient rule:** Special/non-staple ingredients must appear in 2+ meals that week

### Sunday Meal Prep
- User is open to Sunday prep to speed up weeknights
- Items prepped Sunday must hold well through the week without becoming mushy or degrading
- **Good Sunday prep candidates:** Cooked grains (farro, quinoa, barley — hold 5 days), marinated proteins (raw in fridge 2–3 days), roasted garlic, homemade sauces (tzatziki, hummus), washed/chopped vegetables, cooked lentils
- **Avoid Sunday-prepping:** Cooked fish (degrades fast), delicate greens (wilt), anything starchy that gets gummy (mashed potato, couscous)
- Claude should suggest a short Sunday prep list alongside each weekly plan

---

## Equipment Available

| Equipment | Use Cases |
|---|---|
| Normal oven | Roasting, baking, sheet pan meals |
| Convection toaster oven | Smaller sheet pan meals, faster roasting, reheating |
| Instant Pot / pressure cooker | Fast legume cooking, soups, stews, grains, pulled chicken |
| Slow cooker / Crockpot | Set-and-forget soups, stews, braises — can start before work |
| Gas grill | Grilled proteins and vegetables; weekend and weeknight |
| Blackstone griddle | Stir-fries, smash burgers, quesadillas, searing fish/chicken |
| Rice cooker | Hands-free grains |
| Microwave | Reheating only (not cooking) |

**Planning implications:**
- Instant Pot dramatically expands what fits in 30–45 min (dried beans in 25 min, stews in 20 min)
- Slow cooker allows true set-and-forget weeknight meals (start in morning, dinner ready at 6pm)
- Blackstone is excellent for quick high-heat protein cooking (faster than oven for fish, chicken)
- Gas grill adds smokiness and variety; good for summer weeknights

---

## Health Constraints

### Weight Loss
- High-fiber, high-protein, low-glycemic meals
- Olive oil as primary fat
- Limit refined carbs, added sugars, fruit juice (fructose raises uric acid AND causes weight gain)
- High-satiety foods: legumes, oats, Greek yogurt, avocado, nuts

### Cholesterol (Lower LDL / Raise HDL)
**Emphasize:**
- Salmon 2–3x/week (best fish for omega-3/HDL; acceptable uric acid impact)
- Cod, white fish (low purine; lean protein)
- Extra virgin olive oil (HDL, anti-inflammatory)
- Lentils, chickpeas, white beans (soluble fiber → LDL reduction)
- Oats, barley (beta-glucan → strongest dietary LDL intervention)
- Walnuts, almonds, flaxseed, chia seeds
- Low-fat Greek yogurt
- Avocado

**Limit:**
- Red meat: max 1–2x/week, lean cuts, small portions
- Full-fat dairy: use low-fat versions
- Eggs: fine in moderation (1–2/day), not a daily concern

### Uric Acid Management

**Avoid entirely:**
- Organ meats
- Processed/deli meats
- Beer; all alcohol (raises uric acid via renal pathway)
- Fruit juice, HFCS, sweetened drinks (fructose triggers uric acid independently of purines)
- Meat/fish stocks and gravies (purines concentrate in boiling liquid)

**Limit:**
- Sardines, anchovies — occasional only (1–2x/month), not weekly
- Mackerel, herring — same
- Canned tuna — max 2–3x/week
- Shellfish — 1–2x/week fine
- Red meat — max 1–2x/week

**Actively emphasize (lower uric acid):**
- Cherries / unsweetened tart cherry juice
- Low-fat dairy (Greek yogurt, low-fat milk) — one of the strongest interventions
- Vitamin C foods: bell peppers, tomatoes, strawberries, lemon
- Celery
- Adequate hydration; lemon water daily
- Coffee (both caffeinated and decaf help)

**Corrected outdated guidance:**
- Spinach and leafy greens — safe; plant-source purines do not raise uric acid meaningfully
- Legumes — safe; current evidence shows legume purines are not a concern; fiber benefit far outweighs risk

**Cooking technique:** Boiling/poaching meat or fish and discarding the liquid reduces purine load in the protein. Claude should suggest this technique for relevant recipes.

---

## Key Protein Reference

| Protein | Weight | LDL/HDL | Uric Acid | Notes |
|---|---|---|---|---|
| Low-fat Greek yogurt | ✓✓ | ✓ | ✓✓ | Best triple-win; use often |
| Lentils / chickpeas / white beans | ✓✓ | ✓✓ | ✓✓ | Vegetable purines are safe |
| Salmon | ✓✓ | ✓✓ | ✓ | 2–3x/week target |
| Cod / white fish | ✓✓ | ✓ | ✓✓ | Low purine; lean |
| Chicken breast (skinless) | ✓✓ | ✓ | ✓ | Lean, versatile |
| Turkey breast (skinless) | ✓✓ | ✓ | ✓✓ | Slightly lower purine than chicken |
| Eggs | ✓✓ | ✓ | ✓✓ | Fine in moderation |
| Shrimp | ✓✓ | ✓ | ~ | Moderate purine; 1–2x/week |
| Walnuts / almonds | ✓ | ✓✓ | ✓✓ | Excellent for cholesterol |
| Red meat (lean) | ~ | ~ | ~ | 1–2x/week max |
| Sardines | ~ | ✓✓ | ✗ | Occasional only |
| Canned tuna | ✓ | ✓ | ~ | Max 2–3x/week |

---

## Best Grains and Carbs
- **Oats** — gold standard for LDL; very filling; low purine
- **Barley** — beta-glucan; great in soups and grain bowls
- **Bulgur wheat** — base of tabbouleh; low glycemic, low purine
- **Farro** — hearty, high fiber; excellent for grain bowls
- **Quinoa** — complete protein, low purine, low glycemic
- **Brown / wild rice** — family-friendly; rice cooker-friendly
- **Legume-based pasta** (lentil, chickpea) — higher protein + fiber than wheat pasta
- **Whole wheat pita / pasta** — acceptable in moderation
- **Sweet potato** — soluble fiber, filling, low-medium glycemic

---

## Mediterranean Pantry Staples (Always On Hand — Never on Weekly Shopping List)

**Oils:** Extra virgin olive oil

**Canned/Jarred:** Chickpeas, cannellini/white beans, dry or canned lentils, diced tomatoes, tomato paste, roasted red peppers, kalamata olives, capers

**Grains:** Rolled oats, dry red and green lentils, quinoa, farro or barley, whole wheat pasta, brown rice

**Nuts/Seeds:** Walnuts, almonds, ground flaxseed, chia seeds

**Spices:** Oregano, cumin, coriander, smoked paprika, turmeric, cinnamon, red pepper flakes, thyme, rosemary, bay leaves

**Condiments:** Red wine vinegar, Dijon mustard, tahini

**Semi-stable produce:** Garlic, onions, lemons

**Freezer:** Salmon fillets, cod fillets, edamame, frozen spinach, frozen peas, frozen berries

---

## App Features

### Core (Build First)

1. **Weekly plan generator**
   - Input: user preferences, constraints, history, favorites, optional budget
   - Output: 5 lunches + 5 dinners for the week
   - Claude generates all meals dynamically given a rich system prompt
   - Meals not assigned to days — user assigns days manually

2. **Sunday prep list**
   - Alongside the weekly plan, Claude generates a short Sunday prep checklist
   - Items must hold well 5 days; app flags poor candidates (fish, couscous, delicate greens)

3. **Shopping list**
   - Aggregates all ingredients from the week's meals
   - Deduplicates and consolidates quantities
   - Removes pantry staples (configurable staples list)
   - Groups by store section: Produce / Proteins / Dairy & Eggs / Pantry / Frozen / Other
   - Printable / copyable as clean text

4. **Meal history tracker**
   - JSON store of past weekly plans
   - Passed to Claude as context: "These meals were made in the last 6 weeks — avoid repeating exact recipes; variations are okay"

5. **Favorites manager**
   - Save any meal as a favorite from the current week's plan
   - Rate meal (1–5 stars)
   - Add feedback tags: "kids loved it", "too time-consuming", "great leftover", "make again soon", etc.
   - Written feedback can be free-form; Claude uses this to improve future suggestions
   - Claude works favorites in naturally based on recency and ratings — app decides when to surface them

6. **Constraints manager**
   - UI to add/remove/toggle food constraints
   - All active constraints passed to Claude in every generation call
   - Configurable: number of adults bringing lunch (currently 1), weekly budget (currently unset)

### Nice to Have (Later)

7. **Swap / regenerate single meal** — replace one meal without regenerating the whole week
8. **Nutritional summary** — estimated calories, protein, fiber per day
9. **Budget mode** — when budget is set, Claude favors cost-efficient meals and flags expensive ingredient weeks

---

## Leftover Lunch Strategy

- App (Claude) decides the easiest approach per meal:
  - **Cook extra at dinner** — for meals where scaling is trivial (sheet pan, grain bowls, soups)
  - **Batch cook separately** — for meals where extra portions don't follow naturally
- Claude includes explicit instructions in each recipe: "To yield 1 adult lunch: cook X extra of Y"
- Leftover-based lunches must be confirmed microwave-safe (no fish, nothing that degrades badly)
- Cold lunches are valid (grain salads, wraps, hummus plates) and don't need a reheat note

---

## Claude System Prompt Design (Core of the App)

The system prompt passed to Claude on every generation call will encode:
- All health constraints (with nuance — e.g., legumes are fine, sardines are not)
- All food constraints (mushrooms, no orange, no reheated fish at work)
- Household profile (adults adventurous; kids need milder versions of same dish)
- Equipment available (Instant Pot, slow cooker, Blackstone, grill, etc.)
- Ingredient efficiency rules (special ingredients must appear 2+ times)
- Pantry staples list (do not put these on shopping list)
- Recent meal history (last 6 weeks, passed dynamically)
- Active favorites with ratings and feedback
- Sunday prep guidance (what keeps well; suggest a prep list)
- Output format spec (structured JSON for parsing into UI)

---

## Tech Stack

- **Language:** Python 3.13
- **UI:** Streamlit
- **AI:** Claude API (claude-sonnet-4-5 or newer for balance of quality and cost)
- **Data:** JSON files — `history.json`, `favorites.json`, `constraints.json`, `preferences.json`
- **Dependencies:** anthropic, streamlit, pandas
- **Environment:** `.venv/` in project root

---

## Planned Features (Not Yet Built)

### Weekly Email Report

#### Purpose
After generating a week's plan, send a rich email to the user that serves as the week's reference document — readable at a glance, with enough context to make the week feel approachable and motivating.

#### Email Content (Ordered)

1. **Subject line**
   `Your Mediterranean Meal Plan — Week of [date]`

2. **Claude-written intro** (3–5 sentences)
   - What's interesting or cohesive about this week's plan
   - Any standout flavor themes (e.g., "This week leans into North African spicing with za'atar and harissa appearing across three meals")
   - One motivational health note tied to specific meals ("Salmon appears twice this week, which means strong omega-3 coverage for HDL — one of the highest-impact dietary changes for cholesterol")

3. **Week at a Glance** (stats strip)
   - Fish meals / Red meat meals / Vegetarian meals
   - Special ingredients purchased this week
   - Sunday prep time estimate (rough total)

4. **Sunday Prep List**
   - Each task as a checkbox-style item
   - Storage note for each item

5. **Dinners** (one card per meal — not assigned to days)
   - Meal name + cook time + equipment
   - Claude-written 2–3 sentence description: what it tastes like, why it was chosen, what makes it interesting
   - Health highlights (brief)
   - Kid adaptation note
   - Uric acid tip (if applicable)
   - "Generates lunch" note if relevant

6. **Lunches** (one card per meal)
   - Meal name + source (standalone or leftover from which dinner)
   - Reheat method
   - Pack instructions
   - Brief health note

7. **Shopping List**
   - Grouped by store section (Produce / Proteins / Dairy & Eggs / Pantry / Frozen / Other)
   - Clean, scannable format

8. **Health Win of the Week** *(Claude-generated, my addition)*
   - One paragraph highlighting the single most impactful nutritional choice in this week's plan
   - Tied to the user's specific goals (e.g., "The barley in Thursday's soup combined with the lentil-based Tuesday dinner gives you two high-beta-glucan meals this week — this combination is clinically the strongest dietary approach to LDL reduction")
   - Helps the user feel the health progress is real and understood, not just "eat healthy"

9. **Uric Acid Notes Summary** *(my addition)*
   - Consolidates all `uric_acid_tip` fields from the week into one section
   - Reminds of any technique to apply (e.g., discard poaching liquid)
   - Reinforces the positive: "Greek yogurt appears in 3 meals this week — one of the best evidence-based interventions for lowering serum uric acid"

10. **Equipment lineup** *(my addition — practical value)*
    - A quick "what you'll use this week" note:
      `Mon: sheet pan / oven | Tue: Instant Pot | Wed: slow cooker | Thu: Blackstone | Fri: gas grill`
    - Helps with mental prep — user knows which nights are faster vs. more involved

11. **Footer**
    - "Generated by Mediterranean Meal Planner on [date]"
    - Link to open the app (if hosted) or just a note

#### Technical Design

**Email generation approach:** Two-phase Claude call
- Phase 1: `generate_week_plan()` — the existing meal plan JSON (already done)
- Phase 2: `generate_email_narrative(week_plan)` — a separate, shorter Claude call that takes the structured JSON and writes the human-readable narrative sections (intro, per-meal descriptions, health win, uric acid summary). This keeps concerns clean: the first call generates structured data; the second call writes prose.

**Email format:** HTML email with inline CSS
- Clean, readable on both desktop and mobile
- Section headers, subtle dividers, light color accents
- Shopping list in a monospace-style block for easy scanning
- Sunday prep as styled checklist items

**Email sending:** Python `smtplib` (built-in, no extra dependency)
- Works with Gmail (App Password), iCloud Mail, or any SMTP server
- Credentials stored in environment variables (never in `data/` JSON files)
- Future upgrade path: Resend or SendGrid API for better deliverability, but smtplib is sufficient for personal use

**When to send:**
- Manual: "Send Weekly Email" button in the Generate tab, after a plan is generated
- Automatic (optional, configurable): send automatically when "Generate Week" is clicked

**New files needed:**
- `email_report.py` — narrative generation (Claude call) + HTML template assembly + SMTP sending
- Update `schemas.py` — add `EmailConfig` TypedDict
- Update `data/preferences.json` — add `recipient_email`, `auto_send_email` fields
- Update `app.py` — "Send Email" button in Generate tab + email config in Settings tab

**Environment variables (never stored in JSON):**
```
SMTP_HOST       e.g. smtp.gmail.com
SMTP_PORT       e.g. 587
SMTP_USER       e.g. youraddress@gmail.com
SMTP_PASSWORD   App Password (not your Google login password)
```

**New preference fields (added to `data/preferences.json`):**
```json
{
  "recipient_email": "you@example.com",
  "auto_send_email": false
}
```

#### Decisions

| Question | Decision |
|---|---|
| Email service | Gmail via SMTP with App Password |
| Trigger | Manual "Send Weekly Email" button only (so adjustments can be made before sending) |
| Recipients | User only for now (expandable later) |
| Narrative tone | Structured and informative |
| Content | Full content list above is approved — nothing removed |

#### SMTP Setup (Gmail)
Gmail requires an App Password (not your regular login password) when using SMTP with 2FA enabled.
Setup steps (user does this once, outside the app):
1. Go to Google Account → Security → 2-Step Verification → App Passwords
2. Create an App Password named "Meal Planner"
3. Copy the 16-character password
4. Set environment variables:
   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=youraddress@gmail.com
   SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx   # the App Password
   ```

---

### Swap a Meal

#### Purpose
Allow the user to replace a single dinner or lunch from the current week without regenerating the entire plan. Useful when a meal doesn't appeal, an ingredient is unavailable, or the cook time doesn't fit a particular night.

#### Behavior
- Every meal card in the Generate tab has a "Swap this meal" button
- Optional free-text reason field: "too time-consuming", "missing an ingredient", "kids won't eat it", etc.
  - The reason is passed to Claude to guide the replacement choice
- Claude generates exactly one replacement meal that:
  - Fits the same slot type (dinner → dinner, lunch → lunch)
  - Does not repeat any other meal already in the current week
  - Does not repeat meals from recent history
  - Respects all health constraints and food constraints
  - Respects ingredient efficiency — if the swapped meal shared a special ingredient with another meal, Claude tries to maintain that overlap or adjust
- The replacement is swapped into `session_state.week_plan` in place of the original
- Shopping list auto-rebuilds from the updated plan
- The swapped-out meal is **not** added to history (it was never made)

#### Dinner → Lunch Link
If a dinner has `generates_lunch: true`, swapping that dinner may orphan the paired lunch. Two options:
- Claude's replacement also generates a lunch (preferred — keep the pairing intact)
- If not, the paired lunch is flagged in the UI: "This lunch came from a meal that was swapped — you may want to swap it too"

#### Technical Design
- New function: `swap_meal(week_plan, meal_id, reason=None)` in `meal_planner.py`
- Separate, focused Claude call with:
  - The meal being replaced (name + type)
  - All other meals already in the current week (so Claude avoids conflicts)
  - Recent history (last 6 weeks)
  - Active constraints and preferences
  - Optional swap reason
  - Instruction to return a single meal JSON object matching the same schema (DinnerMeal or LunchMeal)
- Response is parsed and inserted into `week_plan["dinners"]` or `week_plan["lunches"]` at the same index
- `week_summary` is regenerated client-side after the swap (fish count, special ingredients, etc.)

#### New files / changes needed
- `meal_planner.py` — add `swap_meal()` function
- `app.py` — add "Swap this meal" button + optional reason input to each meal card expander; trigger shopping list rebuild after swap

#### Decisions

| Question | Decision |
|---|---|
| Swap reason format | Preset options: "Too time-consuming", "Missing ingredient", "Kids won't eat it", "Want something lighter" + "Other" with free-text field that appears when "Other" is selected |
| Dinner-lunch pairing | Auto-swap the paired lunch immediately when its source dinner is swapped — no user prompt needed |

---

### Nutritional Estimates

#### Purpose
Give the user a rough sense of the caloric and macronutrient profile of each meal and the week overall — not as a rigid diet tracker, but as a sanity-check and motivational signal toward weight loss and protein goals.

#### What's Estimated
Per adult serving, per meal:
- Calories (kcal)
- Protein (g)
- Fiber (g)
- Fat (g) — broken into total fat and a note if saturated fat is notable

These are Claude's estimates, not precise nutritional analysis. A disclaimer is shown: *"Estimates are approximate (±15–20%). Actual values vary by exact portions and ingredient brands."*

Weekly aggregate view:
- Average daily calories (1 dinner + 1 lunch, which is what the app tracks)
- Total weekly protein, fiber
- A simple "on track" signal relative to rough targets (e.g., >25g fiber/day, >100g protein/day for an active adult)

#### Implementation Approach
Include nutrition estimates in the **original generation call** — add a `nutrition_estimate` object to `DinnerMeal` and `LunchMeal` in the output schema. This avoids an extra API call and keeps data co-located with the recipe.

No separate on-demand call. Nutrition is always present.

#### Schema additions
```
DinnerMeal / LunchMeal gain:
  nutrition_estimate: {
    calories_per_adult_serving: int,
    protein_g: int,
    fiber_g: int,
    fat_g: int,
    saturated_fat_note: str | null   # e.g. "Low (salmon fat is mostly unsaturated)" or null
  }
```

#### UI placement
- Collapsed by default in each meal card — a "Nutrition estimate" expander at the bottom
- Weekly summary strip in Generate tab gains a nutrition column: avg daily cal, protein, fiber
- Weekly email includes nutrition estimates in each meal card and in the Week at a Glance stats strip

#### Changes needed
- `schemas.py` — add `NutritionEstimate` TypedDict; update `DinnerMeal` and `LunchMeal`
- `system_prompt.py` — add `nutrition_estimate` to `OUTPUT_SCHEMA` and instruct Claude to include it
- `app.py` — add nutrition expander to meal cards; add nutrition to week summary strip
- `email_report.py` — include nutrition in meal cards and stats (when built)

#### Decisions

| Question | Decision |
|---|---|
| "On track" indicator | Yes — show it. Targets calculated from user stats (see below). Adjustable in Settings. |
| Nutrition in email | Yes — include a small nutrition summary section in the weekly email |

#### Calorie & Macro Targets (calculated from user stats)

User profile: 230 lbs current, 190 lbs goal, male. Target: ~1 lb/week loss.

- **Total daily calorie target:** ~2,100 kcal (500 cal deficit from estimated TDEE of ~2,600 for a moderately active man at 230 lbs)
- **App covers lunch + dinner only.** Breakfast and snacks (~600–700 kcal) are outside scope.
- **Lunch + Dinner calorie target:** ~1,400–1,500 kcal/day
- **Protein target (L+D):** ≥100g (supports ~130g/day total — 0.7g per lb of goal body weight, muscle-preserving during weight loss)
- **Fiber target (L+D):** ≥18g (supports ≥25g/day total)

UI note: The "on track" strip shows a small status indicator per metric. A disclaimer reads: *"Targets cover lunch and dinner only. Breakfast and snacks are not tracked by this app."* All targets are editable in Settings.

---

### Season / Weather Awareness

#### Purpose
Subtly adapt the week's meals to the time of year — lighter, fresher dishes in summer; heartier, warming dishes in fall and winter. This makes the plan feel natural rather than suggesting a heavy braise in July or a cold salad plate in January.

#### Behavior
The current date is already available to the app. Claude uses it to:
- **Summer (Jun–Aug):** Favor grilling, cold lunches, lighter proteins (fish, shrimp), raw/fresh vegetable preparations, minimal oven use on hot nights
- **Fall (Sep–Nov):** Introduce heartier soups, roasted root vegetables, slow cooker meals, warming spices (cinnamon, cumin, smoked paprika in stews)
- **Winter (Dec–Feb):** Lean into Instant Pot and slow cooker, braises, lentil and bean stews, baked dishes; fewer cold lunches
- **Spring (Mar–May):** Fresh herbs, lighter proteins, transitional dishes; mix of warm and cold

**Manual override:** A free-text "weather/mood" field in the Generate tab (optional). Examples:
- "It's been cold and rainy all week — give us something hearty"
- "Hot week, keep it light and minimal oven time"
- "Busy week — prioritize the fastest meals"

This field is passed directly to Claude as additional context, giving the user a natural language dial beyond what the calendar season provides.

#### Implementation
Simple — no new API calls, no external weather service.

Changes to `build_system_prompt()`:
- Add `current_date` parameter (passed as `date.today().isoformat()` from `app.py`)
- Add `weather_note` parameter (optional free-text, from the UI)
- Add a season-awareness section to the static prompt

Season is derived from the date server-side using simple month ranges (Northern Hemisphere assumed).

Helper function:
```python
def get_season(date_str: str) -> str:
    month = int(date_str[5:7])
    if month in (12, 1, 2):  return "winter"
    elif month in (3, 4, 5): return "spring"
    elif month in (6, 7, 8): return "summer"
    else:                    return "fall"
```

#### Changes needed
- `system_prompt.py` — add `current_date`, `season`, and `weather_note` parameters to `build_system_prompt()`; add a season-awareness section to the prompt
- `app.py` — add optional "Any notes for this week?" text input on the Generate tab (feeds `weather_note`); pass `date.today().isoformat()` to the prompt builder
- `meal_planner.py` — pass `current_date` and `weather_note` through to `build_system_prompt()`

#### Decisions

| Question | Decision |
|---|---|
| Hemisphere | Northern Hemisphere — user is in Atlanta, GA |
| Field label | "Notes for this week" — broad enough to cover weather, schedule, mood, or anything else |

**Atlanta, GA seasonal context** (added to prompt guidance):
- Summers are hot and humid (Jun–Sep) — strong lean toward grilling, Blackstone, and cold lunches; minimize oven use
- Winters are mild but variable (Dec–Feb) — slow cooker and Instant Pot meals feel appropriate; occasional cold snaps warrant heartier dishes
- Spring and fall are pleasant — balanced mix of techniques

---

### Kid Meal Notes Export

#### Purpose
A simplified, printable/shareable one-pager summarizing the week's dinners for a babysitter, family member, or caregiver who will be making dinner for the kids. Strips out all the adult complexity — health context, adult flavors, nutrition estimates — and focuses purely on what the kids eat and how to prepare it simply.

#### Content (per dinner)
- Meal name (simplified if needed — "Chicken and Rice Bowls" not "Greek Lemon Chicken with Farro and Tzatziki")
- Kid adaptation: what's different for the kids (sauce on the side, no chili flakes, etc.)
- Simple ingredients list for kids' portions only (2 kids, appropriate sizes)
- Reheating/preparation instructions written for a non-cook — short, plain language:
  - "Heat the chicken in a pan for 5 minutes on medium heat until warm throughout"
  - Not "sauté over medium-high heat until internal temperature reaches 165°F"
- Any allergy/constraint notes relevant to the kids (no mushrooms)
- Approximate kid-friendly cook/reheat time

#### Header section
- Week of [date]
- Family constraints relevant to kids: no mushrooms
- Emergency note placeholder: "If kids won't eat [X], backup option: [Y]" (user fills in manually)

#### Language simplification
A **separate Claude call** is used to rewrite instructions in plain, babysitter-friendly language. The existing `instructions` array from `DinnerMeal` is written for competent home cooks — a babysitter may not know what "deglaze with stock" or "bloom the spices" means. Claude rewrites each dinner's kid portion instructions in 3–5 plain steps.

This call is lightweight — no health analysis needed, just a language simplification pass.

#### Output formats
- **Copy as text** — plain text block, copyable from the app (same pattern as shopping list)
- **Download as .txt** — download button
- Future: PDF export (not in initial implementation)

#### UI placement
A "Export Kid Notes" button in the Generate tab, below the dinners section. Only appears once a plan has been generated.

#### Changes needed
- `meal_planner.py` — add `generate_kid_notes(week_plan)` function (Claude call for language simplification)
- `app.py` — add "Export Kid Notes" button in Generate tab; display/copy panel
- No schema changes needed (uses existing `DinnerMeal` fields: `kid_adaptation`, `ingredients`, `instructions`, `cook_time_minutes`, `name`)

#### Decisions

| Question | Decision |
|---|---|
| Scope | Dinners only — lunches are irrelevant for kids |
| Instruction simplification | Claude call — better quality output; lightweight and worth the small cost for a babysitter-facing document |
| Notes line | Yes — include a blank "Notes:" line at the bottom of each meal card for handwritten caregiver notes |

---

## Build Order

1. Design and finalize Claude system prompt (most critical piece)
2. Design JSON schemas for history, favorites, constraints, preferences
3. Build Streamlit UI skeleton (tabs: Generate / Shopping List / Favorites / Settings)
4. Implement plan generation flow with Claude API
5. Implement shopping list aggregation + formatting
6. Add Sunday prep list output
7. Add meal history persistence
8. Add favorites + rating system
9. Add constraints manager UI
10. Test full weekly flow end-to-end
11. Iterate based on real usage
