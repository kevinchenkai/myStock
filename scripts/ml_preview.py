"""Explicit-path localhost preview, separate from scripts/server.sh production service."""
import argparse
from pathlib import Path
from mystock.web.app import app

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--ml-db',required=True);p.add_argument('--port',type=int,default=8896);a=p.parse_args()
    app.config.update(DB_PATH=str(Path(a.db).resolve()),ML_DB_PATH=str(Path(a.ml_db).resolve()))
    app.run(host='127.0.0.1',port=a.port,use_reloader=False)
if __name__=='__main__':main()
