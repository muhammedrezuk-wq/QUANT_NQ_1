//+------------------------------------------------------------------+
//|  QUANT_NQ.mq5   —  محمد رزوق                                      |
//|                                                                  |
//|  الإكسبرت الواحد الشامل. يُصرَّف مرّة واحدة ولا يُلمس بعدها.          |
//|                                                                  |
//|  النظام في بايثون هو المنصّة. هذا الملف يفتح جوف MT5 فقط —         |
//|  يُخرج كل ما يقيسه السوق، وينفّذ كل أمر يصله، ولا يقرّر شيئًا:       |
//|                                                                  |
//|    ? الأسعار   — كل تكّة (CopyTicks) ? 102/103/105                |
//|    ? السبريد   — منقول من المنصّة لا محسوبًا ? prices.spread_pts   |
//|    ? الحساب    — equity/margin/الحالة ? 516/619                   |
//|    ? المراكز   — صورة حيّة ? 609/611/571                          |
//|    ? المواصفات — contract/tick ? symbol_specs (618)              |
//|    ? التقويم   — تقويم MT5 الأصلي (UTC) ? calendar (616/109)      |
//|    ? الإحماء   — آخر 200 شمعة لكل فريم MT5 ? candles_history      |
//|    ? العمق     — دفتر الأوامر (MarketBook) ? depth               |
//|    ? التنفيذ   — يقرأ commands وينفّذ CTrade ويكتب trade_events_v2 |
//|                                                                  |
//|  ??? الإكسبرت غبيّ: بس منفّذ أوامر. كل العقل بالبايثون ???         |
//|  لا استراتيجية · لا حساب حجم · لا حساب وقف · لا إدارة صفقة ·      |
//|  لا قرار دخول أو خروج. متى/الحجم/الوقف/الهدف/التريلينغ/الجزئيّ =   |
//|  يبنيها بايثون ويكتبها أمرًا في جدول commands، وهذا الملف ينفّذ.    |
//|                                                                  |
//|  إدارة الصفقة الذكيّة كلها أوامر: breakeven/trailing = MODIFY_SL · |
//|  تخفيف = CLOSE_PARTIAL · زيادة = OPEN · خروج = CLOSE. الفعل هنا    |
//|  والقرار هناك — فلا يُعاد تصريف هذا الملف حين يتغيّر الذكاء.        |
//+------------------------------------------------------------------+
#property copyright "محمد رزوق"
#property version   "3.12"
#property description "QUANT_NQ — الإكسبرت الواحد الشامل (v3.12 · شموع CopyRates لكل فريم الشارت)"
#property strict

#include <Trade\Trade.mqh>

// النسخة مكتوبة مرّتين بالقصد: #property أعلاه (يقبل نصًّا حرفيًّا فقط)
// وهذا الثابت الذي يمرّ للجسر واللوحة والسجلّ. أي ترقية = بدّل الاثنين معًا.
// v3.10 (2026-08-25): التذكرة تُكتب في صفّ الأمر عند النجاح (كانت تضيع،
// فبقيت المطابقة في بايثون عمياء عن هوية المركز) + اللوحة عربية كاملة
// بأعمدة مفصولة (أمر المالك: «بدي كل شي عربي»).
#define QNQ_VERSION "3.12"

//??????????????????????????????????????????????????????????????????
//  المدخلات
//??????????????????????????????????????????????????????????????????
input group "??? ? الجسر ???"
// اسم مجرّد لا مسار: MT5 يحصر DatabaseOpen في مجلدين، والمسار المطلق
// خارجهما يفشل بالخطأ 5002 — مُثبت بالقياس على هذا الجهاز.
input string InpDatabaseFile  = "nq_brain.db";   // اسم ملف القاعدة (بلا مسار)
input int    InpPollMs        = 10;              // نبضة الجسر (v3.00: 10ms — بلا خنق)
input int    InpMaxSymbols    = 100;             // سقف رموز الإقلاع (أمر النظام يتجاوزه)

input group "??? ? الرموز ???"
// فارغ = كل ما في Market Watch. أو اكتبها مفصولة بفاصلة.
// v3.00: أي رمز يصل بأمر من النظام يُضاف تلقائيًّا (EnsureSymbol) ولو لم يكن هنا.
input string InpSymbols       = ""; // فارغ = أي زوج (كل Market Watch)

input group "??? ? الهوية ???"
input long   InpMagic         = 20260801;        // رقم سحريّ (توقيع صفقات النظام)
input string InpExpertId      = "QUANT_NQ";      // هوية الخبير التي تظهر في الجسر
input string InpProjectBuildId= "QUANT_NQ_FULL_212"; // بصمة بناء المشروع
input int    InpDeviationPts  = 500;             // أقصى انزلاق مسموح (نقاط)

input group "??? ? التغذية ???"
input int    InpWarmupBars     = 200;            // شموع الإحماء لكل رمز ولكل فريم
input int    InpWarmupRefreshS = 300;            // تحديث الإحماء (ثانية)
input int    InpCalRefreshS    = 300;            // تحديث التقويم (ثانية)

input group "??? ? العمق (DOM) ???"
input bool   InpEnableDepth   = true;            // اشترك بدفتر الأوامر
input int    InpDepthLevels    = 10;             // أقصى مستويات لكل جهة

input group "??? ? الشاشة ???"
input bool   InpShowPanel     = true;
input int    InpPanelCorner   = 0;               // 0=يسار أعلى 1=يمين أعلى 2=يسار أسفل 3=يمين أسفل
input int    InpPanelX        = 12;
input int    InpPanelY        = 30;
input int    InpPanelW        = 452;             // عرض اللوحة
input int    InpColW          = 214;             // إزاحة العمود الأيمن (الحساب/الأوامر)
input int    InpKeyW          = 92;              // عرض عمود المفتاح داخل كل عمود
input int    InpTitleSize     = 13;
input int    InpFontSize      = 11;
input int    InpSystemTimeout = 30;              // ثوانٍ بلا إشارة من النظام = غير متصل

input group "??? ? الأمان ???"
// أقصى عمر لأمر PENDING قبل اعتباره بائتًا. بايثون يكتب والذراع ميتة =
// طابور يتراكم؛ وبلا هذا الحدّ يُنفَّذ عند العودة بأسعار ماتت.
input int    InpMaxCmdAgeSec  = 2;
// OPEN بلا وقف خسارة = مركز عارٍ على عقد آجل. النظام يبني الوقف دائمًا.
input bool   InpRequireStop   = true;

//??????????????????????????????????????????????????????????????????
//  الحالة
//??????????????????????????????????????????????????????????????????
CTrade   Trade;

string   Syms[];              // الرموز المراقَبة
long     LastTickMsc[];       // آخر طابع تكّة سُحب لكل رمز — مفتاح CopyTicks
double   LastBid[], LastAsk[];
double   LastSpread[];
bool     DepthDirty[];        // دفتر الرمز تغيّر منذ آخر كتابة (يضبطه OnBookEvent)
int      SymCount = 0;

int      DbHandle = INVALID_HANDLE;   // يُفتح مرّة ويبقى
bool     BridgeUp = false;

// ?? حالة المنصّة نفسها ??????????????????????????????????????????????
bool     TradeAllowed  = false;
bool     ExpertAllowed = false;
bool     Connected     = false;
datetime PlatformSeen  = 0;
int    PositionsSynced = 0;

// إشارة النظام — دليلها أن يكتب النظام في display لا أن يُفتح الملف
double   SysLastSeen  = 0;
datetime SysSeenAt    = 0;
long     LastCmdId    = 0;

// حالة الحساب — تُكتب عند التغيّر فقط
double   LastEquity = -1, LastMargin = -1, LastFree = -1, LastBalance = -1;

// عدّادات الشاشة
ulong    CmdReceived = 0, CmdDone = 0, CmdFailed = 0, CmdUnsupported = 0;
ulong    CmdExpired  = 0;
ulong    TicksCaptured = 0;
datetime LastCmdAt = 0, LastErrorAt = 0;
string   LastError = "";

// ?? v3.00: عدّاد وعمر آخر كتابة لكل جدول — قسم BRIDGE باللوحة ??????
ulong    WrTicksMs = 0, WrSpecsMs = 0, WrAcctMs = 0, WrPosMs = 0;
ulong    WrEventsMs = 0, WrCalMs = 0, WrWarmMs = 0, WrDepthMs = 0;
long     WrSpecs = 0, WrAcct = 0, WrPos = 0, WrEvents = 0;

// ?? v3.00: إيقاعات بلا خنق — فوري عند الحدث + سقف زمنيّ للثوابت ????
ulong    g_last_acc_stamp_ms = 0;   // ختم account_v2 الثابت: كل ثانية
ulong    g_last_flags_ms     = 0;   // أعلام المنصّة: كل ثانية إن لم تتغيّر
ulong    g_last_pos_ms       = 0;   // المراكز: كل 500ms إن لم يقع تداول
ulong    g_last_draw_ms      = 0;   // الرسم معزول: كل 250ms
bool     FlagsSeeded         = false;

// ?? v3.00: حلقات اللوحة — لا يظهر request_id هنا أبدًا (عقد الهوية) ??
#define QNQ_LAST_CMDS 4
#define QNQ_LAST_ERRS 2
#define QNQ_POS_ROWS  4
#define QNQ_MAX_LR   16
#define QNQ_MAX_F    24
string   RecentCmds[QNQ_LAST_CMDS]; bool RecentCmdOk[QNQ_LAST_CMDS]; int RecentCmdN = 0;
string   RecentErrs[QNQ_LAST_ERRS]; int RecentErrN = 0;
// v3.10: صفّ المركز أعمدة مفصولة لا نصًّا واحدًا — كل عمود بخطّه فتستقيم
// المحاذاة ولا تختلط العربية بالأرقام (فوضى الاتجاهين).
string   PanelPosSym[QNQ_POS_ROWS], PanelPosSide[QNQ_POS_ROWS];
string   PanelPosVol[QNQ_POS_ROWS], PanelPosPx[QNQ_POS_ROWS];
double   PanelPosPnL[QNQ_POS_ROWS];
int      PanelPosN = 0, PanelPosMore = 0;

// التغذية الإضافيّة
long     g_cal_written    = 0;     datetime g_last_cal    = 0;
long     g_warmup_written = 0;     datetime g_last_warmup = 0;   bool g_warmup_ok = false;
long     g_depth_written  = 0;     bool     g_depth_ok    = false;

// شاشة الصفقات — يكتبها النظام في display، تُقرأ هنا لا تُحسب
string   DispDailyPct = "—", DispWins = "—", DispLosses = "—";
string   DispTrades   = "—", DispOpen = "—", DispKill = "—";

string Esc(string s) { StringReplace(s, "'", "''"); return s; }

//??????????????????????????????????????????????????????????????????
//  v3.00 — أدوات اللوحة: محاذاة أعمدة Consolas وعمر آخر كتابة
//??????????????????????????????????????????????????????????????????
string Pad(string s, int w)  { while(StringLen(s) < w) s += " ";    return s; }
string PadL(string s, int w) { while(StringLen(s) < w) s = " " + s; return s; }

string AgeStr(ulong last_ms)
{
   if(last_ms == 0) return "-";
   ulong d = GetTickCount64() - last_ms;
   if(d < 10000)   return StringFormat("%.1f ث", d / 1000.0);
   if(d < 120000)  return StringFormat("%d ث", (int)(d / 1000));
   if(d < 7200000) return StringFormat("%d د", (int)(d / 60000));
   return StringFormat("%d س", (int)(d / 3600000));
}

// v3.10 — قاموس اللوحة: رموز القاعدة تبقى إنكليزية (عقد النظام)،
// والمعروض للمالك عربيّ حرفيًّا. مجهولُ الترجمة يُعرض كما هو لا يُخفى.
string ArSide(string side)
{
   if(side == "BUY")  return "شراء";
   if(side == "SELL") return "بيع";
   return side;
}

string ArAct(string act)
{
   if(act == "OPEN")           return "فتح";
   if(act == "CLOSE")          return "إغلاق";
   if(act == "CLOSE_PARTIAL")  return "إغلاق جزئيّ";
   if(act == "MODIFY_SL")      return "تعديل الوقف";
   if(act == "MODIFY_TP")      return "تعديل الهدف";
   if(act == "PENDING_CREATE") return "أمر معلَّق";
   if(act == "PENDING_DELETE") return "حذف معلَّق";
   return act;
}

