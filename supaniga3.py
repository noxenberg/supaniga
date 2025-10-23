# app.py — Dishcovery (Prototype with Rules)
# ------------------------------------------
# - Incremental ingredient inputs (blue valid / red invalid)
# - Requires “enough” ingredients; else suggests sane additions
# - Finds real recipes online (no AI creation) via TheMealDB (free, no key)
# - Uses actual recipe images and links to sources
# - Color wheel customizes site + sidebar colors

import re
import math
from typing import List, Dict, Tuple, Optional, Set
import streamlit as st
import requests
from rapidfuzz import fuzz, process
import pycountry

# ---------------- UI Setup ----------------
st.set_page_config(page_title="Dishcovery", page_icon="🍽️", layout="wide")
DEFAULT_PRIMARY = "#4F46E5"
DEFAULT_ACCENT = "#10B981"
DEFAULT_BG = "#0B0F19"
DEFAULT_TEXT = "#E5E7EB"
DEFAULT_SIDEBAR_BG = "#111827"

# ---------------- Sidebar: Color Wheel ----------------
with st.sidebar:
    st.header("🎨 Theme")
    col1, col2 = st.columns(2)
    with col1:
        primary = st.color_picker("Primary", DEFAULT_PRIMARY, key="c_primary")
        bg = st.color_picker("Background", DEFAULT_BG, key="c_bg")
    with col2:
        accent = st.color_picker("Accent", DEFAULT_ACCENT, key="c_accent")
        textc = st.color_picker("Text", DEFAULT_TEXT, key="c_text")
    sidebar_bg = st.color_picker("Sidebar BG", DEFAULT_SIDEBAR_BG, key="c_sidebar")
    st.caption("Colors apply instantly across the page.")

# Inject CSS theme
st.markdown(f"""
<style>
:root {{
  --primary: {primary};
  --accent: {accent};
  --bg: {bg};
  --text: {textc};
  --sidebar: {sidebar_bg};
}}
html, body, [data-testid="stAppViewContainer"] {{
  background: var(--bg) !important; color: var(--text) !important;
}}
h1,h2,h3,h4,h5,h6, label, p, span, .stMarkdown, div {{
  color: var(--text) !important;
}}
/* Sidebar */
section[data-testid="stSidebar"] > div {{
  background: var(--sidebar) !important;
}}
/* Buttons and inputs */
.stButton>button {{
  background: var(--primary) !important; color: white !important; border: 0; border-radius: 8px;
}}
input, textarea, .stTextInput>div>div>input {{
  background: rgba(255,255,255,0.04) !important; color: var(--text) !important; border-radius: 8px !important;
}}
/* Metric & chips */
.badge {{
  display:inline-block;padding:4px 8px;border-radius:999px;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.13);
  color:var(--text); font-size:0.85rem;
}}
</style>
""", unsafe_allow_html=True)

st.title("🍽️ Dishcovery")
st.caption("Type ingredients one-by-one. Valid turns **blue**, invalid **red**. We’ll only suggest recipes when your list is sensible—and we’ll link to the original source with a real image.")

# ---------------- Ingredient Knowledge ----------------
VALID_INGREDIENTS: Set[str] = {
    # basics & aromatics
    "salt","pepper","olive oil","butter","garlic","onion","ginger","lemon","lime","chili","basil","parsley","cilantro",
    # carbs
    "pasta","spaghetti","penne","rice","bread","tortilla","noodles","udon","ramen","quinoa","couscous","potato",
    # proteins
    "chicken","beef","pork","egg","eggs","tofu","tempeh","shrimp","salmon","tuna","beans","chickpeas","lentils","mushroom","paneer",
    # sauces & dairy
    "tomato","tomato paste","tomato sauce","soy sauce","fish sauce","oyster sauce","vinegar","balsamic vinegar","mustard","honey",
    "milk","cream","yogurt","cheese","parmesan","mozzarella","cheddar","cream cheese",
    # extras
    "spinach","broccoli","bell pepper","carrot","cucumber","olive","capers","anchovy","miso","gochujang","sesame oil","tahini","coconut milk",
}

