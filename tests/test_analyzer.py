import pytest
from analyzer import is_vague_name, is_image_file, extract_keywords


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