from app.scrapers.display_name_audit import audit_school_display_name


def test_audit_school_display_name_flags_repeated_fuller_name():
    school = {
        "id": 393,
        "name_i18n": {"bg": 'Частна детска градина "Детска къща Монтесори" ООД'},
        "attributes": {"display_name_i18n": {"bg": "Montessori House", "en": "Montessori House"}},
        "website_url": "https://www.montessori-bulgaria.com/en",
    }
    pages = [
        {
            "source_url": "https://www.montessori-bulgaria.com/en/about-us",
            "page_category": "about",
            "raw_markdown": (
                "# About us\n"
                "Montessori Children’s House (Detska kushta Montessori) helps the children unfold their potential.\n"
            ),
        },
        {
            "source_url": "https://www.montessori-bulgaria.com/en/admission/documents-and-steps-for-enrollment",
            "page_category": "admission",
            "raw_markdown": (
                "Enrollment Steps: 1. Initial visit to Montessori Children's House of both parents and the child.\n"
            ),
        },
    ]

    finding = audit_school_display_name(school, pages)

    assert finding is not None
    assert finding.candidate_name == "Montessori Children’s House"
    assert finding.repeated_pages == 2
    assert finding.core_pages == 2
    assert finding.score >= 8


def test_audit_school_display_name_ignores_generic_page_headings():
    school = {
        "id": 101,
        "name_i18n": {"bg": '101 СУ "Бачо Киро"'},
        "attributes": {},
        "website_url": "https://101su.bg",
    }
    pages = [
        {
            "source_url": "https://101su.bg/about",
            "page_category": "about",
            "raw_markdown": "# About us\n## Contacts\nDocuments and files\n",
        }
    ]

    finding = audit_school_display_name(school, pages)

    assert finding is None


def test_audit_school_display_name_ignores_markdown_logo_wrappers():
    school = {
        "id": 555,
        "name_i18n": {"bg": 'ЧДГ "Свети Георги"'},
        "attributes": {
            "display_name_i18n": {
                "bg": "St. George International School And Preschool",
                "en": "St. George International School And Preschool",
            }
        },
        "website_url": "https://stgeorgeschool.eu",
    }
    pages = [
        {
            "source_url": "https://stgeorgeschool.eu/about/",
            "page_category": "about",
            "raw_markdown": (
                '[![St. George International School And Preschool]'
                '(https://stgeorgeschool.eu/logo.png)](https://stgeorgeschool.eu "St. George International School And Preschool")\n'
                "[St. George International School And Preschool]"
                '(https://stgeorgeschool.eu "St. George International School And Preschool")\n'
            ),
        }
    ]

    finding = audit_school_display_name(school, pages)

    assert finding is None


def test_audit_school_display_name_ignores_copyright_corporate_footer():
    school = {
        "id": 556,
        "name_i18n": {"bg": '"ЧАСТНА ДЕТСКА ГРАДИНА КАНАДСКО МЕЧЕ" ООД'},
        "attributes": {"display_name_i18n": {"bg": "Maple Bear Sofia", "en": "Maple Bear Sofia"}},
        "website_url": "https://sofia-school.maplebear.bg/en/",
    }
    pages = [
        {
            "source_url": "https://sofia-school.maplebear.bg/en/contact/",
            "page_category": "contact",
            "raw_markdown": "## Copyright 2026 Maple Bear Global School Ltd.\n",
        },
        {
            "source_url": "https://sofia-school.maplebear.bg/en/program/",
            "page_category": "programs",
            "raw_markdown": "## Copyright 2026 Maple Bear Global School Ltd.\n",
        },
    ]

    finding = audit_school_display_name(school, pages)

    assert finding is None


def test_audit_recovers_kindercare_centre_identity_from_official_pages():
    school = {
        "id": 541,
        "name_i18n": {"bg": "Частна детска градина АВСландия"},
        "attributes": {"display_name_i18n": {"en": "ABC Landia"}},
        "website_url": "https://abckinder.org",
    }
    pages = [
        {
            "source_url": "https://abckinder.org",
            "page_category": None,
            "raw_markdown": "ABC KinderCare Centre\nWe provide early childhood education.",
        },
        {
            "source_url": "https://abckinder.org/contact-us",
            "page_category": "contact",
            "raw_markdown": "ABC KinderCare Centre\nContact our admissions team.",
        },
    ]

    finding = audit_school_display_name(school, pages)

    assert finding is not None
    assert finding.candidate_name == "ABC KinderCare Centre"
    assert finding.repeated_pages == 2
