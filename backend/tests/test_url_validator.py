"""Tests for URL validator (Stage 2)."""
import asyncio
from types import SimpleNamespace
import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

from app.scrapers.url_validator import (
    URLValidator,
    ValidationResult,
    URLValidationOutput,
    validate_school_url,
    _is_timeout_reason,
    _update_timeout_failure_state,
    TIMEOUT_FAILURE_ATTR_KEY,
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

    def test_normalize_url_adds_https_scheme(self):
        """Bare domains are normalized to https URLs."""
        validator = URLValidator("bg")
        assert validator.normalize_url("www.school.bg") == "https://www.school.bg"

    def test_normalize_url_fixes_malformed_scheme(self):
        """Common malformed scheme variants are normalized."""
        validator = URLValidator("bg")
        assert validator.normalize_url("http//school.bg") == "http://school.bg"
        assert validator.normalize_url("https//school.bg") == "https://school.bg"

    def test_normalize_url_rejects_invalid(self):
        """Empty/invalid URLs return None."""
        validator = URLValidator("bg")
        assert validator.normalize_url("") is None
        assert validator.normalize_url("   ") is None

    def test_normalize_url_compacts_whitespace_in_domain(self):
        """Whitespace inside domain is compacted to improve malformed inputs."""
        validator = URLValidator("bg")
        assert validator.normalize_url("https://bad domain.bg") == "https://baddomain.bg"


class TestTimeoutFailurePolicy:
    """Test timeout-failure tracking helper logic."""

    def test_is_timeout_reason(self):
        assert _is_timeout_reason("Connection timeout")
        assert _is_timeout_reason("Read TIMEOUT from upstream")
        assert not _is_timeout_reason("HTTP error: 403")

    def test_update_timeout_failure_state_increments_and_resets(self):
        attrs, count, terminal = _update_timeout_failure_state(
            attributes={},
            result=ValidationResult.INVALID,
            reason="Connection timeout",
            threshold=3,
        )
        assert count == 1
        assert terminal is False
        assert attrs[TIMEOUT_FAILURE_ATTR_KEY] == 1

        attrs, count, terminal = _update_timeout_failure_state(
            attributes=attrs,
            result=ValidationResult.INVALID,
            reason="Connection timeout",
            threshold=3,
        )
        assert count == 2
        assert terminal is False

        attrs, count, terminal = _update_timeout_failure_state(
            attributes=attrs,
            result=ValidationResult.INVALID,
            reason="HTTP error: 403",
            threshold=3,
        )
        assert count == 0
        assert terminal is False
        assert attrs[TIMEOUT_FAILURE_ATTR_KEY] == 0

    def test_update_timeout_failure_state_reaches_terminal_threshold(self):
        attrs = {}
        for expected in (1, 2, 3):
            attrs, count, terminal = _update_timeout_failure_state(
                attributes=attrs,
                result=ValidationResult.INVALID,
                reason="Connection timeout",
                threshold=3,
            )
            assert count == expected

        assert terminal is True


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

    async def test_validate_url_rejects_directory_like_listing(self):
        """Directory-style pages should not auto-validate as official school websites."""
        validator = URLValidator("bg")

        html_content = """
        <html>
            <head><title>74 СУ Гоце Делчев - фирмен профил</title></head>
            <body>
                <h1>74 СУ Гоце Делчев</h1>
                <p>училище гимназия учител образование клас паралелка</p>
                <p>Каталог на фирми. Добави фирма. Подобни фирми.</p>
                <a href="/firm111-school-a">A</a>
                <a href="/firm222-school-b">B</a>
                <a href="/firm333-school-c">C</a>
                <a href="/firm444-school-d">D</a>
            </body>
        </html>
        """

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.bg/firm385-74-su-goce-delcev"
        mock_response.text = html_content

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result, final_url, reason = await validator.validate_url(
                "https://example.bg/firm385-74-su-goce-delcev",
                use_llm_fallback=False,
                school_name='74 СУ "Гоце Делчев"',
            )

            assert result == ValidationResult.INVALID
            assert final_url is None
            assert "Directory-like listing signals" in reason

    async def test_validate_url_rejects_directory_like_listing_even_with_llm_fallback(self):
        """Directory pages with school-name mismatch should be rejected before LLM fallback."""
        validator = URLValidator("bg")

        html_content = """
        <html>
            <head><title>Каталог на детски градини</title></head>
            <body>
                <h1>Каталог на фирми</h1>
                <p>Детски градини и училища в България</p>
                <p>Добави фирма. Подобни фирми.</p>
                <a href="/firm111-school-a">A</a>
                <a href="/firm222-school-b">B</a>
                <a href="/firm333-school-c">C</a>
                <a href="/firm444-school-d">D</a>
            </body>
        </html>
        """

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.bg/catalog/schools"
        mock_response.text = html_content

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            with patch.object(validator, "_llm_validate", new_callable=AsyncMock) as llm_mock:
                result, final_url, reason = await validator.validate_url(
                    "https://example.bg/catalog/schools",
                    use_llm_fallback=True,
                    school_name='74 СУ "Гоце Делчев"',
                )

            llm_mock.assert_not_called()
            assert result == ValidationResult.INVALID
            assert final_url is None
            assert "Directory-like listing signals" in reason

    async def test_validate_url_rejects_when_expected_school_name_missing(self):
        """School-name mismatch should not be auto-validated as official website."""
        validator = URLValidator("bg")

        html_content = """
        <html>
            <body>
                <h1>Добре дошли</h1>
                <p>Нашето училище предлага качествено образование.</p>
                <p>Ученици и учители работят в модерни класове.</p>
                <p>Информация за прием и записване.</p>
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
                school_name='74 СУ "Гоце Делчев"',
            )

            assert result == ValidationResult.INVALID
            assert final_url is None
            assert "Expected school name not found" in reason

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

    async def test_validate_url_normalizes_missing_scheme(self):
        """Validation fetches normalized URL when scheme is missing."""
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
        mock_response.url = "https://school.bg"
        mock_response.text = html_content

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result, final_url, _ = await validator.validate_url(
                "school.bg",
                use_llm_fallback=False,
            )

            mock_client.get.assert_awaited_once_with("https://school.bg")
            assert result == ValidationResult.VALID
            assert final_url == "https://school.bg"

    async def test_validate_url_retries_timeout_with_longer_timeout(self):
        """Timeout on first pass should retry once with higher timeout."""
        validator = URLValidator("bg")
        validator.http_timeout = 4.0
        validator.retry_http_timeout = 8.0

        html_content = """
        <html><body>
            <h1>Училище</h1>
            <p>Ученици и учители</p>
            <p>Прием на нови ученици</p>
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
            mock_client.get.side_effect = [
                httpx.TimeoutException("timeout"),
                mock_response,
            ]
            mock_client_class.return_value = mock_client

            result, final_url, reason = await validator.validate_url(
                "https://school.bg",
                use_llm_fallback=False,
            )

            assert result == ValidationResult.VALID
            assert final_url == "https://school.bg"
            assert "after timeout retry" in reason
            assert mock_client.get.await_count == 2


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

    async def test_llm_retries_once_with_cheap_tier_on_model_error(self):
        """Model/provider errors on cheap tier retry once with cheap tier."""
        validator = URLValidator("bg")

        cheap_agent = AsyncMock()
        cheap_agent.run.side_effect = Exception("google/gemini is not a valid model ID")

        retry_agent = AsyncMock()
        retry_agent.run.return_value = SimpleNamespace(
            output=URLValidationOutput(
                is_school_website=True,
                confidence=0.93,
                reason="Contains admission, classes, and school contact details",
            )
        )

        with patch(
            "app.scrapers.url_validator.create_agent",
            side_effect=[cheap_agent, retry_agent],
        ) as mock_create_agent:
            result, final_url, reason = await validator._llm_validate(
                "School page content", "https://school.bg"
            )

            assert result == ValidationResult.VALID
            assert final_url == "https://school.bg"
            assert "confidence: 0.93" in reason
            assert mock_create_agent.call_count == 2
            assert mock_create_agent.call_args_list[0].kwargs["tier"] == "cheap"
            assert mock_create_agent.call_args_list[1].kwargs["tier"] == "cheap"

    async def test_llm_timeout_returns_ambiguous(self):
        """LLM calls are bounded and timeout returns ambiguous."""
        validator = URLValidator("bg")
        validator.llm_timeout = 0.01

        mock_agent = AsyncMock()

        async def slow_run(_prompt):
            await asyncio.sleep(0.1)
            return SimpleNamespace(
                output=URLValidationOutput(
                    is_school_website=True,
                    confidence=0.9,
                    reason="slow but valid",
                )
            )

        mock_agent.run.side_effect = slow_run

        with patch("app.scrapers.url_validator.create_agent", return_value=mock_agent):
            result, final_url, reason = await validator._llm_validate(
                "Some content", "https://school.bg"
            )

            assert result == ValidationResult.AMBIGUOUS
            assert final_url == "https://school.bg"
            assert "LLM validation failed" in reason


@pytest.mark.asyncio
class TestValidateSchoolURL:
    """Test main validate_school_url entry point."""

    async def test_validate_school_url_no_db_update(self):
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


class TestBotProtectionDetection:
    """Unit tests for _is_bot_protection_page."""

    def _soup(self, html: str):
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser")

    def test_detects_sucuri_sgcaptcha_meta_refresh(self):
        validator = URLValidator("bg")
        html = (
            '<html><head>'
            '<meta http-equiv="refresh" content="0;/.well-known/sgcaptcha/?r=%2F&y=ipc:1.2.3.4:99">'
            '</head></html>'
        )
        assert validator._is_bot_protection_page(self._soup(html)) is True

    def test_detects_cloudflare_challenge_meta_refresh(self):
        validator = URLValidator("bg")
        html = (
            '<html><head>'
            '<meta http-equiv="refresh" content="0; url=/cdn-cgi/challenge?s=abc">'
            '</head></html>'
        )
        assert validator._is_bot_protection_page(self._soup(html)) is True

    def test_normal_meta_refresh_is_not_bot_protection(self):
        """A regular redirect meta-refresh (e.g. to /home) must not be flagged."""
        validator = URLValidator("bg")
        html = '<html><head><meta http-equiv="refresh" content="3; url=/home"></head></html>'
        assert validator._is_bot_protection_page(self._soup(html)) is False

    def test_page_without_meta_refresh_is_not_bot_protection(self):
        validator = URLValidator("bg")
        html = "<html><body><p>Детска градина</p></body></html>"
        assert validator._is_bot_protection_page(self._soup(html)) is False


@pytest.mark.asyncio
class TestBotProtectionValidation:
    """Validate_url returns VALID (not INVALID) for bot-protected pages."""

    async def test_bot_protection_page_returns_valid(self):
        """A 202 captcha-challenge page should be accepted so the school proceeds to navigation."""
        validator = URLValidator("bg")

        captcha_html = (
            "<html><head>"
            '<link rel="icon" href="data:;">'
            '<meta http-equiv="refresh" content="0;/.well-known/sgcaptcha/?r=%2F&y=ipc:78.83.254.41:123">'
            "</head></html>"
        )
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.url = "https://dg185.bg/"
        mock_response.text = captcha_html

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result, final_url, reason = await validator.validate_url(
                "https://dg185.bg/",
                use_llm_fallback=False,
            )

        assert result == ValidationResult.VALID
        assert final_url == "https://dg185.bg/"
        assert "Bot protection" in reason

    async def test_bot_protection_redirect_url_is_canonicalized(self):
        """Challenge endpoint redirects must store canonical site URL, not captcha path."""
        validator = URLValidator("bg")

        captcha_html = (
            "<html><head>"
            '<meta http-equiv="refresh" content="0;/.well-known/sgcaptcha/?r=%2F&y=ipc:78.83.254.41:123">'
            "</head></html>"
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://dg185.bg/.well-known/sgcaptcha/?r=%2F&y=ipc:78.83.254.41:123"
        mock_response.text = captcha_html

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result, final_url, reason = await validator.validate_url(
                "https://dg185.bg/",
                use_llm_fallback=False,
            )

        assert result == ValidationResult.VALID
        assert final_url == "https://dg185.bg"
        assert "Bot protection" in reason

    async def test_bot_protection_does_not_call_llm(self):
        """LLM should not be called when bot protection is detected."""
        validator = URLValidator("bg")

        captcha_html = (
            "<html><head>"
            '<meta http-equiv="refresh" content="0;/cdn-cgi/challenge?s=xyz">'
            "</head></html>"
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://protected-school.bg/"
        mock_response.text = captcha_html

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            with patch.object(validator, "_llm_validate", new_callable=AsyncMock) as llm_mock:
                result, _, _ = await validator.validate_url(
                    "https://protected-school.bg/",
                    use_llm_fallback=True,
                )

        llm_mock.assert_not_called()
        assert result == ValidationResult.VALID
