import pytest
from eagle_watcher.analyzer import is_vague_name, is_image_file, extract_keywords


class TestIsVagueName:

    @pytest.mark.parametrize("name,expected", [
        ("12345.jpg", True),
        ("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4.jpg", True),
        ("photo.jpg", False),
        ("白起.jpg", False),
        ("a1b2c.jpg", True),
        ("abcdefghijklmnopqrst.jpg", True),
    ])
    def test_vague_name(self, name, expected):
        assert is_vague_name(name) == expected


class TestIsImageFile:

    @pytest.mark.parametrize("name,expected", [
        ("photo.jpg", True),
        ("photo.jpeg", True),
        ("photo.png", True),
        ("photo.gif", True),
        ("photo.pdf", False),
        ("photo.txt", False),
    ])
    def test_image_extensions(self, name, expected):
        assert is_image_file(name) == expected


class TestExtractKeywords:

    def test_chinese_words(self):
        assert "白起" in extract_keywords("白起_战国.jpg")
        assert "战国" in extract_keywords("白起_战国.jpg")

    def test_english_words(self):
        words = extract_keywords("warrior-king.jpg")
        assert "warrior" in words
        assert "king" in words


class TestDecide:
    """测试 decide() 函数的决策分支"""

    @pytest.mark.parametrize("filename,set_theme,expected_action,expected_theme", [
        # 分支1: 用户设了主题 → 强制归入（无视文件名）
        ("白起剧照.jpg", "武安侯", "import", "武安侯"),
        ("无意义文件名.jpg", "秦始皇", "import", "秦始皇"),
        ("document.pdf", "海报参考", "import", "海报参考"),  # 非图片也强制归入
        # 分支4: 文件名模糊 + 图片 → AI分析
        ("1234567890.jpg", None, "ai_analyze", None),
        # 分支5: 完全不匹配（无主题、无知识库、文件名不模糊）→ inbox
        ("完全无法识别的文件.jpg", None, "inbox", None),
    ])
    def test_decide_paths(self, filename, set_theme, expected_action, expected_theme,
                          mock_data_dir):
        from eagle_watcher.analyzer import decide
        from eagle_watcher.services.state_manager import get_state_manager

        get_state_manager().set_current_theme(set_theme)

        result = decide(filename)
        assert result["action"] == expected_action
        if expected_theme:
            assert result.get("theme") == expected_theme
        else:
            assert result.get("theme") is None

    def test_decide_knowledge_match(self, mock_data_dir):
        """分支3: 知识库匹配 → import（文件名包含已学习的关键词）"""
        from eagle_watcher.analyzer import decide
        from eagle_watcher.knowledge import record_match
        from eagle_watcher.services.state_manager import get_state_manager

        get_state_manager().set_current_theme(None)

        # 预先填充知识库
        record_match("白起.jpg", "白起", "武安侯", ["战国", "武将"])

        result = decide("白起剧照.png")
        assert result["action"] == "import"
        assert result["theme"] == "武安侯"
        assert "战国" in result["tags"]


