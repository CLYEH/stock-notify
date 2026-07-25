"""環境變數設定。

設定錯誤要在啟動時以清楚訊息失敗，而不是在跑到一半才炸在無關的地方
（舊版 `float(os.getenv('VOLUME_MULTIPLIER'))` 直接在建構子拋 ValueError）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_notify.config import ConfigError, load_settings

ENV_KEYS = ["LINE_TOKEN", "LINE_USER_ID", "MONGO_URI", "VOLUME_MULTIPLIER", "STOCK_ENV_FILE"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """清掉真實環境變數，並把工作目錄移開以免載入專案根目錄的 .env。"""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)


def test_defaults_when_nothing_is_set() -> None:
    settings = load_settings()
    assert settings.line_token is None
    assert settings.mongo_uri is None
    assert settings.volume_multiplier == 2.0
    assert settings.line_enabled is False
    assert settings.mongo_enabled is False


def test_reads_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.development"
    env_file.write_text(
        "LINE_TOKEN=token-abc\nLINE_USER_ID=Uxxxx\nVOLUME_MULTIPLIER=3.5\n",
        encoding="utf-8",
    )

    settings = load_settings(str(env_file))
    assert settings.line_token == "token-abc"
    assert settings.line_user_id == "Uxxxx"
    assert settings.volume_multiplier == 3.5
    assert settings.line_enabled is True


def test_stock_env_file_selects_the_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """測試時用 STOCK_ENV_FILE=.env.development 把通知改發給個人而非群組。"""
    env_file = tmp_path / ".env.development"
    env_file.write_text("LINE_USER_ID=Upersonal\n", encoding="utf-8")
    monkeypatch.setenv("STOCK_ENV_FILE", str(env_file))

    assert load_settings().line_user_id == "Upersonal"


def test_missing_env_file_is_not_fatal() -> None:
    """CI 沒有 .env，設定全部來自 GitHub Secrets。"""
    assert load_settings("/nonexistent/.env").volume_multiplier == 2.0


def test_invalid_volume_multiplier_names_the_variable() -> None:
    with pytest.raises(ConfigError, match="VOLUME_MULTIPLIER"):
        load_settings(str(_write(".env", "VOLUME_MULTIPLIER=abc\n")))


def test_line_enabled_requires_both_credentials() -> None:
    settings = load_settings(str(_write(".env.partial", "LINE_TOKEN=only-token\n")))
    assert settings.line_enabled is False


def _write(name: str, content: str) -> Path:
    path = Path.cwd() / name
    path.write_text(content, encoding="utf-8")
    return path
