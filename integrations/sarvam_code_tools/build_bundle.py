"""Build one uploadable SDK module without repository-relative imports."""
from pathlib import Path
import argparse

def bundle():
    root=Path(__file__).parent
    pieces=[root.joinpath('client.py').read_text()]
    for name in ['tools.py','hooks.py']:
        source=root.joinpath(name).read_text()
        source='\n'.join(line for line in source.splitlines() if not line.startswith('from integrations.sarvam_code_tools'))
        pieces.append(source.replace('await client.','await '))
    return '\n\n'.join(pieces)+'\n'

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.write_text(bundle());compile(args.output.read_text(),str(args.output),'exec');print('Single-file Code Tools bundle written and syntax checked.')