class TestDecideSourceUrl:
    """测试 decide() 中 source_url 匹配分支（分支2）"""

    @staticmethod
    def _add_source(source_pattern: str, theme: str, tags: list[str], mock_data_dir):
        """向知识库中添加来源匹配规则"""
        from eagle_watcher.knowledge import _load as kb_load, _save as kb_save
        data = kb_load()
        data.setdefault("sources", {})[source_pattern] = {"theme": theme, "tags": tags}
        kb_save(data)

    def test_source_url_match(self, mock_data_dir):
        """source_url 匹配时应优先于知识库文件名匹配"""
        from eagle_watcher.analyzer import decide
        from eagle_watcher.services.state_manager import get_state_manager

        get_state_manager().set_current_theme(None)
        self._add_source("pinterest.com", "设计灵感", ["设计", "灵感"], mock_data_dir)

        result = decide("unknown.png", source_url="https://pinterest.com/pin/123")
        assert result["action"] == "import"
        assert result["theme"] == "设计灵感"
        assert "设计" in result["tags"]
        assert "灵感" in result["tags"]

    def test_source_url_match_filename_not_match(self, mock_data_dir):
        """source_url 匹配时，即使文件名不匹配（模糊文件名）也应使用来源主题"""
        from eagle_watcher.analyzer import decide
        from eagle_watcher.services.state_manager import get_state_manager

        get_state_manager().set_current_theme(None)
        self._add_source("behance.net", "设计灵感", ["设计"], mock_data_dir)

        # 123456.png 是模糊文件名，但 source_url 匹配优先于 ai_analyze
        result = decide("123456.png", source_url="https://behance.net/gallery/abc")
        assert result["action"] == "import"
        assert result["theme"] == "设计灵感"

    def test_source_url_no_match_falls_through(self, mock_data_dir):
        """source_url 不匹配时，应回落后续逻辑"""
        from eagle_watcher.analyzer import decide
        from eagle_watcher.services.state_manager import get_state_manager

        get_state_manager().set_current_theme(None)
        self._add_source("pinterest.com", "设计灵感", ["设计"], mock_data_dir)

        # source_url 不匹配，文件名不模糊且无知识库 → inbox
        result = decide("photo.png", source_url="https://example.com/img.jpg")
        assert result["action"] == "inbox"


class TestDecideNonexistentProject:
    """当前项目不存在时的回退行为（分支1 fallback）"""

    def test_nonexistent_project_fallback_to_knowledge(self, mock_data_dir):
        """项目不存在时回退到知识库匹配"""
        from eagle_watcher.analyzer import decide
        from eagle_watcher.knowledge import record_match
        from eagle_watcher.services.state_manager import get_state_manager

        get_state_manager().set_current_theme("不存在的项目")
        record_match("白起.jpg", "白起", "武安侯", ["战国", "武将"])

        result = decide("白起剧照.png")
        assert result["action"] == "import"
        assert result["theme"] == "武安侯"
        assert "战国" in result["tags"]

    def test_nonexistent_project_no_knowledge_falls_to_inbox(self, mock_data_dir):
        """项目不存在且无知识库匹配时回退到 inbox"""
        from eagle_watcher.analyzer import decide
        from eagle_watcher.services.state_manager import get_state_manager

        get_state_manager().set_current_theme("不存在的项目")

        result = decide("完全无法识别的文件.jpg")
        assert result["action"] == "inbox"
        assert result.get("theme") is None


class TestExtractKeywordsMixed:
    """extract_keywords() 中英文混合文件名"""

    def test_chinese_english_mixed(self):
        """中英文混合的 token 应全部提取"""
        words = extract_keywords("白起_warrior_战国.jpg")
        assert "白起" in words
        assert "warrior" in words
        assert "战国" in words

    def test_chinese_english_no_separator(self):
        """无分隔符的中英文混合（仅在 token 层面）"""
        words = extract_keywords("IMG_2025_设计草案.png")
        assert "IMG" in words
        assert "设计草案" in words  # extract_keywords 不对中文进一步切分
        assert "2025" not in words    # 纯数字应被排除

    def test_special_chars_separators(self):
        """各种特殊分隔符"""
        words = extract_keywords("白起-设计·草案 test_file.png")
        assert "白起" in words
        assert "设计" in words
        assert "test" in words
        assert "file" in words

    def test_single_char_excluded(self):
        """单字符 token 应被排除"""
        words = extract_keywords("a_b_c_d.jpg")
        assert all(len(w) >= 2 for w in words)
        assert len(words) == 0  # 全是单字符


