"""Chrome startup chooses the distro browser pair when the cloud image has one."""

import pytest

from selenium.common.exceptions import WebDriverException

from src.scrapers import driver as driver_mod


def _executable(path) -> str:
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


def test_resolve_chrome_binaries_requires_a_matching_pair(tmp_path) -> None:
    browser = _executable(tmp_path / "chromium")
    assert driver_mod.resolve_chrome_binaries((browser,), ()) == (None, None)

    driver_bin = _executable(tmp_path / "chromedriver")
    assert driver_mod.resolve_chrome_binaries((browser,), (driver_bin,)) == (
        browser,
        driver_bin,
    )


def test_resolve_chrome_binaries_ignores_a_missing_env_override(
    tmp_path, monkeypatch
) -> None:
    browser = _executable(tmp_path / "chromium")
    driver_bin = _executable(tmp_path / "chromedriver")
    monkeypatch.setenv("CHROME_BIN", str(tmp_path / "missing-chrome"))
    monkeypatch.setenv("CHROMEDRIVER_PATH", str(tmp_path / "missing-driver"))

    assert driver_mod.resolve_chrome_binaries((browser,), (driver_bin,)) == (
        browser,
        driver_bin,
    )


def test_chrome_driver_uses_the_system_pair(monkeypatch) -> None:
    created: dict = {}

    class FakeDriver:
        def quit(self) -> None:
            created["quit"] = True

    def fake_chrome(*args, **kwargs):
        created["kwargs"] = kwargs
        return FakeDriver()

    monkeypatch.setattr(
        driver_mod, "resolve_chrome_binaries", lambda: ("/usr/bin/chromium", "/usr/bin/chromedriver")
    )
    monkeypatch.setattr(driver_mod.webdriver, "Chrome", fake_chrome)

    with driver_mod.chrome_driver() as active:
        assert isinstance(active, FakeDriver)

    assert created["kwargs"]["options"].binary_location == "/usr/bin/chromium"
    assert created["kwargs"]["service"].path == "/usr/bin/chromedriver"
    assert created["quit"] is True


def test_chrome_driver_explains_status_127(monkeypatch) -> None:
    def fake_chrome(*args, **kwargs):
        raise WebDriverException(
            "Message: Service /home/appuser/.cache/selenium/chromedriver/"
            "linux64/155.0.8059.84/chromedriver unexpectedly exited. "
            "Status code was: 127"
        )

    monkeypatch.setattr(driver_mod, "resolve_chrome_binaries", lambda: (None, None))
    monkeypatch.setattr(driver_mod.webdriver, "Chrome", fake_chrome)

    with pytest.raises(RuntimeError, match="status 127"):
        with driver_mod.chrome_driver():
            pass
