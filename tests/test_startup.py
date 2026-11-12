import importlib
import unittest
from unittest.mock import patch, MagicMock

main = importlib.import_module('app.main')

class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def check_startup(self, count, bootstrap=True):
        settings = MagicMock(bootstrap_recent_news_on_startup=bootstrap, auto_sync_on_startup=False)
        scheduler = MagicMock()
        with patch.object(main, 'init_db'), patch.object(main, 'reset_stale_sync_state'), patch.object(main, 'setup_scheduler', return_value=scheduler), patch.object(main, 'count_news_records', return_value=count), patch.object(main, 'get_settings', return_value=settings), patch.object(main, 'start_background_sync', return_value=True) as sync:
            async with main.lifespan(main.app):
                pass
            scheduler.shutdown.assert_called_once_with(wait=False)
            return sync

    async def test_empty_database_starts_small_recent_sync(self):
        sync = await self.check_startup(0)
        sync.assert_called_once_with('启动近期新闻初始化', months=1, max_pages=2, max_items=40)

    async def test_existing_news_does_not_trigger_history_backfill(self):
        sync = await self.check_startup(300)
        sync.assert_not_called()

    async def test_bootstrap_can_be_disabled_for_empty_database(self):
        sync = await self.check_startup(0, bootstrap=False)
        sync.assert_not_called()

if __name__ == '__main__':
    unittest.main()