class TestIsVagueNameEdge:
    """is_vague_name() 边界情况"""

    @pytest.mark.parametrize("name,expected", [
        # 短随机串（5-15位，含数字和字母，无常见元音/辅音模式）
        ("a1b2c3.png", True),
        # 纯字母10位（不满足20+规则，没有数字 → 不模糊）
        ("abcdefghij.png", False),
        # 刚好 20 位纯字母（匹配 ^[a-zA-Z]{20,}$）
        ("abcdefghijklmnopqrst.png", True),
        # 刚好 5 位含数字（匹配短随机串）
        ("abc12.png", True),
        # 刚好 16 位含数字（超出短随机范围，但不足30）
        ("a1b2c3d4e5f6g7h8.png", False),
        # 30 位字母数字（匹配 ^[a-zA-Z0-9]{30,}$）
        ("a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6.png", True),
        # 常见单词+数字后缀不在 common_words 精确匹配集内，仍视为模糊
        ("photo1.png", True),
        ("image2.png", True),
    ])
    def test_vague_name_edge_cases(self, name, expected):
        assert is_vague_name(name) == expected


class TestIsImageFileAll:
    """is_image_file() 所有支持的扩展名"""

    @pytest.mark.parametrize("name,expected", [
        ("test.jpg", True),
        ("test.jpeg", True),
        ("test.png", True),
        ("test.gif", True),
        ("test.webp", True),
        ("test.bmp", True),
        ("test.svg", True),
        ("test.ico", True),
        ("test.tiff", True),
        ("test.tif", True),
        ("test.psd", True),
        ("test.ai", True),
        ("test.eps", True),
        ("test.raw", True),
        ("test.heic", True),
        ("test.heif", True),
        # 大写扩展名
        ("test.JPG", True),
        ("test.PNG", True),
        ("test.SVG", True),
        # 非图片
        ("test.pdf", False),
        ("test.txt", False),
        ("test.mp4", False),
        ("test.doc", False),
        ("test", False),  # 无扩展名
    ])
    def test_all_image_extensions(self, name, expected):
        assert is_image_file(name) == expected
    """测试 match_by_filename() 的3种匹配级别（exact_word / word_boundary / partial）+ 无匹配"""

    @staticmethod
    def setup_knowledge():
        """填充知识库供后续匹配测试使用"""
        from eagle_watcher.knowledge import record_match
        record_match("白起.jpg", "白起", "武安侯", ["战国", "武将"])
        record_match("兵马俑.jpg", "兵马俑", "秦始皇", ["秦朝", "文物"])

    @pytest.mark.parametrize("filename,expected_theme,expected_match_type", [
        # exact_word: 关键词是分词后的独立 token（有分隔符 _）
        ("白起_剧照.jpg", "武安侯", "exact_word"),
        ("兵马俑_博物馆.jpg", "秦始皇", "exact_word"),
        # word_boundary: 关键词紧邻非 \w 字符（! 不在 split 模式中）
        # 注：仅 ASCII 关键词触发 \b；中文关键词因 B5（re.ASCII 修复）降为 partial 匹配
        ("白起!剧照.png", "武安侯", "partial"),
        # partial: 关键词是 token 的子串（无分隔符、无 \b 边界）
        ("白起剧照.png", "武安侯", "partial"),
        ("兵马俑展览馆.jpg", "秦始皇", "partial"),
    ])
    def test_matched(self, filename, expected_theme, expected_match_type, mock_data_dir):
        self.setup_knowledge()
        from eagle_watcher.knowledge import match_by_filename
        result = match_by_filename(filename)
        assert result is not None, f"预期 '{filename}' 应匹配主题 {expected_theme}，但返回 None"
        assert result["theme"] == expected_theme
        assert result["match_type"] == expected_match_type

    @pytest.mark.parametrize("filename", [
        "完全无关的文件.jpg",
        "random_name.png",
    ])
    def test_not_matched(self, filename, mock_data_dir):
        self.setup_knowledge()
        from eagle_watcher.knowledge import match_by_filename
        result = match_by_filename(filename)
        assert result is None