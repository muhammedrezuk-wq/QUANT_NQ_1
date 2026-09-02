# -*- coding: utf-8 -*-
"""اختبار حماية API — المرحلة 16.

القاعدة:
  host != 127.0.0.1 + غياب مفتاح مصادقة صالح → STARTUP = FAIL
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock


def test_remote_without_api_key_fails():
    """Remote governance بدون مفتاح = RuntimeError."""
    # تهيئة البيئة
    with tempfile.TemporaryDirectory() as tmpdir:
        data_root = Path(tmpdir)
        remote_flag = data_root / "governance" / "remote_on.txt"
        remote_flag.parent.mkdir(parents=True, exist_ok=True)
        remote_flag.write_text("on")

        # لا مفتاح API
        with mock.patch.dict(os.environ, {"QUANT_GOV_API_KEY": ""}, clear=False):
            with mock.patch("governance.server.REMOTE_FLAG", remote_flag):
                with mock.patch("governance.server.GOV_API_KEY", ""):
                    try:
                        from governance.server import main
                        main()
                        assert False, "يجب أن يرمي RuntimeError"
                    except RuntimeError as e:
                        assert "SECURITY_VIOLATION" in str(e)
                        print(f"  ✓ Remote بدون مفتاح: STARTUP = FAIL")


def test_remote_with_api_key_succeeds():
    """Remote governance مع مفتاح = يسمح (يُربط 0.0.0.0)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_root = Path(tmpdir)
        remote_flag = data_root / "governance" / "remote_on.txt"
        remote_flag.parent.mkdir(parents=True, exist_ok=True)
        remote_flag.write_text("on")

        with mock.patch.dict(os.environ, {"QUANT_GOV_API_KEY": "test_key_12345"}, clear=False):
            with mock.patch("governance.server.REMOTE_FLAG", remote_flag):
                with mock.patch("governance.server.GOV_API_KEY", "test_key_12345"):
                    with mock.patch("governance.server.ThreadingHTTPServer") as mock_srv:
                        mock_srv.return_value.serve_forever.side_effect = KeyboardInterrupt()
                        try:
                            from governance.server import main
                            main()
                        except (KeyboardInterrupt, SystemExit):
                            pass
                        # تأكد أن ThreadingHTTPServer استُدعي بـ 0.0.0.0
                        call_args = mock_srv.call_args
                        bind_addr = call_args[0][0] if call_args else ("", 0)
                        assert bind_addr[0] == "0.0.0.0", f"Expected 0.0.0.0, got {bind_addr[0]}"
                        print(f"  ✓ Remote مع مفتاح: STARTUP OK — bind=0.0.0.0")


def test_localhost_no_key_is_fine():
    """localhost بدون مفتاح = مسموح (bind=127.0.0.1)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_root = Path(tmpdir)
        # لا remote flag
        with mock.patch.dict(os.environ, {"QUANT_GOV_API_KEY": ""}, clear=False):
            with mock.patch("governance.server.REMOTE_FLAG", data_root / "nonexistent"):
                with mock.patch("governance.server.GOV_API_KEY", ""):
                    with mock.patch("governance.server.ThreadingHTTPServer") as mock_srv:
                        mock_srv.return_value.serve_forever.side_effect = KeyboardInterrupt()
                        try:
                            from governance.server import main
                            main()
                        except (KeyboardInterrupt, SystemExit):
                            pass
                        call_args = mock_srv.call_args
                        bind_addr = call_args[0][0] if call_args else ("", 0)
                        assert bind_addr[0] == "127.0.0.1", f"Expected 127.0.0.1, got {bind_addr[0]}"
                        print(f"  ✓ localhost بدون مفتاح: OK — bind=127.0.0.1")


_ALL_TESTS = [
    test_remote_without_api_key_fails,
    test_remote_with_api_key_succeeds,
    test_localhost_no_key_is_fine,
]


def run() -> int:
    passed = 0
    failed = 0
    for test in _ALL_TESTS:
        try:
            test()
            passed += 1
            print(f"✓ {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"✗ {test.__name__}: {exc}")
    print(f"\n{'='*50}")
    print(f"المرحلة ١٦ — API Security: {passed} نجح · {failed} فشل")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(run())
