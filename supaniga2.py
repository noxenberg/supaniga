# app.py — Smart Recipe Finder (NOT generator)
# -------------------------------------------------
# - Dynamic ingredient inputs: next box appears only when current is valid
# - Valid input = blue border, invalid = red; "cheese" & many types are supported
# - Prevents inedible combos: enforces basic category sufficiency (protein/carb/flavor/veg)
# - Suggests missing categories; only then enables "Find Recipes"
# - Uses real online sources (Spoonacular or Edamam) — shows title, link, and REAL image
# - Dietary restrictions + "on a diet" influence search params
# - Shows cuisine flag only when confident
# - Fully customizable color theme via color pickers (including sidebar)
#
# Keys (use either service):
#   st.secrets["api"]["spoonacular_key"]
#   st.secrets["api"]["edamam_app_id"], st.secrets["api"]["edamam_app_key"]

import re
import os
from typing import List, Dict, Tuple, Optional
import streamlit as st
from rapidfuzz import fuzz, process
import requests
import pycountry

# --------------------------- THEME / COLOR WHEEL ---------------------------
st.set_page_config(page_title="Smart Recipe Finder", page_icon="🍽️", layout="wide")

def apply_theme(primary: str, bg: str, sidebar_bg: str, text: str):
    st.markdown(
        f"""
        <style>
        :root {{
          --primary: {primary};
          --bg: {bg};
          --sidebar: {sidebar_bg};
          --text: {text};
        }}
        html, body, [data-testid="stAppViewContainer"] {{
          background-color: var(--bg) !important;
          color: var(--text) !important;
        }}
        .st-emotion-cache-1jicfl2, [data-testid="stSidebar"] {{
          background-color: var(--sidebar) !important;
        }}
        [data-testid="stMetricValue"], h1, h2, h3, h4, h5, h6, a {{
          color: var(--text) !important;
        }}
        .stButton>button {{
          background-color: var(--primary) !important;
          border-color: var(--primary) !important;
          color: #fff !important;
        }}
        div[data-testid="stTextInput"] div[data-baseweb="base-input"] {{
          border-radius: 8px !important;
        }}
        </style>
        """,
        unsafe_allow_html=True
    )

with st.sidebar:
    st.header("🎨 Theme")
    c_primary = st.color_picker("Primary", "#4F46E5")
    c_bg = st.color_picker("Background", "#0b1117")
    c_sidebar = st.color_picker("Sidebar", "#111827")
    c_text = st.color_picker("Text", "#E5E7EB")

apply_theme(c_primary, c_bg, c_sidebar, c_text)

st.title("🍽️ Smart Recipe Finder (Prototype)")
st.caption("Enter ingredients. We’ll validate them, ensure they’re enough to make a real dish, then search recipes online and link to original sources. No AI recipe generation.")

# --------------------------- INGREDIENT VALIDATION ---------------------------
# Extended set (includes cheese types)
VALID_INGREDIENTS = {
    # produce & herbs
    "tomato","onion","garlic","ginger","carrot","potato","spinach","broccoli","bell pepper","chili","lime","lemon","basil","cilantro","parsley","mint","mushroom",
    # proteins
    "chicken","beef","pork","egg","tofu","tempeh","shrimp","salmon","tuna","chickpeas","lentils","beans","paneer","ham","bacon","sausage",
    # carbs
    "rice","jasmine rice","basmati rice","noodles","pasta","spaghetti","tortilla","bread","quinoa","couscous","udon","soba","ramen noodles","potato",
    # dairy / cheese
    "milk","butter","cream","yogurt","parmesan","cheddar","mozzarella","feta","gouda","brie","camembert","ricotta","pecorino","grana padano","provolone","cream cheese","goat cheese","blue cheese",
    # oils / sauces / spices
    "olive oil","sesame oil","soy sauce","fish sauce","oyster sauce","tomato paste","curry paste","curry powder","turmeric","cumin","coriander","paprika","chili powder","garam masala",
    "vinegar","balsamic vinegar","sugar","salt","pepper","honey","mustard","miso","gochujang","sriracha","tahini","coconut milk","anchovy","capers","olive","pickles","nori"
}