# Ingredient categories to detect “enough to cook”
CATS = {
    "aromatic": {"garlic","onion","ginger"},
    "protein": {"chicken","beef","pork","egg","eggs","tofu","tempeh","shrimp","salmon","tuna","beans","chickpeas","lentils","mushroom","paneer"},
    "carb": {"pasta","spaghetti","penne","rice","noodles","udon","ramen","bread","tortilla","quinoa","couscous","potato"},
    "liquid": {"olive oil","sesame oil","milk","cream","yogurt","coconut milk","tomato sauce"},
    "flavor": {"salt","pepper","soy sauce","fish sauce","oyster sauce","vinegar","balsamic vinegar","mustard","honey","tomato paste","cheese","parmesan","mozzarella","cheddar"},
    "veg": {"tomato","spinach","broccoli","bell pepper","carrot","cucumber"},
}

# “Inedible / bad solo combo” guards (example set)
BAD_COMBOS = [
    {"milk","pasta"},            # needs fat + seasoning + aromatic
    {"rice","milk"},             # not a savory dish without more
    {"tuna","milk"},             # rarely sensible
]

SUGGESTIONS = {
    "milk+pasta": ["olive oil","garlic","parmesan","black pepper"],
    "needs_aromatic": ["garlic","onion"],
    "needs_fat": ["olive oil","butter","sesame oil"],
    "needs_flavor": ["salt","pepper","soy sauce","tomato paste","parmesan"],
    "needs_protein": ["chicken","tofu","eggs","shrimp","mushroom"],
    "needs_carb": ["rice","pasta","bread","noodles"],
    "needs_liquid": ["olive oil","coconut milk","tomato sauce","cream"],
}

# ---------------- Utilities ----------------
def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def ingredient_valid(name: str) -> Tuple[bool, str, Optional[str]]:
    q = normalize_text(name)
    if not q:
        return False, "", None
    if q in VALID_INGREDIENTS:
        return True, q, None
    match, score, _ = process.extractOne(q, VALID_INGREDIENTS, scorer=fuzz.WRatio)
    if score >= 90:
        return True, match, None
    elif score >= 75:
        return False, "", match
    return False, "", None

def inject_input_border_color(widget_key: str, color_hex: str):
    st.markdown(
        f"""
        <style>
        div[data-testid="stTextInput"][data-key="{widget_key}"] div[data-baseweb="base-input"] {{
            border: 2px solid {color_hex} !important;
            border-radius: 8px !important;
        }}
        </style>
        """,
        unsafe_allow_html=True
    )

def classify_categories(ings: List[str]) -> Set[str]:
    cats = set()
    s = set(ings)
    for cat, items in CATS.items():
        if s & items:
            cats.add(cat)
    return cats

def has_bad_combo(ings: List[str]) -> Optional[List[str]]:
    s = set(ings)
    for bad in BAD_COMBOS:
        if bad.issubset(s):
            return list(bad)
    return None

def need_more_for_cook(ings: List[str]) -> Tuple[bool, List[str], List[str]]:
    """
    Decide if we have enough to cook:
    - at least: aromatic OR veg  +  protein OR carb  + flavor  + fat/liquid
    """
    cats = classify_categories(ings)
    reasons = []
    if not (("aromatic" in cats) or ("veg" in cats)) : reasons.append("needs_aromatic")
    if not (("protein" in cats) or ("carb" in cats)) : reasons.append("needs_protein")
    if "flavor" not in cats: reasons.append("needs_flavor")
    if not (("liquid" in cats) or ("fat" in cats) or ("olive oil" in ings)): 
        # We don't have a dedicated "fat" category; treat olive oil in liquid.
        if "liquid" not in cats and "olive oil" not in ings and "butter" not in ings and "sesame oil" not in ings:
            reasons.append("needs_fat")
    enough = (len(reasons) == 0)
    suggestions = []
    # special pair warnings
    bad = has_bad_combo(ings)
    if bad:
        suggestions += SUGGESTIONS.get("milk+pasta", [])
    for r in reasons:
        suggestions += SUGGESTIONS.get(r, [])
    # dedupe preserve order
    seen = set()
    suggestions = [x for x in suggestions if not (x in seen or seen.add(x))]
    return enough, reasons, suggestions

