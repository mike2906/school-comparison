"""Tests for URL validator (Stage 2)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.scrapers.url_validator import (
    URLValidator,
    ValidationResult,
    URLValidationOutput,
    validate_school_url,
)


class TestURLValidator:
    """Test URL validator initialization and configuration."""

    def test_init_default_keywords(self):
        """Validator loads default keywords."""
        validator = URLValidator("bg")
        assert len(validator.keywords) > 0
        assert "училище" in validator.keywords

    def test_init_english_keywords(self):
        """Validator loads English keywords."""
        validator = URLValidator("en")
        assert len(validator.keywords) > 0
        assert "school" in validator.keywords

    def test_blocked_domains(self):
        """Blocked domains are detected."""
        validator = URLValidator("bg")

        assert validator._is_blocked_domain("https://facebook.com/school")
        assert validator._is_blocked_domain("https://instagram.com/myschool")
        assert validator._is_blocked_domain("https://bg-mamma.com/topic/school")

        assert not validator._is_blocked_domain("https://school.bg")
        assert not validator._is_blocked_domain("https://училище.bg")


@pytest.mark.asyncio
class TestURLValidatorHeuristics:
    """Test heuristic keyword matching."""

    async def test_validate_url_blocked_domain(self):
        """Blocked domains are rejected immediately."""
        validator = URLValidator("bg")

        result, final_url, reason = await validator.validate_url(
            "https://facebook.com/school",
            use_llm_fallback=False,
        )

        assert result == ValidationResult.INVALID
        assert final_url is None
        assert "Blocked domain" in reason

    async def test_validate_url_timeout(self):
        """Timeout errors are handled."""
        validator = URLValidator("bg")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.side_effect = Exception("Timeout")
            mock_client_class.return_value = mock_client

            result, final_url, reason = await validator.validate_url(
                "https://slow-site.bg",
                use_llm_fallback=False,
            )

            assert result == ValidationResult.INVALID
            assert final_url is None

    async def test_validate_url_strong_keywords(self):
        """URLs with 3+ keywords are validated without LLM."""
        validator = URLValidator("bg")

        # Mock HTML with school keywords
        html_content = """
        <html>
            <body>
                <h1>Добре дошли в нашето училище</h1>
                <p>Информация за прием на ученици</p>
                <p>Нашите учители са високо квалифицирани</p>
                <p>Класове от 1 до 12</p>
            </body>
        </html>
        """

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://school.bg"
        mock_response.text = html_content

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result, final_url, reason = await validator.validate_url(
                "https://school.bg",
                use_llm_fallback=False,
            )

            assert result == ValidationResult.VALID
            assert final_url == "https://school.bg"
            assert "Keyword match" in reason

    async def test_validate_url_no_keywords(self):
        """URLs with no keywords are rejected (or marked ambiguous with LLM)."""
        validator = URLValidator("bg")

        # Mock HTML with no school keywords
        html_content = """
        <html>
            <body>
                <h1>Welcome to our website</h1>
                <p>This is a generic website about various topics.</p>
            </body>
        </html>
        """

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://generic.bg"
        mock_response.text = html_content

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result, final_url, reason = await validator.validate_url(
                "https://generic.bg",
                use_llm_fallback=False,
            )

            assert result == ValidationResult.INVALID
            assert final_url is None
            assert "No school-related keywords" in reason

    async def test_validate_url_ambiguous(self):
        """URLs with 1-2 keywords are marked ambiguous without LLM."""
        validator = URLValidator("bg")

        # Mock HTML with only 1-2 Bulgarian keywords
        html_content = """
        <html>
            <body>
                <h1>Информация за образование</h1>
                <p>Ученици могат да намерят информация тук.</p>
                <p>Много друго съдържание без връзка със училища.</p>
            </body>
        </html>
        """

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://ambiguous.bg"
        mock_response.text = html_content

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result, final_url, reason = await validator.validate_url(
                "https://ambiguous.bg",
                use_llm_fallback=False,
            )

            assert result == ValidationResult.AMBIGUOUS
            assert final_url == "https://ambiguous.bg"
            assert "Ambiguous" in reason

    async def test_validate_url_redirect(self):
        """Redirects are followed and final URL is returned."""
        validator = URLValidator("bg")

        html_content = """
        <html><body>
            <h1>Училище</h1>
            <p>Ученици и учители</p>
            <p>Прием на нови ученици</p>
        </body></html>
        """

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://school-new.bg"  # Redirected URL
        mock_response.text = html_content

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result, final_url, reason = await validator.validate_url(
                "https://school-old.bg",
                use_llm_fallback=False,
            )

            assert result == ValidationResult.VALID
            assert final_url == "https://school-new.bg"  # Final URL after redirect


@pytest.mark.asyncio
class TestURLValidatorLLM:
    """Test LLM fallback validation."""

    async def test_llm_validate_school_website(self):
        """LLM correctly identifies school website."""
        validator = URLValidator("bg")

        # Mock LLM response
        mock_agent = AsyncMock()
        mock_result = AsyncMock()
        mock_result.data = URLValidationOutput(
            is_school_website=True,
            confidence=0.9,
            reason="Contains school information and admission details",
        )
        mock_agent.run.return_value = mock_result

        with patch("app.scrapers.url_validator.create_agent", return_value=mock_agent):
            result, final_url, reason = await validator._llm_validate(
                "School page content here", "https://school.bg"
            )

            assert result == ValidationResult.VALID
            assert final_url == "https://school.bg"
            assert "confidence: 0.90" in reason

    async def test_llm_validate_not_school(self):
        """LLM correctly rejects non-school website."""
        validator = URLValidator("bg")

        # Mock LLM response
        mock_agent = AsyncMock()
        mock_result = AsyncMock()
        mock_result.data = URLValidationOutput(
            is_school_website=False,
            confidence=0.95,
            reason="This is a news article about schools, not a school website",
        )
        mock_agent.run.return_value = mock_result

        with patch("app.scrapers.url_validator.create_agent", return_value=mock_agent):
            result, final_url, reason = await validator._llm_validate(
                "News article content", "https://news.bg/article"
            )

            assert result == ValidationResult.INVALID
            assert final_url is None
            assert "not a school website" in reason

    async def test_llm_validate_low_confidence(self):
        """LLM low confidence results in invalid."""
        validator = URLValidator("bg")

        # Mock LLM response with low confidence
        mock_agent = AsyncMock()
        mock_result = AsyncMock()
        mock_result.data = URLValidationOutput(
            is_school_website=True,
            confidence=0.5,  # Below 0.7 threshold
            reason="Might be a school but not clear",
        )
        mock_agent.run.return_value = mock_result

        with patch("app.scrapers.url_validator.create_agent", return_value=mock_agent):
            result, final_url, reason = await validator._llm_validate(
                "Ambiguous content", "https://ambiguous.bg"
            )

            assert result == ValidationResult.INVALID
            assert final_url is None


@pytest.mark.asyncio
class TestValidateSchoolURL:
    """Test main validate_school_url entry point."""

    async def test_validate_school_url_no_db_update(self, db_session):
        """Validate URL without database update."""
        # Mock HTTP response
        html_content = """
        <html><body>
            <h1>Училище</h1>
            <p>Ученици</p>
            <p>Учители</p>
            <p>Прием</p>
        </body></html>
        """

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://school.bg"
        mock_response.text = html_content

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result, final_url, reason = await validate_school_url(
                school_id=1,
                url="https://school.bg",
                country_code="bg",
                update_db=False,
            )

            assert result == ValidationResult.VALID
            assert final_url == "https://school.bg"
