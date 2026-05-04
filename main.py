import streamlit as st

pg = st.navigation([
    st.Page("app.py", title="Meal Planner", icon="🫒"),
    st.Page("pages/kitchen.py", title="Kitchen Mode", icon="🍳"),
])
pg.run()
