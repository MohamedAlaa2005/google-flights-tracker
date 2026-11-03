import os
import sys

# Ensure root directory is on sys.path so modules like web_app, database, routes are found
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from web_app import app

# Vercel entrypoint looks for 'app'
__all__ = ["app"]
