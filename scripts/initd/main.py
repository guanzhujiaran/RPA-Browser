from scripts.initd.download_browser_forge import download
from scripts.initd.init_database import create_tables
from scripts.initd.install_ungoogled_chromium import install_chromium
import asyncio


async def init_dependencies():

    download()
    create_tables()
    await install_chromium()


if __name__ == "__main__":
    asyncio.run(init_dependencies())
