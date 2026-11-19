
import re

file_path = "/Users/ryan/Documents/GitHub/updater-ce-neo/src/ui/flash_page.py"
with open(file_path, 'r') as f:
    content = f.read()

# Fix show_completion_note signature and call to _render_completion_note
new_signature = "def show_completion_note(self, visible: bool, software: str = \"\", model: str = \"\", themes_installed: bool = False, post_install_once: bool = False) -> None:"
pattern = r"def show_completion_note\(\s*self, visible: bool, software: str = \"\", model: str = \"\", themes_installed: bool = False\s*\) -> None:"

content = re.sub(pattern, new_signature, content)
content = content.replace("self._render_completion_note()", "self._render_completion_note(post_install_once=post_install_once)")

with open(file_path, 'w') as f:
    f.write(content)
print("Updated flash_page.py")
