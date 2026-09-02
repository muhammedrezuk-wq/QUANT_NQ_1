# -*- coding: utf-8 -*-
"""
عمليّات خزنة الأسرار — محرّك واحد تستعمله اللوحة وسطر الأوامر معًا.

**لماذا وُجد هذا الملفّ:** كان منطق الخزنة داخل `secrets_admin.py` مشدودًا إلى
`getpass` و`SystemExit`، فلا يصلح لخادم الحوكمة. ونسخه مرّة ثانية للّوحة يعني
تنفيذين للتشفير يفترقان يومًا. فصُرنا: **محرّك واحد هنا**، وسطر الأوامر واجهة
عليه، واللوحة واجهة ثانية.

**قواعده الثابتة:**
  * **لا يُرجع قيمة سرّ أبدًا** — أسماء المفاتيح فقط. لا دالّة هنا تخرج قيمة.
  * **لا يطبع شيئًا** ولا يقرأ من الشاشة — يُرجع `(ok, رسالة, بيانات)` ولا يرمي.
  * **لا يميّز «عبارة خاطئة» عن «ملفّ تالف»** — التمييز يفيد من يخمّن.
  * **العبارة لا تُحفظ ولا تُسجَّل** ولا تبقى بالذاكرة بعد العمليّة.
  * **كل عمليّة تُقيَّد بسجلّ تدقيق** فيه ما جرى ومتى واسم المفتاح — **بلا قيمة**.
"""
from __future__ import annotations

import base64
import json
import os
import re
import stat
import sys
import tempfile
import threading
import functools
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptography.fernet import Fernet, InvalidToken   # noqa: E402

from security.keys import KdfParams, derive_key, wipe  # noqa: E402
from security.providers import VAULT_FORMAT, VAULT_VERSION  # noqa: E402


_VAULT_LOCK = threading.RLock()

def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _VAULT_LOCK:
            return fn(*args, **kwargs)
    return wrapper

DEFAULT_VAULT = ROOT / "runtime" / "secrets.enc"
AUDIT_PATH = ROOT / "var" / "governance" / "vault_audit.log"

KEY_NAME = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
MIN_PASSPHRASE = 12

# رسالة واحدة لكل فشل فتح — لا تميّز بين عبارة خاطئة وملفّ تالف.
ERR_OPEN = "تعذّر فتح الخزنة: عبارة المرور غير صحيحة، أو الملفّ تالف."


def audit(op: str, key: str = "", ok: bool = True, source: str = "cli",
          vault: Path | None = None) -> None:
    """سطر تدقيق بلا أي قيمة سرّيّة — من فعل ماذا وعلى أي خزنة ومتى ونجح أم لا.

    اسم الخزنة جزء من السطر عمدًا: السجلّ ملفّ واحد، وعمليّة على خزنة تجريبيّة
    كانت تظهر بجانب عمليّة على خزنة النظام بلا ما يفرّقهما شيء — سجلّ يلبّس.
    """
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        name = (vault or DEFAULT_VAULT).name
        if (vault or DEFAULT_VAULT).resolve() != DEFAULT_VAULT.resolve():
            name += " (غير خزنة النظام)"
        line = "%s\t%s\t%s\t%s\t%s\t%s\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), source, op,
            key or "-", "ok" if ok else "fail", name)
        with AUDIT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass          # فشل التدقيق لا يُسقط العمليّة، ولا يُخفي نتيجتها


