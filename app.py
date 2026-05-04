"""
app.py
------
Main Streamlit entry point for the Mediterranean Meal Planner.

Run with:
    streamlit run app.py

Tabs:
    Generate        — Generate this week's plan; swap meals; export kid notes
    Shopping List   — Aggregated, grouped shopping list; copyable/downloadable
    Favorites       — Saved meals with ratings and feedback
    Recipe Library  — All ever-generated recipes; search and add any back to the current week
    Settings        — Constraints, preferences, nutrition targets, email config
"""

import copy
import uuid
from datetime import date

import streamlit as st

import data_store
import drive_upload
import meal_planner
import pdf_export
import shopping
from meal_planner import MealPlanError
from schemas import WeekPlan

# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Mediterranean Meal Planner",
    page_icon="🫒",
    layout="wide",
)

st.title("🫒 Mediterranean Meal Planner")

# ─────────────────────────────────────────────────────────────────────────────
# Session state initialisation
# ─────────────────────────────────────────────────────────────────────────────
if "week_plan" not in st.session_state:
    st.session_state.week_plan: WeekPlan | None = None
if "shopping_sections" not in st.session_state:
    st.session_state.shopping_sections = None
if "kid_notes" not in st.session_state:
    st.session_state.kid_notes: str | None = None
if "week_start" not in st.session_state:
    st.session_state.week_start: str = date.today().isoformat()
if "show_prompt_dialog" not in st.session_state:
    st.session_state.show_prompt_dialog: bool = False
if "pending_system_prompt" not in st.session_state:
    st.session_state.pending_system_prompt: str = ""
if "pending_user_message" not in st.session_state:
    st.session_state.pending_user_message: str = ""
if "swapping" not in st.session_state:
    # Tuple of (meal_id, meal_type, meal_name) while dialog is open; None otherwise
    st.session_state.swapping: tuple | None = None
if "deleted_meals" not in st.session_state:
    # List of {"type": "dinner"|"lunch", "meal": dict, "removed_lunches": list}
    st.session_state.deleted_meals: list = []

