import os
import sys

sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv
load_dotenv()

from mangum import Mangum
from src.backend.api import app

handler = Mangum(app, lifespan="off")