string ArResult(string r)
{
   if(r == "OK")                       return "تمّ";
   if(r == "NO_VOLUME")                return "بلا حجم";
   if(r == "BAD_SIDE")                 return "جهة فاسدة";
   if(r == "BAD_PARAMS")               return "معطيات فاسدة";
   if(r == "NO_STOP")                  return "بلا وقف خسارة";
   if(r == "NO_TICK")                  return "بلا سعر";
   if(r == "NO_POSITION")              return "لا مركز";
   if(r == "NO_CONFIRMED_DEAL")        return "بلا تأكيد صفقة";
   if(r == "UNKNOWN_ACTION")           return "أمر مجهول";
   if(r == "NOT_SUPPORTED_BY_DESIGN")  return "غير مدعوم عمدًا";
   if(r == "COMMAND_OWNERSHIP_MISMATCH") return "ملكيّة غير مطابقة";
   if(r == "PROJECT_BUILD_MISMATCH")   return "بناء غير مطابق";
   if(r == "BROKER_DISCONNECTED")      return "الوسيط مقطوع";
   if(r == "OWNER_HALT")               return "التداول مطفأ";
   if(r == "EXPERT_NOT_ALLOWED")       return "الإكسبرت ممنوع";
   if(r == "NEUTRAL_PAIR_MUST_HAVE_NO_DIRECTIONAL_LEVELS") return "زوج متعادل بلا مستويات";
   if(StringFind(r, "RETCODE_") == 0)
      return "رفض المنصّة " + StringSubstr(r, 8);
   if(StringFind(r, "STALE_") == 0)
      return "بائت " + StringSubstr(r, 6);
   if(StringFind(r, "SYMBOL_UNAVAILABLE") == 0)
      return "الرمز غير متوفّر عند الوسيط";
   return r;
}

// آخر الأوامر بنتيجتها — نصّ الأمر فقط (فعل/رمز/جهة/نتيجة)، لا معرّفات
void PushCmd(string line, bool ok)
{
   for(int i = QNQ_LAST_CMDS - 1; i > 0; i--)
   { RecentCmds[i] = RecentCmds[i-1]; RecentCmdOk[i] = RecentCmdOk[i-1]; }
   RecentCmds[0]  = TimeToString(TimeCurrent(), TIME_SECONDS) + " " + line;
   RecentCmdOk[0] = ok;
   if(RecentCmdN < QNQ_LAST_CMDS) RecentCmdN++;
}

void PushErr(string text)
{
   for(int i = QNQ_LAST_ERRS - 1; i > 0; i--) RecentErrs[i] = RecentErrs[i-1];
   RecentErrs[0] = TimeToString(TimeCurrent(), TIME_SECONDS) + " " + text;
   if(RecentErrN < QNQ_LAST_ERRS) RecentErrN++;
}

//??????????????????????????????????????????????????????????????????
//  القاعدة
//??????????????????????????????????????????????????????????????????
bool BridgeOpen()
{
   if(DbHandle != INVALID_HANDLE) return true;

   ResetLastError();
   DbHandle = DatabaseOpen(InpDatabaseFile,
                DATABASE_OPEN_READWRITE | DATABASE_OPEN_CREATE | DATABASE_OPEN_COMMON);
   if(DbHandle == INVALID_HANDLE)
   {
      static datetime complained = 0;
      if(TimeCurrent() - complained >= 60)
      {
         LastError = StringFormat("DB open failed: %d", GetLastError());
         LastErrorAt = TimeCurrent();
         PushErr(LastError);
         PrintFormat("? [BRIDGE] تعذّر فتح '%s' — خطأ=%d", InpDatabaseFile, GetLastError());
         complained = TimeCurrent();
      }
      return false;
   }

   // وضع WAL يسمح لقارئ بايثون والكاتب هنا أن يعملا معًا بلا "database is locked"
   DatabaseExecute(DbHandle, "PRAGMA journal_mode=WAL;");
   DatabaseExecute(DbHandle, "PRAGMA busy_timeout=3000;");
   DatabaseExecute(DbHandle, "PRAGMA synchronous=NORMAL;");
   return true;
}

//+------------------------------------------------------------------+
//| يضيف عمودًا إن غاب عن جدول موجود مسبقًا.                          |
//+------------------------------------------------------------------+
bool ColumnExists(string table, string column)
{
   int req = DatabasePrepare(DbHandle, "PRAGMA table_info(" + table + ");");
   if(req == INVALID_HANDLE) return false;
   bool found = false;
   while(DatabaseRead(req))
   {
      string name;
      DatabaseColumnText(req, 1, name);
      if(name == column) { found = true; break; }
   }
   DatabaseFinalize(req);
   return found;
}

void AddColumn(string table, string column, string type)
{
   if(ColumnExists(table, column)) return;
   ResetLastError();
   if(DatabaseExecute(DbHandle, StringFormat(
         "ALTER TABLE %s ADD COLUMN %s %s;", table, column, type)))
      PrintFormat("?? [SCHEMA] %s.%s أُضيف", table, column);
   else
      PrintFormat("? [SCHEMA] تعذّرت إضافة %s.%s — خطأ=%d",
                  table, column, GetLastError());
}

void MigrateSchema()
{
   AddColumn("prices", "contract_size", "REAL");
   AddColumn("prices", "tick_value",    "REAL");
   AddColumn("prices", "tick_size",     "REAL");
   AddColumn("prices", "digits",        "INTEGER");
   AddColumn("trade_events", "request_id", "TEXT");
   AddColumn("trade_events", "profit", "REAL");
   // حكم المالك 2026-08-16: الربح الخام وحده كان يُقرأ كأداء سوق، والحقيقة
   // أن جزءا منه ثمن تنفيذ. الذرة 517 تحسب الصافي، وهذه الاعمدة مصدرها.
   AddColumn("trade_events", "commission", "REAL");
   AddColumn("trade_events", "swap", "REAL");
   AddColumn("trade_events", "fee", "REAL");
   AddColumn("positions", "swap", "REAL");
   AddColumn("positions", "magic", "INTEGER");
   AddColumn("positions", "comment", "TEXT");
   AddColumn("account", "connected", "INTEGER");
   AddColumn("account", "trade_allowed", "INTEGER");
   AddColumn("account", "expert_allowed", "INTEGER");
   AddColumn("account", "bridge_beat", "REAL");
   AddColumn("account", "account_id", "TEXT");
   AddColumn("account", "broker", "TEXT");
   AddColumn("account", "account_server", "TEXT");
   AddColumn("account", "expert_id", "TEXT");
   AddColumn("account", "expert_version", "TEXT");
   AddColumn("account", "project_build_id", "TEXT");
   AddColumn("commands", "account_id", "TEXT");
   AddColumn("commands", "magic", "INTEGER");
   AddColumn("commands", "project_build_id", "TEXT");
   AddColumn("positions", "account_id", "TEXT");
   AddColumn("trade_events", "account_id", "TEXT");
   // ?? Phase 0: مواصفات دقيقة يحتاجها محرّك المركز الدائم (بايثون) ??
   AddColumn("symbol_specs", "point",        "REAL");
   AddColumn("symbol_specs", "digits",       "INTEGER");
   AddColumn("symbol_specs", "stops_level",  "INTEGER");
   AddColumn("symbol_specs", "freeze_level", "INTEGER");
   AddColumn("symbol_specs", "volume_min",   "REAL");
   AddColumn("symbol_specs", "volume_max",   "REAL");
   AddColumn("symbol_specs", "volume_step",  "REAL");
   AddColumn("symbol_specs", "filling_mode", "INTEGER");
   AddColumn("account",      "margin_mode",  "INTEGER");
   AddColumn("positions",    "commission",   "REAL");
}

bool BuildSchema()
{
   // ? الأسعار: صف لكل رمز، يُحدَّث في مكانه
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS prices ("
      "symbol TEXT PRIMARY KEY, bid REAL, ask REAL, spread_pts REAL,"
      "last REAL, volume REAL, tick_ms INTEGER, ticks_in_window INTEGER,"
      "high REAL, low REAL, contract_size REAL, tick_value REAL,"
      "tick_size REAL, digits INTEGER, updated_at REAL);")) return false;

   // ? التكّات: الذرة 103 تبني الشموع من كل تكّة، فالضياع شمعة خاطئة
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS ticks ("
      "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,"
      "bid REAL, ask REAL, last REAL, volume REAL, tick_ms INTEGER NOT NULL,"
      "flags INTEGER);")) return false;
   DatabaseExecute(DbHandle, "CREATE INDEX IF NOT EXISTS idx_ticks_id ON ticks(id);");
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS ticks_v2 ("
      "id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, symbol TEXT NOT NULL,"
      "bid REAL, ask REAL, last REAL, volume REAL, tick_ms INTEGER NOT NULL, flags INTEGER);")) return false;
   DatabaseExecute(DbHandle, "CREATE INDEX IF NOT EXISTS idx_ticks_v2_id ON ticks_v2(id);");

   // ? الحساب: صف لكل حساب — الجسر مشترك بين الطرفيات، فالهوية هي المفتاح
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS account ("
      "account_id TEXT PRIMARY KEY, id INTEGER, balance REAL, equity REAL,"
      "margin REAL, free_margin REAL, margin_level REAL, currency TEXT,"
      "leverage INTEGER, open_count INTEGER, updated_at REAL,"
      "connected INTEGER, trade_allowed INTEGER, expert_allowed INTEGER,"
      "bridge_beat REAL, broker TEXT, account_server TEXT,"
      "expert_id TEXT, expert_version TEXT, project_build_id TEXT);"))
      return false;
   // Legacy releases keyed this table by the constant id=1. SQLite cannot
   // replace that primary key in place while several terminals share the DB,
   // therefore v2 is the canonical account-scoped table.
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS account_v2 ("
      "account_id TEXT PRIMARY KEY, balance REAL, equity REAL, margin REAL,"
      "free_margin REAL, margin_level REAL, currency TEXT, leverage INTEGER,"
      "open_count INTEGER, updated_at REAL, connected INTEGER, trade_allowed INTEGER,"
      "expert_allowed INTEGER, bridge_beat REAL, broker TEXT, account_server TEXT,"
      "margin_mode INTEGER, expert_id TEXT, expert_version TEXT, project_build_id TEXT);"))
      return false;

   // ? الأوامر: طابور. بايثون يُدخل، وهذا الملف يعلّم الحالة
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS commands ("
      "id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,"
      "action TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT, volume REAL,"
      "price REAL, stop_loss REAL, take_profit REAL, ticket INTEGER,"
      "trail_dist REAL, trail_step REAL, params_json TEXT,"
      "magic INTEGER NOT NULL DEFAULT 0, account_id TEXT NOT NULL DEFAULT '', project_build_id TEXT,"
      "status TEXT NOT NULL DEFAULT 'PENDING', result TEXT,"
      "created_at REAL NOT NULL, taken_at REAL, done_at REAL);")) return false;
   DatabaseExecute(DbHandle,
      "CREATE INDEX IF NOT EXISTS idx_cmd_status ON commands(status, id);");

   // ? التنفيذ: العقد كما تقرأه الذرة 563 حرفًا بحرف
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS trade_events ("
      "id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,"
      "ticket INTEGER, symbol TEXT NOT NULL, side TEXT NOT NULL,"
      "volume REAL NOT NULL, entry_price REAL, exit_price REAL,"
      "open_time REAL, close_time REAL, reason TEXT, request_id TEXT,"
      "profit REAL, commission REAL, swap REAL, fee REAL,"
      "written_at REAL NOT NULL);")) return false;
   DatabaseExecute(DbHandle,
      "CREATE INDEX IF NOT EXISTS idx_events_id ON trade_events(id);");
   // v2 is the sole operational execution ledger: every row owns an account.
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS trade_events_v2 ("
      "id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL,"
      "event_type TEXT NOT NULL,ticket INTEGER,symbol TEXT NOT NULL,side TEXT NOT NULL,"
      "volume REAL NOT NULL,entry_price REAL,exit_price REAL,open_time REAL,close_time REAL,"
      "reason TEXT,request_id TEXT,profit REAL,commission REAL,swap REAL,fee REAL,"
      "written_at REAL NOT NULL);")) return false;
   DatabaseExecute(DbHandle,
      "CREATE INDEX IF NOT EXISTS idx_events_v2_account_id ON trade_events_v2(account_id,id);");

   // ? المراكز: صورة حيّة يقرؤها 609/611
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS positions ("
      "ticket INTEGER PRIMARY KEY, symbol TEXT NOT NULL, side TEXT,"
      "volume REAL, entry_price REAL, current_price REAL,"
      "stop_loss REAL, take_profit REAL, profit REAL, swap REAL,"
      "magic INTEGER, comment TEXT, opened_at REAL, updated_at REAL);")) return false;
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS positions_v2 ("
      "account_id TEXT NOT NULL, ticket INTEGER NOT NULL, symbol TEXT NOT NULL, side TEXT,"
      "volume REAL, entry_price REAL, current_price REAL, stop_loss REAL, take_profit REAL,"
      "profit REAL, swap REAL, commission REAL, magic INTEGER, comment TEXT,"
      "opened_at REAL, updated_at REAL, PRIMARY KEY(account_id,ticket));")) return false;

   // ? اللوحة: يكتبها النظام، يقرؤها هذا الملف للشاشة فقط
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS display ("
      "id INTEGER PRIMARY KEY, daily_pct REAL, wins INTEGER,"
      "losses INTEGER, trades INTEGER, open_trades INTEGER,"
      "kill_switch INTEGER, updated_at REAL);")) return false;
   DatabaseExecute(DbHandle, "INSERT OR IGNORE INTO display (id) VALUES (1);");

   // ? المواصفات: تقرؤها الذرة 618 (جدول مستقلّ لا أعمدة داخل prices)
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS symbol_specs ("
      "symbol TEXT PRIMARY KEY, contract_size REAL, tick_value REAL,"
      "tick_size REAL);")) return false;
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS symbol_specs_v2 ("
      "account_id TEXT NOT NULL, symbol TEXT NOT NULL, contract_size REAL, tick_value REAL,"
      "tick_size REAL, point REAL, digits INTEGER, stops_level INTEGER, freeze_level INTEGER,"
      "volume_min REAL, volume_max REAL, volume_step REAL, filling_mode INTEGER,"
      "PRIMARY KEY(account_id,symbol));")) return false;

   // ? التقويم: تقويم MT5 الأصلي، تقرؤه 616 (بأوقات UTC)
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS calendar ("
      "id TEXT PRIMARY KEY, title TEXT, country TEXT, currency TEXT,"
      "impact_level TEXT, scheduled_at REAL, actual TEXT, forecast TEXT,"
      "previous TEXT, written_at REAL);")) return false;

   // ? الإحماء: آخر شموع M1، تقرؤها الذرة 602 وتعيد تشغيلها بعلَم warmup
   //    المخطط ملزِم لـ602: symbol,period_seconds,period_start,o,h,l,c,volume
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS candles_history ("
      "symbol TEXT, period_seconds INTEGER, period_start REAL,"
      "open REAL, high REAL, low REAL, close REAL, volume REAL);")) return false;

   // ? العمق: دفتر الأوامر — level ترتيبيّ كما ورد، والسعر يفصل الأفضل.
   //    لا مستهلك اليوم؛ يُكتب ليجده تحليلُ السيولة القادم بلا لمس هذا الملف.
   if(!DatabaseExecute(DbHandle,
      "CREATE TABLE IF NOT EXISTS depth ("
      "symbol TEXT NOT NULL, level INTEGER NOT NULL, side TEXT NOT NULL,"
      "price REAL, volume REAL, updated_at REAL,"
      "PRIMARY KEY(symbol, side, level));")) return false;

   MigrateSchema();
   DatabaseExecute(DbHandle,
      "INSERT OR IGNORE INTO account_v2 (account_id,balance,equity,margin,free_margin,margin_level,currency,leverage,open_count,updated_at,connected,trade_allowed,expert_allowed,bridge_beat,broker,account_server,margin_mode,expert_id,expert_version,project_build_id) "
      "SELECT account_id,balance,equity,margin,free_margin,margin_level,currency,leverage,open_count,updated_at,connected,trade_allowed,expert_allowed,bridge_beat,broker,account_server,margin_mode,expert_id,expert_version,project_build_id FROM account WHERE account_id IS NOT NULL AND account_id<>'';");
   DatabaseExecute(DbHandle,
      "INSERT OR IGNORE INTO trade_events_v2 (id,account_id,event_type,ticket,symbol,side,volume,entry_price,exit_price,open_time,close_time,reason,request_id,profit,commission,swap,fee,written_at) "
      "SELECT id,account_id,event_type,ticket,symbol,side,volume,entry_price,exit_price,open_time,close_time,reason,request_id,profit,commission,swap,fee,written_at FROM trade_events WHERE account_id IS NOT NULL AND account_id<>'';");
   DatabaseExecute(DbHandle, StringFormat(
      "INSERT OR IGNORE INTO account_v2 (account_id) VALUES ('%s');",
      Esc(IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)))));
   return true;
}

