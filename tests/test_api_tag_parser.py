"""
Tests for api.parsers.tag_parser – using real tag page HTML files.
"""
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import pytest

from api.parsers.tag_parser import parse_tag_page
from api.models import TagOption, TagCategory, TagPageResult

HTML_DIR = os.path.join(project_root, 'html')


def _load_html(filename):
    path = os.path.join(HTML_DIR, filename)
    if not os.path.exists(path):
        pytest.skip(f'HTML test file not found: {filename}')
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


# ===================================================================
# tag_单体作品.html – single selection (c7=28)
# Best file for full ID extraction since only one category selected.
# ===================================================================

class TestTagPageSingleSelection:
    """Tests using tag_单体作品.html (URL: /tags?c7=28)."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.html = _load_html('tag_单体作品.html')
        self.result = parse_tag_page(self.html)

    def test_has_movie_list(self):
        assert self.result.has_movie_list is True
        assert len(self.result.movies) > 0

    def test_has_categories(self):
        assert len(self.result.categories) > 0
        cat_names = [c.name for c in self.result.categories]
        assert '基本' in cat_names
        assert '年份' in cat_names
        assert '主題' in cat_names
        assert '角色' in cat_names
        assert '服裝' in cat_names
        assert '體型' in cat_names
        assert '行爲' in cat_names
        assert '玩法' in cat_names
        assert '類別' in cat_names
        assert '時長' in cat_names

    def test_current_selections(self):
        assert '7' in self.result.current_selections
        assert self.result.current_selections['7'] == '28'

    def test_selected_tag_identified(self):
        cat7 = self.result.get_category_by_id('7')
        assert cat7 is not None
        selected = cat7.get_selected()
        assert len(selected) == 1
        assert selected[0].name == '單體作品'
        assert selected[0].tag_id == '28'

    # --- ID mapping from non-selected categories ---

    def test_basic_category_ids(self):
        """c10: 基本 – all tags should have IDs since c10 is not selected."""
        cat = self.result.get_category_by_id('10')
        assert cat is not None
        assert cat.name == '基本'
        id_map = cat.get_id_to_name_map()
        assert id_map.get('6') == '可播放'
        assert id_map.get('7') == '中字可播放'
        assert id_map.get('1') == '含磁鏈'
        assert id_map.get('2') == '含字幕'
        assert id_map.get('4') == '單體影片'
        assert id_map.get('3') == '含預覽圖'
        assert id_map.get('5') == '含預覽視頻'

    def test_year_category_ids(self):
        """c11: 年份 – year values as IDs."""
        cat = self.result.get_category_by_id('11')
        assert cat is not None
        id_map = cat.get_id_to_name_map()
        assert id_map.get('2026') == '2026'
        assert id_map.get('2025') == '2025'
        assert id_map.get('2001') == '2001'

    def test_theme_category_ids(self):
        """c1: 主題 – verify key mappings."""
        cat = self.result.get_category_by_id('1')
        assert cat is not None
        assert cat.name == '主題'
        id_map = cat.get_id_to_name_map()
        assert id_map.get('23') == '淫亂真實'
        assert id_map.get('51') == '出軌'
        assert id_map.get('52') == '強姦'
        assert id_map.get('54') == '亂倫'
        assert id_map.get('64') == '女同性戀'

    def test_character_category_ids(self):
        """c2: 角色."""
        cat = self.result.get_category_by_id('2')
        assert cat is not None
        id_map = cat.get_id_to_name_map()
        assert id_map.get('1') == '高中女生'
        assert id_map.get('5') == '美少女'

    def test_body_type_category_ids(self):
        """c4: 體型 – the user's example: c4=15 should be 熟女."""
        cat = self.result.get_category_by_id('4')
        assert cat is not None
        assert cat.name == '體型'
        id_map = cat.get_id_to_name_map()
        assert id_map.get('15') == '熟女'
        assert id_map.get('17') == '巨乳'

    def test_behavior_category_ids(self):
        """c5: 行爲."""
        cat = self.result.get_category_by_id('5')
        assert cat is not None
        id_map = cat.get_id_to_name_map()
        assert id_map.get('14') == '乳交'
        assert id_map.get('18') == '中出'

    def test_play_category_ids(self):
        """c6: 玩法."""
        cat = self.result.get_category_by_id('6')
        assert cat is not None
        id_map = cat.get_id_to_name_map()
        # Verify at least some mappings exist
        assert len(id_map) > 10

    def test_clothing_category_ids(self):
        """c3: 服裝."""
        cat = self.result.get_category_by_id('3')
        assert cat is not None
        id_map = cat.get_id_to_name_map()
        assert id_map.get('3') == '眼鏡'
        assert id_map.get('43') == '角色扮演'

    def test_duration_category_ids(self):
        """c9: 時長 – uses string IDs like 'lt-45', '45-90'."""
        cat = self.result.get_category_by_id('9')
        assert cat is not None
        assert cat.name == '時長'
        id_map = cat.get_id_to_name_map()
        assert id_map.get('lt-45') == '45分鍾以內'
        assert id_map.get('45-90') == '45-90分鍾'
        assert id_map.get('90-120') == '90-120分鍾'
        assert id_map.get('gt-120') == '120分鍾以上'

    def test_same_category_sibling_tags_have_ids(self):
        """c7: 類別 – siblings of the selected tag should also have IDs
        (multi-select pattern: c7=28,80 for 首次亮相 etc.)."""
        cat = self.result.get_category_by_id('7')
        assert cat is not None
        id_map = cat.get_id_to_name_map()
        # Selected tag
        assert id_map.get('28') == '單體作品'
        # Sibling tags (their hrefs are like c7=28,80)
        assert id_map.get('80') == '首次亮相'
        assert id_map.get('164') == '故事集'
        assert id_map.get('212') == 'VR'
        assert id_map.get('347') == '4K'

    def test_reverse_lookup(self):
        """name_to_id should work as reverse mapping."""
        cat = self.result.get_category_by_id('4')
        name_map = cat.get_name_to_id_map()
        assert name_map['熟女'] == '15'
        assert name_map['巨乳'] == '17'

    def test_full_id_to_name_map(self):
        """get_full_id_to_name_map returns a flat (cid, tid) -> name map."""
        full_map = self.result.get_full_id_to_name_map()
        assert full_map[('4', '15')] == '熟女'
        assert full_map[('1', '23')] == '淫亂真實'
        assert full_map[('7', '28')] == '單體作品'

    def test_get_category_by_name(self):
        cat = self.result.get_category_by_name('體型')
        assert cat is not None
        assert cat.category_id == '4'


