# Mediterranean Meal Planner — Code Review & Optimization Plan

I've reviewed the current state of the Mediterranean Meal Planner. You've built a robust, feature-rich application. The prompt engineering is excellent, the data schemas are well-structured, and the functional separation between API calls, data storage, and the UI is very clean. 

However, you are hitting the natural limits of Streamlit. Streamlit's execution model (re-running the entire script top-to-bottom on every interaction) causes the "clunky" feel you described.

Here is my review as a Senior Software Developer, covering architectural options, optimizations, and active bugs I've identified.

## 1. Architectural Options (Escaping Streamlit)

Streamlit is incredible for prototyping and data science, but for a consumer-facing CRUD application with complex state (like a meal planner with shopping lists, swapping, and drag-and-drop needs), it becomes a bottleneck.

**Dash?** 
Dash is primarily built for complex data visualizations (Plotly). While it uses React under the hood, its Python callback system is just as clunky (if not more so) for standard web app state management as Streamlit. I do not recommend Dash for this.

**The Recommended Path: FastAPI (Backend) + Next.js or Vite (Frontend)**
To achieve a premium, snappy, and modern UI, you should decouple the frontend from the backend:
*   **Backend (FastAPI in Python):** Keep all your excellent Python logic (`meal_planner.py`, `schemas.py`, `pdf_export.py`, `shopping.py`). Wrap them in FastAPI endpoints. This is extremely easy since your logic is already cleanly separated.
*   **Frontend (Next.js or Vite with React):** Build the UI in React using Tailwind CSS and Radix/shadcn UI components. 
    *   *Why?* It runs entirely in the user's browser, meaning instant feedback. Opening a modal, toggling a constraint, or swapping a meal happens instantly without waiting for a Python script to re-run. We can add beautiful micro-animations, drag-and-drop meal rearranging, and a much more responsive layout for mobile.

## 2. Bugs Identified in the Current State

I found a few subtle bugs in the current Streamlit implementation that are worth fixing whether we stay in Streamlit or migrate:

### A. The "Lunch Drop" Bug during Meal Swaps
In `meal_planner.py` (`swap_meal` function, line 354), you determine whether to ask Claude for a replacement lunch based on the `generates_lunch` boolean of the **original meal being replaced**. 
If you swap a dinner that *didn't* generate lunch, but Claude decides the *new* dinner *does* generate lunch, the code completely drops the new lunch because it's looking at the old meal's flag.
*   **Fix:** The logic should check if the *replacement* dinner generates a lunch, and if so, safely append it to the lunch list (and vice versa for removing).

### B. Child Schema Confusion
In `app.py`, if a user deletes all children in the settings, `prefs.get("children")` evaluates to `[]`. The `OUTPUT_SCHEMA` (line 158 of `system_prompt.py`) dynamically sets the number of kid servings to `0`, which is correct. However, the schema still explicitly requires `kid_adaptation: "string — always required"`. This creates a contradictory prompt for Claude where it is told there are 0 kids, but must provide a mandatory kid adaptation. This can lead to hallucinations or JSON schema validation failures.
*   **Fix:** If `kids_count == 0`, dynamically remove the `kid_adaptation` field from the expected JSON schema and the prompt rules.

### C. In-Place State Mutation
In `app.py` (e.g., line 474 when restoring a deleted dinner), you mutate `plan["dinners"].append(...)`. In Streamlit, mutating a dictionary that is currently inside `st.session_state` *before* explicitly re-assigning it can cause race conditions or fail to trigger a proper reactive re-render in complex apps.
*   **Fix:** Always deep copy the state object before mutating, then reassign it: `new_plan = copy.deepcopy(st.session_state.week_plan)`.

## 3. Optimizations (If keeping Streamlit)

If you prefer to stay in Streamlit for now, here are optimizations to make it less clunky:

*   **Move File I/O off the UI Thread:** Every time you click "Active" on a constraint, `toggle_constraint()` synchronously writes to `constraints.json` and calls `st.rerun()`. This blocks the UI. You should use `@st.cache_data` or migrate from flat JSON files to a local `sqlite3` database. SQLite is faster, handles concurrent reads/writes natively, and doesn't require rewriting the entire JSON array on every tiny update.
*   **Fragment Rendering (`@st.fragment`):** Streamlit 1.37+ introduced `@st.fragment`. You can wrap your settings toggles or the "Favorites" tab in a fragment so that interacting with them only reruns that specific function, not the entire 1000-line `app.py`. This alone would drastically reduce the clunky feel.
*   **Shopping List Aggregation:** `ShoppingItem.merge` simply concatenates strings (`"1 lb + 1 lb"`). While safe from unit-conversion errors, it makes the list messy. We could implement a lightweight unit parser (e.g., `pint` library) to sum common units (lbs, cups, oz).

---

## Next Steps

> [!IMPORTANT]
> **User Review Required**
> 
> How would you like to proceed?
> 1. **Migrate to a modern stack (FastAPI + Next.js/React)** to make this a truly premium, fast application. (I can generate the new architecture and rewrite the frontend).
> 2. **Stay in Streamlit** and let me fix the bugs (Lunch drop, schema issues) and implement optimizations (SQLite, Streamlit fragments) to polish the current app.
