import streamlit as st

pg = st.navigation([
    st.Page("app.py", title="Meal Planner", icon=":material/local_dining:"),
    st.Page("pages/kitchen.py", title="Kitchen Mode", icon=":material/skillet:"),
])
pg.run()
