"""Source-free checks; real browser regression is test_dose_session_browser.mjs."""

import json
import shutil
import subprocess
import unittest

from dose_report_defaults import HTML_MARKER
from dose_session_ui import SESSION_MARKUP, SESSION_SCRIPT


class DoseSessionTests(unittest.TestCase):
    def test_workflow_requires_separate_attestations(self):
        for identifier in ('sessionOperator', 'rmtConfirmed', 'actualMso',
                           'actualConfirmed', 'lockRmt', 'generateSession'):
            self.assertIn(f'id="{identifier}"', SESSION_MARKUP)
        self.assertIn('Preview only', SESSION_MARKUP)
        self.assertIn('tans-measured-dose-v1', HTML_MARKER)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for JS syntax check')
    def test_complete_script_parses(self):
        result = subprocess.run(['node', '--check'], input=SESSION_SCRIPT,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for PDF test')
    def test_pdf_object_offsets_and_stream_lengths(self):
        start = SESSION_SCRIPT.index('function sessionPdf(')
        end = SESSION_SCRIPT.index('function sessionDownload(')
        script = SESSION_SCRIPT[start:end] + r'''
const canvas={width:2400,height:1600,toDataURL:()=>
 'data:image/jpeg;base64,'+Buffer.from([255,216,255,217]).toString('base64')};
(async()=>{const bytes=Buffer.from(await sessionPdf(canvas).arrayBuffer());
const data=bytes.toString('latin1'),at=Number(data.match(/startxref\n(\d+)/)[1]);
if(data.slice(at,at+4)!=='xref')throw Error('Bad xref position');
const lines=data.slice(at).split('\n');
for(let i=1;i<=5;i++){
 const pos=Number(lines[2+i].slice(0,10));
 if(!data.slice(pos).startsWith(i+' 0 obj'))throw Error('Bad object offset');
}
const image=data.indexOf('/Length 4 >>\nstream\n')+'/Length 4 >>\nstream\n'.length;
if(!bytes.subarray(image,image+4).equals(Buffer.from([255,216,255,217])))throw Error('Image stream mismatch');
console.log(JSON.stringify({valid:true}));})();
'''
        result = subprocess.run(['node', '-e', script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'valid': True})


if __name__ == '__main__':
    unittest.main()
