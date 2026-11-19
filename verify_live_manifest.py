
import requests
import xml.etree.ElementTree as ET
from src.manifest import _dedupe_duplicate_attributes, _close_unterminated_elements

url = "https://raw.githubusercontent.com/y1-community/innioasis-updater/refs/heads/main/slidia_manifest.xml"
try:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    xml_text = response.text
    
    deduped = _dedupe_duplicate_attributes(xml_text)
    repaired = _close_unterminated_elements(deduped)
    root = ET.fromstring(repaired)
    
    found = False
    for pkg in root.findall('package'):
        name = pkg.get('name')
        print(f"Found package: {name}")
        if name and 'Reborn' in name:
            ifw = pkg.get('install_first_when', '')
            print(f"Reborn package found. install_first_when: {ifw}")
            if 'y1-community/y2-ata-rom' in ifw:
                print("SUCCESS: y1-community/y2-ata-rom is in install_first_when.")
                found = True
            else:
                print("FAILURE: y1-community/y2-ata-rom is NOT in install_first_when.")
            break
    if not found:
        print("Reborn package not found in live manifest.")

except Exception as e:
    print(f"Error: {e}")
