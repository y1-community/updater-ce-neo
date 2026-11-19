
import requests
import xml.etree.ElementTree as ET
from src.manifest import _dedupe_duplicate_attributes, _close_unterminated_elements

url = "https://innioasis.app/slidia_manifest.xml"
try:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    xml_text = response.text
    print("Successfully fetched live manifest.")
    
    try:
        ET.fromstring(xml_text)
        print("Live manifest parsed successfully!")
    except ET.ParseError as e:
        print(f"Initial parse failed: {e}")
        deduped = _dedupe_duplicate_attributes(xml_text)
        repaired = _close_unterminated_elements(deduped)
        try:
            ET.fromstring(repaired)
            print("Repaired manifest parsed successfully!")
        except ET.ParseError as e:
            print(f"Repaired parse also failed: {e}")
            # print(repaired) # Too big, don't print
except Exception as e:
    print(f"Fetch failed: {e}")
