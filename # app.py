# app.py — Smart Recipe Builder (Prototype)
# -----------------------------------------
# - Incremental ingredient inputs (next box appears only when current is valid)
# - Valid ingredient = blue border; invalid = red border
# - Dietary restrictions + "on a diet" option affect substitutions & instructions
# - Attempts cuisine/origin inference; shows flag only if confidence is high
# - Generates a basic finished-dish image (bottom-right)
#
# To run:
#   pip install -r requirements.txt
#   streamlit run app.py

import re
from typing import List, Dict, Tuple, Optional
import streamlit as st
from rapidfuzz import fuzz, process
from PIL import Image, ImageDraw, ImageFont
import pycountry

# -------------------------- UI SETUP --------------------------
st.set_page_config(page_title="Supanniga Pochana ", page_icon="🍳", layout="wide")
st.title("🍳 Supanniga Pochana")
st.caption("Add ingredients one-by-one. Boxes turn **blue** when valid, **red** if invalid. The next box appears only when the current one is valid.")

# -------------------------- INGREDIENTS DB (Prototype) --------------------------
# A compact but broad set for demo. You can expand this list easily.
VALID_INGREDIENTS = {
    # produce
    "tomato","onion","garlic","ginger","carrot","potato","spinach","broccoli","bell pepper","chili","lime","lemon","basil","cilantro","parsley","mint",
    # proteins
    "chicken","beef","pork","egg","tofu","tempeh","shrimp","salmon","tuna","mushroom","chickpeas","lentils","beans","paneer",
    # carbs & grains
    "rice","jasmine rice","basmati rice","noodles","pasta","spaghetti","tortilla","bread","quinoa","couscous",
    # dairy & fats
    "milk","butter","cream","yogurt","parmesan","cheddar","mozzarella","olive oil","sesame oil","ghee","coconut milk",
    # sauces & spices
    "soy sauce","fish sauce","oyster sauce","tomato paste","curry paste","curry powder","turmeric","cumin","coriander","paprika","chili powder","garam masala",
    "vinegar","balsamic vinegar","sugar","salt","pepper","honey","mustard","miso","gochujang","sriracha","tahini",
    # nuts & extras
    "peanut","peanuts","cashew","almond","walnut","sesame","nori","anchovy","capers","olive","pickles",
}

# simple substitutions for restrictions
SUBSTITUTIONS = {
    "butter": {"vegan": "olive oil", "dairy_free": "olive oil"},
    "milk": {"vegan": "oat milk", "dairy_free": "oat milk"},
    "cream": {"vegan": "coconut cream", "dairy_free": "coconut cream"},
    "yogurt": {"vegan": "soy yogurt", "dairy_free": "soy yogurt"},
    "parmesan": {"vegan": "nutritional yeast", "dairy_free": "nutritional yeast"},
    "cheddar": {"vegan": "vegan cheddar", "dairy_free": "vegan cheddar"},
    "mozzarella": {"vegan": "vegan mozzarella", "dairy_free": "vegan mozzarella"},
    "fish sauce": {"vegan": "soy sauce + kombu", "vegetarian": "soy sauce + kombu"},
    "oyster sauce": {"vegan": "mushroom sauce", "vegetarian": "mushroom sauce"},
    "chicken": {"vegan": "tofu", "vegetarian": "paneer"},
    "beef": {"vegan": "tempeh", "vegetarian": "mushroom"},
    "pork": {"vegan": "tofu", "vegetarian": "mushroom"},
    "shrimp": {"vegan": "tofu", "vegetarian": "mushroom"},
}

RESTRICTION_RULES = {
    "vegan": {"forbid": {"chicken","beef","pork","shrimp","fish sauce","oyster sauce","milk","butter","cream","yogurt","parmesan","cheddar","mozzarella","egg"}},
    "vegetarian": {"forbid": {"chicken","beef","pork","shrimp","fish sauce","oyster sauce"}},
    "dairy_free": {"forbid": {"milk","butter","cream","yogurt","parmesan","cheddar","mozzarella"}},
    "nut_free": {"forbid": {"peanut","peanuts","cashew","almond","walnut"}},
    "gluten_free": {"forbid": {"pasta","spaghetti","bread","tortilla"}},
}

