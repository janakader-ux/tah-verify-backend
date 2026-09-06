"""Create an isolated browser test page in deploy previews only. Never in production."""
from pathlib import Path
import os,shutil,re
root=Path(__file__).resolve().parents[1]
out=root/'public/__qa__'
if out.exists():shutil.rmtree(out)
if os.environ.get('CONTEXT')=='deploy-preview':
 out.mkdir()
 source=(root/'public/apply.html').read_text()
 source=source.replace('<head>','<head><meta name="robots" content="noindex,nofollow" />')
 source=re.sub(r'(src|href)="\./',r'\1="/',source)
 source=source.replace('</head>','<script src="/__qa__/mock.js"></script></head>')
 source=source.replace('<body','<body',1)
 (out/'apply.html').write_text(source)
 shutil.copy(root/'scripts/qa/mock.js',out/'mock.js')
 (out/'checkout.html').write_text('''<!doctype html><html><head><meta name="robots" content="noindex,nofollow"><title>Dummy payment — no charge</title></head><body><h1>Dummy checkout — no real payment</h1><p>No card details are collected. This changes only the isolated preview fixture.</p><button id="paid">Simulate successful payment</button><button id="pending">Simulate declined payment</button><p id="status" role="status"></p><script>const key=new URLSearchParams(location.search).get('case');document.getElementById('paid').onclick=()=>{localStorage.setItem('qa-paid-'+key,'paid');document.getElementById('status').textContent='Dummy payment confirmed. Return to the application and check status.'};document.getElementById('pending').onclick=()=>{localStorage.removeItem('qa-paid-'+key);document.getElementById('status').textContent='Dummy card declined. No payment taken.'};</script></body></html>''')
 (out/'mobile.html').write_text('<!doctype html><html><head><meta name="robots" content="noindex,nofollow"><title>390px mobile workflow test</title></head><body><h1>Mobile viewport: 390 × 844</h1><iframe title="Mobile application" src="/__qa__/apply.html" style="width:390px;height:844px;border:1px solid #333"></iframe></body></html>')
 print('Isolated dummy-payment browser fixture generated for deploy preview.')
else:
 assert not out.exists()
 print('Production build: no dummy-payment fixtures.')