//??????????????????????????????????????????????????????????????????
//  المواصفات
//??????????????????????????????????????????????????????????????????
//+------------------------------------------------------------------+
//| يكتب صفّ مواصفات كاملًا في symbol_specs_v2 المعزول بالحساب.       |
//+------------------------------------------------------------------+
void WriteSymbolSpec(string sym)
{
   double cs   = SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE);
   double tv   = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double ts   = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double pt   = SymbolInfoDouble(sym, SYMBOL_POINT);
   double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double vstp = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   int    dg   = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   int    slv  = (int)SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL);
   int    frz  = (int)SymbolInfoInteger(sym, SYMBOL_TRADE_FREEZE_LEVEL);
   int    fm   = (int)SymbolInfoInteger(sym, SYMBOL_FILLING_MODE);
   if(DatabaseExecute(DbHandle, StringFormat(
      "REPLACE INTO symbol_specs_v2 (account_id,symbol,contract_size,tick_value,tick_size,point,digits,stops_level,freeze_level,volume_min,volume_max,volume_step,filling_mode) "
      "VALUES ('%s','%s',%.10g,%.10g,%.10g,%.10g,%d,%d,%d,%.10g,%.10g,%.10g,%d);",
      Esc(IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))), Esc(sym), cs, tv, ts, pt,
      dg, slv, frz, vmin, vmax, vstp, fm)))
   { WrSpecs++; WrSpecsMs = GetTickCount64(); }
}

//+------------------------------------------------------------------+
//| يكتب معاملات كل رمز مرّة عند الإقلاع (وسوق مغلقة لا تنتظر تكّة).    |
//+------------------------------------------------------------------+
void SeedSymbolSpecs()
{
   if(!BridgeUp) return;
   DatabaseTransactionBegin(DbHandle);
   for(int i = 0; i < SymCount; i++)
   {
      // Only the account-scoped v2 specification table is operational.
      WriteSymbolSpec(Syms[i]);
   }
   DatabaseTransactionCommit(DbHandle);
   PrintFormat("?? [SPEC] معاملات %d رمز كُتبت", SymCount);
}

//??????????????????????????????????????????????????????????????????
//  المراكز
//??????????????????????????????????????????????????????????????????
void SyncPositions()
{
   if(!BridgeUp) return;

   int total = PositionsTotal();
   int snap  = 0;                     // v3.00: لقطة اللوحة تُملأ هنا — لا SQL إضافيّ
   DatabaseTransactionBegin(DbHandle);

   string live = "";
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(live != "") live += ",";
      live += IntegerToString((long)ticket);
   }

   string purge = (live == "")
      ? StringFormat("DELETE FROM positions_v2 WHERE account_id='%s';", Esc(IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))))
      : StringFormat("DELETE FROM positions_v2 WHERE account_id='%s' AND ticket NOT IN (%s);", Esc(IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))), live);
   DatabaseExecute(DbHandle, purge);

   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;

      string sym   = PositionGetString(POSITION_SYMBOL);
      long   ptype = PositionGetInteger(POSITION_TYPE);
      string side  = (ptype == POSITION_TYPE_BUY) ? "BUY" : "SELL";
      string cmt   = PositionGetString(POSITION_COMMENT);
      StringReplace(cmt, "'", "");
      // نقرأ كل حقول المركز أوّلًا (قبل اختيار السجلّ) لئلّا يتأثّر أيّ حقل
      double p_vol  = PositionGetDouble(POSITION_VOLUME);
      double p_open = PositionGetDouble(POSITION_PRICE_OPEN);
      double p_cur  = PositionGetDouble(POSITION_PRICE_CURRENT);
      double p_sl   = PositionGetDouble(POSITION_SL);
      double p_tp   = PositionGetDouble(POSITION_TP);
      double p_prof = PositionGetDouble(POSITION_PROFIT);
      double p_swap = PositionGetDouble(POSITION_SWAP);
      long   p_mag  = PositionGetInteger(POSITION_MAGIC);
      long   p_time = PositionGetInteger(POSITION_TIME);

      // العمولة الفعليّة للمركز = مجموع عمولات صفقاته (Phase 0 · يقرؤها دفتر 518)
      double comm = 0.0;
      if(HistorySelectByPosition((long)ticket))
      {
         int deals = HistoryDealsTotal();
         for(int d = 0; d < deals; d++)
         {
            ulong dtk = HistoryDealGetTicket(d);
            if(dtk > 0) comm += HistoryDealGetDouble(ddtk > 0) comm += HistoryDealGetDouble(dtk, DEAL_COMMISSION);
         }
      }

      // v3.00: صفّ اللوحة — المركز ملوَّنًا بربحه، من نفس القيم المقروءة أعلاه
      if(snap < QNQ_POS_ROWS)
      {
         int pdg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
         PanelPosSym[snap]  = sym;
         PanelPosSide[snap] = ArSide(side);
         PanelPosVol[snap]  = DoubleToString(p_vol, 2);
         PanelPosPx[snap]   = DoubleToString(p_open, pdg) + "  "
                            + DoubleToString(p_cur, pdg)
                            + PadL(StringFormat("%+.2f", p_prof), 11);
         PanelPosPnL[snap] = p_prof;
         snap++;
      }

      string q = StringFormat(
         "INSERT INTO positions_v2 (account_id,ticket,symbol,side,volume,entry_price,"
         "current_price,stop_loss,take_profit,profit,swap,commission,magic,comment,opened_at,updated_at) "
         "VALUES ('%s',%I64d,'%s','%s',%.10g,%.10g,%.10g,%.10g,%.10g,%.10g,%.10g,%.10g,"
         "%I64d,'%s',%d,%d) "
         "ON CONFLICT(account_id,ticket) DO UPDATE SET current_price=excluded.current_price,"
         "stop_loss=excluded.stop_loss,take_profit=excluded.take_profit,"
         "profit=excluded.profit,swap=excluded.swap,commission=excluded.commission,"
         "volume=excluded.volume,updated_at=excluded.updated_at;",
         Esc(IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))), (long)ticket, Esc(sym), side,
         p_vol, p_open, p_cur, p_sl, p_tp, p_prof, p_swap, comm, p_mag, cmt,
         (int)p_time, (int)TimeGMT());
      DatabaseExecute(DbHandle, q);
   }

   DatabaseTransactionCommit(DbHandle);
   PositionsSynced = total;
   PanelPosN    = snap;
   PanelPosMore = (total > snap) ? (total - snap) : 0;
   WrPos++; WrPosMs = GetTickCount64();
}

//??????????????????????????????????????????????????????????????????
//  حالة المنصّة
//??????????????????????????????????????????????????????????????????
void ReadPlatformState()
{
   bool ta = (bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
   bool ea = (bool)MQLInfoInteger(MQL_TRADE_ALLOWED);
   bool cn = (bool)TerminalInfoInteger(TERMINAL_CONNECTED);
   bool changed = (!FlagsSeeded || ta != TradeAllowed || ea != ExpertAllowed
                   || cn != Connected);
   TradeAllowed  = ta;
   ExpertAllowed = ea;
   Connected     = cn;
   FlagsSeeded   = true;
   PlatformSeen  = TimeGMT();

   if(!BridgeUp) return;

   // v3.00 بلا خنق: الأعلام تُكتب فورًا عند التغيّر، والختم الثابت كل ثانية —
   // لا فيضان UPDATE كل 10ms. قارئ 619 حدّه 300 ثانية فالثانية سخيّة.
   if(!changed && GetTickCount64() - g_last_flags_ms < 1000) return;
   g_last_flags_ms = GetTickCount64();

   DatabaseExecute(DbHandle, StringFormat(
      "UPDATE account_v2 SET connected=%d, trade_allowed=%d, expert_allowed=%d,"
      " bridge_beat=%d, expert_id='%s', expert_version='%s', project_build_id='%s'"
      " WHERE account_id='%s';",
      Connected ? 1 : 0, TradeAllowed ? 1 : 0, ExpertAllowed ? 1 : 0,
      (int)TimeGMT(), Esc(InpExpertId), QNQ_VERSION, Esc(InpProjectBuildId),
      Esc(IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)))));
   WrAcct++; WrAcctMs = GetTickCount64();
}

bool CanTrade()
{
   return TradeAllowed && ExpertAllowed && Connected;
}

