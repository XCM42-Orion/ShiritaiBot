import pytest_asyncio

from shiritaibot.app import Application


@pytest_asyncio.fixture
async def app(tmp_path):
    application = Application(tmp_path)
    await application.start()
    try:
        yield application
    finally:
        await application.close()
