#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
secrets_admin.py — إدارة مخزن الأسرار من سطر الأوامر.

**واجهة فقط.** كل المنطق في `governance/vault_ops.py`، وهو نفسه المحرّك الذي
تستعمله اللوحة — فلا يوجد تنفيذان للتشفير يفترقان يومًا.
**والطريق المعتاد للمالك هو اللوحة: قسم «الأمان».** هذا الملفّ للطوارئ ولمن
يفضّل الطرفيّة.

    secrets_admin.py init          # إنشاء خزنة جديدة
    secrets_admin.py set KEY       # إضافة/تعديل سرّ (القيمة تُطلب مخفيّة)
    secrets_admin.py list          # أسماء المفاتيح فقط — لا قيم
    secrets_admin.py remove KEY
    secrets_admin.py rotate        # تغيير عبارة المرور
    secrets_admin.py check         # فحص بلا كشف
    secrets_admin.py audit         # آخر عمليّات الخزنة (بلا قيم)

لا يطبع أي سرّ على الشاشة، ولا يكتب أي قيمة بنصّ صريح على القرص.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from governance import vault_ops as ops  # noqa: E402


def _ask(confirm: bool = False, prompt: str = "عبارة مرور المخزن: ") -> str:
    first = getpass.getpass(prompt)
    if confirm:
        if first != getpass.getpass("أعد إدخالها للتأكيد: "):
            print("العبارتان غير متطابقتين.", file=sys.stderr)
            raise SystemExit(2)
    return first


def main() -> int:
    parser = argparse.ArgumentParser(description="إدارة مخزن أسرار QUANT_NQ")
    parser.add_argument("--vault", type=Path, default=ops.DEFAULT_VAULT)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("init", "list", "check", "rotate", "audit"):
        sub.add_parser(name)
    for name in ("set", "remove"):
        cmd = sub.add_parser(name)
        cmd.add_argument("key")

    args = parser.parse_args()
    vault: Path = args.vault

    if args.cmd == "check":
        st = ops.status(vault)
        if not st["exists"]:
            print("لا خزنة في %s" % st["path"])
            return 1
        print("المسار    : %s" % st["path"])
        print("الصيغة    : %s v%s" % (st.get("format"), st.get("version")))
        print("الاشتقاق  : %s" % st.get("kdf"))
        print("الحجم     : %s بايت" % st.get("size"))
        print("سليمة     : %s" % ("نعم" if st.get("valid") else "لا"))
        return 0 if st.get("valid") else 1

    if args.cmd == "audit":
        rows = ops.audit_tail(40)
        if not rows:
            print("(لا سجلّ بعد)")
        for r in rows:
            print("  %s  %-9s %-7s %-24s %s" % (
                r["at"], r["source"], r["op"], r["key"], r["result"]))
        return 0

    if args.cmd == "init":
        ok, msg = ops.init(_ask(confirm=True), vault)
        print(msg, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.cmd == "rotate":
        old = _ask(prompt="عبارة المرور الحاليّة: ")
        print("أدخل عبارة المرور الجديدة:")
        ok, msg = ops.rotate(old, _ask(confirm=True), vault)
        print(msg, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    passphrase = _ask()

    if args.cmd == "list":
        ok, msg, keys = ops.list_keys(passphrase, vault)
        if not ok:
            print(msg, file=sys.stderr)
            return 1
        if not keys:
            print("(المخزن فارغ)")
        for k in keys:
            print("  %s" % k)
        return 0

    if args.cmd == "set":
        value = getpass.getpass("قيمة '%s' (مخفيّة): " % args.key)
        ok, msg = ops.set_secret(passphrase, args.key, value, vault)
        print(msg, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.cmd == "remove":
        ok, msg = ops.remove_secret(passphrase, args.key, vault)
        print(msg, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