//??????????????????????????????????????????????????????????????????
//  الأسعار — CopyTicks: كل تكّة مرّت لا آخر لقطة
//??????????????????????????????????????????????????????????????????
void PumpPrices()
{
   if(!BridgeUp) return;

   bool opened = false;

   for(int i = 0; i < SymCount; i++)
   {
      MqlTick ticks[];
      long from = (LastTickMsc[i] > 0) ? LastTickMsc[i] + 1 : 0;

      int n = (from > 0)
            ? CopyTicks(Syms[i], ticks, COPY_TICKS_ALL, from, 0)
            : CopyTicks(Syms[i], ticks, COPY_TICKS_ALL, 0, 1);

      if(n <= 0)
      {
         if(from == 0) continue;   // أول نداء قد يفشل حتى تتزامن قاعدة التكّات
         continue;                 // لا تكّات جديدة: لا كتابة
      }

      double hi = 0, lo = 0, vol = 0;
      for(int k = 0; k < n; k++)
      {
         double mid = (ticks[k].bid + ticks[k].ask) / 2.0;
         if(hi == 0 || mid > hi) hi = mid;
         if(lo == 0 || mid < lo) lo = mid;
         vol += ticks[k].volume_real > 0 ? ticks[k].volume_real : (double)ticks[k].volume;
      }

      MqlTick last = ticks[n-1];
      LastTickMsc[i] = last.time_msc;
      TicksCaptured += (ulong)n;

      // السبريد من المنصّة لا محسوبًا: المنقول أصدق من المشتقّ
      double spread_pts = (double)SymbolInfoInteger(Syms[i], SYMBOL_SPREAD);

      bool changed = (last.bid != LastBid[i] || last.ask != LastAsk[i]
                      || spread_pts != LastSpread[i]);

      if(!opened) { DatabaseTransactionBegin(DbHandle); opened = true; }

      // كل تكّة تُكتب: 103 تحتاجها كلها
      for(int k = 0; k < n; k++)
      {
         string q = StringFormat(
            "INSERT INTO ticks_v2 (account_id,symbol,bid,ask,last,volume,tick_ms,flags) "
            "VALUES ('%s','%s',%.10g,%.10g,%.10g,%.10g,%I64d,%d);",
            Esc(IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))), Syms[i], ticks[k].bid, ticks[k].ask, ticks[k].last,
            (ticks[k].volume_real > 0 ? ticks[k].volume_real : (double)ticks[k].volume),
            ticks[k].time_msc, (int)ticks[k].flags);
         DatabaseExecute(DbHandle, q);
      }

      if(changed)
      {
         // Legacy `prices`/`symbol_specs` are migration-only; v2 is operational.
         // ويُحدَّث symbol_specs_v2 مع كل لقطة — يبقى ما تقرؤه 618 طازجًا
         WriteSymbolSpec(Syms[i]);

         LastBid[i] = last.bid; LastAsk[i] = last.ask; LastSpread[i] = spread_pts;
      }
   }

   if(opened)
   {
      DatabaseTransactionCommit(DbHandle);
      WrTicksMs = GetTickCount64();
   }
}

//??????????????????????????????????????????????????????????????????
//  الحساب
//??????????????????????????????????????????????????????????????????
void PumpAccount()
{
   if(!BridgeUp) return;

   double bal  = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq   = AccountInfoDouble(ACCOUNT_EQUITY);
   double mar  = AccountInfoDouble(ACCOUNT_MARGIN);
   double fre  = AccountInfoDouble(ACCOUNT_MARGIN_FREE);

   // حكم المالك 2026-08-15: منع الكتابة الثقيلة حين لا تتغيّر الأرقام سليم،
   // لكنّه كان يبتلع معه ختم الوقت. و updated_at معناه «متى نظرتُ» لا «متى
   // تغيّر الرقم». وبلا مركز مفتوح تبقى الأرقام الأربعة متطابقة إلى الأبد،
   // فبقي الختم ميّتًا 68.7 ساعة و 585 يرفض التنفيذ على بيانات صحيحة.
   // الكتابة الثقيلة تبقى مشروطة بالتغيّر؛ الختم وحده تحرّر.
   // v3.00: التغيّر يُكتب فورًا (كل نبضة 10ms تفحصه)، والختم الثابت كل ثانية —
   // 619 ينشر فقط حين يتحرّك updated_at وحدّه 300 ثانية، فالثانية تكفي وتفيض.
   if(bal == LastBalance && eq == LastEquity && mar == LastMargin && fre == LastFree)
   {
      if(GetTickCount64() - g_last_acc_stamp_ms < 1000) return;
      g_last_acc_stamp_ms = GetTickCount64();
      DatabaseExecute(DbHandle, StringFormat(
         "UPDATE account_v2 SET updated_at=%d WHERE account_id='%s';",
         (int)TimeGMT(),
         Esc(IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)))));
      WrAcct++; WrAcctMs = GetTickCount64();
      return;
   }

   string q = StringFormat(
      "UPDATE account_v2 SET balance=%.2f,equity=%.2f,margin=%.2f,free_margin=%.2f,"
      "margin_level=%.2f,currency='%s',leverage=%d,open_count=%d,updated_at=%d,"
      "broker='%s',account_server='%s',margin_mode=%d,"
      "expert_id='%s',expert_version='%s',project_build_id='%s'"
      " WHERE account_id='%s';",
      bal, eq, mar, fre,
      AccountInfoDouble(ACCOUNT_MARGIN_LEVEL),
      AccountInfoString(ACCOUNT_CURRENCY),
      (int)AccountInfoInteger(ACCOUNT_LEVERAGE),
      PositionsTotal(), (int)TimeGMT(),
      Esc(AccountInfoString(ACCOUNT_COMPANY)),
      Esc(AccountInfoString(ACCOUNT_SERVER)),
      (int)AccountInfoInteger(ACCOUNT_MARGIN_MODE),
      Esc(InpExpertId), QNQ_VERSION, Esc(InpProjectBuildId),
      Esc(IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))));
   DatabaseExecute(DbHandle, q);
   g_last_acc_stamp_ms = GetTickCount64();
   WrAcct++; WrAcctMs = GetTickCount64();

   LastBalance = bal; LastEquity = eq; LastMargin = mar; LastFree = fre;
}

//??????????????????????????????????????????????????????????????????
//  التقويم — تقويم MT5 الأصلي ? calendar (أوقات UTC)
//??????????????????????????????????????????????????????????????????
string ImpactStr(ENUM_CALENDAR_EVENT_IMPORTANCE imp)
{
   if(imp == CALENDAR_IMPORTANCE_HIGH)     return "HIGH";
   if(imp == CALENDAR_IMPORTANCE_MODERATE) return "MEDIUM";
   if(imp == CALENDAR_IMPORTANCE_LOW)      return "LOW";
   return "UNKNOWN";
}

string CalVal(long v) { if(v == LONG_MIN) return ""; return DoubleToString(v/1000000.0, 4); }

void WriteCalendar()
{
   if(!BridgeUp) return;
   datetime from = TimeCurrent() - 24*60*60;
   datetime to   = TimeCurrent() + 2*24*60*60;
   MqlCalendarValue values[];
   int n = CalendarValueHistory(values, from, to);
   if(n <= 0) return;
   long srv_off = (long)TimeCurrent() - (long)TimeGMT();   // وقت الوسيط ? UTC (تلقائيّ، آمن صيفيًّا)
   DatabaseTransactionBegin(DbHandle);
   for(int i = 0; i < n; i++)
   {
      MqlCalendarEvent ev;
      if(!CalendarEventById(values[i].event_id, ev)) continue;
      MqlCalendarCountry ct;
      string country = "", currency = "";
      if(CalendarCountryById(ev.country_id, ct)) { country = ct.name; currency = ct.currency; }
      // نمط الـFeed المُثبت حرفًا بحرف: (string)id + DoubleToString للأوقات
      string q = "REPLACE INTO calendar(id,title,country,currency,impact_level,scheduled_at,"
         "actual,forecast,previous,written_at) VALUES('"
         + (string)values[i].id + "','" + Esc(ev.name) + "','" + Esc(country) + "','"
         + Esc(currency) + "','" + ImpactStr(ev.importance) + "',"
         + DoubleToString((double)((long)values[i].time - srv_off), 0) + ",'"
         + CalVal(values[i].actual_value) + "','" + CalVal(values[i].forecast_value) + "','"
         + CalVal(values[i].prev_value) + "'," + DoubleToString((double)TimeGMT(), 0) + ")";
      if(DatabaseExecute(DbHandle, q)) { g_cal_written++; WrCalMs = GetTickCount64(); }
   }
   DatabaseTransactionCommit(DbHandle);
}

//??????????????????????????????????????????????????????????????????
//  الإحماء — CopyRates ميتاتريدر لكل فريم الشارت ? candles_history
//  العدد = InpWarmupBars (افتراضي 200، آخر مثال). الطابع كما تُعطيه المنصّة.
//??????????????????????????????????????????????????????????????????
void WriteCandlesHistory()
{
   if(!BridgeUp) return;
   DatabaseExecute(DbHandle, "DELETE FROM candles_history;");
   g_warmup_written = 0;
   ENUM_TIMEFRAMES periods[12];
   periods[0]  = PERIOD_M1;  periods[1]  = PERIOD_M3;  periods[2]  = PERIOD_M5;
   periods[3]  = PERIOD_M15; periods[4]  = PERIOD_M30; periods[5]  = PERIOD_H1;
   periods[6]  = PERIOD_H2;  periods[7]  = PERIOD_H3;  periods[8]  = PERIOD_H4;
   periods[9]  = PERIOD_D1;  periods[10] = PERIOD_W1;  periods[11] = PERIOD_MN1;
   DatabaseTransactionBegin(DbHandle);
   for(int s = 0; s < SymCount; s++)
   {
      string sym = Syms[s];
      int    dg  = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      for(int p = 0; p < 12; p++)
      {
         int psec = PeriodSeconds(periods[p]);
         MqlRates rates[];
         ArraySetAsSeries(rates, false);              // الأقدم ? الأحدث
         int copied = CopyRates(sym, periods[p], 0, InpWarmupBars, rates);
         for(int i = 0; i < copied; i++)
         {
            // نمط الـFeed المُثبت حرفًا بحرف: DoubleToString لا %.*f
            string q = "INSERT INTO candles_history(symbol,period_seconds,period_start,"
               "open,high,low,close,volume) VALUES('" + Esc(sym) + "'," + (string)psec + ","
               + DoubleToString((double)rates[i].time, 0) + ","
               + DoubleToString(rates[i].open, dg) + "," + DoubleToString(rates[i].high, dg) + ","
               + DoubleToString(rates[i].low, dg) + "," + DoubleToString(rates[i].close, dg) + ","
               + DoubleToString((double)rates[i].tick_volume, 2) + ")";
            if(DatabaseExecute(DbHandle, q)) { g_warmup_written++; WrWarmMs = GetTickCount64(); }
         }
      }
   }
   DatabaseTransactionCommit(DbHandle);
}

//??????????????????????????????????????????????????????????????????
//  العمق — دفتر الأوامر (MarketBook) ? depth
//  يُكتب رمزٌ فقط حين يتغيّر دفتره (علَم يضبطه OnBookEvent) — لا فيضان.
//  level ترتيبيّ كما ورد؛ بايثون يفرز الأفضل بالسعر. لا مستهلك اليوم.
//??????????????????????????????????????????????????????????????????
void WriteDepth(int idx)
{
   if(!BridgeUp) return;
   string sym = Syms[idx];
   MqlBookInfo book[];
   if(!MarketBookGet(sym, book)) return;          // لا دفتر: لا كتابة (صدق لا اختلاق)
   int n = ArraySize(book);
   if(n <= 0) return;

   DatabaseTransactionBegin(DbHandle);
   DatabaseExecute(DbHandle, StringFormat("DELETE FROM depth WHERE symbol='%s';", sym));

   int bid_lvl = 0, ask_lvl = 0, written = 0;
   for(int i = 0; i < n; i++)
   {
      string side; int level;
      if(book[i].type == BOOK_TYPE_BUY || book[i].type == BOOK_TYPE_BUY_MARKET)
         { side = "BID"; level = bid_lvl++; }
      else if(book[i].type == BOOK_TYPE_SELL || book[i].type == BOOK_TYPE_SELL_MARKET)
         { side = "ASK"; level = ask_lvl++; }
      else continue;
      if(level >= InpDepthLevels) continue;

      double v = (book[i].volume_real > 0) ? book[i].volume_real : (double)book[i].volume;
      DatabaseExecute(DbHandle, StringFormat(
         "INSERT INTO depth (symbol,level,side,price,volume,updated_at) "
         "VALUES ('%s',%d,'%s',%.10g,%.10g,%d);",
         sym, level, side, book[i].price, v, (int)TimeGMT()));
      written++;
   }
   DatabaseTransactionCommit(DbHandle);
   g_depth_written += written;
   if(written > 0) { g_depth_ok = true; WrDepthMs = GetTickCount64(); }
}

