"""Production OCR must stay inside business regions."""
import unittest
from pathlib import Path

from tools.validate_schema import load_jsonc


class BusinessROITest(unittest.TestCase):
    def test_production_ocr_never_scans_the_full_frame(self):
        allowed=set()
        def visit(value, node):
            if isinstance(value, dict):
                if value.get('type')=='OCR':
                    roi=value.get('param',{}).get('roi')
                    if node not in allowed:
                        self.assertIsNotNone(roi,node)
                        self.assertNotIn(roi,[True,[0,0,0,0],[0,0,1280,720]],node)
                for child in value.values():
                    visit(child,node)
            elif isinstance(value,list):
                for child in value:
                    visit(child,node)
        folder=Path(__file__).resolve().parents[1]/'assets/resource/base/pipeline'
        for path in folder.glob('*.json'):
            if path.name!='Example.json':
                for name,node in load_jsonc(path).items():
                    visit(node,name)
