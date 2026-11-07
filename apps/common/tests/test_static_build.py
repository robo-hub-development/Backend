import pytest
from django.core.management import call_command


def test_the_production_static_build_succeeds(settings, tmp_path):
    """Render runs `collectstatic` with WhiteNoise's manifest storage, which follows every url() and
    sourceMappingURL inside the CSS/JS files and fails the whole deploy when one points at a missing file
    (this happened with the bundled editor's source map). Running it here catches that before a deploy."""
    pytest.importorskip("whitenoise")
    static_root = tmp_path / "static"  # not tmp_path itself: other fixtures use it as MEDIA_ROOT
    settings.STATIC_ROOT = static_root
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }
    call_command(
        "collectstatic", interactive=False, verbosity=0
    )  # raises MissingFileError on a bad reference
    assert (static_root / "staticfiles.json").exists()
    assert list(
        (static_root / "richtext" / "vendor").glob("quill.*.js")
    )  # the editor is hashed and published


def test_bundled_vendor_files_do_not_reference_source_maps_we_do_not_ship():
    from pathlib import Path

    vendor = Path(__file__).parents[2] / "common" / "static" / "richtext" / "vendor"
    for file in [*vendor.glob("*.js"), *vendor.glob("*.css")]:
        text = file.read_text()
        assert "sourceMappingURL" not in text, f"{file.name} points at a source map that is not bundled"