def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def validate_ingredient(name: str) -> Tuple[bool, str, Optional[str]]:
    q = normalize_text(name)
    if not q:
        return False, "", None
    if q in VALID_INGREDIENTS:
        return True, q, None
    match = process.extractOne(q, VALID_INGREDIENTS, scorer=fuzz.WRatio)
    if match and match[1] >= 90:
        return True, match[0], None
    if match and match[1] >= 75:
        return False, "", match[0]
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

# --------------------------- “ENOUGH TO MAKE A RECIPE” RULES ---------------------------
# We require a minimal set of categories before allowing search
CATEGORIES = {
    "protein": {"chicken","beef","pork","egg","tofu","tempeh","shrimp","salmon","tuna","chickpeas","lentils","beans","paneer","ham","bacon","sausage"},
    "carb": {"rice","jasmine rice","basmati rice","noodles","pasta","spaghetti","tortilla","bread","quinoa","couscous","udon","soba","ramen noodles","potato"},
    "flavor": {"soy sauce","fish sauce","oyster sauce","tomato paste","curry paste","curry powder","turmeric","cumin","coriander","paprika","chili powder","garam masala","vinegar","balsamic vinegar","miso","gochujang","sriracha","tahini"},
    "veg": {"tomato","onion","garlic","ginger","carrot","spinach","broccoli","bell pepper","mushroom","olive","capers","pickles","nori"}
}
MIN_REQUIRED = {"protein": 1, "carb": 1, "flavor": 1, "veg": 1}

def categorize(ing: str) -> List[str]:
    cats = []
    for k, s in CATEGORIES.items():
        if ing in s:
            cats.append(k)
    return cats

def sufficiency_report(ings: List[str]) -> Tuple[bool, Dict[str, int], Dict[str, int]]:
    have = {k:0 for k in CATEGORIES}
    for ing in ings:
        for k in categorize(ing):
            have[k] += 1
    missing = {k: max(0, MIN_REQUIRED[k]-have[k]) for k in MIN_REQUIRED}
    ok = all(missing[k] == 0 for k in missing)
    return ok, have, missing

# --------------------------- DIET / RESTRICTIONS → API MAPPING ---------------------------
def map_restrictions_to_api(choices: List[str], diet_mode: bool):
    # Spoonacular: intolerances / diet; Edamam: health / diet labels
    spoonacular_params = {}
    edamam_params = {}
    if "vegan" in choices:
        spoonacular_params["diet"] = "vegan"
        edamam_params.setdefault("health", []).append("vegan")
    if "vegetarian" in choices:
        spoonacular_params["diet"] = "vegetarian"
        edamam_params.setdefault("health", []).append("vegetarian")
    if "dairy_free" in choices:
        edamam_params.setdefault("health", []).append("dairy-free")
    if "gluten_free" in choices:
        spoonacular_params["intolerances"] = "gluten"
        edamam_params.setdefault("health", []).append("gluten-free")
    if "nut_free" in choices:
        edamam_params.setdefault("health", []).append("tree-nut-free")

    # diet_mode: prefer “low-fat” style
    if diet_mode:
        edamam_params.setdefault("diet", []).append("low-fat")
    return spoonacular_params, edamam_params

def country_flag(iso2: Optional[str]) -> str:
    if not iso2 or len(iso2) != 2:
        return ""
    base = 127397
    return chr(base + ord(iso2[0].upper())) + chr(base + ord(iso2[1].upper()))

