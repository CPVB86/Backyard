import pytest

from operations.activate_audio_retention import env_text


def test_activation_preserves_other_settings_and_updates_only_requested_names():
    original = "# preserve\nBACKYARD_API_TOKEN=example-local-placeholder\nBACKYARD_AUDIO_RETENTION_ENABLED=false\n"
    updated = env_text(original, {"BACKYARD_AUDIO_RETENTION_ENABLED": "true", "BACKYARD_AUDIO_REVIEW_DAYS": 14})
    assert "BACKYARD_API_TOKEN=example-local-placeholder\n" in updated
    assert "# preserve\n" in updated
    assert updated.count("BACKYARD_AUDIO_RETENTION_ENABLED=") == 1
    assert "BACKYARD_AUDIO_RETENTION_ENABLED=true\n" in updated
    assert "BACKYARD_AUDIO_REVIEW_DAYS=14\n" in updated
    assert env_text(updated, {"BACKYARD_AUDIO_RETENTION_ENABLED": "true", "BACKYARD_AUDIO_REVIEW_DAYS": 14}) == updated


def test_activation_refuses_ambiguous_duplicate_settings():
    with pytest.raises(ValueError, match="Duplicate"):
        env_text("KEY=one\nKEY=two\n", {"KEY": "three"})