//??????????????????????????????????????????????????????????????????
//  التنفيذ
//??????????????????????????????????????????????????????????????????
//+------------------------------------------------------------------+
//| يكتب حدث تنفيذ — العقد الذي تقرأه 563 حرفًا بحرف                  |
//| سطر جديد لكل حدث، ولا UPDATE. entry/exit هما السعر المنفَّذ فعلًا.  |
//+------------------------------------------------------------------+
void WriteEvent(string kind, ulong ticket, string sym, string side, double vol,
                double entry, double exitp, datetime otime, datetime ctime,
                string reason, string req_id, double profit = 0.0,
                double commission = 0.0, double swap = 0.0, double fee = 0.0,
                bool costs_known = false)
{
   if(!BridgeUp) return;

   string e  = (entry > 0) ? StringFormat("%.10g", entry) : "NULL";
   string x  = (exitp > 0) ? StringFormat("%.10g", exitp) : "NULL";
   string ot = (otime > 0) ? IntegerToString((long)otime) : "NULL";
   string ct = (ctime > 0) ? IntegerToString((long)ctime) : "NULL";
   string tk = (ticket != 0) ? IntegerToString((long)ticket) : "NULL";
   string rs = (reason != "") ? ("'" + Esc(reason) + "'") : "NULL";
   string ri = (req_id != "") ? ("'" + Esc(req_id) + "'") : "NULL";
   // الربح للإغلاق فقط (P&L الوسيط الدقيق من DEAL_PROFIT) — NULL للفتح/الرفض
   string pf = (kind == "CLOSED" || kind == "PARTIAL")
             ? StringFormat("%.2f", profit) : "NULL";
   // حكم المالك 2026-08-16: الصفر ليس دليلا على غياب التكلفة. اذا لم تُقرأ
   // من الصفقة فعلا تُكتب NULL لتبقى «مجهولة»، والذرة 517 تستثنيها من الجمع
   // وتعلن ان الصافي جزئي — بدل ان يُدفن النقص تحت صفر يبدو حقيقة.
   string cm = costs_known ? StringFormat("%.2f", commission) : "NULL";
   string sw = costs_known ? StringFormat("%.2f", swap)       : "NULL";
   string fe = costs_known ? StringFormat("%.2f", fee)        : "NULL";

   string q = StringFormat(
      "INSERT INTO trade_events_v2 (account_id,event_type,ticket,symbol,side,volume,"
      "entry_price,exit_price,open_time,close_time,reason,request_id,"
      "profit,commission,swap,fee,written_at) "
      "VALUES ('%s','%s',%s,'%s','%s',%.10g,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%d);",
      IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)), kind, tk, Esc(sym), side,
      vol, e, x, ot, ct, rs, ri, pf, cm, sw, fe, (int)TimeGMT());

   if(!DatabaseExecute(DbHandle, q))
   {
      LastError   = StringFormat("EVENT_%s_ERR_%d", kind, GetLastError());
      LastErrorAt = TimeCurrent();
      PushErr(LastError);
      PrintFormat("? [EVENTS] فشلت كتابة %s — خطأ=%d", kind, GetLastError());
   }
   else
   {
      WrEvents++; WrEventsMs = GetTickCount64();
      PrintFormat("?? [EVENTS] %s %s %s %.2f", kind, sym, side, vol);
   }
}

bool SelectPos(ulong ticket, string sym, string account_id, long command_magic)
{
   string current_account = IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
   if(account_id == "" || account_id != current_account) return false;
   if(command_magic != InpMagic) return false;
   if(ticket == 0 || !PositionSelectByTicket(ticket)) return false;
   if(PositionGetString(POSITION_SYMBOL) != sym) return false;
   if(PositionGetInteger(POSITION_MAGIC) != command_magic) return false;
   return true;
}

bool TradeResultOk(bool allow_placed=false)
{
   uint rc = Trade.ResultRetcode();
   if(rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_DONE_PARTIAL) return true;
   return allow_placed && rc == TRADE_RETCODE_PLACED;
}

//+------------------------------------------------------------------+
//| عقد البداية المحايدة: استثناء صريح ومحدود من InpRequireStop.       |
//| لا يُقبل إلا إذا مرّت بيانات الزوج كاملة من بايثون.                 |
//+------------------------------------------------------------------+
bool IsNeutralHedgeParams(string params_json)
{
   int pair_at = StringFind(params_json, "\"pair_id\":\"");
   bool pair_nonempty = pair_at >= 0 && StringSubstr(params_json, pair_at + 11, 1) != "\"";
   bool role = StringFind(params_json, "\"leg_role\":\"BUY\"") >= 0
            || StringFind(params_json, "\"leg_role\":\"SELL\"") >= 0;
   return StringFind(params_json, "\"protection_mode\":\"NEUTRAL_HEDGE\"") >= 0
       && StringFind(params_json, "\"pair_required\":true") >= 0
       && pair_nonempty && role;
}

//+------------------------------------------------------------------+
//| v3.00 — أي زوج: يضمن أن رمز الأمر مراقَب ومسجَّل بكل المضخّات.       |
//| رمز غير مراقَب: SymbolSelect تلقائيّ + تحقّق SYMBOL_SELECT، ثم       |
//| يُلحق فورًا بمضخّات التكّات/المواصفات/العمق قبل أول تنفيذ عليه.      |
//| رفض الوسيط = سبب صريح يعود للنظام في result — لا صمت.              |
//+------------------------------------------------------------------+
int FindSymIndex(string sym)
{
   for(int i = 0; i < SymCount; i++)
      if(Syms[i] == sym) return i;
   return -1;
}

bool EnsureSymbol(string sym, string &out_result)
{
   if(FindSymIndex(sym) >= 0) return true;

   ResetLastError();
   bool selected = SymbolSelect(sym, true);
   if(!selected || !(bool)SymbolInfoInteger(sym, SYMBOL_SELECT))
   {
      out_result = StringFormat("SYMBOL_UNAVAILABLE_AT_BROKER_ERR_%d", GetLastError());
      return false;
   }

   // التسجيل الفوريّ بالمضخّات — أمر النظام الصريح أعلى من سقف الإقلاع
   int idx = SymCount;
   ArrayResize(Syms, idx + 1);        ArrayResize(LastTickMsc, idx + 1);
   ArrayResize(LastBid, idx + 1);     ArrayResize(LastAsk, idx + 1);
   ArrayResize(LastSpread, idx + 1);  ArrayResize(DepthDirty, idx + 1);
   Syms[idx] = sym;
   LastTickMsc[idx] = 0;
   LastBid[idx] = 0; LastAsk[idx] = 0; LastSpread[idx] = -1;
   DepthDirty[idx] = true;            // مضخّة العمق: اكتب أوّل دفتر يصل
   SymCount++;

   WriteSymbolSpec(sym);              // مضخّة المواصفات: فورًا لا عند أول تكّة
   if(InpEnableDepth && !MarketBookAdd(sym))
      PrintFormat("? [DEPTH] تعذّر الاشتراك بعمق %s (قد لا يوفّره الوسيط)", sym);
   PrintFormat("?? [SYM] '%s' أُضيف بطلب النظام — %d رمز مراقَب", sym, SymCount);
   return true;
}

//+------------------------------------------------------------------+
//| ينفّذ أمرًا واحدًا — كل قيمة تأتي من الأمر، ولا يُحسب هنا شيء        |
//+------------------------------------------------------------------+
bool RunCommand(string action, string sym, string side, double vol,
                double price, double sl, double tp, ulong ticket,
                string req_id, string params_json, string account_id,
                long command_magic, string &out_result, ulong &out_ticket)
{
   // v3.10: تذكرة الفعل تُعاد لصفّ الأمر — بايثون يربط بها هوية المركز
   // (المطابقة 520 كانت عمياء: النتيجة كانت تعود بلا تذكرة).
   out_ticket = 0;
   string current_account = IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
   if(account_id == "" || account_id != current_account || req_id == "" || sym == ""
      || command_magic != InpMagic)
   {
      out_result = "COMMAND_OWNERSHIP_MISMATCH";
      return false;
   }
   if(!CanTrade())
   {
      out_result = !Connected     ? "BROKER_DISCONNECTED"
                 : !TradeAllowed  ? "OWNER_HALT"
                                  : "EXPERT_NOT_ALLOWED";
      return false;
   }

   // v3.00 أي زوج: الرمز يُضمَن قبل أي فعل — والرفض حدث REJECTED مسجَّل
   if(!EnsureSymbol(sym, out_result))
   {
      WriteEvent("REJECTED", 0, sym, side, vol, 0, 0, 0, 0, out_result, req_id);
      return false;
   }

   Trade.SetExpertMagicNumber(command_magic);

   //?? فتح ??????????????????????????????????????????????????????
   if(action == "OPEN")
   {
      if(vol <= 0) { out_result = "NO_VOLUME"; return false; }
      if(side != "BUY" && side != "SELL") { out_result = "BAD_SIDE"; return false; }
      bool neutral_hedge = IsNeutralHedgeParams(params_json);
      if(InpRequireStop && sl <= 0 && !neutral_hedge)
      {
         out_result = "NO_STOP";
         WriteEvent("REJECTED", 0, sym, side, vol, 0, 0, 0, 0, out_result, req_id);
         return false;
      }
      if(neutral_hedge && (sl > 0 || tp > 0))
      {
         out_result = "NEUTRAL_PAIR_MUST_HAVE_NO_DIRECTIONAL_LEVELS";
         WriteEvent("REJECTED", 0, sym, side, vol, 0, 0, 0, 0, out_result, req_id);
         return false;
      }

      bool is_buy = (side == "BUY");
      bool ok = is_buy ? Trade.Buy(vol, sym, 0, sl, tp, "NQ")
                       : Trade.Sell(vol, sym, 0, sl, tp, "NQ");
      if(!ok || !TradeResultOk(false))
      {
         out_result = StringFormat("RETCODE_%d", Trade.ResultRetcode());
         WriteEvent("REJECTED", 0, sym, side, vol, 0, 0, 0, 0, out_result, req_id);
         return false;
      }

      ulong deal = Trade.ResultDeal();
      ulong pid = 0; double fill = 0; datetime otime = TimeCurrent();
      if(deal > 0 && HistoryDealSelect(deal))
      {
         pid = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
         fill = HistoryDealGetDouble(deal, DEAL_PRICE);
         otime = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
      }
      if(deal == 0 || pid == 0 || fill <= 0)
      {
         out_result = "NO_CONFIRMED_DEAL";
         WriteEvent("REJECTED", 0, sym, side, vol, 0, 0, 0, 0, out_result, req_id);
         return false;
      }
      WriteEvent("OPENED", pid, sym, side, vol, fill, 0, otime, 0, "", req_id);
      out_result = "OK";
      out_ticket = pid;
      return true;
   }

   //?? إغلاق كامل ???????????????????????????????????????????????
   if(action == "CLOSE")
   {
      if(!SelectPos(ticket, sym, account_id, command_magic)) { out_result = "NO_POSITION"; return false; }
      ulong tk = (ulong)PositionGetInteger(POSITION_TICKET);
      if(!Trade.PositionClose(tk) || !TradeResultOk(false))
      {
         out_result = StringFormat("RETCODE_%d", Trade.ResultRetcode());
         return false;
      }
      // حدث الإغلاق يُكتب في OnTradeTransaction بالسعر المنفَّذ الحقيقيّ
      out_result = "OK";
      out_ticket = tk;
      return true;
   }

   //?? إغلاق جزئي (التخفيف/جني جزء) ??????????????????????????????
   if(action == "CLOSE_PARTIAL")
   {
      if(!SelectPos(ticket, sym, account_id, command_magic)) { out_result = "NO_POSITION"; return false; }
      if(vol <= 0) { out_result = "NO_VOLUME"; return false; }
      ulong tk = (ulong)PositionGetInteger(POSITION_TICKET);
      if(!Trade.PositionClosePartial(tk, vol) || !TradeResultOk(false))
      {
         out_result = StringFormat("RETCODE_%d", Trade.ResultRetcode());
         return false;
      }
      out_result = "OK";
      out_ticket = tk;
      return true;
   }

   //?? تعديل الوقف أو الهدف (breakeven/trailing = MODIFY_SL) ??????
   if(action == "MODIFY_SL" || action == "MODIFY_TP")
   {
      if(!SelectPos(ticket, sym, account_id, command_magic)) { out_result = "NO_POSITION"; return false; }
      ulong  tk      = (ulong)PositionGetInteger(POSITION_TICKET);
      double cur_sl  = PositionGetDouble(POSITION_SL);
      double cur_tp  = PositionGetDouble(POSITION_TP);
      double new_sl  = (action == "MODIFY_SL") ? sl : cur_sl;
      double new_tp  = (action == "MODIFY_TP") ? tp : cur_tp;
      if(!Trade.PositionModify(tk, new_sl, new_tp) || !TradeResultOk(false))
      {
         out_result = StringFormat("RETCODE_%d", Trade.ResultRetcode());
         return false;
      }
      out_result = "OK";
      out_ticket = tk;
      return true;
   }

   //?? أمر معلّق ????????????????????????????????????????????????
   if(action == "PENDING_CREATE")
   {
      if(vol <= 0 || price <= 0 || (side != "BUY" && side != "SELL")) { out_result = "BAD_PARAMS"; return false; }
      if(InpRequireStop && sl <= 0) { out_result = "NO_STOP"; return false; }
      MqlTick t;
      if(!SymbolInfoTick(sym, t)) { out_result = "NO_TICK"; return false; }
      bool ok;
      if(side == "BUY")
         ok = (price < t.ask) ? Trade.BuyLimit(vol, price, sym, sl, tp)
                              : Trade.BuyStop (vol, price, sym, sl, tp);
      else
         ok = (price > t.bid) ? Trade.SellLimit(vol, price, sym, sl, tp)
                              : Trade.SellStop (vol, price, sym, sl, tp);
      if(!ok || !TradeResultOk(true) || Trade.ResultOrder() == 0)
      {
         out_result = StringFormat("RETCODE_%d", Trade.ResultRetcode());
         WriteEvent("REJECTED", 0, sym, side, vol, 0, 0, 0, 0, out_result, req_id);
         return false;
      }
      out_result = "OK";
      out_ticket = Trade.ResultOrder();
      return true;
   }

   if(action == "PENDING_DELETE")
   {
      if(account_id != IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) || command_magic != InpMagic
         || ticket == 0 || !OrderSelect(ticket) || OrderGetInteger(ORDER_MAGIC) != command_magic
         || OrderGetString(ORDER_SYMBOL) != sym) { out_result = "NO_OR_FOREIGN_ORDER"; return false; }
      if(!Trade.OrderDelete(ticket) || !TradeResultOk(false))
      {
         out_result = StringFormat("RETCODE_%d", Trade.ResultRetcode());
         return false;
      }
      out_result = "OK";
      return true;
   }

   //?? التريلينغ المستمرّ ليس من عمل الذراع: النظام يديره بـMODIFY_SL ??
   if(action == "TRAIL_START" || action == "TRAIL_STOP")
   {
      out_result = "NOT_SUPPORTED_BY_DESIGN";
      return false;
   }

   out_result = "UNKNOWN_ACTION";
   return false;
}