# cuisine hints: count matches; if high enough, we "know" the origin
CUISINE_HINTS: Dict[str, Dict[str, int]] = {
    "Italian": {"tomato":1, "basil":1, "olive oil":1, "parmesan":2, "mozzarella":2, "pasta":2, "spaghetti":2, "balsamic vinegar":1},
    "Thai": {"fish sauce":2, "lime":1, "chili":1, "garlic":1, "coconut milk":2, "basil":1, "jasmine rice":2},
    "Indian": {"garam masala":2, "turmeric":1, "cumin":1, "coriander":1, "ghee":1, "basmati rice":2, "paneer":2},
    "Japanese": {"soy sauce":1, "miso":2, "nori":2, "sesame oil":1, "ginger":1, "rice":1},
    "Mexican": {"tortilla":2, "chili powder":1, "cumin":1, "lime":1, "beans":1, "corn":1, "cilantro":1},
    "Chinese": {"soy sauce":1, "oyster sauce":2, "ginger":1, "garlic":1, "sesame oil":1, "noodles":2},
    "Middle Eastern": {"tahini":2, "cumin":1, "coriander":1, "yogurt":1, "olive oil":1},
}

CUISINE_COUNTRY = {
    "Italian": ("IT", "Italy"),
    "Thai": ("TH", "Thailand"),
    "Indian": ("IN", "India"),
    "Japanese": ("JP", "Japan"),
    "Mexican": ("MX", "Mexico"),
    "Chinese": ("CN", "China"),
    "Middle Eastern": (None, None),  # region, so we will not show a single-country flag
}

# -------------------------- HELPERS --------------------------
def to_flag(iso2: str) -> str:
    """Convert ISO-2 country code to emoji flag."""
    if not iso2 or len(iso2) != 2:
        return ""
    base = 127397  # regional indicator symbol letter A
    return chr(base + ord(iso2[0].upper())) + chr(base + ord(iso2[1].upper()))

