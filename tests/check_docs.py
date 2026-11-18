"""Parse documented installer examples and check packaged screenshots; run no setup."""
from pathlib import Path
import re, shlex, sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import setup
count=0
images=0
for name in ['README.md','GUIDE.en.md']:
    text=(ROOT/name).read_text()
    for block in re.findall(r'```bash\n(.*?)```',text,re.S):
        for line in block.replace('\\\n',' ').splitlines():
            words=shlex.split(line)
            if words[:2]==['bash','install.sh']:
                if words[2:]!=['--version']: setup.parser().parse_args(words[2:])
                count+=1
    for required in ['--save-seller-key','--seller-key-file','--new-site','--public-ip','--email','--user','--home','--skill','/openany/v1/runs','http_json / poll','0600','queued','official']:
        assert required in text,(name,required)
    assert 'http_json / callback' not in text,(name,'delivery mode drift')
    if name=='GUIDE.en.md':
        for required in ['API_SERVER_KEY','config.yaml','--port','fixed_body.instructions','/artifacts','hermes model','401/403']:
            assert required in text,(name,required)
        refs=re.findall(r'!\[[^\]]*\]\(([^)]+)\)',text)
        assert refs,'Guide must include the Dashboard screenshots'
        for ref in refs:
            path=(ROOT/ref).resolve()
            assert path.is_relative_to(ROOT.resolve()) and path.is_file()
            assert path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
            images+=1
print('PASS:',count,'installer examples parsed;',images,'screenshots checked; no operations executed.')
