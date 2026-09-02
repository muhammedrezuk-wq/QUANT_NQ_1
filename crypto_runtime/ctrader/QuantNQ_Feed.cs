using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using cAlgo.API;
using cAlgo.API.Internals;

// MARKET-DATA-ONLY bridge. It must never place, modify, or close orders.
// MT5 is the sole execution authority; cTrader supplies reference market truth.
//
// v3.0.0 (2026-08-19) -- multi-symbol feed, owner's direct order:
//   * Symbols list is a UI Parameter (comma separated). Broker symbol names
//     differ between brokers, so the owner edits the list from the cTrader
//     interface -- no recompile needed. Unknown names are skipped with an
//     alert (cTrader log + "missing" array in the start record); the robot
//     never crashes because of one bad name.
//   * ZERO throttling (owner's direct order): every tick and every depth
//     update the broker delivers is appended to the bridge file immediately.
//     TickMinMs / DepthMinMs from v2.0.0 are removed entirely.
//   * hb every 1 second with s:"*" (was 2 seconds in v2.0.0).
//   * spec at startup for every symbol + periodic refresh (SpecRefreshSeconds).
//   * broker identity field on EVERY record type (v2.0.0 had it on tick/start
//     only); account_id/s/ts/sequence identity unchanged.
//   * Single monotonic sequence counter for the whole account across all
//     record types, injected under one lock -- unchanged from v2.0.0.
//
// Reader contract: atoms/617_cTrader_feed/atom.py (QUANT_NQ). File path and
// every field name are frozen -- the atom must read this output unchanged.
namespace cAlgo.Robots
{
    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess)]
    public class QuantNQ_Feed : Robot
    {
        [Parameter("Bridge file path (empty = auto)", DefaultValue = "")]
        public string BridgeFilePath { get; set; }

        [Parameter("Symbols (comma separated)", DefaultValue = "BTCUSD,XAUUSD,EURUSD,GBPUSD,US30,USTEC,US500,DXY")]
        public string SymbolsCsv { get; set; }

        [Parameter("Spec refresh seconds (0 = start only)", DefaultValue = 300)]
        public int SpecRefreshSeconds { get; set; }

        [Parameter("Maximum bridge file MB", DefaultValue = 50)]
        public int MaxBridgeFileMb { get; set; }

        private const string BuildId = "QUANT_NQ_CTRADER_FEED_3.0.0";
        private string _file;
        private readonly object _lock = new object();
        private StreamWriter _writer;
        private readonly List<Symbol> _symbols = new List<Symbol>();
        private readonly HashSet<string> _badQuoteWarned = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        private long _sequence;
        private long _heartbeats;

        protected override void OnStart()
        {
            _file = ResolveFilePath(BridgeFilePath);
            var names = (SymbolsCsv ?? "")
                .Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries)
                .Select(x => x.Trim())
                .Where(x => x.Length > 0)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToArray();

            if (names.Length == 0)
                names = new[] { Symbol.Name };

            var missing = new List<string>();
            foreach (var name in names)
            {
                Symbol symbol;
                try { symbol = Symbols.GetSymbol(name); }
                catch { symbol = null; }

                if (symbol == null)
                {
                    // Broker does not list this name: skip it, announce it, keep going.
                    missing.Add(name);
                    Print("QuantNQ_Feed: symbol not found at this broker, skipped: " + name);
                    continue;
                }

                _symbols.Add(symbol);
                symbol.Tick += args => OnTick(symbol);
                WriteSpec(symbol);

                try
                {
                    var depth = MarketData.GetMarketDepth(symbol.Name);
                    depth.Updated += () => OnDepth(symbol.Name, depth);
                }
                catch (Exception ex)
                {
                    Print("QuantNQ_Feed: depth unavailable for " + symbol.Name + ": " + ex.Message);
                }
            }

            WriteLine(Head("start", "*") +
                      ",\"ts\":" + F(NowUtc()) +
                      ",\"symbols\":[" + string.Join(",", _symbols.Select(s => Js(s.Name))) + "]" +
                      ",\"missing\":[" + string.Join(",", missing.Select(Js)) + "]}");
            Timer.Start(TimeSpan.FromSeconds(1));
            Print("QuantNQ_Feed " + BuildId + " started -> " + _file +
                  " | symbols: " + _symbols.Count + " | missing: " + missing.Count);
        }

        private string ResolveFilePath(string configured)
        {
            if (!string.IsNullOrWhiteSpace(configured))
                return configured.Trim();

            var documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
            var directory = Path.Combine(documents, "cTrader", "User Files", "quant_nq_bridge");
            Directory.CreateDirectory(directory);
            return Path.Combine(directory, "ctrader_bridge.jsonl");
        }

        // §٣٠ — الوسيط جزء من هوية الحساب، و`provider` مصدر تغذية لا وسيط.
        // اسم الوسيط موجود في cTrader نفسه (`Account.BrokerName`)؛ يُرسَل من
        // مصدره ولا يُخمَّن في المصبّ. بدونه تُرفض التِكّة بهوية ناقصة.
        // v3.0.0: كتلة الهوية موحّدة هنا وتُرسَل مع كل الأنواع بلا استثناء.
        private string Head(string type, string symbol)
        {
            return "{\"t\":" + Js(type) +
                   ",\"provider\":\"CTRADER\"" +
                   ",\"build\":" + Js(BuildId) +
                   ",\"account_id\":" + Js(Account.Number.ToString(CultureInfo.InvariantCulture)) +
                   ",\"broker\":" + Js(Account.BrokerName) +
                   ",\"s\":" + Js(symbol);
        }

        private void OnTick(Symbol symbol)
        {
            var bid = symbol.Bid;
            var ask = symbol.Ask;
            // 617 contract: bid must be > 0 and ask >= bid, otherwise the reader
            // rejects the line as invalid input and flags feed health. A zero or
            // crossed quote (closed market, session boundary) is a broker glitch,
            // not a market tick -- skip that write. This is NOT throttling: every
            // valid tick is written the instant it arrives, with no delay ever.
            if (bid <= 0 || ask < bid)
            {
                if (_badQuoteWarned.Add(symbol.Name))
                    Print("QuantNQ_Feed: invalid quote skipped for " + symbol.Name +
                          " (bid=" + F(bid) + " ask=" + F(ask) + ")");
                return;
            }
            WriteLine(Head("tick", symbol.Name) +
                      ",\"bid\":" + F(bid) + ",\"ask\":" + F(ask) +
                      ",\"price\":" + F((bid + ask) / 2.0) +
                      ",\"ts\":" + F(NowUtc()) + "}");
        }

        private void OnDepth(string name, MarketDepth depth)
        {
            var builder = new StringBuilder(256);
            builder.Append(Head("depth", name)).Append(",\"bid\":[");
            var first = true;
            foreach (var entry in depth.BidEntries)
            {
                if (!first) builder.Append(',');
                first = false;
                builder.Append("{\"p\":").Append(F(entry.Price)).Append(",\"v\":").Append(F(entry.VolumeInUnits)).Append('}');
            }
            builder.Append("],\"ask\":[");
            first = true;
            foreach (var entry in depth.AskEntries)
            {
                if (!first) builder.Append(',');
                first = false;
                builder.Append("{\"p\":").Append(F(entry.Price)).Append(",\"v\":").Append(F(entry.VolumeInUnits)).Append('}');
            }
            builder.Append("],\"ts\":").Append(F(NowUtc())).Append('}');
            WriteLine(builder.ToString());
        }

        private void WriteSpec(Symbol symbol)
        {
            WriteLine(Head("spec", symbol.Name) +
                      ",\"digits\":" + symbol.Digits.ToString(CultureInfo.InvariantCulture) +
                      ",\"point\":" + F(symbol.PipSize) +
                      ",\"tick_size\":" + F(symbol.TickSize) +
                      ",\"tick_value\":" + F(symbol.TickValue) +
                      ",\"contract_size\":" + F(symbol.LotSize) +
                      ",\"spread\":" + F(symbol.Spread) +
                      ",\"volume_min\":" + F(symbol.VolumeInUnitsMin / symbol.LotSize) +
                      ",\"volume_max\":" + F(symbol.VolumeInUnitsMax / symbol.LotSize) +
                      ",\"volume_step\":" + F(symbol.VolumeInUnitsStep / symbol.LotSize) +
                      ",\"volume_min_units\":" + F(symbol.VolumeInUnitsMin) +
                      ",\"volume_max_units\":" + F(symbol.VolumeInUnitsMax) +
                      ",\"volume_step_units\":" + F(symbol.VolumeInUnitsStep) +
                      ",\"volume_unit\":\"lots\"" +
                      ",\"ts\":" + F(NowUtc()) + "}");
        }

        protected override void OnTimer()
        {
            // 1-second pulse: heartbeat every beat; full spec refresh every
            // SpecRefreshSeconds beats (0 disables the refresh, start-only).
            WriteLine(Head("hb", "*") + ",\"ts\":" + F(NowUtc()) + "}");
            _heartbeats++;
            if (SpecRefreshSeconds > 0 && _heartbeats % Math.Max(1, SpecRefreshSeconds) == 0)
            {
                foreach (var symbol in _symbols)
                    WriteSpec(symbol);
            }
        }

        protected override void OnStop()
        {
            WriteLine(Head("stop", "*") + ",\"ts\":" + F(NowUtc()) + "}");
            lock (_lock)
            {
                _writer?.Flush();
                _writer?.Dispose();
                _writer = null;
            }
            Print("QuantNQ_Feed stopped.");
        }

        private void OpenWriter()
        {
            var directory = Path.GetDirectoryName(_file);
            if (!string.IsNullOrEmpty(directory)) Directory.CreateDirectory(directory);
            _writer = new StreamWriter(new FileStream(_file, FileMode.Append, FileAccess.Write, FileShare.ReadWrite), new UTF8Encoding(false))
            {
                AutoFlush = true
            };
        }

        private void RotateIfNeeded(string nextLine)
        {
            if (MaxBridgeFileMb <= 0 || !System.IO.File.Exists(_file)) return;
            var nextBytes = Encoding.UTF8.GetByteCount(nextLine + Environment.NewLine);
            var maxBytes = (long)MaxBridgeFileMb * 1024L * 1024L;
            if (new FileInfo(_file).Length + nextBytes < maxBytes) return;
            _writer?.Flush();
            _writer?.Dispose();
            _writer = null;
            var archived = _file + "." + DateTime.UtcNow.ToString("yyyyMMdd_HHmmss");
            if (System.IO.File.Exists(archived)) archived += "_" + DateTime.UtcNow.Ticks;
            System.IO.File.Move(_file, archived);
            OpenWriter();
            Print("QuantNQ_Feed rotated bridge file -> " + archived);
        }

        private void WriteLine(string line)
        {
            try
            {
                lock (_lock)
                {
                    _sequence++;
                    line = WithSequence(line, _sequence);
                    if (_writer == null) OpenWriter();
                    RotateIfNeeded(line);
                    _writer.WriteLine(line);
                }
            }
            catch (Exception ex)
            {
                Print("QuantNQ_Feed write error: " + ex.Message);
            }
        }

        private static string WithSequence(string line, long sequence)
        {
            var end = line.LastIndexOf('}');
            if (end < 0) throw new InvalidDataException("bridge message is not a JSON object");
            return line.Substring(0, end) + ",\"sequence\":" +
                   sequence.ToString(CultureInfo.InvariantCulture) + line.Substring(end);
        }

        private static string Js(string value)
        {
            return "\"" + (value ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "").Replace("\n", "") + "\"";
        }

        private static string F(double value)
        {
            return value.ToString("R", CultureInfo.InvariantCulture);
        }

        private static long NowUtc()
        {
            return DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        }
    }
}
