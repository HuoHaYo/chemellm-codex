"""Validate public asset isolation, links and portable showcase files."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import re
import struct
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit, urljoin

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('website',ROOT/'scripts/build_website.py')
website=importlib.util.module_from_spec(spec)
spec.loader.exec_module(website)


class WebsiteTests(unittest.TestCase):
    def test_public_links_and_explicit_assets(self):
        public=website.public_files()
        self.assertIn('showcase/fr2023/pfd.svg',public)
        self.assertIn('showcase/tianzheng/scene.glb',public)
        self.assertFalse(any(n.startswith(('data/','output/','.chem/')) for n in public))
        for name,data in public.items():
            if not name.endswith('.html'):continue
            for link in re.findall(r'(?:href|src)="([^"]+)"',data.decode()):
                target=urlsplit(link)
                if target.scheme or target.netloc or not target.path:continue
                if target.path.startswith('/downloads/'):continue
                path=unquote(urlsplit(urljoin('/'+name,link)).path).lstrip('/')
                if not path or path.endswith('/'):path+='index.html'
                self.assertTrue(path in public,(name,link,path))

    def test_modified_asset_is_rejected(self):
        actual=Path.read_bytes
        def read(path):
            data=actual(path)
            return data+b'changed' if path.name=='scene.glb' else data
        with patch.object(Path,'read_bytes',read):
            with self.assertRaisesRegex(ValueError,'Public asset changed'):website.public_files()

    def test_model_self_contained_and_svg_passive(self):
        binary=(ROOT/'site/showcase/tianzheng/scene.glb').read_bytes()
        magic,version,length=struct.unpack_from('<III',binary)
        self.assertEqual((magic,version,length),(0x46546C67,2,len(binary)))
        size,kind=struct.unpack_from('<II',binary,12)
        self.assertEqual(kind,0x4E4F534A)
        model=json.loads(binary[20:20+size])
        self.assertGreater(len(model['nodes']),100)
        for collection in ('images','buffers'):
            self.assertFalse(any(i.get('uri') for i in model.get(collection,[])))
        svg=(ROOT/'site/showcase/fr2023/pfd.svg').read_text(encoding='utf-8')
        self.assertNotRegex(svg,r'<(?:script|foreignObject)\b|\bon\w+\s*=')
        self.assertNotIn('data:font',svg)
        self.assertNotIn('<style',svg)
        self.assertIn('data-search=',svg)


if __name__=='__main__':unittest.main(verbosity=2)