# --------------------------- API CLIENTS (REAL SOURCES) ---------------------------
def search_spoonacular(ings: List[str], spoon_key: str, diet_map: dict, number: int = 6) -> List[dict]:
    # Complex Search with includeIngredients
    url = "https://api.spoonacular.com/recipes/complexSearch"
    params = {
        "apiKey": spoon_key,
        "includeIngredients": ",".join(ings),
        "addRecipeInformation": "true",
        "number": number,
        "instructionsRequired": "true",
        "fillIngredients": "true",
        "sort": "max-used-ingredients",
    }
    params.update(diet_map)
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json().get("results", [])
    out = []
    for it in data:
        # Extract fields: title, sourceUrl, image, cuisines[]
        out.append({
            "title": it.get("title"),
            "url": it.get("sourceUrl") or it.get("spoonacularSourceUrl"),
            "image": it.get("image"),
            "cuisines": it.get("cuisines") or [],
            "source": "Spoonacular"
        })
    return out

def search_edamam(ings: List[str], app_id: str, app_key: str, diet_map: dict, number: int = 6) -> List[dict]:
    # Edamam Recipe Search
    url = "https://api.edamam.com/search"
    q = " ".join(ings)
    params = {"q": q, "app_id": app_id, "app_key": app_key, "to": number}
    for h in diet_map.get("health", []):
        params.setdefault("health", []).append(h)
    for d in diet_map.get("diet", []):
        params.setdefault("diet", []).append(d)
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json().get("hits", [])
    out = []
    for h in data:
        rec = h.get("recipe", {})
        out.append({
            "title": rec.get("label"),
            "url": rec.get("url"),
            "image": rec.get("image"),
            "cuisines": rec.get("cuisineType") or [],
            "source": rec.get("source") or "Edamam"
        })
    return out

def decide_cuisine_and_flag(cuisines: List[str]) -> Tuple[Optional[str], Optional[str]]:
    # Simple map for single clear cuisine → flag
    map_iso = {
        "italian":"IT","thai":"TH","indian":"IN","japanese":"JP","mexican":"MX","chinese":"CN","french":"FR","spanish":"ES","korean":"KR","vietnamese":"VN","greek":"GR","turkish":"TR"
    }
    if not cuisines:
        return None, None
    cand = normalize_text(cuisines[0])
    iso = map_iso.get(cand)
    if iso:
        country = pycountry.countries.get(alpha_2=iso).name if pycountry.countries.get(alpha_2=iso) else None
        return country, iso
    return None, None

# --------------------------- SIDEBAR: DIET SETTINGS ---------------------------
with st.sidebar:
    st.header("⚙️ Preferences")
    restrictions = st.multiselect(
        "Dietary restrictions",
        ["vegan","vegetarian","dairy_free","gluten_free","nut_free"],
        default=[]
    )
    diet_mode = st.toggle("I'm on a diet (leaner focus)", value=False)
    st.markdown("---")
    st.caption("Recipes are fetched from real sources (Spoonacular / Edamam).")

# --------------------------- DYNAMIC INGREDIENT INPUTS ---------------------------
if "ingredients_raw" not in st.session_state:
    st.session_state.ingredients_raw = [""]  # start with one box
if "ingredients_valid" not in st.session_state:
    st.session_state.ingredients_valid = [False]

def render_ingredient_input(idx: int):
    key = f"ing_{idx}"
    val = st.text_input(f"Ingredient #{idx+1}", st.session_state.ingredients_raw[idx], key=key)
    ok, canonical, suggestion = validate_ingredient(val)
    st.session_state.ingredients_raw[idx] = val
    st.session_state.ingredients_valid[idx] = ok and bool(val.strip())

    if val.strip():
        inject_input_border_color(key, "#1f77b4" if st.session_state.ingredients_valid[idx] else "#d62728")
        if not st.session_state.ingredients_valid[idx]:
            if suggestion:
                st.caption(f"❌ Not recognized. Did you mean **{suggestion}**?")
            else:
                st.caption("❌ Not recognized. Try a common ingredient name.")
        else:
            st.caption("✅ Looks good!")