# ---------------- Online Recipe Search (TheMealDB) ----------------
MEALDB_BASE = "https://www.themealdb.com/api/json/v1/1"

def mealdb_filter_by_ingredient(ingredient: str) -> List[Dict]:
    """Return list of {idMeal, strMeal, strMealThumb} for one ingredient."""
    url = f"{MEALDB_BASE}/filter.php"
    r = requests.get(url, params={"i": ingredient}, timeout=15)
    data = r.json() if r.ok else {}
    return data.get("meals") or []

def mealdb_lookup_by_id(meal_id: str) -> Optional[Dict]:
    url = f"{MEALDB_BASE}/lookup.php"
    r = requests.get(url, params={"i": meal_id}, timeout=15)
    data = r.json() if r.ok else {}
    meals = data.get("meals") or []
    return meals[0] if meals else None

def intersect_meals_by_ingredients(ings: List[str]) -> List[Dict]:
    """
    TheMealDB filter only supports one ingredient at a time.
    Strategy:
      - fetch IDs per ingredient
      - intersect meal IDs across ingredients
    """
    id_sets = []
    thumb_index = {}
    for ing in ings:
        lst = mealdb_filter_by_ingredient(ing)
        ids = set()
        for m in lst:
            ids.add(m["idMeal"])
            thumb_index[m["idMeal"]] = m.get("strMealThumb")
        if not ids:
            # if one ingredient yields nothing, intersection will be empty; keep empty
            return []
        id_sets.append(ids)
    final_ids = set.intersection(*id_sets) if id_sets else set()
    results = []
    for mid in final_ids:
        details = mealdb_lookup_by_id(mid)
        if details:
            details["strMealThumb"] = details.get("strMealThumb") or thumb_index.get(mid)
            results.append(details)
    return results

# ---------------- State & Inputs ----------------
if "ingredients_raw" not in st.session_state:
    st.session_state.ingredients_raw = [""]
if "ingredients_valid" not in st.session_state:
    st.session_state.ingredients_valid = [False]

def render_ingredient_input(idx: int):
    key = f"ing_{idx}"
    val = st.text_input(f"Ingredient #{idx+1}", st.session_state.ingredients_raw[idx], key=key)
    ok, canon, suggestion = ingredient_valid(val)
    st.session_state.ingredients_raw[idx] = val
    st.session_state.ingredients_valid[idx] = ok and bool(val.strip())
    if val.strip():
        inject_input_border_color(key, primary if st.session_state.ingredients_valid[idx] else "#DC2626")
    if val.strip() and not st.session_state.ingredients_valid[idx]:
        if suggestion:
            st.caption(f"❌ Not recognized. Did you mean **{suggestion}**?")
        else:
            st.caption("❌ Not recognized. Try a common ingredient name.")
    elif val.strip() and st.session_state.ingredients_valid[idx]:
        st.caption("✅ Looks good!")

# Draw dynamic inputs
added_this_run = False
for i in range(len(st.session_state.ingredients_raw)):
    render_ingredient_input(i)
    if i == len(st.session_state.ingredients_raw) - 1:
        if st.session_state.ingredients_valid[i] and st.session_state.ingredients_raw[i].strip() and not added_this_run:
            st.session_state.ingredients_raw.append("")
            st.session_state.ingredients_valid.append(False)
            added_this_run = True

# Validated list (canonicalize when fuzzy-accepted)
validated: List[str] = []
for val, ok in zip(st.session_state.ingredients_raw, st.session_state.ingredients_valid):
    if ok and val.strip():
        ok2, canon, sugg = ingredient_valid(val)
        validated.append(canon if canon else normalize_text(val))

