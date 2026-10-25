"""Pin and bundle browser dependencies with their licenses; no runtime CDN."""
import io
import json
import tarfile
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1] / 'web' / 'vendor'
root.mkdir(exist_ok=True)
packages = {'marked': ['lib/marked.umd.js'], 'dompurify': ['dist/purify.min.js'],
            'highlight.js': ['lib/core.js'], 'katex': ['dist/katex.min.js', 'dist/katex.min.css', 'dist/contrib/auto-render.min.js']}
manifest = {}
pins = {'marked':'18.1.0','dompurify':'3.4.16','highlight.js':'11.12.0','katex':'0.19.0'}
for name, paths in packages.items():
    with urllib.request.urlopen('https://registry.npmjs.org/' + name + '/' + pins[name], timeout=30) as r:
        meta = json.load(r)
    version = meta['version']
    with urllib.request.urlopen(meta['dist']['tarball'], timeout=30) as r:
        data = r.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for member in archive.getmembers():
            relative = member.name.removeprefix('package/')
            if member.isfile() and (relative in paths or relative.lower() in {'license', 'license.md', 'license.txt'} or (name == 'katex' and relative.startswith('dist/fonts/'))):
                # Only known tar members; never extract archive-supplied paths.
                out = root / (name + '-LICENSE' if 'license' in relative.lower() else Path(relative).name)
                if '/fonts/' in relative:
                    out = root / 'fonts' / Path(relative).name
                out.parent.mkdir(exist_ok=True)
                out.write_bytes(archive.extractfile(member).read())
    manifest[name] = version
    print(name, version)
# Browser build supplied by the highlight.js project CDN repository.
with urllib.request.urlopen('https://cdnjs.cloudflare.com/ajax/libs/highlight.js/' + manifest['highlight.js'] + '/highlight.min.js', timeout=30) as r:
    (root / 'highlight.min.js').write_bytes(r.read())
(root / 'versions.json').write_text(json.dumps(manifest, indent=2))