def _write_atomic(path: Path, data: str) -> None:
    """كتابة ذرّية + صلاحيات مالك فقط قبل أن يظهر الملفّ بمكانه."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".vault.", suffix=".tmp")
    try:
        if hasattr(os, "fchmod"):          # ويندوز يرث ACL الحساب؛ لينكس 0600
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _load(path: Path, passphrase: str) -> tuple[dict | None, KdfParams | None]:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        params = KdfParams.from_dict(envelope["kdf"])
    except (OSError, ValueError, KeyError):
        return None, None
    buf = bytearray(passphrase.encode("utf-8"))
    key = derive_key(buf, params)
    try:
        plain = Fernet(bytes(key)).decrypt(base64.b64decode(envelope["payload"]))
    except (InvalidToken, ValueError, KeyError):
        return None, None
    finally:
        wipe(key)
        wipe(buf)
    try:
        data = json.loads(plain.decode("utf-8"))
    except ValueError:
        return None, None
    finally:
        wipe(bytearray(plain))
    return (data, params) if isinstance(data, dict) else (None, None)


def _save(path: Path, data: dict, passphrase: str, params: KdfParams) -> None:
    buf = bytearray(passphrase.encode("utf-8"))
    key = derive_key(buf, params)
    try:
        token = Fernet(bytes(key)).encrypt(
            json.dumps(data, ensure_ascii=False).encode("utf-8"))
    finally:
        wipe(key)
        wipe(buf)
    _write_atomic(path, json.dumps({
        "format": VAULT_FORMAT, "version": VAULT_VERSION, "cipher": "fernet",
        "kdf": params.to_dict(), "payload": base64.b64encode(token).decode(),
    }, ensure_ascii=False, indent=2) + "\n")


# ═══════════════════════════ العمليّات المتاحة ═══════════════════════════════

@_locked
def status(path: Path = DEFAULT_VAULT) -> dict[str, Any]:
    """حالة الخزنة **بلا عبارة مرور** — وجودها وصيغتها فقط، لا محتواها."""
    if not path.exists():
        return {"exists": False, "path": str(path)}
    out: dict[str, Any] = {"exists": True, "path": str(path),
                           "size": path.stat().st_size,
                           "modified": path.stat().st_mtime}
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        out["format"] = envelope.get("format")
        out["version"] = envelope.get("version")
        out["kdf"] = (envelope.get("kdf") or {}).get("algorithm")
        out["valid"] = envelope.get("format") == VAULT_FORMAT
    except (OSError, ValueError):
        out["valid"] = False
    return out


@_locked
def init(passphrase: str, path: Path = DEFAULT_VAULT,
         source: str = "cli") -> tuple[bool, str]:
    if path.exists():
        return False, "الخزنة موجودة أصلًا — لا تُنشأ فوق خزنة قائمة."
    if len(passphrase) < MIN_PASSPHRASE:
        return False, "عبارة المرور قصيرة — الحدّ الأدنى %d محرفًا." % MIN_PASSPHRASE
    try:
        _save(path, {}, passphrase, KdfParams.new())
    except Exception as exc:  # noqa: BLE001
        audit("init", ok=False, source=source, vault=path)
        return False, "تعذّر إنشاء الخزنة: %s" % type(exc).__name__
    audit("init", source=source, vault=path)
    return True, "أُنشئت خزنة فارغة. عبارة المرور لا تُخزَّن بأي مكان — احفظها بنفسك."


@_locked
def list_keys(passphrase: str, path: Path = DEFAULT_VAULT,
              source: str = "cli") -> tuple[bool, str, list[str]]:
    """**أسماء المفاتيح فقط.** لا دالّة بهذا الملفّ تُرجع قيمة سرّ."""
    if not path.exists():
        return False, "لا خزنة بعد.", []
    data, _ = _load(path, passphrase)
    if data is None:
        audit("list", ok=False, source=source, vault=path)
        return False, ERR_OPEN, []
    audit("list", source=source, vault=path)
    return True, "فُتحت الخزنة.", sorted(data)


@_locked
def set_secret(passphrase: str, key: str, value: str,
               path: Path = DEFAULT_VAULT, source: str = "cli") -> tuple[bool, str]:
    if not KEY_NAME.fullmatch(key or ""):
        return False, "اسم المفتاح غير صالح — حروف وأرقام و( _ . - ) فقط."
    if value == "":
        return False, "القيمة فارغة — استعمل الحذف بدل حفظ فراغ."
    if not path.exists():
        return False, "لا خزنة بعد — أنشئها أوّلًا."
    data, params = _load(path, passphrase)
    if data is None or params is None:
        audit("set", key, ok=False, source=source, vault=path)
        return False, ERR_OPEN
    existed = key in data
    data[key] = value
    try:
        _save(path, data, passphrase, params)
    except Exception as exc:  # noqa: BLE001
        audit("set", key, ok=False, source=source, vault=path)
        return False, "تعذّر الحفظ: %s" % type(exc).__name__
    finally:
        data.clear()
    audit("set", key, source=source, vault=path)
    return True, ("حُدِّث «%s»." if existed else "أُضيف «%s».") % key


@_locked
def remove_secret(passphrase: str, key: str, path: Path = DEFAULT_VAULT,
                  source: str = "cli") -> tuple[bool, str]:
    if not path.exists():
        return False, "لا خزنة بعد."
    data, params = _load(path, passphrase)
    if data is None or params is None:
        audit("remove", key, ok=False, source=source, vault=path)
        return False, ERR_OPEN
    if data.pop(key, None) is None:
        data.clear()
        return False, "لا مفتاح باسم «%s»." % key
    try:
        _save(path, data, passphrase, params)
    except Exception as exc:  # noqa: BLE001
        audit("remove", key, ok=False, source=source, vault=path)
        return False, "تعذّر الحذف: %s" % type(exc).__name__
    finally:
        data.clear()
    audit("remove", key, source=source, vault=path)
    return True, "حُذف «%s»." % key


@_locked
def rotate(old_passphrase: str, new_passphrase: str, path: Path = DEFAULT_VAULT,
           source: str = "cli") -> tuple[bool, str]:
    if len(new_passphrase) < MIN_PASSPHRASE:
        return False, "العبارة الجديدة قصيرة — الحدّ الأدنى %d محرفًا." % MIN_PASSPHRASE
    if not path.exists():
        return False, "لا خزنة بعد."
    data, _ = _load(path, old_passphrase)
    if data is None:
        audit("rotate", ok=False, source=source, vault=path)
        return False, ERR_OPEN
    try:
        _save(path, data, new_passphrase, KdfParams.new())   # ملح جديد كذلك
    except Exception as exc:  # noqa: BLE001
        audit("rotate", ok=False, source=source, vault=path)
        return False, "تعذّر التغيير: %s" % type(exc).__name__
    finally:
        data.clear()
    audit("rotate", source=source, vault=path)
    return True, "غُيّرت عبارة المرور وملح الاشتقاق."


@_locked
def archive(path: Path = DEFAULT_VAULT, source: str = "cli") -> tuple[bool, str]:
    """يُزيح الخزنة جانبًا ليمكن إنشاء غيرها. **لا يحذف شيئًا أبدًا.**

    يلزم حين تُنسى عبارة المرور: لا استرجاع بالتصميم (٦٠٠ ألف دورة اشتقاق)،
    والبديل الوحيد خزنة جديدة. والقديمة تبقى على القرص باسم مؤرَّخ —
    فلو عادت العبارة للذاكرة يومًا، محتواها ما زال موجودًا.
    """
    if not path.exists():
        return False, "لا خزنة لإزاحتها."
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    target = path.with_name(path.name + ".مؤرشفة_" + stamp)
    try:
        path.rename(target)
    except OSError as exc:
        audit("archive", ok=False, source=source, vault=path)
        return False, "تعذّرت الإزاحة: %s" % type(exc).__name__
    audit("archive", target.name, source=source, vault=path)
    return True, "أُزيحت القديمة إلى «%s» — لم تُحذف. تقدر تنشئ خزنة جديدة الآن." % target.name


# ═════════════════════ الربط بحساب ويندوز (DPAPI) ═══════════════════════════

def _dpapi_protect(data: bytes) -> bytes | None:
    """يغلّف بايتات بحساب مستخدم ويندوز. لا تُفكّ إلا بنفس الحساب وعلى نفس الجهاز."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        blob_in = _BLOB(len(data), ctypes.cast(
            ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
        blob_out = _BLOB()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    except Exception:  # noqa: BLE001
        return None


