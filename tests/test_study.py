"""不访问外网、不改写真实数据库的阅读视图检查。"""
import unittest
from datetime import datetime
from types import SimpleNamespace

from app.study import prepare_readings, render_study


def news(id, title, source="people_cn", day=12):
    return SimpleNamespace(id=id, title=title, source=source, category="时政",
                           summary="可靠的原文导读", content="正文", link="https://www.people.com.cn/test",
                           published_at=datetime(2026, 11, day))


class StudyTests(unittest.TestCase):
    def render(self, items, today_items=None, **kwargs):
        return render_study(items=items, today_items=items if today_items is None else today_items,
                            track="all", topic="all", page=1, base="/prefix", total=len(items),
                            now=datetime(2026, 11, 12), **kwargs)

    def test_same_day_same_title_merges_but_followup_does_not(self):
        rows = [news(1, "科技成果发布！"), news(2, "科技成果发布", "xinhuanet"),
                news(3, "科技成果发布", day=11), news(4, "科技成果应用进展")]
        groups = prepare_readings(rows)
        self.assertEqual(len(groups), 3)
        merged = next(group for group in groups if len(group.articles) == 2)
        self.assertEqual(merged.key, "1")

    def test_selected_track_changes_recommendation_order(self):
        rows = [news(1, "党建理论学习"), news(2, "航天科技成果发布")]
        self.assertEqual(prepare_readings(rows, "postgrad")[0].item.id, 1)
        self.assertEqual(prepare_readings(rows, "civil")[0].item.id, 2)
        self.assertEqual(len(prepare_readings(rows, topic="science")), 1)

    def test_untrusted_content_is_escaped_and_unsafe_link_is_excluded(self):
        row = news(1, '<script>alert("title")</script>')
        row.summary = '<img src=x onerror="alert(1)">'
        row.link = "javascript:alert(1)"
        html = self.render([row], source='<script>alert("source")</script>')
        self.assertNotIn('<script>alert', html)
        self.assertNotIn('<img src=x', html)
        self.assertNotIn('href="javascript:', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('/prefix/static/study.css', html)
        self.assertIn('/prefix/news/1', html)

    def test_fallback_only_uses_most_recent_date(self):
        html = self.render([news(1, "最新日期材料", day=11), news(2, "更早日期材料", day=10)], today_items=[])
        self.assertIn("今日暂无更新", html)
        self.assertIn("最新日期材料", html)
        self.assertNotIn("更早日期材料", html)

    def test_empty_database_has_clear_guidance(self):
        html = self.render([])
        self.assertIn("这里还没有阅读材料", html)
        self.assertNotIn('本页阅读进度', html)
        self.assertNotIn('class="reading-card"', html)

    def test_directory_and_cards_use_the_same_page(self):
        rows = [news(i, f"阅读材料 {i}") for i in range(1, 13)]
        html = render_study(items=rows, today_items=rows, track="all", topic="all",
                            page=2, base="/prefix", total=12, now=datetime(2026, 11, 12))
        self.assertIn('href="#reading-11"', html)
        self.assertIn('id="reading-11"', html)
        self.assertNotIn('href="#reading-1"', html)
        self.assertIn('今日阅读 / 2 篇', html)
        self.assertIn('/prefix/today?page=1', html)

    def test_fallback_date_sheet_matches_material_date(self):
        html = self.render([news(1, "往期材料", day=11)], today_items=[])
        self.assertIn('<div class="day">11</div>', html)
        self.assertIn('星期三', html)
        self.assertIn('近期阅读', html)


if __name__ == "__main__":
    unittest.main()
