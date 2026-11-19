
import re

file_path = "/Users/ryan/Documents/GitHub/updater-ce-neo/src/ui/main_window.py"
with open(file_path, 'r') as f:
    content = f.read()

# Pattern to find calls to show_completion_note that don't have post_install_once
# It's a bit tricky to match across lines, I'll use a simpler approach
# Replace all occurrences of show_completion_note(...) with the new logic if it's missing post_install_once

def replace_call(match):
    call_content = match.group(0)
    if "post_install_once=" in call_content:
        return call_content
    
    # Extract software and model
    software_match = re.search(r"software\s*=\s*([^,)]+)", call_content)
    model_match = re.search(r"model\s*=\s*([^,)]+)", call_content)
    
    software = software_match.group(1).strip() if software_match else "software"
    model = model_match.group(1).strip() if model_match else "model"
    
    new_call = f"""
            from .. import catalog
            package_list = catalog.packages_for_model_software({model}, {software})
            package = package_list[0] if package_list else None
            post_install_once = getattr(package, "post_install_once", False) if package else False
            {call_content.replace(")", ", post_install_once=post_install_once)")}"""
    return new_call

# Regex to match show_completion_note(...)
# This is hard because of the newlines.
# I'll just manually replace them since there are only 2 more.
print("Manual edit needed for other calls")