# ─────────────────────────────────────────────────────────────────────────────
# Tabs
# ─────────────────────────────────────────────────────────────────────────────
tab_generate, tab_shopping, tab_favorites, tab_library, tab_settings = st.tabs(
    ["Generate", "Shopping List", "Favorites", "Recipe Library", "Settings"]
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _rebuild_shopping():
    if st.session_state.week_plan:
        st.session_state.shopping_sections = shopping.build_shopping_list(
            st.session_state.week_plan
        )


def _on_track_icon(value: int, target: int) -> str:
    return "✅" if value >= target else "⚠️"


@st.dialog("Review & Edit Prompt", width="large")
def _prompt_preview_dialog():
    st.caption(
        "Review and edit the prompts before sending. "
        "Changes here are one-off and won't affect future generations."
    )
    system_edited = st.text_area(
        "System Prompt",
        value=st.session_state.pending_system_prompt,
        height=400,
    )
    user_edited = st.text_area(
        "User Message",
        value=st.session_state.pending_user_message,
        height=100,
    )

    col_send, col_cancel = st.columns(2)
    with col_send:
        if st.button("Send to Claude", type="primary", use_container_width=True):
            with st.spinner("Asking Claude to plan your week..."):
                try:
                    plan = meal_planner.generate_week_plan_from_prompts(
                        system_edited, user_edited
                    )
                except MealPlanError as e:
                    st.error(f"Could not generate plan: {e}")
                    plan = None
            if plan is not None:
                st.session_state.week_plan = plan
                st.session_state.week_start = date.today().isoformat()
                st.session_state.kid_notes = None
                _rebuild_shopping()
                data_store.append_to_history(plan)
                data_store.add_recipes_from_plan(plan)
                st.session_state.show_prompt_dialog = False
                st.rerun()
    with col_cancel:
        if st.button("Cancel", use_container_width=True):
            st.session_state.show_prompt_dialog = False
            st.rerun()


SWAP_REASONS = [
    "Too time-consuming",
    "Missing an ingredient",
    "Kids won't eat it",
    "Want something lighter",
    "Other",
]


@st.dialog("Replace Meal", width="small")
def _swap_dialog():
    meal_id, meal_type, meal_name = st.session_state.swapping
    st.markdown(f"Replace **{meal_name}**?")
    reason = st.selectbox(
        "Reason (optional — helps Claude pick something better)",
        options=[""] + SWAP_REASONS,
        index=0,
        label_visibility="collapsed",
        placeholder="Reason for swapping (optional)",
    )
    other_text = ""
    if reason == "Other":
        other_text = st.text_input(
            "Describe the reason",
            placeholder="e.g. 'We already had salmon this week'",
        )
    final_reason = other_text if reason == "Other" else reason

    col_confirm, col_cancel = st.columns(2)
    with col_confirm:
        if st.button("Replace it", type="primary", use_container_width=True):
            with st.spinner(f"Finding a replacement for '{meal_name}'..."):
                try:
                    updated = meal_planner.swap_meal(
                        st.session_state.week_plan, meal_id, meal_type,
                        reason=final_reason,
                    )
                    st.session_state.week_plan = updated
                    st.session_state.swapping = None
                    _rebuild_shopping()
                    data_store.update_history_plan(updated, st.session_state.week_start)
                    st.success("Meal replaced!")
                    st.rerun()
                except MealPlanError as e:
                    st.error(f"Swap failed: {e}")
    with col_cancel:
        if st.button("Cancel", use_container_width=True):
            st.session_state.swapping = None
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Tab: Generate
# ─────────────────────────────────────────────────────────────────────────────
with tab_generate:
    st.header("This Week's Meal Plan")

    # Notes for this week + generate button
    week_notes = st.text_input(
        "Notes for this week (optional)",
        placeholder="e.g. 'Busy week — keep it fast' · 'Hot weather, avoid oven meals' · 'Kids are picky this week'",
        help="Claude will adapt the plan based on your note — weather, schedule, mood, anything.",
    )
    cuisine_notes = st.text_input(
        "Cuisine preferences this week (optional)",
        placeholder="e.g. 'All Mediterranean' · 'One Asian meal' · '2 Mexican-inspired dinners' · 'Surprise me'",
        help="Override the default mix. Leave blank for the usual 3–4 Mediterranean + 1–2 other cuisines.",
    )

    col_btn, col_note = st.columns([1, 4])
    with col_btn:
        generate_clicked = st.button("Generate Week", type="primary", use_container_width=True)
    with col_note:
        st.caption(
            "Claude generates 5 dinners + 5 lunches. Meals aren't assigned to days — "
            "arrange them however suits your week."
        )

    if generate_clicked:
        sys_p, usr_m = meal_planner.build_generation_prompts(
            week_notes or None, cuisine_notes or None
        )
        st.session_state.pending_system_prompt = sys_p
        st.session_state.pending_user_message = usr_m
        st.session_state.show_prompt_dialog = True

    if st.session_state.show_prompt_dialog:
        _prompt_preview_dialog()

    if st.session_state.swapping:
        _swap_dialog()

    # ── Load a previous week ──────────────────────────────────────────────────
    history = data_store.load_history()
    saved_weeks = [e for e in history if e.get("plan")]
    if saved_weeks:
        with st.expander("Load a previous week"):
            for entry in saved_weeks:
                col_label, col_btn = st.columns([5, 1])
                dinner_names = ", ".join(entry.get("meal_names", [])[:3])
                if len(entry.get("meal_names", [])) > 3:
                    dinner_names += ", …"
                col_label.markdown(f"**{entry['week_start']}** — {dinner_names}")
                if col_btn.button("Load", key=f"load_{entry['week_start']}"):
                    st.session_state.week_plan = entry["plan"]
                    st.session_state.week_start = entry["week_start"]
                    st.session_state.kid_notes = None
                    _rebuild_shopping()
                    data_store.add_recipes_from_plan(entry["plan"])
                    st.rerun()

    plan = st.session_state.week_plan

    if plan:
        prefs = data_store.load_preferences()
        summary = plan.get("week_summary", {})

        # ── Week summary strip ────────────────────────────────────────────────
        all_meals = plan.get("dinners", []) + plan.get("lunches", [])
        avg_cal = (
            sum(m.get("nutrition_estimate", {}).get("calories_per_adult_serving", 0)
                for m in all_meals) // len(all_meals)
            if all_meals else 0
        )
        avg_protein = (
            sum(m.get("nutrition_estimate", {}).get("protein_g", 0)
                for m in plan.get("dinners", [])) // max(len(plan.get("dinners", [])), 1)
        )
        avg_fiber = (
            sum(m.get("nutrition_estimate", {}).get("fiber_g", 0)
                for m in plan.get("dinners", [])) // max(len(plan.get("dinners", [])), 1)
        )
        t_cal = prefs.get("target_calories_lunch_dinner", 1450)
        t_prot = prefs.get("target_protein_g", 100)
        t_fib = prefs.get("target_fiber_g", 18)

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Fish meals", summary.get("fish_meal_count", "-"))
        c2.metric("Red meat meals", summary.get("red_meat_meal_count", "-"))
        c3.metric("Vegetarian meals", summary.get("vegetarian_meal_count", "-"))
        weekly_cost = summary.get("estimated_weekly_grocery_cost_usd")
        if weekly_cost:
            c1.metric("Est. grocery cost", f"${weekly_cost:.0f}", help="Non-pantry ingredients only. Publix/Kroger pricing. ±20–30%.")
        c4.metric(
            f"Avg cal/day L+D {_on_track_icon(avg_cal, t_cal)}",
            f"{avg_cal} kcal",
            help=f"Target: {t_cal} kcal",
        )
        c5.metric(
            f"Avg protein/day {_on_track_icon(avg_protein, t_prot)}",
            f"{avg_protein}g",
            help=f"Target: {t_prot}g",
        )
        c6.metric(
            f"Avg fiber/day {_on_track_icon(avg_fiber, t_fib)}",
            f"{avg_fiber}g",
            help=f"Target: {t_fib}g",
        )
        st.caption("Nutrition covers lunch + dinner only. Breakfast and snacks not tracked. Estimates ±15–20%.")

        special = summary.get("special_ingredients", [])
        if special:
            st.caption(f"Special ingredients this week: {', '.join(special)}")
        if summary.get("ingredient_overlap_notes"):
            with st.expander("Ingredient overlap notes"):
                st.write(summary["ingredient_overlap_notes"])

        # ── PDF export + Drive upload ──────────────────────────────────────────
        exp_col1, exp_col2 = st.columns([1, 1])

        with exp_col1:
            if st.button("📄 Build & Download Weekly PDF", use_container_width=True):
                if not st.session_state.shopping_sections:
                    _rebuild_shopping()
                with st.spinner("Building PDF..."):
                    try:
                        pdf_bytes = pdf_export.build_pdf_bytes(
                            plan,
                            st.session_state.shopping_sections or {},
                            week_start=st.session_state.week_start,
                        )
                        st.download_button(
                            "⬇ Download PDF",
                            data=pdf_bytes,
                            file_name=f"meal_plan_{st.session_state.week_start}.pdf",
                            mime="application/pdf",
                            use_container_width=True,
                        )
                    except Exception as e:
                        st.error(f"PDF generation failed: {e}")

        with exp_col2:
            drive_ready = drive_upload.credentials_configured()
            drive_help = (
                "Uploads this week's PDF to the 'Mediterranean Meal Plans' folder in your Google Drive."
                if drive_ready
                else "client_secrets.json not found — see Drive Setup in Settings."
            )
            if st.button(
                "☁ Upload PDF to Drive",
                use_container_width=True,
                disabled=not drive_ready,
                help=drive_help,
            ):
                if not st.session_state.shopping_sections:
                    _rebuild_shopping()
                with st.spinner("Building PDF and uploading to Drive..."):
                    try:
                        pdf_bytes = pdf_export.build_pdf_bytes(
                            plan,
                            st.session_state.shopping_sections or {},
                            week_start=st.session_state.week_start,
                        )
                        filename = f"meal_plan_{st.session_state.week_start}.pdf"
                        url = drive_upload.upload_pdf_to_drive(pdf_bytes, filename)
                        if url:
                            st.success(f"Uploaded! [Open in Drive]({url})")
                        else:
                            st.success("Uploaded to Drive.")
                    except FileNotFoundError as e:
                        st.error(str(e))
                    except RuntimeError as e:
                        st.error(str(e))
                    except Exception as e:
                        st.error(f"Upload failed: {e}")

        st.divider()

        # ── Dinners ──────────────────────────────────────────────────────────
        st.subheader("Dinners")
        for dinner in plan.get("dinners", []):
            did = dinner["id"]

            # Header row: meal name + swap + remove buttons
            hdr_col, swap_col, remove_col = st.columns([8, 1, 1])
            hdr_col.markdown(
                f"**{dinner['name']}** &nbsp;·&nbsp; {dinner['cook_time_minutes']} min"
                f" &nbsp;·&nbsp; {dinner['primary_equipment']}",
                unsafe_allow_html=True,
            )
            if swap_col.button("🔄 Swap", key=f"swap_{did}", use_container_width=True):
                st.session_state.swapping = (did, "dinner", dinner["name"])
            if remove_col.button("✕ Remove", key=f"remove_{did}", use_container_width=True):
                removed_lunches = [l for l in plan["lunches"] if l.get("leftover_from_dinner_id") == did]
                st.session_state.deleted_meals.append({
                    "type": "dinner", "meal": dinner, "removed_lunches": removed_lunches,
                })
                plan["dinners"] = [d for d in plan["dinners"] if d["id"] != did]
                plan["lunches"] = [l for l in plan["lunches"] if l.get("leftover_from_dinner_id") != did]
                st.session_state.week_plan = plan
                _rebuild_shopping()
                data_store.update_history_plan(plan, st.session_state.week_start)
                st.rerun()

            with st.expander("Recipe & details", expanded=False):
                if dinner.get("health_highlights"):
                    st.caption(" · ".join(dinner["health_highlights"]))

                cols = st.columns([3, 2])
                with cols[0]:
                    st.markdown("**Ingredients**")
                    for ing in dinner.get("ingredients", []):
                        staple = " *(pantry)*" if ing.get("pantry_staple") else ""
                        st.markdown(f"- {ing['quantity']} {ing['unit']} {ing['name']}{staple}")

                with cols[1]:
                    st.markdown("**Instructions**")
                    for i, step in enumerate(dinner.get("instructions", []), 1):
                        st.markdown(f"{i}. {step}")

                if dinner.get("kid_adaptation"):
                    st.info(f"**Kids:** {dinner['kid_adaptation']}")
                if dinner.get("uric_acid_tip"):
                    st.success(f"**Uric acid tip:** {dinner['uric_acid_tip']}")
                if dinner.get("sunday_prep"):
                    st.warning(f"**Sunday prep:** {dinner['sunday_prep']}")
                if dinner.get("generates_lunch"):
                    st.markdown(f"**Lunch tomorrow:** {dinner.get('lunch_scaling_instructions', '')}")

                # Serving sizes
                serving_sizes = dinner.get("serving_sizes", [])
                if serving_sizes:
                    with st.expander("Serving sizes"):
                        cols = st.columns([3, 3, 3])
                        cols[0].markdown("**Component**")
                        cols[1].markdown("**Adult**")
                        cols[2].markdown("**Kids**")
                        for s in serving_sizes:
                            cols[0].write(s.get("component", ""))
                            cols[1].write(s.get("adult_portion", ""))
                            cols[2].write(s.get("kid_portion", "—"))

                # Cost estimate
                cost = dinner.get("cost_estimate")
                if cost:
                    st.caption(f"Est. ingredient cost: ${cost.get('total_ingredient_cost_usd', 0):.2f} total  ·  ${cost.get('cost_per_serving_usd', 0):.2f}/serving  *(non-pantry, ±20–30%)*")

                # Nutrition estimate
                nut = dinner.get("nutrition_estimate")
                if nut:
                    with st.expander("Nutrition estimate (per adult serving)"):
                        nc1, nc2, nc3, nc4 = st.columns(4)
                        nc1.metric("Calories", nut.get("calories_per_adult_serving", "—"))
                        nc2.metric("Protein", f"{nut.get('protein_g', '—')}g")
                        nc3.metric("Fiber", f"{nut.get('fiber_g', '—')}g")
                        nc4.metric("Fat", f"{nut.get('fat_g', '—')}g")
                        if nut.get("saturated_fat_note"):
                            st.caption(nut["saturated_fat_note"])

                # Favorites
                if st.button("Save as Favorite", key=f"fav_{did}"):
                    try:
                        data_store.save_favorite(dinner)
                        st.success(f"'{dinner['name']}' saved!")
                    except ValueError as e:
                        st.warning(str(e))

        # ── Recently removed (dinners) ────────────────────────────────────────
        removed_dinners = [r for r in st.session_state.deleted_meals if r["type"] == "dinner"]
        if removed_dinners:
            with st.expander(f"Recently removed dinners ({len(removed_dinners)})"):
                for i, removed in enumerate(removed_dinners):
                    col_name, col_restore = st.columns([8, 1])
                    col_name.markdown(f"**{removed['meal']['name']}**")
                    if col_restore.button("Restore", key=f"restore_dinner_{i}", use_container_width=True):
                        plan["dinners"].append(removed["meal"])
                        plan["lunches"].extend(removed["removed_lunches"])
                        st.session_state.deleted_meals.remove(removed)
                        st.session_state.week_plan = plan
                        _rebuild_shopping()
                        data_store.update_history_plan(plan, st.session_state.week_start)
                        st.rerun()

        # ── Export Kid Notes ──────────────────────────────────────────────────
        st.markdown("")
        if st.button("📋 Export Kid Notes (Babysitter Guide)"):
            with st.spinner("Generating babysitter-friendly guide..."):
                try:
                    notes = meal_planner.generate_kid_notes(plan)
                    st.session_state.kid_notes = notes
                except MealPlanError as e:
                    st.error(f"Kid notes failed: {e}")

        if st.session_state.kid_notes:
            st.subheader("Kids' Meal Guide")
            st.text_area(
                "Copy or download for your babysitter",
                value=st.session_state.kid_notes,
                height=500,
                label_visibility="collapsed",
            )
            st.download_button(
                "Download Kid Notes (.txt)",
                data=st.session_state.kid_notes,
                file_name=f"kid_meal_guide_{st.session_state.week_start}.txt",
                mime="text/plain",
            )

        st.divider()

        # ── Lunches ──────────────────────────────────────────────────────────
        st.subheader("Lunches")
        for lunch in plan.get("lunches", []):
            lid = lunch["id"]
            source_label = "Leftover from dinner" if lunch["source"] == "leftover" else "Standalone"
            reheat_label = "Microwave" if lunch["reheat"] == "microwave" else "Cold (no reheat)"

            # Header row: meal name + swap + remove buttons
            hdr_col, swap_col, remove_col = st.columns([8, 1, 1])
            hdr_col.markdown(
                f"**{lunch['name']}** &nbsp;·&nbsp; {source_label} &nbsp;·&nbsp; {reheat_label}",
                unsafe_allow_html=True,
            )
            if swap_col.button("🔄 Swap", key=f"swap_{lid}", use_container_width=True):
                st.session_state.swapping = (lid, "lunch", lunch["name"])
            if remove_col.button("✕ Remove", key=f"remove_{lid}", use_container_width=True):
                st.session_state.deleted_meals.append({
                    "type": "lunch", "meal": lunch, "removed_lunches": [],
                })
                plan["lunches"] = [l for l in plan["lunches"] if l["id"] != lid]
                st.session_state.week_plan = plan
                _rebuild_shopping()
                data_store.update_history_plan(plan, st.session_state.week_start)
                st.rerun()

            with st.expander("Recipe & details", expanded=False):
                if lunch.get("health_highlights"):
                    st.caption(" · ".join(lunch["health_highlights"]))

                st.markdown("**Ingredients**")
                for ing in lunch.get("ingredients", []):
                    staple = " *(pantry)*" if ing.get("pantry_staple") else ""
                    st.markdown(f"- {ing['quantity']} {ing['unit']} {ing['name']}{staple}")

                if lunch.get("pack_instructions"):
                    st.info(f"**Pack:** {lunch['pack_instructions']}")
                if lunch.get("prep_at_lunchtime_minutes"):
                    st.caption(f"Office prep: {lunch['prep_at_lunchtime_minutes']} min")

                serving_sizes = lunch.get("serving_sizes", [])
                if serving_sizes:
                    with st.expander("Serving sizes"):
                        for s in serving_sizes:
                            st.markdown(f"- **{s.get('component', '')}:** {s.get('adult_portion', '')}")

                cost = lunch.get("cost_estimate")
                if cost:
                    st.caption(f"Est. ingredient cost: ${cost.get('total_ingredient_cost_usd', 0):.2f}  *(non-pantry, ±20–30%)*")

                nut = lunch.get("nutrition_estimate")
                if nut:
                    with st.expander("Nutrition estimate (per adult serving)"):
                        nc1, nc2, nc3, nc4 = st.columns(4)
                        nc1.metric("Calories", nut.get("calories_per_adult_serving", "—"))
                        nc2.metric("Protein", f"{nut.get('protein_g', '—')}g")
                        nc3.metric("Fiber", f"{nut.get('fiber_g', '—')}g")
                        nc4.metric("Fat", f"{nut.get('fat_g', '—')}g")

        # ── Recently removed (lunches) ────────────────────────────────────────
        removed_lunches = [r for r in st.session_state.deleted_meals if r["type"] == "lunch"]
        if removed_lunches:
            with st.expander(f"Recently removed lunches ({len(removed_lunches)})"):
                for i, removed in enumerate(removed_lunches):
                    col_name, col_restore = st.columns([8, 1])
                    col_name.markdown(f"**{removed['meal']['name']}**")
                    if col_restore.button("Restore", key=f"restore_lunch_{i}", use_container_width=True):
                        plan["lunches"].append(removed["meal"])
                        st.session_state.deleted_meals.remove(removed)
                        st.session_state.week_plan = plan
                        _rebuild_shopping()
                        data_store.update_history_plan(plan, st.session_state.week_start)
                        st.rerun()

        st.divider()

        # ── Sunday Prep ───────────────────────────────────────────────────────
        st.subheader("Sunday Prep List")
        sunday_tasks = plan.get("sunday_prep_list", [])
        if sunday_tasks:
            for task in sunday_tasks:
                with st.expander(f"**{task['task']}**", expanded=True):
                    if task.get("yields_for"):
                        st.markdown("*Used in:* " + ", ".join(task["yields_for"]))
                    if task.get("storage"):
                        st.caption(f"Storage: {task['storage']}")
        else:
            st.caption("No Sunday prep suggested for this week.")

    else:
        st.info("Click **Generate Week** above to create this week's meal plan.")


# ─────────────────────────────────────────────────────────────────────────────
# Tab: Shopping List
# ─────────────────────────────────────────────────────────────────────────────
with tab_shopping:
    st.header("Shopping List")

    sections = st.session_state.shopping_sections
    if not sections:
        if st.session_state.week_plan is None:
            st.info("Generate a meal plan first to build your shopping list.")
        else:
            _rebuild_shopping()
            sections = st.session_state.shopping_sections

    if sections:
        list_text = shopping.format_shopping_list_text(sections)

        st.text_area(
            "Copy this list",
            value=list_text,
            height=400,
            label_visibility="collapsed",
        )
        st.download_button(
            label="Download as .txt",
            data=list_text,
            file_name=f"shopping_list_{st.session_state.week_start}.txt",
            mime="text/plain",
        )
        st.divider()

        section_list = [(s, items) for s, items in sections.items() if items]
        mid = (len(section_list) + 1) // 2
        col_left, col_right = st.columns(2)
        for col, chunk in ((col_left, section_list[:mid]), (col_right, section_list[mid:])):
            with col:
                for section, items in chunk:
                    st.markdown(f"**{section}**")
                    for item in items:
                        st.markdown(f"- {item.name} — {item.display_quantity()}")
                    st.write("")


# ─────────────────────────────────────────────────────────────────────────────
# Tab: Favorites
# ─────────────────────────────────────────────────────────────────────────────
with tab_favorites:
    st.header("Favorite Meals")
    st.caption("Claude works these into future plans naturally, based on ratings and how recently they were made.")

    favorites = data_store.load_favorites()
    if not favorites:
        st.info("No favorites yet. Generate a plan and save meals you enjoy.")
    else:
        for fav in sorted(favorites, key=lambda f: f["rating"], reverse=True):
            stars = "★" * fav["rating"] + "☆" * (5 - fav["rating"])
            with st.expander(
                f"**{fav['name']}** {stars}  ·  Last made: {fav['last_made']}"
            ):
                all_tags = [
                    "kids loved it", "great leftover", "make again soon",
                    "easy to make", "bold flavors", "comfort food",
                    "too time-consuming", "needs adjustment",
                ]
                new_tags = st.multiselect(
                    "Tags", options=all_tags, default=fav.get("tags", []),
                    key=f"tags_{fav['id']}",
                )
                new_rating = st.slider(
                    "Rating", min_value=1, max_value=5, value=fav["rating"],
                    key=f"rating_{fav['id']}",
                )
                new_feedback = st.text_input(
                    "Notes for Claude",
                    value=fav.get("feedback", ""),
                    placeholder="e.g. 'Kids liked it more with the sauce on the side'",
                    key=f"feedback_{fav['id']}",
                )
                col_save, col_delete = st.columns(2)
                with col_save:
                    if st.button("Save changes", key=f"save_{fav['id']}"):
                        data_store.update_favorite(
                            fav["id"], rating=new_rating, tags=new_tags, feedback=new_feedback
                        )
                        st.success("Updated!")
                        st.rerun()
                with col_delete:
                    if st.button("Remove favorite", key=f"del_{fav['id']}"):
                        data_store.delete_favorite(fav["id"])
                        st.warning(f"'{fav['name']}' removed.")
                        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Tab: Recipe Library
# ─────────────────────────────────────────────────────────────────────────────
with tab_library:
    st.header("Recipe Library")
    st.caption("Every meal Claude has ever generated for you — auto-saved. Add any recipe back into your current week.")

    library = data_store.load_recipe_library()
    if not library:
        st.info("No recipes yet. Generate a meal plan to start building your library.")
    else:
        col_search, col_filter = st.columns([3, 1])
        search_query = col_search.text_input(
            "Search", placeholder="Search by name…", label_visibility="collapsed"
        )
        meal_type_filter = col_filter.selectbox(
            "Type", options=["All", "Dinners", "Lunches"], label_visibility="collapsed"
        )

        filtered = library
        if search_query:
            q = search_query.lower()
            filtered = [r for r in filtered if q in r["name"].lower()]
        if meal_type_filter == "Dinners":
            filtered = [r for r in filtered if r["meal_type"] == "dinner"]
        elif meal_type_filter == "Lunches":
            filtered = [r for r in filtered if r["meal_type"] == "lunch"]

        filtered = sorted(filtered, key=lambda r: r["last_generated"], reverse=True)

        has_plan = st.session_state.week_plan is not None

        dinners = [r for r in filtered if r["meal_type"] == "dinner"]
        lunches = [r for r in filtered if r["meal_type"] == "lunch"]

        def _render_library_section(recipes: list, section_label: str):
            if not recipes:
                return
            st.subheader(section_label)
            for rec in recipes:
                meal = rec["meal"]
                with st.expander(f"**{rec['name']}** &nbsp;·&nbsp; Last generated: {rec['last_generated']}", expanded=False):
                    if rec["meal_type"] == "dinner":
                        st.caption(f"{meal.get('cook_time_minutes', '?')} min · {meal.get('primary_equipment', '')}")
                        if meal.get("health_highlights"):
                            st.caption(" · ".join(meal["health_highlights"]))

                        cols = st.columns([3, 2])
                        with cols[0]:
                            st.markdown("**Ingredients**")
                            for ing in meal.get("ingredients", []):
                                staple = " *(pantry)*" if ing.get("pantry_staple") else ""
                                st.markdown(f"- {ing['quantity']} {ing['unit']} {ing['name']}{staple}")
                        with cols[1]:
                            st.markdown("**Instructions**")
                            for i, step in enumerate(meal.get("instructions", []), 1):
                                st.markdown(f"{i}. {step}")

                        if meal.get("kid_adaptation"):
                            st.info(f"**Kids:** {meal['kid_adaptation']}")
                        if meal.get("uric_acid_tip"):
                            st.success(f"**Uric acid tip:** {meal['uric_acid_tip']}")
                    else:
                        source_label = "Leftover from dinner" if meal.get("source") == "leftover" else "Standalone"
                        st.caption(source_label)
                        st.markdown("**Ingredients**")
                        for ing in meal.get("ingredients", []):
                            staple = " *(pantry)*" if ing.get("pantry_staple") else ""
                            st.markdown(f"- {ing['quantity']} {ing['unit']} {ing['name']}{staple}")
                        if meal.get("pack_instructions"):
                            st.info(f"**Pack:** {meal['pack_instructions']}")

                    btn_col, del_col = st.columns([3, 1])
                    with btn_col:
                        add_help = "Load a week plan first." if not has_plan else None
                        if st.button(
                            "Add to This Week",
                            key=f"lib_add_{rec['id']}",
                            use_container_width=True,
                            disabled=not has_plan,
                            help=add_help,
                        ):
                            added = copy.deepcopy(meal)
                            added["id"] = str(uuid.uuid4())
                            active_plan = st.session_state.week_plan
                            if rec["meal_type"] == "dinner":
                                active_plan["dinners"].append(added)
                            else:
                                active_plan["lunches"].append(added)
                            st.session_state.week_plan = active_plan
                            _rebuild_shopping()
                            data_store.update_history_plan(active_plan, st.session_state.week_start)
                            st.success(f"'{rec['name']}' added to this week.")
                            st.rerun()
                    with del_col:
                        if st.button("Delete", key=f"lib_del_{rec['id']}", use_container_width=True):
                            data_store.delete_from_library(rec["id"])
                            st.rerun()

        if meal_type_filter != "Lunches":
            _render_library_section(dinners, f"Dinners ({len(dinners)})")
        if meal_type_filter != "Dinners":
            _render_library_section(lunches, f"Lunches ({len(lunches)})")

        if not filtered:
            st.caption("No recipes match your search.")


# ─────────────────────────────────────────────────────────────────────────────
# Tab: Settings
# ─────────────────────────────────────────────────────────────────────────────
with tab_settings:
    st.header("Settings")
    prefs = data_store.load_preferences()

    # ── Children ──────────────────────────────────────────────────────────────
    st.subheader("Children")
    st.caption("Used to set serving sizes and kid adaptations in every generated plan.")

    children = prefs.get("children", [])
    for i, child in enumerate(children):
        col_name, col_age, col_del = st.columns([3, 1, 1])
        new_name = col_name.text_input(
            "Name (optional)",
            value=child.get("name", ""),
            placeholder="e.g. Emma",
            key=f"child_name_{i}",
            label_visibility="collapsed",
        )
        new_age = col_age.number_input(
            "Age",
            min_value=0, max_value=17,
            value=child.get("age", 1),
            step=1,
            key=f"child_age_{i}",
            label_visibility="collapsed",
        )
        if col_del.button("✕", key=f"del_child_{i}"):
            children = [c for j, c in enumerate(children) if j != i]
            data_store.update_preferences(children=children)
            st.rerun()
        if new_name != child.get("name", "") or new_age != child.get("age"):
            children[i] = {"name": new_name, "age": int(new_age)}
            data_store.update_preferences(children=children)

    with st.form("add_child_form", clear_on_submit=True):
        col_n, col_a, col_add = st.columns([3, 1, 1])
        add_name = col_n.text_input("Name (optional)", placeholder="e.g. Liam", label_visibility="collapsed")
        add_age = col_a.number_input("Age", min_value=0, max_value=17, value=1, step=1, label_visibility="collapsed")
        if st.form_submit_button("Add child", use_container_width=True):
            children = prefs.get("children", []) + [{"name": add_name.strip(), "age": int(add_age)}]
            data_store.update_preferences(children=children)
            st.rerun()

    st.divider()

    # ── Meal Planning Preferences ─────────────────────────────────────────────
    st.subheader("Meal Planning")

    lunch_count = st.number_input(
        "Adults bringing lunch to work",
        min_value=1, max_value=4,
        value=prefs["lunch_adult_count"], step=1,
        help="Affects lunch serving sizes.",
    )
    budget_input = st.text_input(
        "Weekly grocery budget (optional)",
        value=prefs.get("budget") or "",
        placeholder="e.g. $150/week — leave blank for no constraint",
    )

    if st.button("Save Meal Planning Preferences"):
        data_store.update_preferences(
            lunch_adult_count=int(lunch_count),
            budget=budget_input.strip() if budget_input.strip() else None,
        )
        st.success("Saved.")

    st.divider()

    # ── Nutrition Targets ─────────────────────────────────────────────────────
    st.subheader("Nutrition Targets (Lunch + Dinner)")
    st.caption(
        "These cover lunch and dinner only — breakfast and snacks are outside the app's scope. "
        "Defaults are calculated for a 230 lb man targeting 190 lbs at ~1 lb/week loss."
    )

    t_cal = st.number_input(
        "Daily calorie target (kcal)",
        min_value=800, max_value=3000,
        value=prefs.get("target_calories_lunch_dinner", 1450), step=50,
    )
    t_prot = st.number_input(
        "Daily protein target (g)",
        min_value=40, max_value=250,
        value=prefs.get("target_protein_g", 100), step=5,
    )
    t_fib = st.number_input(
        "Daily fiber target (g)",
        min_value=5, max_value=60,
        value=prefs.get("target_fiber_g", 18), step=1,
    )
    if st.button("Save Nutrition Targets"):
        data_store.update_preferences(
            target_calories_lunch_dinner=int(t_cal),
            target_protein_g=int(t_prot),
            target_fiber_g=int(t_fib),
        )
        st.success("Saved.")

    st.divider()

    # ── Food Constraints ──────────────────────────────────────────────────────
    st.subheader("Food Constraints")
    st.caption(
        "Added on top of the hardcoded defaults: no mushrooms, no oranges, no fish at lunch. "
        "Toggle off to keep a constraint saved without sending it to Claude."
    )

    constraints = data_store.load_constraints()
    if constraints:
        for c in constraints:
            col_toggle, col_text, col_del = st.columns([1, 6, 1])
            with col_toggle:
                active = st.toggle(
                    "Active", value=c["active"],
                    key=f"toggle_{c['id']}", label_visibility="collapsed",
                )
                if active != c["active"]:
                    data_store.toggle_constraint(c["id"])
                    st.rerun()
            with col_text:
                st.write(c["text"])
            with col_del:
                if st.button("✕", key=f"delc_{c['id']}"):
                    data_store.delete_constraint(c["id"])
                    st.rerun()
    else:
        st.caption("No custom constraints yet.")

    new_constraint = st.text_input(
        "Add a constraint",
        placeholder="e.g. No cilantro",
        key="new_constraint_input",
    )
    if st.button("Add Constraint") and new_constraint.strip():
        data_store.add_constraint(new_constraint.strip())
        st.success("Constraint added.")
        st.rerun()

    st.divider()

    # ── Google Drive Setup ────────────────────────────────────────────────────
    st.subheader("Google Drive Setup")
    if drive_upload.credentials_configured():
        st.success(
            "✅ `data/client_secrets.json` found — Drive upload is ready. "
            "Your token is stored at `data/drive_token.json` after the first upload."
        )
    else:
        st.warning("⚠️ `data/client_secrets.json` not found — Drive upload is disabled.")

    with st.expander("Step-by-step: connect Google Drive"):
        st.markdown("""
**One-time setup (~10 minutes)**

1. **Go to** [console.cloud.google.com](https://console.cloud.google.com) and sign in with your Google account.
2. Click **Select a project** → **New Project** → give it any name (e.g. "Meal Planner") → **Create**.
3. In the left sidebar: **APIs & Services → Library**. Search for **Google Drive API** → click it → **Enable**.
4. **APIs & Services → OAuth consent screen**:
   - User type: **External** → Create
   - Fill in App name (anything), support email, developer email → Save and Continue
   - Skip Scopes → Save and Continue
   - Add yourself as a **Test user** (your Gmail address) → Save and Continue → Back to Dashboard
5. **APIs & Services → Credentials** → **+ Create Credentials → OAuth client ID**:
   - Application type: **Desktop app** → Name it anything → **Create**
6. Click **⬇ Download JSON** on the new credential → save that file as **`data/client_secrets.json`** in this project folder.
7. Come back to the app, refresh the Settings tab — the warning above should turn green.
8. Click **☁ Upload PDF to Drive** in the Generate tab — a browser window opens asking you to sign in and allow access. Do so once and the token is cached forever (auto-refreshed).

**Your files land in** Google Drive → "Mediterranean Meal Plans" folder. Open the Drive app on your phone, find the file, and cast it to your Nest Hub.
        """)

    st.divider()

    # ── Meal History ──────────────────────────────────────────────────────────
    st.subheader("Meal History")
    st.caption("Last 6 weeks are sent to Claude to avoid repetition.")
    history = data_store.load_history()
    if history:
        for entry in history[:6]:
            with st.expander(f"Week of {entry['week_start']}"):
                for name in entry.get("meal_names", []):
                    st.markdown(f"- {name}")
    else:
        st.caption("No history yet.")