more_added = False
for i in range(len(st.session_state.ingredients_raw)):
    render_ingredient_input(i)
    if i == len(st.session_state.ingredients_raw) - 1:
        # show next box only when current is valid
        if st.session_state.ingredients_valid[i] and st.session_state.ingredients_raw[i].strip():
            st.session_state.ingredients_raw.append("")
            st.session_state.ingredients_valid.append(False)
            more_added = True

# Optional explicit "Add more" even if already sufficient
if st.button("➕ Add another ingredient"):
    st.session_state.ingredients_raw.append("")
    st.session_state.ingredients_valid.append(False)

# Collect canonical-valid ings
validated_ings = []
for val, ok in zip(st.session_state.ingredients_raw, st.session_state.ingredients_valid):
    if ok and val.strip():
        ok2, can, _ = validate_ingredient(val)
        if ok2:
            validated_ings.append(can)

st.markdown("---")

# --------------------------- CHECK SUFFICIENCY / SUGGESTIONS ---------------------------
enough, have, missing = sufficiency_report(validated_ings)
if not enough:
    st.warning("We need more to make a sensible dish. Consider adding:")
    tips = []
    for k, n in missing.items():
        if n > 0:
            # suggest a few from that category
            sample = list(CATEGORIES[k])[:5]
            tips.append(f"- **{k.title()}**: try {', '.join(sample)}")
    st.markdown("\n".join(tips))

# --------------------------- SEARCH RECIPES (REAL SOURCES) ---------------------------
colA, colB = st.columns([3, 1])

with colA:
    st.subheader("🔎 Find Real Recipes")
    spoon_map, eda_map = map_restrictions_to_api(restrictions, diet_mode)

    can_search = enough and len(validated_ings) >= 3  # prevent inedible combos & milk-pasta memes
    if not can_search:
        st.info("Enter enough complementary ingredients (protein + carb + flavor + veg) to search recipes.")
    else:
        # prefer Spoonacular if available, else Edamam
        use_spoon = "api" in st.secrets and st.secrets["api"].get("spoonacular_key")
        use_eda = "api" in st.secrets and st.secrets["api"].get("edamam_app_id") and st.secrets["api"].get("edamam_app_key")

        results = []
        try:
            if use_spoon:
                results = search_spoonacular(validated_ings, st.secrets["api"]["spoonacular_key"], spoon_map, number=8)
            elif use_eda:
                results = search_edamam(validated_ings, st.secrets["api"]["edamam_app_id"], st.secrets["api"]["edamam_app_key"], eda_map, number=8)
            else:
                st.info("No API keys found. Add Spoonacular or Edamam keys to `st.secrets` to fetch real recipes.")
        except Exception as e:
            st.error(f"Error while searching recipes: {e}")

        if results:
            for rec in results:
                with st.container(border=True):
                    country_name, iso2 = decide_cuisine_and_flag(rec.get("cuisines") or [])
                    flag = country_flag(iso2) if iso2 else ""
                    title = rec["title"] or "Untitled recipe"
                    src = rec["source"]
                    url = rec.get("url")

                    if flag and country_name:
                        st.markdown(f"**Origin:** {flag} {country_name} *(inferred from API)*")
                    st.markdown(f"### {title}")
                    if url:
                        st.markdown(f"[Open recipe source]({url})  \n*via {src}*")

                    cols = st.columns([3,1])
                    with cols[0]:
                        # brief info
                        st.write("**Ingredients searched:**", ", ".join(validated_ings))
                    with cols[1]:
                        # bottom-right style: show real image if available
                        if rec.get("image"):
                            st.image(rec["image"], caption="Recipe image", use_container_width=True)

with colB:
    st.subheader("🧺 Your Basket")
    if validated_ings:
        st.write(", ".join(validated_ings))
    else:
        st.info("Valid ingredients will appear here.")

st.markdown("---")
st.caption("Prototype — We never generate recipes; we only link to sources & show real images. We also require balanced ingredients before searching to avoid inedible results.")