@_locked
def bind_windows(passphrase: str, path: Path = DEFAULT_VAULT,
                 blob: Path | None = None, source: str = "cli") -> tuple[bool, str]:
    """يربط الخزنة بحساب ويندوز: بعدها تُفتح بلا عبارة وبلا متغيّر بيئة.

    نغلّف **مفتاح الخزنة المشتقّ** لا العبارة نفسها. والملفّ الناتج لا ينفع على
    جهاز آخر ولا بحساب ويندوز آخر — وهذا هو المطلوب.
    **والعبارة تبقى صالحة كما هي**، فالربط إضافة لا استبدال.
    """
    blob = blob or (path.parent / "device.key")
    if not path.exists():
        return False, "لا خزنة بعد."
    data, params = _load(path, passphrase)
    if data is None or params is None:
        audit("bind_windows", ok=False, source=source, vault=path)
        return False, ERR_OPEN
    data.clear()
    buf = bytearray(passphrase.encode("utf-8"))
    key = derive_key(buf, params)
    try:
        wrapped = _dpapi_protect(bytes(key))
    finally:
        wipe(key)
        wipe(buf)
    if wrapped is None:
        audit("bind_windows", ok=False, source=source, vault=path)
        return False, "ويندوز رفض التغليف (DPAPI غير متاح على هذا النظام)."
    try:
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(wrapped)
    except OSError as exc:
        audit("bind_windows", ok=False, source=source, vault=path)
        return False, "تعذّرت الكتابة: %s" % type(exc).__name__
    audit("bind_windows", blob.name, source=source, vault=path)
    return True, ("رُبطت بحساب ويندوز («%s»). تُفتح الآن بلا عبارة وبلا متغيّر بيئة — "
                  "وعبارتك تبقى صالحة كما هي." % blob.name)


@_locked
def windows_bound(path: Path = DEFAULT_VAULT, blob: Path | None = None) -> bool:
    return (blob or (path.parent / "device.key")).is_file()


@_locked
def audit_tail(limit: int = 30) -> list[dict[str, str]]:
    """آخر عمليّات الخزنة — بلا قيم، للعرض باللوحة."""
    if not AUDIT_PATH.is_file():
        return []
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:][::-1]:
        parts = line.split("\t")
        # السطور القديمة ٥ خانات (قبل إضافة اسم الخزنة) والجديدة ٦.
        # نقرأ الاثنين وإلا اختفى نصف السجلّ بصمت عند التبديل.
        if len(parts) not in (5, 6):
            continue
        out.append({"at": parts[0], "source": parts[1], "op": parts[2],
                    "key": parts[3], "result": parts[4],
                    "vault": parts[5] if len(parts) == 6 else ""})
    return out