def normalize_text(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def validate_ingredient(name: str, valid_set: set) -> Tuple[bool, str, Optional[str]]:
    """
    Returns: (is_valid, normalized_canonical, suggestion)
    - exact match (case-insensitive) -> valid
    - fuzzy match >= 90 -> accept as that canonical
    - fuzzy 75..89 -> invalid but suggest
    - otherwise -> invalid
    """
    q = normalize_text(name)
    if not q:
        return False, "", None
    # exact
    if q in valid_set:
        return True, q, None
    # fuzzy
    match, score, _ = process.extractOne(q, valid_set, scorer=fuzz.WRatio)
    if score >= 90:
        return True, match, None
    elif score >= 75:
        return False, "", match
    return False, "", None

def infer_cuisine(ingredients: List[str]) -> Tuple[Optional[str], int]:
    """Score cuisines by ingredient hints; return (best_cuisine, score)."""
    scores = {}
    ing_set = set(ingredients)
    for cuisine, hints in CUISINE_HINTS.items():
        s = 0
        for ing, w in hints.items():
            if ing in ing_set:
                s += w
        if s > 0:
            scores[cuisine] = s
    if not scores:
        return None, 0
    best = max(scores, key=scores.get)
    return best, scores[best]

def apply_restrictions(ings: List[str], restrictions: List[str]) -> Tuple[List[str], List[str]]:
    """Return (adjusted_ingredients, notes) with simple substitutions."""
    notes = []
    out = ings[:]
    forbids = set()
    for r in restrictions:
        forbids |= RESTRICTION_RULES.get(r, {}).get("forbid", set())
    # substitutions
    replaced = []
    for i, ing in enumerate(out):
        if ing in forbids:
            sub = None
            for r in restrictions:
                sub = SUBSTITUTIONS.get(ing, {}).get(r)
                if sub:
                    break
            if sub:
                notes.append(f"Replaced **{ing}** due to *{', '.join(restrictions)}* ➜ **{sub}**")
                out[i] = sub
                replaced.append(ing)
            else:
                notes.append(f"Removed **{ing}** (restricted: {', '.join(restrictions)})")
                out[i] = None
    out = [x for x in out if x]
    return out, notes

def generate_steps(ings: List[str], cuisine: Optional[str], diet_mode: bool) -> List[str]:
    """Very simple step generator."""
    base_fat = "olive oil"
    if "sesame oil" in ings:
        base_fat = "sesame oil"
    lean_note = " (use less oil, grill/steam where possible)" if diet_mode else ""

    steps = [
        f"Prep: wash/chop your ingredients{lean_note}.",
        f"Heat 1 tbsp {base_fat} in a pan.",
        "Sauté aromatics (e.g., garlic/ginger/onion) until fragrant.",
        "Add main ingredients and cook until tender.",
    ]
    if cuisine == "Italian" and ("tomato" in ings or "tomato paste" in ings):
        steps += ["Add tomato/tomato paste, simmer 10–15 min, season with salt & pepper.",
                  "Finish with basil and (optional) parmesan."]
    elif cuisine == "Thai" and ("fish sauce" in ings or "lime" in ings or "chili" in ings):
        steps += ["Splash in fish/soy sauce, a squeeze of lime, and chilies to taste.",
                  "Balance salty-sour-sweet with sugar or palm sugar."]
    elif cuisine == "Indian" and ("curry powder" in ings or "garam masala" in ings or "turmeric" in ings):
        steps += ["Bloom spices in oil briefly, then add coconut milk/water.",
                  "Simmer 12–15 min until flavors meld."]
    elif cuisine == "Japanese" and ("soy sauce" in ings or "miso" in ings):
        steps += ["Season with soy/miso; add a little mirin or sugar to balance.",
                  "Finish with sesame oil or toasted sesame."]
    elif cuisine == "Mexican" and ("tortilla" in ings or "beans" in ings or "chili powder" in ings):
        steps += ["Season with cumin/chili powder; warm tortillas if using.",
                  "Top with lime and cilantro."]
    elif cuisine == "Chinese" and ("oyster sauce" in ings or "soy sauce" in ings):
        steps += ["Add soy/oyster sauce and a splash of water; stir-fry on high heat.",
                  "Thicken with a little cornstarch slurry if desired."]
    else:
        steps += ["Season to taste and serve hot."]

    return steps

def create_dish_image(title: str, size=(420, 280)) -> Image.Image:
    """Generate a simple 'finished dish' image with the dish title."""
    img = Image.new("RGB", size, (245, 240, 230))
    draw = ImageDraw.Draw(img)
    # Draw a plate-like circle
    cx, cy = size[0] // 2, size[1] // 2
    r = min(size) // 2 - 18
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255), outline=(200, 200, 200), width=3)
    # Title text
    text = title[:30]
    try:
        # Fallback to default font
        font = ImageFont.load_default()
    except:
        font = None
    tw, th = draw.textlength(text, font=font), 12
    draw.text((cx - tw/2, cy - th/2), text, fill=(40, 40, 40), font=font)
    return img

def inject_input_border_color(widget_key: str, color_hex: str):
    """CSS hack to color a specific text_input by its data-key."""
    st.markdown(
        f"""
        <style>
        div[data-testid="stTextInput"][data-baseweb="input"] div[data-baseweb="base-input"] input:focus {{
            outline: none !important;
        }}
        div[data-testid="stTextInput"][data-key="{widget_key}"] div[data-baseweb="base-input"] {{
            border: 2px solid {color_hex} !important;
            box-shadow: 0 0 0 0px {color_hex} !important;
            border-radius: 8px !important;
        }}
        </style>
        """,
        unsafe_allow_html=True
    )

# -------------------------- SIDEBAR CONTROLS --------------------------
with st.sidebar:
    st.header("⚙️ Preferences")
    restrictions = st.multiselect(
        "Dietary restrictions",
        ["vegan","vegetarian","dairy_free","gluten_free","nut_free"],
        default=[]
    )
    diet_mode = st.toggle("I'm on a diet (leaner method)", value=False)
    st.caption("Restrictions will substitute or remove conflicting ingredients.")

# -------------------------- DYNAMIC INGREDIENT INPUTS --------------------------
if "ingredients_raw" not in st.session_state:
    st.session_state.ingredients_raw = [""]  # start with one input box
if "ingredients_valid" not in st.session_state:
    st.session_state.ingredients_valid = [False]