//+------------------------------------------------------------------+
//| يقرأ الأوامر المعلّقة وينفّذها بالترتيب — مطالبة ذرّية وحارس عمر     |
//+------------------------------------------------------------------+
void PumpCommands()
{
   if(!BridgeUp) return;

   int req = DatabasePrepare(DbHandle,
      "SELECT id, request_id, action, symbol, side, volume, price, stop_loss,"
      " take_profit, ticket, params_json, magic, account_id, project_build_id, created_at"
      " FROM commands WHERE status='PENDING' AND account_id='" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + "' ORDER BY id LIMIT 50;");
   if(req == INVALID_HANDLE) return;

      long   ids[];    string rids[], acts[], syms[], sides[], params[], accounts[], builds[];
      double vols[], prices[], sls[], tps[], creats[];
      long   tickets[], magics[];
   int    n = 0;

   while(DatabaseRead(req))
   {
      ArrayResize(ids,n+1);   ArrayResize(rids,n+1);  ArrayResize(acts,n+1);
      ArrayResize(syms,n+1);  ArrayResize(sides,n+1); ArrayResize(vols,n+1);
      ArrayResize(prices,n+1);ArrayResize(sls,n+1);   ArrayResize(tps,n+1);
      ArrayResize(tickets,n+1); ArrayResize(params,n+1); ArrayResize(magics,n+1);
      ArrayResize(accounts,n+1); ArrayResize(builds,n+1); ArrayResize(creats,n+1);
      DatabaseColumnLong  (req, 0, ids[n]);
      DatabaseColumnText  (req, 1, rids[n]);
      DatabaseColumnText  (req, 2, acts[n]);
      DatabaseColumnText  (req, 3, syms[n]);
      DatabaseColumnText  (req, 4, sides[n]);
      DatabaseColumnDouble(req, 5, vols[n]);
      DatabaseColumnDouble(req, 6, prices[n]);
      DatabaseColumnDouble(req, 7, sls[n]);
      DatabaseColumnDouble(req, 8, tps[n]);
      DatabaseColumnLong  (req, 9, tickets[n]);
      DatabaseColumnText  (req,10, params[n]);
      DatabaseColumnLong  (req,11, magics[n]);
      DatabaseColumnText  (req,12, accounts[n]);
      DatabaseColumnText  (req,13, builds[n]);
      DatabaseColumnDouble(req,14, creats[n]);
      n++;
   }
   DatabaseFinalize(req);
   if(n == 0) return;

   for(int i = 0; i < n; i++)
   {
      string current_account = IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
      if(accounts[i] == "" || accounts[i] != current_account || rids[i] == ""
         || syms[i] == "" || magics[i] != InpMagic)
      {
         DatabaseExecute(DbHandle, StringFormat(
            "UPDATE commands SET status='FAILED', result='COMMAND_OWNERSHIP_MISMATCH', done_at=%d WHERE id=%I64d AND status='PENDING' AND account_id='%s';",
            (int)TimeGMT(), ids[i], Esc(accounts[i])));
         CmdFailed++;
         PushCmd(StringFormat("%s %s — ملكيّة غير مطابقة", ArAct(acts[i]), syms[i]), false);
         continue;
      }
      if(builds[i] != InpProjectBuildId)
      {
         DatabaseExecute(DbHandle, StringFormat(
            "UPDATE commands SET status='FAILED', result='PROJECT_BUILD_MISMATCH', done_at=%d WHERE id=%I64d AND status='PENDING';",
            (int)TimeGMT(), ids[i]));
         CmdFailed++;
         PushCmd(StringFormat("%s %s — بناء غير مطابق", ArAct(acts[i]), syms[i]), false);
         continue;
      }
      // حارس العمر: أمر أقدم من الحدّ يُختم EXPIRED ولا يلمس السوق
      if(InpMaxCmdAgeSec > 0 && creats[i] > 0
         && ((double)TimeGMT() - creats[i]) > (double)InpMaxCmdAgeSec)
      {
         DatabaseExecute(DbHandle, StringFormat(
            "UPDATE commands SET status='EXPIRED', result='STALE_%ds',"
            " done_at=%d WHERE id=%I64d AND status='PENDING';",
            (int)((double)TimeGMT() - creats[i]), (int)TimeGMT(), ids[i]));
         CmdExpired++;
         PushCmd(StringFormat("%s %s — بائت", ArAct(acts[i]), syms[i]), false);
         PrintFormat("? [CMD] %s %s تجاوز العمر — EXPIRED", acts[i], syms[i]);
         continue;
      }

      CmdReceived++;
      LastCmdAt = TimeCurrent();

      // مطالبة ذرّية: الشرط status='PENDING' يمنع ذراعين من تنفيذ الأمر مرّتين
      DatabaseExecute(DbHandle, StringFormat(
         "UPDATE commands SET status='TAKEN', taken_at=%d"
         " WHERE id=%I64d AND status='PENDING' AND account_id='%s';",
         (int)TimeGMT(), ids[i], IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))));
      int chg = DatabasePrepare(DbHandle, "SELECT changes();");
      long claimed = 0;
      if(chg != INVALID_HANDLE)
      {
         if(DatabaseRead(chg)) DatabaseColumnLong(chg, 0, claimed);
         DatabaseFinalize(chg);
      }
      if(claimed == 0) { CmdReceived--; continue; }

      string result = "";
      ulong  done_ticket = 0;
      StringToUpper(acts[i]); StringToUpper(sides[i]);
      bool ok = RunCommand(acts[i], syms[i], sides[i], vols[i], prices[i],
                           sls[i], tps[i], (ulong)tickets[i], rids[i], params[i],
                           accounts[i], magics[i], result, done_ticket);

      if(ok) CmdDone++;
      else if(StringFind(result, "NOT_SUPPORTED") >= 0) CmdUnsupported++;
      else { CmdFailed++; LastError = ArResult(result); LastErrorAt = TimeCurrent(); PushErr(ArResult(result)); }

      // v3.10: التذكرة تُكتب مع النتيجة عند توفّرها — بايثون (601←520)
      // يربط بها الساق المرغوبة فتصير المطابقة بعد التنفيذ حقيقية.
      // أمرٌ بلا تذكرة فعلٍ لا يُدَاس على حقل ticket الوارد فيه.
      if(done_ticket > 0)
         DatabaseExecute(DbHandle, StringFormat(
            "UPDATE commands SET status='%s', result='%s', done_at=%d, ticket=%I64u WHERE id=%I64d;",
            (ok ? "DONE" : "FAILED"), Esc(result), (int)TimeGMT(), done_ticket, ids[i]));
      else
         DatabaseExecute(DbHandle, StringFormat(
            "UPDATE commands SET status='%s', result='%s', done_at=%d WHERE id=%I64d;",
            (ok ? "DONE" : "FAILED"), Esc(result), (int)TimeGMT(), ids[i]));
      PushCmd(StringFormat("%s %s %s — %s",
              ArAct(acts[i]), syms[i], ArSide(sides[i]), ArResult(result)), ok);

      PrintFormat("%s [CMD] %s %s %s ? %s",
                  ok ? "?" : "?", acts[i], syms[i], sides[i], result);
   }
}

//??????????????????????????????????????????????????????????????????
//  الشاشة — عرض فقط، بلا حساب. v3.00: العناوين إنكليزية قصيرة لأن
//  رسم MT5 لا يضمن العربية؛ ترجمتها كاملة في تعليمات_التجميع_MT5.md.
//  حالة واحدة أعلى اليمين: LIVE / NO PYTHON / HALTED / NO BROKER / NO DB
//??????????????????????????????????????????????????????????????????
bool SystemAlive()
{
   if(SysSeenAt == 0) return false;
   return (TimeCurrent() - SysSeenAt) <= InpSystemTimeout;
}

void ReadDisplay()
{
   if(!BridgeUp) return;
   int req = DatabasePrepare(DbHandle,
      "SELECT daily_pct, wins, losses, trades, open_trades, kill_switch,"
      " updated_at FROM display WHERE id=1;");
   if(req == INVALID_HANDLE) return;
   if(DatabaseRead(req))
   {
      double d; long w,l,t,o,k;
      double stamp = 0;
      if(DatabaseColumnDouble(req, 6, stamp) && stamp > 0 && stamp != SysLastSeen)
      {
         SysLastSeen = stamp;
         SysSeenAt   = TimeCurrent();
      }
      if(DatabaseColumnDouble(req,0,d) && d != 0) DispDailyPct = StringFormat("%+.2f%%", d);
      if(DatabaseColumnLong(req,1,w)) DispWins   = IntegerToString((int)w);
      if(DatabaseColumnLong(req,2,l)) DispLosses = IntegerToString((int)l);
      if(DatabaseColumnLong(req,3,t)) DispTrades = IntegerToString((int)t);
      if(DatabaseColumnLong(req,4,o)) DispOpen   = IntegerToString((int)o);
      if(DatabaseColumnLong(req,5,k)) DispKill   = (k != 0) ? "ACTIVE" : "off";
   }
   DatabaseFinalize(req);
}

// خطّان: Tahoma للمفاتيح العربيّة (Consolas بلا حروف عربيّة)،
// وConsolas للقيم الرقميّة وحدها لأنّ عرض حرفه ثابت فتتحاذى الأرقام.
#define QNQ_FONT_AR  "Tahoma"
#define QNQ_FONT_NUM "Consolas"

void Cell(string id, int x, int y, string text, color tint, int size, string font)
{
   string nm = "QNQ_" + id;
   if(ObjectFind(0, nm) < 0)
   {
      ObjectCreate(0, nm, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, nm, OBJPROP_CORNER,     InpPanelCorner);
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, nm, OBJPROP_HIDDEN,     true);
   }
   // صفّ فارغ: يُخفى ولا يُترك بنصّ فارغ — OBJ_LABEL بنصّ فارغ يعرض اسمه
   // الافتراضيّ "Label" على الشارت (سلوك المنصّة، لا خلل بالمنطق).
   if(StringLen(text) == 0)
   {
      ObjectSetInteger(0, nm, OBJPROP_TIMEFRAMES, OBJ_NO_PERIODS);
      return;
   }
   ObjectSetInteger(0, nm, OBJPROP_TIMEFRAMES, OBJ_ALL_PERIODS);
   ObjectSetString (0, nm, OBJPROP_FONT,      font);
   ObjectSetInteger(0, nm, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, nm, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, nm, OBJPROP_FONTSIZE,  size);
   ObjectSetInteger(0, nm, OBJPROP_COLOR,     tint);
   ObjectSetString (0, nm, OBJPROP_TEXT,      text);
}

int RowY(int row) { return InpPanelY + 34 + row * (InpFontSize + 9); }

// Consolas بلا حروف عربيّة: أي نصّ فيه عربيّ يأخذ Tahoma، والأرقام وحدها
// تبقى على Consolas فتتحاذى أعمدتها بعرض الحرف الثابت.
bool HasArabic(string s)
{
   for(int i = StringLen(s) - 1; i >= 0; i--)
   {
      ushort c = StringGetCharacter(s, i);
      if(c >= 0x0600 && c <= 0x06FF) return true;
   }
   return false;
}

string FontFor(string s) { return HasArabic(s) ? QNQ_FONT_AR : QNQ_FONT_NUM; }

// صفّ مفتاح/قيمة: كائنان — لكلٍّ خطّه، فلا حشو ولا انزياح
void KV(string id, int x, int row, string key, string val, color tint)
{
   Cell(id + "K", x,           RowY(row), key, tint, InpFontSize, QNQ_FONT_AR);
   Cell(id + "V", x + InpKeyW, RowY(row), val, tint, InpFontSize, FontFor(val));
}

// صفّ ممتدّ بنصّ واحد (عناوين وقوائم)
void Span(string id, int x, int row, string text, color tint)
{
   Cell(id + "K", x, RowY(row), text, tint, InpFontSize, FontFor(text));
   Cell(id + "V", x, RowY(row), "",   tint, InpFontSize, QNQ_FONT_NUM);
}