st.markdown("---")

# ---------------- Decision & Suggestions ----------------
colL, colR = st.columns([2,1], gap="large")

with colL:
    st.subheader("🧾 Recipe Suggestions (from the Web)")
    if len(validated) == 0:
        st.info("Add your first ingredient to get started.")
    else:
        enough, reasons, suggestions = need_more_for_cook(validated)
        bad = has_bad_combo(validated)
        # Helper pills
        st.markdown("**Your ingredients:** " + " ".join([f"<span class='badge'>{i}</span>" for i in validated]), unsafe_allow_html=True)

        if not enough:
            # Recommend more ingredients to avoid inedible dishes
            if bad:
                st.warning(f"⚠️ That combo can be inedible: {', '.join(bad)}. Consider adding:")
            else:
                st.info("Not quite enough to make a sensible dish. Consider adding:")
            if suggestions:
                st.markdown(" " + " ".join([f"<span class='badge'>{s}</span>" for s in suggestions]), unsafe_allow_html=True)

            # user can choose to search anyway
            proceed = st.toggle("Search online recipes anyway", value=False)
            if not proceed:
                st.stop()

        # Search TheMealDB by intersecting ingredient filters
        with st.spinner("Searching real recipes online…"):
            results = intersect_meals_by_ingredients(validated[:6])  # keep intersection manageable
        if not results:
            st.error("No exact recipe found matching all ingredients. Try adding one of the suggestions above or remove a rare ingredient.")
        else:
            # Show top 3 results with real image + source link
            for meal in results[:3]:
                title = meal.get("strMeal")
                img = meal.get("strMealThumb")
                source = meal.get("strSource") or meal.get("strYoutube")
                area = meal.get("strArea")  # origin / cuisine
                # Only show flag if a clear country exists (TheMealDB 'Area' is cuisine; map obvious ones)
                flag = ""
                iso2 = None
                country_map = {"Italian":"IT","Thai":"TH","Indian":"IN","Japanese":"JP","Mexican":"MX","Chinese":"CN","French":"FR","British":"GB","American":"US","Spanish":"ES","Greek":"GR","Turkish":"TR","Moroccan":"MA"}
                if area in country_map:
                    iso2 = country_map[area]
                    flag = "".join([chr(127397 + ord(c)) for c in iso2])

                st.markdown(f"### {title} {' ' + flag if flag else ''}")
                cols = st.columns([3,2])
                with cols[0]:
                    # ingredients list from detail
                    ing_list = []
                    for i in range(1, 21):
                        ing = meal.get(f"strIngredient{i}")
                        meas = meal.get(f"strMeasure{i}")
                        if ing and ing.strip():
                            ing_list.append(f"- {meas.strip() if meas else ''} {ing.strip()}".strip())
                    if ing_list:
                        with st.expander("Ingredients (from source)"):
                            st.markdown("\n".join(ing_list))
                    instr = (meal.get("strInstructions") or "").strip()
                    if instr:
                        st.markdown("**Instructions (from source):**")
                        st.write(instr[:800] + ("…" if len(instr) > 800 else ""))
                    if source:
                        st.markdown(f"[Open original source]({source})")
                with cols[1]:
                    if img:
                        st.image(img, caption="Source image", use_container_width=True)
                st.markdown("---")

with colR:
    st.subheader("➕ Add More")
    st.caption("You can keep adding ingredients to refine results.")
    if suggestions:
        st.markdown("**Smart suggestions:** " + " ".join([f"<span class='badge'>{s}</span>" for s in suggestions]), unsafe_allow_html=True)
    st.markdown("Tip: add an **aromatic** (garlic/onion), a **protein/carb**, a **flavor** (salt/soy/tomato paste), and a **fat/liquid** (olive oil/coconut milk).")

st.markdown("---")
st.caption("No AI recipes are generated. Results come from public recipe sources (TheMealDB). Images are from the linked recipe pages. Flags are shown only when origin is clear.")
