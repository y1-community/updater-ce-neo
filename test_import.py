import sys
import os
sys.path.insert(0, os.getcwd())
try:
    from src.ui.widgets import CollapsibleWidget
    print("Import success!")
except ImportError as e:
    print(f"Import failed: {e}")
except Exception as e:
    print(f"Other error: {e}")