// v3.10 — صفّ ثلاثيّ: مفتاح عربيّ / عدّاد رقميّ / عمر عربيّ.
// كل خليّة بخطّها فلا يكسر «ث/د» محاذاة أعمدة Consolas.
void KV3(string id, int x, int row, string key, string num, string age, color tint)
{
   Cell(id + "K", x,               RowY(row), key, tint, InpFontSize, QNQ_FONT_AR);
   Cell(id + "V", x + InpKeyW,     RowY(row), num, tint, InpFontSize, QNQ_FONT_NUM);
   Cell(id + "A", x + InpKeyW + 62, RowY(row), age, tint, InpFontSize, QNQ_FONT_AR);
}

// v3.10 — صفّ مركز بأربعة أعمدة: رمز/جهة/حجم/أسعار وربح.
// العربيّة (الجهة) معزولة بخليّتها فلا فوضى اتجاهين مع الأرقام.
void PosRow(string id, int x, int row, string sym, string side, string vol,
            string px, color tint)
{
   Cell(id + "S", x,        RowY(row), sym,  tint, InpFontSize, QNQ_FONT_NUM);
   Cell(id + "D", x + 92,   RowY(row), side, tint, InpFontSize, QNQ_FONT_AR);
   Cell(id + "W", x + 140,  RowY(row), vol,  tint, InpFontSize, QNQ_FONT_NUM);
   Cell(id + "X", x + 186,  RowY(row), px,   tint, InpFontSize, QNQ_FONT_NUM);
}

// الحالة الواحدة — بترتيب الخطورة: القاعدة فالوسيط فالإذن فبايثون
string StatusText(color &tint, color c_ok, color c_warn, color c_bad)
{
   if(!BridgeUp)                       { tint = c_bad;  return "لا قاعدة"; }
   if(!Connected)                      { tint = c_bad;  return "لا وسيط"; }
   if(!TradeAllowed || !ExpertAllowed) { tint = c_warn; return "موقوف"; }
   if(!SystemAlive())                  { tint = c_warn; return "بايثون صامت"; }
   tint = c_ok; return "حيّ";
}

void DrawBackground(int rows)
{
   string nm = "QNQ_BG";
   int h = 34 + rows * (InpFontSize + 9) + 16;
   if(ObjectFind(0, nm) < 0)
   {
      ObjectCreate(0, nm, OBJ_RECTANGLE_LABEL, 0, 0, 0);
      ObjectSetInteger(0, nm, OBJPROP_BGCOLOR,      C'22,26,36');
      ObjectSetInteger(0, nm, OBJPROP_BORDER_TYPE,  BORDER_FLAT);
      ObjectSetInteger(0, nm, OBJPROP_COLOR,        C'70,82,110');
      ObjectSetInteger(0, nm, OBJPROP_WIDTH,        1);
      ObjectSetInteger(0, nm, OBJPROP_BACK,         false);
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE,   false);
      ObjectSetInteger(0, nm, OBJPROP_HIDDEN,       true);
      ObjectSetInteger(0, nm, OBJPROP_ZORDER,       0);
   }
   ObjectSetInteger(0, nm, OBJPROP_CORNER,    InpPanelCorner);
   ObjectSetInteger(0, nm, OBJPROP_XDISTANCE, InpPanelX - 6);
   ObjectSetInteger(0, nm, OBJPROP_YDISTANCE, InpPanelY - 6);
   ObjectSetInteger(0, nm, OBJPROP_XSIZE,     InpPanelW);
   ObjectSetInteger(0, nm, OBJPROP_YSIZE,     h);
}

void DrawPanel()
{
   if(!InpShowPanel) return;

   color C_TEXT = C'215,222,235';
   color C_OK   = C'95,205,140';
   color C_WARN = C'240,185,90';
   color C_BAD  = C'235,105,105';
   color C_DIM  = C'135,145,165';
   color C_HEAD = C'110,165,225';

   // v3.10: العمود الأيسر ثلاثيّ الخلايا (مفتاح/عدّاد/عمر) — لا خلط خطوط
   string LK[QNQ_MAX_LR], LN[QNQ_MAX_LR], LA[QNQ_MAX_LR]; color LC[QNQ_MAX_LR]; int nl = 0;
   string RK[QNQ_MAX_LR], RV[QNQ_MAX_LR]; color RC[QNQ_MAX_LR]; int nr = 0;  // يمين: الحساب والأوامر
   string F[QNQ_MAX_F];  color FC[QNQ_MAX_F];  int nf = 0;                   // بالعرض

   //== الجسر: نبض بايثون ثم عدّاد وعمر آخر كتابة لكل جدول ==========
   LK[nl] = "الجسر"; LN[nl] = ""; LA[nl] = ""; LC[nl] = C_HEAD; nl++;
   if(SysSeenAt == 0)
   { LK[nl] = "بايثون"; LN[nl] = ""; LA[nl] = "لم يصل"; LC[nl] = C_WARN; nl++; }
   else
   {
      int idle = (int)(TimeCurrent() - SysSeenAt);
      LK[nl] = "بايثون"; LN[nl] = "";
      LA[nl] = StringFormat("%s %d ث", (SystemAlive() ? "حيّ" : "صامت"), idle);
      LC[nl] = SystemAlive() ? C_OK : C_WARN; nl++;
   }
   LK[nl] = "التِكّات";   LN[nl] = IntegerToString((long)TicksCaptured); LA[nl] = AgeStr(WrTicksMs);  LC[nl] = C_TEXT; nl++;
   LK[nl] = "الحساب";    LN[nl] = IntegerToString(WrAcct);              LA[nl] = AgeStr(WrAcctMs);   LC[nl] = C_TEXT; nl++;
   LK[nl] = "المراكز";   LN[nl] = IntegerToString(WrPos);               LA[nl] = AgeStr(WrPosMs);    LC[nl] = C_TEXT; nl++;
   LK[nl] = "الأحداث";   LN[nl] = IntegerToString(WrEvents);            LA[nl] = AgeStr(WrEventsMs); LC[nl] = C_TEXT; nl++;
   LK[nl] = "المواصفات"; LN[nl] = IntegerToString(WrSpecs);             LA[nl] = AgeStr(WrSpecsMs);  LC[nl] = C_TEXT; nl++;
   LK[nl] = "التقويم";   LN[nl] = IntegerToString(g_cal_written);       LA[nl] = AgeStr(WrCalMs);    LC[nl] = C_TEXT; nl++;
   LK[nl] = "التسخين";   LN[nl] = IntegerToString(g_warmup_written);    LA[nl] = AgeStr(WrWarmMs);   LC[nl] = C_TEXT; nl++;
   if(!InpEnableDepth)
   { LK[nl] = "العمق"; LN[nl] = ""; LA[nl] = "مطفأ"; LC[nl] = C_DIM; nl++; }
   else
   { LK[nl] = "العمق"; LN[nl] = IntegerToString(g_depth_written); LA[nl] = AgeStr(WrDepthMs); LC[nl] = C_TEXT; nl++; }
   LK[nl] = "الرموز"; LN[nl] = IntegerToString(SymCount); LA[nl] = ""; LC[nl] = C_DIM; nl++;

   //== الحساب ثم الأوامر ==========================================
   RK[nr] = "الحساب"; RV[nr] = ""; RC[nr] = C_HEAD; nr++;
   string st; color stc;
   if(CanTrade())         { st = "سليم";            stc = C_OK;   }
   else if(!Connected)    { st = "مقطوع";           stc = C_BAD;  }
   else if(!TradeAllowed) { st = "التداول مطفأ";    stc = C_WARN; }
   else                   { st = "الإكسبرت ممنوع";  stc = C_WARN; }
   RK[nr] = "الحالة";  RV[nr] = st; RC[nr] = stc; nr++;
   RK[nr] = "الرصيد";  RV[nr] = PadL(LastBalance < 0 ? "-" : DoubleToString(LastBalance, 2), 12); RC[nr] = C_TEXT; nr++;
   RK[nr] = "الملكيّة"; RV[nr] = PadL(LastEquity  < 0 ? "-" : DoubleToString(LastEquity, 2), 12);  RC[nr] = C_TEXT; nr++;
   double margin_pct = (LastEquity > 0) ? (LastMargin / LastEquity * 100.0) : 0.0;
   RK[nr] = "الهامش";  RV[nr] = PadL(LastMargin < 0 ? "-" : DoubleToString(margin_pct, 1) + "%", 12);
   RC[nr] = (margin_pct < 50) ? C_TEXT : C_WARN; nr++;
   RK[nr] = "المتاح";  RV[nr] = PadL(LastFree < 0 ? "-" : DoubleToString(LastFree, 2), 12); RC[nr] = C_TEXT; nr++;
   RK[nr] = "رقم الحساب"; RV[nr] = PadL(IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)), 12); RC[nr] = C_DIM; nr++;
   RK[nr] = "اليوم";   RV[nr] = PadL(DispDailyPct, 12); RC[nr] = C_TEXT; nr++;
   RK[nr] = ""; RV[nr] = ""; RC[nr] = C_DIM; nr++;
   RK[nr] = "الأوامر"; RV[nr] = ""; RC[nr] = C_HEAD; nr++;
   RK[nr] = "الطابور";
   RV[nr] = StringFormat("%d وصل · %d نُفِّذ", (int)CmdReceived, (int)CmdDone);
   RC[nr] = (CmdReceived > 0) ? C_OK : C_DIM; nr++;
   RK[nr] = "المرفوض";
   RV[nr] = StringFormat("%d فشل · %d بائت · %d مرفوض",
                         (int)CmdFailed, (int)CmdExpired, (int)CmdUnsupported);
   RC[nr] = (CmdFailed + CmdExpired + CmdUnsupported > 0) ? C_WARN : C_DIM; nr++;
   RK[nr] = "القاطع";
   RV[nr] = (DispKill == "ACTIVE") ? "مفعَّل" : (DispKill == "off" ? "مطفأ" : DispKill);
   RC[nr] = (DispKill == "ACTIVE") ? C_BAD : C_DIM; nr++;

   //== بالعرض: عنوان المراكز ثم صفوفها بأعمدة، فالأوامر فالأخطاء ====
   // F[] يحمل العناوين والسطور النصيّة؛ صفوف المراكز تُرسم بأعمدة PosRow
   // في مواضعها من نفس تسلسل الصفوف (خانة F فارغة تحجز السطر).
   int pos_rows[QNQ_POS_ROWS]; int npos = 0;
   F[nf] = "المراكز المفتوحة"; FC[nf] = C_HEAD; nf++;
   if(PanelPosN == 0) { F[nf] = " لا شيء"; FC[nf] = C_DIM; nf++; }
   for(int i = 0; i < PanelPosN && i < QNQ_POS_ROWS; i++)
   { pos_rows[npos] = nf; npos++; F[nf] = ""; FC[nf] = (PanelPosPnL[i] >= 0) ? C_OK : C_BAD; nf++; }
   if(PanelPosMore > 0)
   { F[nf] = StringFormat(" وغيرها %d", PanelPosMore); FC[nf] = C_DIM; nf++; }

   F[nf] = "آخر الأوامر"; FC[nf] = C_HEAD; nf++;
   if(RecentCmdN == 0) { F[nf] = " لا شيء"; FC[nf] = C_DIM; nf++; }
   for(int i = 0; i < RecentCmdN && i < QNQ_LAST_CMDS; i++)
   { F[nf] = " " + RecentCmds[i]; FC[nf] = RecentCmdOk[i] ? C_OK : C_BAD; nf++; }

   F[nf] = "آخر الأخطاء"; FC[nf] = C_HEAD; nf++;
   if(RecentErrN == 0) { F[nf] = " لا شيء"; FC[nf] = C_DIM; nf++; }
   for(int i = 0; i < RecentErrN && i < QNQ_LAST_ERRS; i++)
   { F[nf] = " " + RecentErrs[i]; FC[nf] = C_BAD; nf++; }

   int top = (nl > nr) ? nl : nr;
   DrawBackground(top + nf);

   //== العنوان عربيّ، والحالة أعلى اليمين ==========================
   Cell("TITLE", InpPanelX + 8, InpPanelY + 8,
        "QUANT_NQ  نسخة " + QNQ_VERSION + "  "
        + TimeToString(__DATETIME__, TIME_DATE|TIME_MINUTES),
        C_TEXT, InpTitleSize, QNQ_FONT_AR);
   color stat_c = C_OK;
   string stat = StatusText(stat_c, C_OK, C_WARN, C_BAD);
   Cell("STATUS", InpPanelX + InpPanelW - 96, InpPanelY + 8,
        stat, stat_c, InpTitleSize, QNQ_FONT_AR);

   for(int i = 0; i < QNQ_MAX_LR; i++)
   {
      KV3(StringFormat("L%d", i), InpPanelX + 8, i,
          (i < nl) ? LK[i] : "", (i < nl) ? LN[i] : "", (i < nl) ? LA[i] : "",
          (i < nl) ? LC[i] : C_DIM);
      KV(StringFormat("R%d", i), InpPanelX + InpColW, i,
         (i < nr) ? RK[i] : "", (i < nr) ? RV[i] : "", (i < nr) ? RC[i] : C_DIM);
   }
   for(int i = 0; i < QNQ_MAX_F; i++)
      Span(StringFormat("F%d", i), InpPanelX + 8, top + i,
           (i < nf) ? F[i] : "", (i < nf) ? FC[i] : C_DIM);
   for(int i = 0; i < QNQ_POS_ROWS; i++)
   {
      bool live = (i < npos);
      int  r    = live ? top + pos_rows[i] : 0;
      PosRow(StringFormat("P%d", i), InpPanelX + 14, r,
             live ? PanelPosSym[i]  : "", live ? PanelPosSide[i] : "",
             live ? PanelPosVol[i]  : "", live ? PanelPosPx[i]   : "",
             live ? FC[pos_rows[i]] : C_DIM);
   }

   ChartRedraw(0);
}