def render_ingredient_input(idx: int):
    key = f"ing_{idx}"
    val = st.text_input(f"Ingredient #{idx+1}", st.session_state.ingredients_raw[idx], key=key)
    # validate current value
    is_valid, canonical, suggestion = validate_ingredient(val, VALID_INGREDIENTS)
    st.session_state.ingredients_raw[idx] = val
    st.session_state.ingredients_valid[idx] = is_valid and bool(val.strip())
    # color the box
    if val.strip():
        inject_input_border_color(key, "#1f77b4" if st.session_state.ingredients_valid[idx] else "#d62728")
    # show helper text
    if val.strip() and not st.session_state.ingredients_valid[idx]:
        if suggestion:
            st.caption(f"❌ Not recognized. Did you mean **{suggestion}**?")
        else:
            st.caption("❌ Not recognized. Try a common ingredient name.")
    elif val.strip() and st.session_state.ingredients_valid[idx]:
        st.caption("✅ Looks good!")

# draw ingredient inputs, adding the next one only if the current is valid
more_possible = True
for i in range(len(st.session_state.ingredients_raw)):
    render_ingredient_input(i)
    # If this is the last input and it's valid & non-empty, we may add one more blank box.
    if i == len(st.session_state.ingredients_raw) - 1:
        if st.session_state.ingredients_valid[i] and st.session_state.ingredients_raw[i].strip():
            if more_possible:
                st.session_state.ingredients_raw.append("")
                st.session_state.ingredients_valid.append(False)
                more_possible = False  # prevent adding more than one per rerun

# Consolidate validated ingredients (canonicalize names)
validated_ingredients: List[str] = []
for val, ok in zip(st.session_state.ingredients_raw, st.session_state.ingredients_valid):
    if ok and val.strip():
        # Map to canonical (exact or fuzzy-accept)
        is_valid, canonical, suggestion = validate_ingredient(val, VALID_INGREDIENTS)
        validated_ingredients.append(canonical if canonical else normalize_text(val))

st.markdown("---")

# -------------------------- GENERATE RECIPE --------------------------
colA, colB = st.columns([2,1])

with colA:
    st.subheader("🧾 Recipe")
    if len(validated_ingredients) < 2:
        st.info("Enter at least **two valid ingredients** to generate a recipe.")
    else:
        # Apply restrictions/substitutions
        adj_ings, notes = apply_restrictions(validated_ingredients, restrictions)

        if not adj_ings:
            st.error("All ingredients were restricted or removed. Please adjust your list or restrictions.")
        else:
            # cuisine inference
            cuisine, score = infer_cuisine(adj_ings)
            confident = bool(cuisine and score >= 3)  # simple confidence rule

            # title
            main = ", ".join(adj_ings[:3]) + ("…" if len(adj_ings) > 3 else "")
            title = f"{cuisine + ' ' if confident else ''}{main.title()} Skillet"

            # steps
            steps = generate_steps(adj_ings, cuisine if confident else None, diet_mode)

            # header line with optional flag
            header_flag = ""
            if confident and cuisine in CUISINE_COUNTRY and CUISINE_COUNTRY[cuisine][0]:
                iso2, country_name = CUISINE_COUNTRY[cuisine]
                header_flag = to_flag(iso2) + "  "
                st.write(f"**Origin:** {header_flag}{country_name} *(inferred)*")

            st.markdown(f"### {title}")
            st.markdown("**Ingredients:** " + ", ".join(adj_ings))
            if notes:
                with st.expander("Substitutions/Notes due to restrictions"):
                    for n in notes:
                        st.markdown("- " + n)

            st.markdown("**Instructions:**")
            for i, step in enumerate(steps, 1):
                st.markdown(f"{i}. {step}")

            st.markdown("**Serving suggestion:** Serve hot. Adjust salt/acid/heat to taste.")

with colB:
    st.subheader("🍽️ Finished Dish")
    # Always show a dish image if we have enough info
    if len(validated_ingredients) >= 2:
        main = ", ".join(validated_ingredients[:2])
        img = create_dish_image(f"{main.title()}")
        st.image(img, use_column_width=True, caption="Generated preview")
    else:
        st.info("Your dish preview will appear here.")

# Footer
st.markdown("---")
st.caption("Prototype for coursework — flags shown only when cuisine inference is confident. This is a simplified demo; expand ingredient lists, rules, and images as you iterate.")
