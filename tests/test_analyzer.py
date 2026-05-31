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


class TestMatchByFilename:
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
        # Chinese 字符在 Python3 re 中属于 \w，需要非 \w 字符触发边界
        ("白起!剧照.png", "武安侯", "word_boundary"),
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