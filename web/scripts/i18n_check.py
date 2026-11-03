import pathlib
import re

keys = set()
for path in pathlib.Path("src").rglob("*.jsx"):
    text = path.read_text()
    for m in re.finditer(r'\bt\(\s*"((?:[^"\\]|\\.)*)"', text):
        keys.add(m.group(1).replace('\\"', '"'))
keys.discard("nav.signin")


def load(name):
    text = pathlib.Path(f"src/i18n/{name}.js").read_text()
    return eval(text[text.index("{"): text.rindex("}") + 1], {"__builtins__": {}}, {})


for name in ("pt", "es", "zh", "ru"):
    d = load(name)
    gap = sorted(k for k in keys if k not in d)
    extra = sorted(k for k in d if k not in keys)
    blank = sorted(k for k, v in d.items() if not str(v).strip())
    print(name, len(d), "missing", len(gap), "extra", len(extra), "blank", len(blank))
    for k in gap[:6]:
        print("   missing:", repr(k))
    for k in extra[:6]:
        print("   extra:", repr(k))
