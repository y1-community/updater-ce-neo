with open("src/ui/flash_page.py", "r") as f:
    content = f.read()

target = 'import sys\nprint(f"DEBUG: sys.path={sys.path}")\n'

if target in content:
    with open("src/ui/flash_page.py", "w") as f:
        f.write(content.replace(target, ""))
    print("Fixed!")
else:
    print("Target not found!")