void ClearPanel()
{
   ObjectDelete(0, "QNQ_BG");
   ObjectDelete(0, "QNQ_TITLE");
   ObjectDelete(0, "QNQ_STATUS");
   for(int i = 0; i < QNQ_MAX_LR; i++)
   {
      ObjectDelete(0, StringFormat("QNQ_L%dK", i));
      ObjectDelete(0, StringFormat("QNQ_L%dV", i));
      ObjectDelete(0, StringFormat("QNQ_L%dA", i));
      ObjectDelete(0, StringFormat("QNQ_R%dK", i));
      ObjectDelete(0, StringFormat("QNQ_R%dV", i));
   }
   for(int i = 0; i < QNQ_MAX_F; i++)
   {
      ObjectDelete(0, StringFormat("QNQ_F%dK", i));
      ObjectDelete(0, StringFormat("QNQ_F%dV", i));
   }
   for(int i = 0; i < QNQ_POS_ROWS; i++)
   {
      ObjectDelete(0, StringFormat("QNQ_P%dS", i));
      ObjectDelete(0, StringFormat("QNQ_P%dD", i));
      ObjectDelete(0, StringFormat("QNQ_P%dW", i));
      ObjectDelete(0, StringFormat("QNQ_P%dX", i));
   }
   ChartRedraw(0);
}

//??????????????????????????????????????????????????????????????????
//  الرموز
//??????????????????????????????????????????????????????????????????
void LoadSymbols()
{
   string wanted[];
   int n = 0;

   if(StringLen(InpSymbols) > 0)
   {
      string s = InpSymbols;
      StringReplace(s, " ", "");
      n = StringSplit(s, ',', wanted);
   }
   else
   {
      n = SymbolsTotal(true);
      ArrayResize(wanted, n);
      for(int i = 0; i < n; i++) wanted[i] = SymbolName(i, true);
   }

   if(n > InpMaxSymbols) n = InpMaxSymbols;

   ArrayResize(Syms, n);        ArrayResize(LastTickMsc, n);
   ArrayResize(LastBid, n);     ArrayResize(LastAsk, n);
   ArrayResize(LastSpread, n);  ArrayResize(DepthDirty, n);

   SymCount = 0;
   for(int i = 0; i < n; i++)
   {
      string s = wanted[i];
      StringTrimLeft(s); StringTrimRight(s);
      if(s == "") continue;
      if(!SymbolSelect(s, true))
      {
         PrintFormat("? [SYM] تعذّر إضافة '%s' إلى Market Watch — يُتخطّى", s);
         continue;
      }
      Syms[SymCount] = s;
      LastTickMsc[SymCount] = 0;
      LastBid[SymCount] = 0; LastAsk[SymCount] = 0; LastSpread[SymCount] = -1;
      DepthDirty[SymCount] = true;   // اكتب العمق مرّة أوّل تشغيل
      SymCount++;
   }
   ArrayResize(Syms, SymCount);
   PrintFormat("?? [SYM] %d رمز مراقَب", SymCount);
}

//??????????????????????????????????????????????????????????????????
//  الأحداث
//??????????????????????????????????????????????????????????????????
int OnInit()
{
   Trade.SetExpertMagicNumber(InpMagic);
   Trade.SetDeviationInPoints(InpDeviationPts);

   if(!BridgeOpen()) return INIT_FAILED;
   if(!BuildSchema())
   {
      PrintFormat("? [BRIDGE] فشل بناء المخطط — خطأ=%d", GetLastError());
      return INIT_FAILED;
   }
   BridgeUp = true;

   // تطهير الإقلاع: كل PENDING أقدم من الحدّ كُتب ونحن غائبون — يُختم EXPIRED دفعة
   if(InpMaxCmdAgeSec > 0)
      DatabaseExecute(DbHandle, StringFormat(
         "UPDATE commands SET status='EXPIRED', result='STALE_ON_STARTUP',"
         " done_at=%d WHERE status='PENDING' AND account_id='%s' AND created_at < %d;",
         (int)TimeGMT(), Esc(IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))),
         (int)TimeGMT() - InpMaxCmdAgeSec));
   DatabaseExecute(DbHandle, StringFormat(
      "UPDATE commands SET status='FAILED', result='UNKNOWN_AFTER_CRASH', done_at=%d "
      "WHERE status='TAKEN' AND account_id='%s';", (int)TimeGMT(),
      Esc(IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)))));

   PrintFormat("?? [BRIDGE] %s\\Files\\%s",
               TerminalInfoString(TERMINAL_COMMONDATA_PATH), InpDatabaseFile);

   LoadSymbols();
   SeedSymbolSpecs();

   // الاشتراك بدفتر الأوامر — من فشل رمزه لا يوقف الباقي
   if(InpEnableDepth)
      for(int i = 0; i < SymCount; i++)
         if(!MarketBookAdd(Syms[i]))
            PrintFormat("? [DEPTH] تعذّر الاشتراك بعمق %s (قد لا يوفّره الوسيط)", Syms[i]);

   EventSetMillisecondTimer(InpPollMs > 0 ? InpPollMs : 10);

   PrintFormat("? QUANT_NQ v%s (build %s) | %d رمز | نبضة %dms | magic=%I64d | عمق=%s",
               QNQ_VERSION, TimeToString(__DATETIME__, TIME_DATE|TIME_MINUTES),
               SymCount, InpPollMs, InpMagic,
               InpEnableDepth ? "on" : "off");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(InpEnableDepth)
      for(int i = 0; i < SymCount; i++) MarketBookRelease(Syms[i]);
   ClearPanel();
   if(DbHandle != INVALID_HANDLE) { DatabaseClose(DbHandle); DbHandle = INVALID_HANDLE; }
   PrintFormat("? QUANT_NQ توقّف — السبب %d | تكّات=%d أوامر=%d",
               reason, (int)TicksCaptured, (int)CmdReceived);
}

void OnTimer()
{
   if(!BridgeUp && !BridgeOpen()) return;

   ulong now = GetTickCount64();

   ReadPlatformState();      // فوري عند تغيّر الأعلام + ختم كل ثانية (داخليًّا)
   PumpPrices();             // نبضة 10ms — و OnTick يكتب فور وقوع التكّة

   // العمق: يُكتب الرمز الذي تغيّر دفتره فقط (علَم OnBookEvent) — فوري
   if(InpEnableDepth)
      for(int i = 0; i < SymCount; i++)
         if(DepthDirty[i]) { DepthDirty[i] = false; WriteDepth(i); }

   // المراكز: فورًا عند OnTradeTransaction، وإلا كل 500ms (حدّ 609: عشر ثوانٍ)
   if(now - g_last_pos_ms >= 500) { g_last_pos_ms = now; SyncPositions(); }

   PumpAccount();            // فوري عند تغيّر الأرقام + ختم كل ثانية (داخليًّا)
   PumpCommands();           // كل نبضة — زمن استجابة الأوامر هو الغاية

   // التقويم والإحماء: على فترات، لا كل نبضة
   if(TimeCurrent() - g_last_cal >= InpCalRefreshS)
   { g_last_cal = TimeCurrent(); WriteCalendar(); }
   if(!g_warmup_ok || TimeCurrent() - g_last_warmup >= InpWarmupRefreshS)
   { g_last_warmup = TimeCurrent(); WriteCandlesHistory(); g_warmup_ok = (g_warmup_written > 0); }

   // الرسم معزول عن الجسر: كل 250ms مهما صغرت النبضة
   if(now - g_last_draw_ms >= 250)
   { g_last_draw_ms = now; ReadDisplay(); DrawPanel(); }
}

//+------------------------------------------------------------------+
//| v3.00 — بلا خنق: التكّة تُكتب فور وقوعها لا عند النبضة القادمة.      |
//| OnTick يصل لرمز الشارت فقط؛ نبضة 10ms تغطّي بقيّة الرموز.           |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!BridgeUp) return;
   PumpPrices();
}

//+------------------------------------------------------------------+
//| دفتر الأوامر تغيّر: علِّم الرمز ليُعاد كتابة عمقه في النبضة القادمة  |
//+------------------------------------------------------------------+
void OnBookEvent(const string &symbol)
{
   if(!InpEnableDepth) return;
   for(int i = 0; i < SymCount; i++)
      if(Syms[i] == symbol) { DepthDirty[i] = true; return; }
}

//+------------------------------------------------------------------+
//| الإغلاق يُلتقط هنا: السعر المنفَّذ لا يُعرف قبل وقوع الصفقة          |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &req,
                        const MqlTradeResult &res)
{
   // v3.00: المراكز فورًا عند أيّ حركة تداول — لا انتظار دورة الـ500ms
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD
      || trans.type == TRADE_TRANSACTION_POSITION)
   { SyncPositions(); g_last_pos_ms = GetTickCount64(); }

   if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
   if(!HistoryDealSelect(trans.deal)) return;
   // الفلترة بـmagic المركز (من صفقة الفتح) لا صفقة الإغلاق — الإغلاق
   // اليدوي magic=0 فكان يُرفض. تُحسب pos_magic أدناه ثم تُفلتر قبل الكتابة.

   long entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY && entry != DEAL_ENTRY_INOUT)
      return;

   ulong  pid   = (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
   string sym   = HistoryDealGetString(trans.deal, DEAL_SYMBOL);
   double px    = HistoryDealGetDouble (trans.deal, DEAL_PRICE);
   double vol   = HistoryDealGetDouble (trans.deal, DEAL_VOLUME);
   double pnl   = HistoryDealGetDouble (trans.deal, DEAL_PROFIT);   // P&L الوسيط الدقيق
   // حكم المالك 2026-08-16: تكاليف التنفيذ من صفقة الخروج نفسها، لا مجموعة
   // من مكان اخر. DEAL_PROFIT وحده كان يُقرأ كأداء سوق بينما جزء منه ثمن
   // دخول — فأوقف القاطع التداول على «خسائر» هي في الحقيقة تكلفة.
   double d_comm = HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);
   double d_swap = HistoryDealGetDouble(trans.deal, DEAL_SWAP);
   double d_fee  = HistoryDealGetDouble(trans.deal, DEAL_FEE);
   datetime ct  = (datetime)HistoryDealGetInteger(trans.deal, DEAL_TIME);

   long dtype   = HistoryDealGetInteger(trans.deal, DEAL_TYPE);
   string side  = (dtype == DEAL_TYPE_SELL) ? "BUY" : "SELL";

   string reason = "MANUAL";
   long dr = HistoryDealGetInteger(trans.deal, DEAL_REASON);
   if(dr == DEAL_REASON_SL)          reason = "SL";
   else if(dr == DEAL_REASON_TP)     reason = "TP";
   else if(dr == DEAL_REASON_SO)     reason = "MARGIN";
   else if(dr == DEAL_REASON_EXPERT) reason = "SYSTEM";

   double open_px = 0; datetime open_tm = 0;
   long pos_magic = -1;
   bool still_open = PositionSelectByTicket(pid);
   if(still_open)
   {
      open_px = PositionGetDouble(POSITION_PRICE_OPEN);
      open_tm = (datetime)PositionGetInteger(POSITION_TIME);
      pos_magic = PositionGetInteger(POSITION_MAGIC);
   }
   else if(HistorySelectByPosition(pid))
   {
      int deals = HistoryDealsTotal();
      for(int d = 0; d < deals; d++)
      {
         ulong dticket = HistoryDealGetTicket(d);
         if(dticket == 0) continue;
         if(HistoryDealGetInteger(dticket, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;
         open_px = HistoryDealGetDouble(dticket, DEAL_PRICE);
         open_tm = (datetime)HistoryDealGetInteger(dticket, DEAL_TIME);
         pos_magic = HistoryDealGetInteger(dticket, DEAL_MAGIC);
         break;
      }
   }

   if(pos_magic != InpMagic) return;

   // الحدود الثلاثة قُرئت من الصفقة فعلا، فتُكتب كأرقام معلومة لا كصفر مفترض.
   WriteEvent(still_open ? "PARTIAL" : "CLOSED", pid, sym, side, vol,
              open_px, px, open_tm, ct, reason, "", pnl,
              d_comm, d_swap, d_fee, true);
}
//+------------------------------------------------------------------+