# ===================================================================
# tag_单体作品&捆绑&VR.html – multi-select (c6=29, c7=28,212)
# ===================================================================

class TestTagPageMultiSelection:
    """Tests using tag_单体作品&捆绑&VR.html (URL: /tags?c6=29&c7=28,212)."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.html = _load_html('tag_单体作品&捆绑&VR.html')
        self.result = parse_tag_page(self.html)

    def test_current_selections(self):
        assert '6' in self.result.current_selections
        assert self.result.current_selections['6'] == '29'
        assert '7' in self.result.current_selections
        assert self.result.current_selections['7'] == '28,212'

    def test_category7_has_two_selected(self):
        """c7 should have both 單體作品 and VR selected."""
        cat7 = self.result.get_category_by_id('7')
        assert cat7 is not None
        selected = cat7.get_selected()
        selected_names = {s.name for s in selected}
        assert '單體作品' in selected_names
        assert 'VR' in selected_names

    def test_category7_selected_ids(self):
        """Selected tags in c7 should have their IDs resolved."""
        cat7 = self.result.get_category_by_id('7')
        selected = cat7.get_selected()
        selected_ids = {s.tag_id for s in selected}
        assert '28' in selected_ids
        assert '212' in selected_ids

    def test_category6_selected(self):
        """c6 should have 捆綁 selected with ID 29."""
        cat6 = self.result.get_category_by_id('6')
        assert cat6 is not None
        selected = cat6.get_selected()
        assert len(selected) == 1
        assert selected[0].name == '捆綁'
        assert selected[0].tag_id == '29'

    def test_unselected_categories_have_ids(self):
        """Categories without selections should have full ID mappings."""
        cat4 = self.result.get_category_by_id('4')
        assert cat4 is not None
        id_map = cat4.get_id_to_name_map()
        assert id_map.get('15') == '熟女'
        assert id_map.get('17') == '巨乳'


# ===================================================================
# tag_2026&淫乱真实&单体作品&多P&捆绑.html – many selections
# ===================================================================

class TestTagPageManySelections:
    """Tests using tag_2026&淫乱真实&单体作品&多P&捆绑.html
    (URL: /tags?c1=23&c5=24&c6=29&c7=28&c11=2026)."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.html = _load_html('tag_2026&淫乱真实&单体作品&多P&捆绑.html')
        self.result = parse_tag_page(self.html)

    def test_current_selections(self):
        sels = self.result.current_selections
        assert sels.get('1') == '23'
        assert sels.get('5') == '24'
        assert sels.get('6') == '29'
        assert sels.get('7') == '28'
        assert sels.get('11') == '2026'

    def test_selected_tags_resolved(self):
        """All selected tags should have their IDs from the URL."""
        cat1 = self.result.get_category_by_id('1')
        selected1 = cat1.get_selected()
        assert len(selected1) == 1
        assert selected1[0].name == '淫亂真實'
        assert selected1[0].tag_id == '23'

        cat5 = self.result.get_category_by_id('5')
        selected5 = cat5.get_selected()
        assert len(selected5) == 1
        assert selected5[0].name == '多P'
        assert selected5[0].tag_id == '24'

        cat11 = self.result.get_category_by_id('11')
        selected11 = cat11.get_selected()
        assert len(selected11) == 1
        assert selected11[0].name == '2026'
        assert selected11[0].tag_id == '2026'

    def test_has_movies(self):
        assert self.result.has_movie_list is True
        assert len(self.result.movies) > 0

    def test_unselected_categories_still_have_some_ids(self):
        """c2 (角色) and c4 (體型) have no selections so they should
        still carry ID mappings — but on this particular page the tags
        use javascript:; hrefs because multiple other categories are
        selected.  Expect either IDs or empty strings depending on
        the page's rendering."""
        cat4 = self.result.get_category_by_id('4')
        assert cat4 is not None
        # At minimum, the category should be parsed with options
        assert len(cat4.options) > 0


# ===================================================================
# Model-level tests
# ===================================================================

class TestTagModels:
    def test_tag_option_defaults(self):
        opt = TagOption(name='Test')
        assert opt.tag_id == ''
        assert opt.selected is False

    def test_tag_category_maps(self):
        cat = TagCategory(
            category_id='4',
            name='體型',
            options=[
                TagOption(name='熟女', tag_id='15'),
                TagOption(name='巨乳', tag_id='17'),
                TagOption(name='Unknown', tag_id=''),
            ],
        )
        id_map = cat.get_id_to_name_map()
        assert id_map == {'15': '熟女', '17': '巨乳'}

        name_map = cat.get_name_to_id_map()
        assert name_map == {'熟女': '15', '巨乳': '17'}

    def test_tag_page_result_lookup(self):
        result = TagPageResult(
            categories=[
                TagCategory(category_id='1', name='主題'),
                TagCategory(category_id='4', name='體型'),
            ],
        )
        assert result.get_category_by_id('4').name == '體型'
        assert result.get_category_by_name('主題').category_id == '1'
        assert result.get_category_by_id('99') is None
        assert result.get_category_by_name('不存在') is None
