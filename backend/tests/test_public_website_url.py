"""The website address a response shows: never a non-http(s) link, and never discovery's
unchecked pick before URL validation accepts it."""

import pytest

from app.schemas.school import SchoolBase
from app.utils.website_data import public_website_url, safe_website_url


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        ("https://school.bg/", "https://school.bg/"),
        ("  http://school.bg  ", "http://school.bg"),
        ("www.school.bg", "www.school.bg"),  # register addresses often have no scheme
        ("javascript:alert(1)", None),
        ("JavaScript:alert(1)", None),
        ("data:text/html,x", None),
        ("", None),
        (None, None),
    ],
)
def test_safe_website_url(stored, shown):
    assert safe_website_url(stored) == shown


def test_unchecked_discovery_pick_is_withheld_until_validation_accepts_it():
    attrs = {"website_candidate_url": "https://lookalike.example/", "website_candidate_method": "search"}
    assert public_website_url("https://lookalike.example/", attrs, "pending") is None
    assert public_website_url("https://lookalike.example/", attrs, "failed_validate") is None
    assert public_website_url("https://lookalike.example/", attrs, "validated") == "https://lookalike.example/"


def test_guessed_domain_is_withheld_until_validation_accepts_it():
    attrs = {"website_candidate_url": "https://guess.example/", "website_candidate_method": "email_domain:email"}
    assert public_website_url("https://guess.example/", attrs, "pending") is None


def test_register_address_is_shown_while_pending():
    old_pick = {"website_candidate_url": "https://old-pick.example/", "website_candidate_method": "search"}
    assert public_website_url("https://register.example/", old_pick, "pending") == "https://register.example/"
    assert public_website_url("https://register.example/", {}, "pending") == "https://register.example/"
    kept = {"website_candidate_url": "https://register.example/", "website_candidate_method": "existing"}
    assert public_website_url("https://register.example/", kept, "pending") == "https://register.example/"


def test_response_serializes_the_public_address_only():
    base = {"name_i18n": {"bg": "Училище"}, "school_type": "state", "education_level": "primary"}
    pick = SchoolBase.model_validate(
        {
            **base,
            "website_url": "https://lookalike.example/",
            "attributes": {"website_candidate_url": "https://lookalike.example/", "website_candidate_method": "search"},
            "scrape_status": "pending",
        }
    )
    assert pick.model_dump()["website_url"] is None
    assert pick.website_url == "https://lookalike.example/"  # the gates still read the stored value

    script = SchoolBase.model_validate({**base, "website_url": "javascript:alert(1)", "scrape_status": "validated"})
    assert script.model_dump(by_alias=True)["website_url"] is None
