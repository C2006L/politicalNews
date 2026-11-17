"""Collect bounded near-90-day official website supplements."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.official_sources import collect
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--limit',type=int,default=12)
    args=parser.parse_args()
    print(json.dumps(collect(args.output,args.limit),ensure_ascii=True))
