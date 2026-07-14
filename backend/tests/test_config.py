from app.config import Settings


def test_spot_check_timeout_default_covers_capable_model_latency():
    settings = Settings(_env_file=None)

    assert settings.validation_spot_check_timeout_seconds == 45.0
