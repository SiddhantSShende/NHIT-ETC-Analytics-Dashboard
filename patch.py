with open("build_data.py", "r", encoding="utf-8") as f:
    content = f.read()

# find insert point
target1 = """    # Reset SQLite."""
replacement1 = """    all_plazas = set(NHIT_PLAZAS)
    for entry in parsed:
        for r in entry.get("rows", []):
            canon = match_nhit_plaza(r["plaza_name"])
            if canon:
                all_plazas.add(canon)
    all_plazas_list = sorted(list(all_plazas))

    # Reset SQLite."""
content = content.replace(target1, replacement1)

content = content.replace("for p in NHIT_PLAZAS:", "for p in all_plazas_list:")
content = content.replace("canonical_plazas=NHIT_PLAZAS", "canonical_plazas=all_plazas_list")
content = content.replace('"plazas": sorted(NHIT_PLAZAS),', '"plazas": all_plazas_list,')
content = content.replace('"data": {p: {} for p in NHIT_PLAZAS},', '"data": {p: {} for p in all_plazas_list},')
content = content.replace('"monthly_totals": {p: [] for p in NHIT_PLAZAS},', '"monthly_totals": {p: [] for p in all_plazas_list},')

target_tax = """    except FileNotFoundError as e:
        log.warning("Taxonomy unavailable (%s) \u2014 proceeding without it.", e)
        taxonomy = {"rows": [], "spvs": [], "rounds": [], "unmatched": []}"""

replacement_tax = target_tax + """

    known_canon_in_tax = {r["canonical_plaza"] for r in taxonomy["rows"] if r.get("canonical_plaza")}
    extra_plazas = [p for p in all_plazas_list if p not in known_canon_in_tax]
    if extra_plazas:
        for p in extra_plazas:
            taxonomy["rows"].append({
                "s_no": 9999,
                "spv": "Unknown SPV",
                "round": "Unknown Round",
                "project": "Unknown Project",
                "excel_plaza": p,
                "canonical_plaza": p,
            })
        if "Unknown SPV" not in taxonomy["spvs"]:
            taxonomy["spvs"].append("Unknown SPV")
            taxonomy["spvs"].sort()
        if "Unknown Round" not in taxonomy["rounds"]:
            taxonomy["rounds"].append("Unknown Round")
            taxonomy["rounds"].sort()
"""

content = content.replace(target_tax, replacement_tax)

with open("build_data.py", "w", encoding="utf-8") as f:
    f.write(content)
