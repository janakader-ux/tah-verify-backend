"""Check every local HTML resource and navigation target before publishing."""
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote
root=Path('public').resolve()
errors=[]
class Links(HTMLParser):
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        refs=[attrs[k] for k in ('href','src','poster') if attrs.get(k)]
        if attrs.get('srcset'): refs += [part.strip().split()[0] for part in attrs['srcset'].split(',')]
        for ref in refs:
            u=urlsplit(ref)
            if u.scheme or u.netloc or not u.path: continue
            target=(root/unquote(u.path).lstrip('/')) if u.path.startswith('/') else self.page.parent/unquote(u.path)
            if not any(p.is_file() for p in (target,Path(str(target)+'.html'),target/'index.html')):
                errors.append(f'{self.page.relative_to(root)}: {ref}')
for page in root.rglob('*.html'):
    parser=Links();parser.page=page;parser.feed(page.read_text())
assert not errors, '\n'.join(errors)
print(f'All local links/resources resolve across {len(list(root.rglob("*.html")))} pages.')
