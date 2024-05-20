import os

from dotenv import load_dotenv

root_env = os.environ.get("SEAPLANE_ENV_FILE", ".env")
loaded = load_dotenv(root_env)
