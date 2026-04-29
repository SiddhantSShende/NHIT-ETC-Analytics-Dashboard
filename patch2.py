with open("server.py", "r", encoding="utf-8") as f:
    content = f.read()

target = "canon_plazas = sorted({r[\"canonical_plaza\"] for r in rows})"
replacement = """    canon_plazas = sorted({r["canonical_plaza"] for r in rows})
    if len(canon_plazas) == 0 or (not spv and not round_ and not project and not plaza):
        canon_plazas = SNAPSHOT["plazas"]"""

content = content.replace(target, replacement)

with open("server.py", "w", encoding="utf-8") as f:
    f.write(content)
