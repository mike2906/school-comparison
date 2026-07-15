from app.config import Settings


def test_spot_check_timeout_default_covers_capable_model_latency():
    settings = Settings(_env_file=None)

    assert settings.validation_spot_check_timeout_seconds == 45.0


def test_launch_scope_flags_default_fail_closed():
    settings = Settings(_env_file=None)

    assert settings.publish_summaries is False
    assert settings.publish_website_admission_fields is False
