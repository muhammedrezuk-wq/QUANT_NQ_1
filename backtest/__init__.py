# -*- coding: utf-8 -*-
"""محرك الاختبارات الرجعية (Backtest Engine) — QUANT_NQ.

يستقبل البيانات التاريخية من cTrader عبر WebSocket، يُشغّل الاستراتيجيات
عليها، ويحسب المقاييس (أرباح، خسائر، drawdown، Sharpe، إلخ).

البنية:
  models.py     — نماذج البيانات (Tick, Candle, Trade, Result)
  data_feed.py  — مستقبل بيانات WebSocket من cTrader
  engine.py     — المحرك الرئيسي للبكتست
  strategies.py — واجهة الاستراتيجيات + استراتيجيات جاهزة
  metrics.py    — حاسبة المقاييس
  api.py        — نقاط API لخادم الحوكمة
"""
