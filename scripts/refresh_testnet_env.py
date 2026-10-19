"""Copy testnet fields from an operator-selected env file; never print values."""
import argparse
from pathlib import Path
from dotenv import dotenv_values, set_key

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("source",type=Path)
    parser.add_argument("destination",type=Path)
    args=parser.parse_args()
    fields=dotenv_values(args.source)
    count=0
    for key,value in fields.items():
        if key.startswith(("BYBIT_TESTNET_","GRVT_TESTNET_")) and value is not None:
            set_key(args.destination,key,value)
            count+=1
    print("Updated testnet setting count:",count)
